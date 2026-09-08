"""State store.

The client spec never mentions a database, but §5 (no duplicates), §6 (stop
reminding once done), §7 (2-day timers) and §8 (thread monitoring) are all
impossible without one. This is the memory that makes them work.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    select,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    relationship,
    sessionmaker,
)

from .config import settings


def _now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class TrackedTask(Base):
    """One Slack request that became (or may become) one Plane work item."""

    __tablename__ = "tracked_tasks"
    __table_args__ = (
        # §5: the same Slack message can never produce two Plane items.
        UniqueConstraint("slack_channel_id", "slack_message_ts", name="uq_slack_msg"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    slack_channel_id: Mapped[str] = mapped_column(String(32))
    slack_message_ts: Mapped[str] = mapped_column(String(32))
    slack_thread_ts: Mapped[str | None] = mapped_column(String(32), nullable=True)
    slack_sender_id: Mapped[str] = mapped_column(String(32))
    slack_permalink: Mapped[str] = mapped_column(Text, default="")

    plane_issue_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    plane_sequence_id: Mapped[str | None] = mapped_column(String(32), nullable=True)

    title: Mapped[str] = mapped_column(Text)
    # Normalised form used for near-duplicate matching; see dedupe.py.
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    reference: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)

    status: Mapped[str] = mapped_column(String(32), default="New")
    due_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # §7: when we last saw movement from whoever we are waiting on.
    waiting_since: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_followed_up_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )

    # §8: the newest thread message already reflected in the Plane item.
    last_thread_ts_seen: Mapped[str | None] = mapped_column(String(32), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_now, onupdate=_now
    )

    reminders: Mapped[list[ReminderLog]] = relationship(
        back_populates="task", cascade="all, delete-orphan"
    )


class ReminderLog(Base):
    """§6: what we have already pinged about, so we never ping twice."""

    __tablename__ = "reminder_log"
    __table_args__ = (UniqueConstraint("task_id", "kind", "for_date", name="uq_ping"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tracked_tasks.id"))
    # "due_soon" | "due_today" | "overdue" | "follow_up"
    kind: Mapped[str] = mapped_column(String(24))
    for_date: Mapped[str] = mapped_column(String(10))
    sent_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    task: Mapped[TrackedTask] = relationship(back_populates="reminders")


class PendingConfirmation(Base):
    """§3/§10: a draft the bot has asked about but the user has not answered."""

    __tablename__ = "pending_confirmations"

    id: Mapped[int] = mapped_column(primary_key=True)
    slack_channel_id: Mapped[str] = mapped_column(String(32))
    slack_message_ts: Mapped[str] = mapped_column(String(32))
    # Serialised Classification, replayed if the user hits "Confirm".
    payload_json: Mapped[str] = mapped_column(Text)
    question: Mapped[str] = mapped_column(Text)
    asked_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)


_engine = create_engine(settings.database_url, future=True)
SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, future=True)


def init_db() -> None:
    """Create tables. Replace with Alembic migrations before production."""
    Base.metadata.create_all(_engine)


def find_by_slack_message(
    session: Session, channel_id: str, message_ts: str
) -> TrackedTask | None:
    return session.scalar(
        select(TrackedTask).where(
            TrackedTask.slack_channel_id == channel_id,
            TrackedTask.slack_message_ts == message_ts,
        )
    )


def open_tasks(session: Session) -> list[TrackedTask]:
    return list(
        session.scalars(
            select(TrackedTask).where(
                TrackedTask.status.not_in(("Completed", "Cancelled"))
            )
        )
    )
