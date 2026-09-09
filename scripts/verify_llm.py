#!/usr/bin/env python3
"""Measure the classifier against the labelled sample in eval/messages.json.

This is the §7 acceptance test, not a smoke test. It reports the two numbers
the client cares about - precision and recall - plus the one criterion that is
pass/fail rather than a percentage: whether the model ever invents a reference
or a date that is not in the message.

    python scripts/verify_llm.py            # run every case
    python scripts/verify_llm.py --limit 5  # a cheap sample first

Works against whichever provider LLM_PROVIDER selects, so a free model and a
paid one can be compared on identical inputs.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

# .env before importing settings, which reads the environment at import time.
dotenv = ROOT / ".env"
if dotenv.exists():
    for line in dotenv.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))

from catalogbot.classifier import Classifier  # noqa: E402
from catalogbot.config import settings  # noqa: E402
from catalogbot.llm import LLMError  # noqa: E402
from catalogbot.models import SlackContext, Verdict  # noqa: E402

OK, BAD, WARN, INFO = "  ok ", " FAIL", " warn", "     "


def fabrications(text: str, draft) -> list[str]:
    """§10: anything in the draft that is not in the message is invented."""
    if draft is None:
        return []
    found = []

    if draft.reference:
        # Compare loosely - the model may reformat B08-XYZ-1234 as B08XYZ1234.
        loose = re.sub(r"[^A-Za-z0-9]", "", draft.reference).upper()
        haystack = re.sub(r"[^A-Za-z0-9]", "", text).upper()
        if loose and loose not in haystack:
            found.append(f"reference {draft.reference!r} is not in the message")

    if draft.due_date is not None:
        # A real date needs some digit run from the message to back it up.
        digits = set(re.findall(r"\d+", text))
        stated = {
            str(draft.due_date.day),
            f"{draft.due_date.day:02d}",
            str(draft.due_date.year),
        }
        if not (digits & stated):
            found.append(f"due date {draft.due_date} has no basis in the message")

    return found


def main() -> int:
    cases = json.loads((ROOT / "eval" / "messages.json").read_text(encoding="utf-8"))
    if "--limit" in sys.argv:
        cases = cases[: int(sys.argv[sys.argv.index("--limit") + 1])]

    print(f"{INFO} provider {settings.llm_provider} · model {settings.classifier_model}")
    print(f"{INFO} {len(cases)} labelled messages\n")

    try:
        classifier = Classifier()
    except LLMError as error:
        print(f"{BAD} {error}")
        return 2

    results, invented, errors = [], [], []

    for case in cases:
        context = SlackContext(
            channel_id="C0TEST",
            channel_name=case["channel"],
            sender_id="U0TEST",
            sender_name=case["sender"],
            message_ts=case["id"],
        )
        try:
            result = classifier.classify(case["text"], context)
        except LLMError as error:
            print(f"{BAD} {case['id']}  {str(error)[:110]}")
            errors.append(case["id"])
            continue

        got, want = result.verdict.value, case["expect"]
        results.append((case, got))

        made_up = fabrications(case["text"], result.draft)
        invented += [(case["id"], reason) for reason in made_up]

        mark = OK if got == want else WARN
        title = result.draft.task if result.draft else "—"
        print(f"{mark} {case['id']}  want {want:<11} got {got:<11} {title[:48]}")
        for reason in made_up:
            print(f"{BAD}       INVENTED: {reason}")

    _summarise(results, invented, errors)
    return 1 if (invented or errors) else 0


def _summarise(results, invented, errors) -> None:
    print()
    if not results:
        print(f"{BAD} no cases completed")
        return

    # A "filed" prediction is anything the system would turn into a task.
    # Ambiguous is not a failure - §10 wants a question, not a guess.
    true_positive = sum(
        1 for c, g in results if c["expect"] == "actionable" and g == Verdict.ACTIONABLE
    )
    false_positive = sum(
        1 for c, g in results if c["expect"] == "not_a_task" and g == Verdict.ACTIONABLE
    )
    missed = sum(
        1 for c, g in results if c["expect"] == "actionable" and g == Verdict.NOT_A_TASK
    )
    real = sum(1 for c, _ in results if c["expect"] == "actionable")

    precision = true_positive / (true_positive + false_positive or 1)
    recall = true_positive / (real or 1)
    exact = sum(1 for c, g in results if c["expect"] == g) / len(results)

    print(f"{INFO} exact verdict match  {exact:.0%}")
    print(f"{INFO} precision            {precision:.0%}  (target ≥ 90%)")
    print(f"{INFO} recall               {recall:.0%}  (target ≥ 80%)")
    if false_positive:
        print(f"{WARN} {false_positive} chatter message(s) would have become tasks")
    if missed:
        print(f"{WARN} {missed} real task(s) dropped silently")

    print()
    if errors:
        print(f"{BAD} {len(errors)} case(s) errored: {', '.join(errors)}")
    if invented:
        print(f"{BAD} {len(invented)} fabrication(s) — this criterion is pass/fail")
    elif precision >= 0.9 and recall >= 0.8:
        print(f"{OK} meets the §7 acceptance criteria on this sample")
    else:
        print(f"{WARN} below target — tune the prompt, or use a stronger model")


if __name__ == "__main__":
    sys.exit(main())
