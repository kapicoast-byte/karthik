"""The interpretation layer (§3, §4, §10).

Takes one Slack message plus its thread context and returns a `Classification`.
The guardrails in §10 are enforced twice: stated in the prompt, and re-checked
in code by `_enforce_guardrails` — a prompt is guidance, not a guarantee.
"""

from __future__ import annotations

import logging

from .llm import Backend, Refusal, build_backend
from .models import Classification, SlackContext, Verdict

log = logging.getLogger(__name__)

# Above this, an ACTIONABLE message may be filed without asking. Start at 1.01
# (i.e. confirm everything) for the first weeks of Phase 1, then lower it once
# precision is measured against the labelled sample. See docs/scope.md §7.
AUTO_FILE_CONFIDENCE = 1.01

SYSTEM_PROMPT = """\
You triage Slack messages for a catalog-operations manager who sells on Amazon \
and Walmart. You decide whether a message is real work, and if so you extract \
it into a fixed template.

The person you work for is drowning in Slack. Filing junk tasks costs them more \
than missing one, because a task list they cannot trust is worthless. When \
unsure, return "ambiguous" and ask — never guess.

## What counts as a task
Actionable: someone asks for something to be done, a fix, an upload, a case to \
open, a price or listing change, a report, a deadline to hit.
NOT actionable: chit-chat, FYIs, acknowledgements ("thanks", "got it", "ok"), \
status updates that ask nothing, jokes, links shared without a request, and \
being mentioned in passing. Being @-mentioned is not by itself a task.

## Verdicts
- "actionable"  — a clear action is requested, and you know what it is.
- "ambiguous"   — plausibly work, but the action, the owner, or a critical \
detail is genuinely unclear. Set clarifying_question to the single most useful \
question to ask.
- "not_a_task"  — no action requested. Set draft to null.

## Hard rules — violating any of these is a failure
1. Never invent an ASIN, SKU, Item ID, case ID, URL, date, price, or product \
detail. Copy references character-for-character or leave them null.
2. Never derive a due_date from "ASAP", "urgent", "soon", "this week", or \
"end of day". Those set due_date_is_vague=true and leave due_date null. Only an \
explicitly stated calendar date becomes a due_date.
3. Never set owner unless a person is named as responsible.
4. Never set status to "Completed" or "Cancelled". New capture is always "New".
5. Never upgrade priority because a message sounds stressed. Priority is set \
only when stated ("critical", "high priority", "P1"). Otherwise "Not Specified".
6. Marketplace is set only when the message says or clearly implies it \
(an ASIN implies Amazon; a Walmart Item ID implies Walmart).

## Threads
When thread context is supplied, the LATEST valid instruction wins over earlier \
conflicting ones. Describe the current requirement, not the original one.

## Style
`task` is one imperative line naming the actual action — "Fix the bullet points \
on B08XYZ1234", not "Slack request from Priya". `details` carries the rest.
`reasoning` is one or two sentences explaining your verdict; the user reads it.
"""


class Classifier:
    def __init__(self, backend: Backend | None = None) -> None:
        self._backend = backend or build_backend()

    def classify(
        self,
        text: str,
        context: SlackContext,
        thread: list[str] | None = None,
    ) -> Classification:
        """Classify one message. Raises on API failure; caller decides retry."""
        try:
            result = self._backend.classify(
                SYSTEM_PROMPT,
                _build_prompt(text, context, thread),
                Classification,
            )
        except Refusal:
            # A declined message is never a silent drop — it goes to a human.
            log.warning("classifier refused message %s", context.message_ts)
            return Classification(
                verdict=Verdict.AMBIGUOUS,
                confidence=0.0,
                reasoning="Could not classify this message automatically.",
                clarifying_question="Is this a task? If so, what needs doing?",
                draft=None,
            )

        return _enforce_guardrails(result)


def _build_prompt(
    text: str, context: SlackContext, thread: list[str] | None
) -> str:
    parts = [
        f"Channel: {context.channel_name or context.channel_id}",
        f"From: {context.sender_name or context.sender_id}",
    ]
    if thread:
        parts.append(
            "\nThread so far (oldest first):\n"
            + "\n".join(f"  - {m}" for m in thread)
        )
    parts.append(f"\nMessage to classify:\n{text}")
    return "\n".join(parts)


def _enforce_guardrails(result: Classification) -> Classification:
    """Re-assert §10 in code. The prompt states these; this guarantees them."""
    draft = result.draft

    if result.verdict is Verdict.NOT_A_TASK:
        return result.model_copy(update={"draft": None, "clarifying_question": None})

    if draft is None:
        # A task verdict with no draft is unusable — demote it to a question.
        return result.model_copy(
            update={
                "verdict": Verdict.AMBIGUOUS,
                "clarifying_question": result.clarifying_question
                or "What exactly needs to be done here?",
            }
        )

    if draft.due_date_is_vague and draft.due_date is not None:
        # Rule 2: a vague deadline must not carry a concrete date.
        log.warning("dropped invented due_date %s", draft.due_date)
        draft = draft.model_copy(update={"due_date": None})

    if result.verdict is Verdict.AMBIGUOUS and not result.clarifying_question:
        result = result.model_copy(
            update={"clarifying_question": "Should I add this as a task?"}
        )

    return result.model_copy(update={"draft": draft})
