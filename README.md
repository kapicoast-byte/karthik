# catalogbot — Slack → Plane catalog task automation

Turns actionable Slack messages into Plane work items, chases due dates and
stalled hand-offs, and posts a daily control-tower digest.

**Status: Phase 1 skeleton.** The intake pipeline, classification schema,
guardrails and dedupe logic are implemented and unit-tested. The Slack and
Plane transport layers are written but not yet verified against live
credentials — that happens in Phase 0 (see below).

Start here:

- [`docs/scope.md`](docs/scope.md) — what the client asked for, what we will
  build, phasing, risks, acceptance criteria.
- [`docs/open-questions.md`](docs/open-questions.md) — the eight things we need
  answered. **Q1 and Q2 gate the quote.**

## How it works

```
Slack message
   ↓  slack_app.py     capture, filter, pull thread context
   ↓  classifier.py    LLM → validated Classification (models.py)
   ↓                   §10 guardrails re-checked in code
   ↓  dedupe.py        exact / reference / title-similarity match
   ↓  pipeline.py      file it · ask the user · skip as duplicate · ignore
   ↓  plane.py         create the Plane work item
       store.py        remember it (SQLite → Postgres)
       scheduler.py    reminders, 2-day follow-ups, daily digest
```

| Module | Spec § | Responsibility |
|---|---|---|
| `models.py` | 4 | The 13-field task template as a validated schema |
| `classifier.py` | 3, 4, 10 | Is this work? What work? Guardrails enforced twice |
| `dedupe.py` | 5 | Don't file the same thing twice |
| `pipeline.py` | 3, 5, 10 | The decision: file / ask / skip / ignore |
| `plane.py` | 5, 6 | Plane REST client, status and priority mapping |
| `store.py` | 5–8 | The state that makes dedupe and timers possible |
| `scheduler.py` | 6, 7, 9 | Reminders, follow-ups, daily digest |
| `slack_app.py` | 3, 8, 10 | Capture and the confirm / ignore buttons |

## Two design decisions worth knowing

**It asks before it files.** `AUTO_FILE_CONFIDENCE` in `classifier.py` starts at
`1.01` — deliberately unreachable, so every draft goes to the owner as a
Confirm / Not-a-task prompt. §10 is emphatic that a noisy task list is worse
than a missed message. Lower the threshold once precision is measured against a
labelled sample of real Slack history (`docs/scope.md` §7).

**Guardrails live in code, not only in the prompt.** `_enforce_guardrails()`
re-checks the §10 rules after every classification — a vague deadline can never
carry a date, a task verdict can never arrive without a draft, and a refusal
from the model becomes a question to a human rather than a silent drop.

## Running it

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env      # fill in the credentials
.venv/bin/python -m catalogbot.main
```

Tests need no credentials and no network:

```bash
.venv/bin/python -m pytest
```

## Before this can run against real data (Phase 0)

1. **Slack app + admin approval.** DM capture needs a user token
   (`im:history`, `mpim:history`, `channels:history`, `groups:history`).
   `capture_scope()` logs which mode is active. See open question Q1.
2. **Plane sandbox project.** Verify the endpoint paths and payload shapes in
   `plane.py` against a real project, and confirm the status→state mapping in
   `STATUS_TO_STATE_NAME` with the client (Q3).
3. **Labelled sample.** ~200 real Slack messages tagged task / not-a-task, from
   a history export. This is both the acceptance test and the tuning set.
4. **Replace `init_db()` with Alembic migrations** before anything touches
   production data.
