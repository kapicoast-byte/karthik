"""§10 must hold even when the model ignores the prompt."""

from datetime import date

from catalogbot.classifier import _enforce_guardrails
from catalogbot.models import Classification, TaskDraft, Verdict


def _classification(**overrides) -> Classification:
    base = dict(
        verdict=Verdict.ACTIONABLE,
        confidence=0.9,
        reasoning="A fix was requested.",
        draft=TaskDraft(task="Fix bullets on B08XYZ1234"),
    )
    return Classification(**{**base, "clarifying_question": None, **overrides})


def test_vague_deadline_never_carries_a_date():
    result = _enforce_guardrails(
        _classification(
            draft=TaskDraft(
                task="Fix bullets",
                due_date=date(2026, 9, 9),
                due_date_is_vague=True,
            )
        )
    )
    assert result.draft.due_date is None


def test_stated_date_is_kept():
    result = _enforce_guardrails(
        _classification(
            draft=TaskDraft(task="Fix bullets", due_date=date(2026, 9, 9))
        )
    )
    assert result.draft.due_date == date(2026, 9, 9)


def test_not_a_task_carries_no_draft():
    result = _enforce_guardrails(
        _classification(verdict=Verdict.NOT_A_TASK, reasoning="Just a thank-you.")
    )
    assert result.draft is None


def test_task_verdict_without_a_draft_becomes_a_question():
    result = _enforce_guardrails(_classification(draft=None))
    assert result.verdict is Verdict.AMBIGUOUS
    assert result.clarifying_question


def test_ambiguous_always_asks_something():
    result = _enforce_guardrails(
        _classification(verdict=Verdict.AMBIGUOUS, clarifying_question=None)
    )
    assert result.clarifying_question
