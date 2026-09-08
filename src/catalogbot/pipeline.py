"""Intake pipeline: one Slack message in, one decision out.

    classify → guardrails → dedupe → (file in Plane | ask the user | drop)

Kept free of Slack and Plane transport details so it can be tested against the
labelled message sample without network access (docs/scope.md §7).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from sqlalchemy.orm import Session

from .classifier import AUTO_FILE_CONFIDENCE, Classifier
from .dedupe import find_duplicate, fingerprint
from .models import Classification, SlackContext, Verdict
from .plane import PlaneClient, render_description
from .store import PendingConfirmation, TrackedTask

log = logging.getLogger(__name__)


class Outcome(StrEnum):
    FILED = "filed"
    ASKED = "asked"
    DUPLICATE = "duplicate"
    IGNORED = "ignored"


@dataclass
class Decision:
    outcome: Outcome
    message: str
    classification: Classification | None = None
    task: TrackedTask | None = None


class Pipeline:
    def __init__(self, classifier: Classifier, plane: PlaneClient) -> None:
        self._classifier = classifier
        self._plane = plane

    def handle_message(
        self,
        session: Session,
        text: str,
        context: SlackContext,
        thread: list[str] | None = None,
    ) -> Decision:
        result = self._classifier.classify(text, context, thread)

        if result.verdict is Verdict.NOT_A_TASK:
            log.debug("ignored %s: %s", context.message_ts, result.reasoning)
            return Decision(Outcome.IGNORED, result.reasoning, result)

        draft = result.draft
        assert draft is not None  # guaranteed by _enforce_guardrails

        duplicate = find_duplicate(
            session,
            channel_id=context.channel_id,
            message_ts=context.message_ts,
            title=draft.task,
            reference=draft.reference,
        )
        if duplicate is not None and duplicate.certain:
            return Decision(
                Outcome.DUPLICATE,
                f"Already tracked ({duplicate.reason}).",
                result,
                duplicate.task,
            )

        # §10: ask rather than guess. An uncertain duplicate is also a question,
        # because silently dropping a real request is the worse failure.
        needs_human = (
            result.verdict is Verdict.AMBIGUOUS
            or result.confidence < AUTO_FILE_CONFIDENCE
            or duplicate is not None
        )
        if needs_human:
            question = result.clarifying_question or "Shall I add this as a task?"
            if duplicate is not None:
                question = (
                    f"This looks like a duplicate — {duplicate.reason}. "
                    "Add it anyway?"
                )
            session.add(
                PendingConfirmation(
                    slack_channel_id=context.channel_id,
                    slack_message_ts=context.message_ts,
                    payload_json=result.model_dump_json(),
                    question=question,
                )
            )
            session.commit()
            return Decision(Outcome.ASKED, question, result)

        return self.file(session, result, context)

    def file(
        self,
        session: Session,
        result: Classification,
        context: SlackContext,
    ) -> Decision:
        """Create the Plane work item and record it. Used on confirmation too."""
        draft = result.draft
        assert draft is not None

        issue = self._plane.create_issue(draft, render_description(draft, context))

        task = TrackedTask(
            slack_channel_id=context.channel_id,
            slack_message_ts=context.message_ts,
            slack_thread_ts=context.thread_ts,
            slack_sender_id=context.sender_id,
            slack_permalink=context.permalink,
            plane_issue_id=issue.get("id"),
            plane_sequence_id=str(issue.get("sequence_id") or ""),
            title=draft.task,
            fingerprint=fingerprint(draft.task),
            reference=draft.reference,
            status=draft.status.value,
            due_date=(
                datetime.combine(draft.due_date, datetime.min.time())
                if draft.due_date
                else None
            ),
            last_thread_ts_seen=context.message_ts,
        )
        session.add(task)
        session.commit()

        log.info("filed %s as Plane %s", context.message_ts, task.plane_sequence_id)
        return Decision(Outcome.FILED, f"Created {draft.task}", result, task)
