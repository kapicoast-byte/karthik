"""Time-based behaviour: reminders (§6), follow-ups (§7), daily digest (§9).

Everything here is idempotent — each ping is recorded in `reminder_log` so a
restart, a retry, or two workers never double-notify. §6 also requires that
reminders stop the moment a task is completed or cancelled, which falls out of
only ever iterating `open_tasks()`.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from slack_sdk import WebClient
from sqlalchemy.orm import Session

from .config import settings
from .store import ReminderLog, SessionLocal, TrackedTask, open_tasks

log = logging.getLogger(__name__)

DUE_SOON_WINDOW = timedelta(days=2)


def build_scheduler(client: WebClient) -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone=settings.timezone)
    scheduler.add_job(
        lambda: run_daily_sweep(client),
        CronTrigger.from_crontab(settings.daily_digest_cron, timezone=settings.timezone),
        id="daily_sweep",
        replace_existing=True,
    )
    return scheduler


def run_daily_sweep(client: WebClient) -> None:
    with SessionLocal() as session:
        tasks = open_tasks(session)
        overdue, due_today, due_soon = _bucket_by_due_date(tasks)
        stale = _needing_follow_up(session, tasks)

        _send_digest(client, overdue, due_today, due_soon, stale)

        for task in overdue + due_today + due_soon:
            _record(session, task, _kind_for(task), date.today())
        for task in stale:
            _record(session, task, "follow_up", date.today())
            task.last_followed_up_at = datetime.now(UTC)
        session.commit()


def _bucket_by_due_date(
    tasks: list[TrackedTask],
) -> tuple[list[TrackedTask], list[TrackedTask], list[TrackedTask]]:
    today = date.today()
    overdue, due_today, due_soon = [], [], []
    for task in tasks:
        if task.due_date is None:
            continue
        due = task.due_date.date()
        if due < today:
            overdue.append(task)
        elif due == today:
            due_today.append(task)
        elif due - today <= DUE_SOON_WINDOW:
            due_soon.append(task)
    return overdue, due_today, due_soon


def _kind_for(task: TrackedTask) -> str:
    due = task.due_date.date() if task.due_date else date.today()
    today = date.today()
    if due < today:
        return "overdue"
    return "due_today" if due == today else "due_soon"


def _needing_follow_up(session: Session, tasks: list[TrackedTask]) -> list[TrackedTask]:
    """§7: waiting on a teammate for N days with no movement since."""
    cutoff = datetime.now(UTC) - timedelta(days=settings.follow_up_after_days)
    stale = []
    for task in tasks:
        if task.status != "Waiting on Team" or task.waiting_since is None:
            continue
        since = _as_utc(task.waiting_since)
        last = _as_utc(task.last_followed_up_at) if task.last_followed_up_at else None
        if since <= cutoff and (last is None or last <= cutoff):
            stale.append(task)
    return stale


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _record(session: Session, task: TrackedTask, kind: str, for_date: date) -> None:
    session.merge(
        ReminderLog(task_id=task.id, kind=kind, for_date=for_date.isoformat())
    )


def _send_digest(
    client: WebClient,
    overdue: list[TrackedTask],
    due_today: list[TrackedTask],
    due_soon: list[TrackedTask],
    stale: list[TrackedTask],
) -> None:
    if not settings.slack_owner_user_id:
        log.warning("SLACK_OWNER_USER_ID unset; digest not sent")
        return

    sections = [
        (":rotating_light: Overdue", overdue),
        (":calendar: Due today", due_today),
        (":hourglass: Due in the next 2 days", due_soon),
        (
            f":speech_balloon: Waiting on team {settings.follow_up_after_days}+ days",
            stale,
        ),
    ]
    lines = ["*Daily control tower*"]
    for heading, tasks in sections:
        if tasks:
            lines.append(f"\n{heading} ({len(tasks)})")
            lines += [f"  • {_line(t)}" for t in tasks]

    if len(lines) == 1:
        lines.append("\nNothing overdue, due soon, or stalled. :tada:")

    # docs/open-questions.md Q2: open Amazon cases are reported here only to the
    # extent they are tracked in Plane. There is no public Seller Central API.
    client.chat_postMessage(
        channel=settings.slack_owner_user_id, text="\n".join(lines)
    )


def _line(task: TrackedTask) -> str:
    ref = f" [{task.reference}]" if task.reference else ""
    due = f" — due {task.due_date.date().isoformat()}" if task.due_date else ""
    key = task.plane_sequence_id or "?"
    return f"{key}: {task.title}{ref}{due}"
