"""The task template from §4 of the client spec, as a validated schema.

This module is the contract between the classifier and everything downstream.
The classifier is asked to emit exactly `Classification`; nothing else in the
system invents fields the client did not ask for.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum

from pydantic import BaseModel, Field


class Marketplace(StrEnum):
    AMAZON = "Amazon"
    WALMART = "Walmart"
    OTHER = "Other"
    NOT_SPECIFIED = "Not Specified"


class Priority(StrEnum):
    CRITICAL = "Critical"
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"
    NOT_SPECIFIED = "Not Specified"


class Status(StrEnum):
    NEW = "New"
    IN_PROGRESS = "In Progress"
    WAITING_ON_ME = "Waiting on Me"
    WAITING_ON_TEAM = "Waiting on Team"
    WAITING_ON_MARKETPLACE = "Waiting on Marketplace"
    COMPLETED = "Completed"
    CANCELLED = "Cancelled"


class Verdict(StrEnum):
    """What the classifier concluded about a message.

    Only ACTIONABLE may be filed without a human in the loop, and only when
    `confidence` clears the threshold in `classifier.py`.
    """

    ACTIONABLE = "actionable"
    AMBIGUOUS = "ambiguous"
    NOT_A_TASK = "not_a_task"


class SlackContext(BaseModel):
    """Provenance. Populated from the Slack event, never by the model."""

    channel_id: str
    channel_name: str = ""
    sender_id: str
    sender_name: str = ""
    message_ts: str
    thread_ts: str | None = None
    permalink: str = ""


class TaskDraft(BaseModel):
    """§4 task template. Every field the model may not know is optional."""

    task: str = Field(description="The action to be completed, imperative, one line.")
    marketplace: Marketplace = Marketplace.NOT_SPECIFIED
    priority: Priority = Priority.NOT_SPECIFIED
    due_date: date | None = Field(
        default=None,
        description=(
            "Only when an exact date is stated. Never derive one from 'ASAP', "
            "'soon', or 'urgent' — leave null and set due_date_is_vague."
        ),
    )
    due_date_is_vague: bool = Field(
        default=False,
        description="True when a deadline was implied ('ASAP') but not stated.",
    )
    owner: str | None = Field(default=None, description="Only when explicitly stated.")
    details: str = Field(default="", description="Instructions and context, verbatim.")
    reference: str | None = Field(
        default=None,
        description=(
            "ASIN, SKU, Item ID, case ID or URL — copied character-for-character "
            "from the message. Never reconstructed or guessed."
        ),
    )
    status: Status = Status.NEW


class Classification(BaseModel):
    """What the classifier returns for one Slack message."""

    verdict: Verdict
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str = Field(description="One or two sentences. Shown to the user.")
    clarifying_question: str | None = Field(
        default=None,
        description="Required when verdict is 'ambiguous'; null otherwise.",
    )
    draft: TaskDraft | None = Field(
        default=None,
        description="Required unless verdict is 'not_a_task'.",
    )
