"""Duplicate detection (§5).

Three checks, cheapest first:

1. Same Slack message → already handled. Exact, free, catches retried events.
2. Same reference (ASIN/SKU/case ID) on an open task → almost certainly the
   same work item.
3. Similar title on an open task → likely duplicate, worth asking about.

Only (1) is treated as certain. (2) and (3) return a *candidate*; the caller
decides whether to skip, link, or ask the user. §10 says do not create
duplicates — it does not say silently discard work, so an uncertain match
becomes a question, not a deletion.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from sqlalchemy.orm import Session

from .store import TrackedTask, find_by_slack_message, open_tasks

# Words that carry no signal when comparing two task titles.
_STOPWORDS = frozenset(
    """a an and the to for of on in at is are be please can you we i our my
    need needs needed asap urgent kindly thanks thank hi hey hello""".split()
)

TITLE_SIMILARITY_THRESHOLD = 0.82


@dataclass(frozen=True)
class DuplicateMatch:
    task: TrackedTask
    reason: str
    certain: bool


def normalise_reference(reference: str | None) -> str | None:
    """Case- and punctuation-insensitive form of an ASIN / SKU / case ID."""
    if not reference:
        return None
    cleaned = re.sub(r"[^A-Za-z0-9]", "", reference).upper()
    return cleaned or None


def fingerprint(title: str) -> str:
    """Stable hash of a title's meaningful words, order-independent."""
    words = sorted(set(_significant_words(title)))
    return hashlib.sha256(" ".join(words).encode()).hexdigest()[:32]


def _significant_words(title: str) -> list[str]:
    tokens = re.findall(r"[a-z0-9]+", title.lower())
    return [t for t in tokens if t not in _STOPWORDS and len(t) > 1]


def title_similarity(left: str, right: str) -> float:
    a, b = " ".join(_significant_words(left)), " ".join(_significant_words(right))
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def find_duplicate(
    session: Session,
    *,
    channel_id: str,
    message_ts: str,
    title: str,
    reference: str | None,
) -> DuplicateMatch | None:
    if existing := find_by_slack_message(session, channel_id, message_ts):
        return DuplicateMatch(existing, "same Slack message", certain=True)

    normalised = normalise_reference(reference)
    candidates = open_tasks(session)

    if normalised:
        for task in candidates:
            if normalise_reference(task.reference) == normalised:
                return DuplicateMatch(
                    task, f"open task already references {reference}", certain=False
                )

    best: tuple[float, TrackedTask] | None = None
    for task in candidates:
        score = title_similarity(title, task.title)
        if score >= TITLE_SIMILARITY_THRESHOLD and (best is None or score > best[0]):
            best = (score, task)

    if best is not None:
        score, task = best
        return DuplicateMatch(
            task, f"title {score:.0%} similar to {task.title!r}", certain=False
        )

    return None
