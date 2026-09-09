"""Plane Cloud REST client (§5).

Endpoint paths and payload shapes follow Plane's public API documentation and
MUST be verified against a sandbox project during Phase 0 (see docs/scope.md).
Every Plane call in this codebase goes through this module so that a schema
change is a one-file fix.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

import httpx2 as httpx

from .config import settings
from .models import Marketplace, Priority, Status, TaskDraft

log = logging.getLogger(__name__)

# §4 statuses → Plane states. The right-hand side is the *state name* in the
# client's project; resolved to a UUID at runtime by `resolve_state_ids`.
# Confirm this mapping with the client — docs/open-questions.md Q3.
STATUS_TO_STATE_NAME: dict[Status, str] = {
    Status.NEW: "Todo",
    Status.IN_PROGRESS: "In Progress",
    Status.WAITING_ON_ME: "Blocked",
    Status.WAITING_ON_TEAM: "Blocked",
    Status.WAITING_ON_MARKETPLACE: "Blocked",
    Status.COMPLETED: "Done",
    Status.CANCELLED: "Cancelled",
}

# The three "Waiting on ..." statuses collapse onto one Plane state, so the
# distinction is carried by a label.
STATUS_TO_LABEL: dict[Status, str] = {
    Status.WAITING_ON_ME: "waiting:me",
    Status.WAITING_ON_TEAM: "waiting:team",
    Status.WAITING_ON_MARKETPLACE: "waiting:marketplace",
}

PRIORITY_TO_PLANE: dict[Priority, str] = {
    Priority.CRITICAL: "urgent",
    Priority.HIGH: "high",
    Priority.MEDIUM: "medium",
    Priority.LOW: "low",
    Priority.NOT_SPECIFIED: "none",
}


class PlaneError(RuntimeError):
    pass


class PlaneClient:
    def __init__(
        self,
        api_key: str | None = None,
        workspace: str | None = None,
        project_id: str | None = None,
    ) -> None:
        self._workspace = workspace or settings.plane_workspace_slug
        self._project = project_id or settings.plane_project_id
        self._http = httpx.Client(
            base_url=settings.plane_base_url.rstrip("/"),
            # Plane matches this header name case-sensitively: a client that
            # normalises it (urllib title-cases it to X-Api-Key) gets a 403
            # "Given API token is not valid" for a perfectly good token.
            # httpx preserves the case given here; keep it exactly as written.
            headers={
                "X-API-Key": api_key or settings.plane_api_key,
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )
        self._state_ids: dict[str, str] = {}
        self._label_ids: dict[str, str] = {}

    # -- plumbing --------------------------------------------------------

    @property
    def _issues_path(self) -> str:
        return f"/api/v1/workspaces/{self._workspace}/projects/{self._project}/issues/"

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self._http.request(method, path, **kwargs)
        if response.status_code >= 400:
            raise PlaneError(
                f"Plane {method} {path} → {response.status_code}: {response.text[:500]}"
            )
        return response.json() if response.content else None

    def close(self) -> None:
        self._http.close()

    # -- metadata --------------------------------------------------------

    def resolve_state_ids(self) -> dict[str, str]:
        """Map state name → UUID. Cached for the life of the client."""
        if not self._state_ids:
            path = (
                f"/api/v1/workspaces/{self._workspace}"
                f"/projects/{self._project}/states/"
            )
            payload = self._request("GET", path)
            self._state_ids = {s["name"]: s["id"] for s in payload.get("results", [])}
        return self._state_ids

    def resolve_label_ids(self) -> dict[str, str]:
        if not self._label_ids:
            path = (
                f"/api/v1/workspaces/{self._workspace}"
                f"/projects/{self._project}/labels/"
            )
            payload = self._request("GET", path)
            self._label_ids = {l["name"]: l["id"] for l in payload.get("results", [])}
        return self._label_ids

    # -- work items ------------------------------------------------------

    def create_issue(self, draft: TaskDraft, description: str) -> dict[str, Any]:
        body: dict[str, Any] = {
            "name": draft.task[:255],
            "description_html": description,
            "priority": PRIORITY_TO_PLANE[draft.priority],
        }

        state_name = STATUS_TO_STATE_NAME[draft.status]
        if state_id := self.resolve_state_ids().get(state_name):
            body["state"] = state_id
        else:
            log.warning("Plane state %r not found; using project default", state_name)

        if draft.due_date is not None:
            body["target_date"] = draft.due_date.isoformat()

        if label_ids := self._labels_for(draft):
            body["labels"] = label_ids

        return self._request("POST", self._issues_path, json=body)

    def update_status(self, issue_id: str, status: Status) -> dict[str, Any]:
        state_name = STATUS_TO_STATE_NAME[status]
        state_id = self.resolve_state_ids().get(state_name)
        if state_id is None:
            raise PlaneError(f"Plane state {state_name!r} does not exist")
        return self._request(
            "PATCH", f"{self._issues_path}{issue_id}/", json={"state": state_id}
        )

    def list_open_issues(self) -> list[dict[str, Any]]:
        """Open work items, used for duplicate detection and the daily digest."""
        payload = self._request("GET", self._issues_path, params={"expand": "state"})
        results = payload.get("results", []) if isinstance(payload, dict) else payload
        return [i for i in results if _state_group(i) not in {"completed", "cancelled"}]

    def issues_due_on_or_before(self, cutoff: date) -> list[dict[str, Any]]:
        return [
            issue
            for issue in self.list_open_issues()
            if (target := issue.get("target_date"))
            and date.fromisoformat(target[:10]) <= cutoff
        ]

    # -- helpers ---------------------------------------------------------

    def _labels_for(self, draft: TaskDraft) -> list[str]:
        wanted = [
            name
            for name in (
                STATUS_TO_LABEL.get(draft.status),
                None
                if draft.marketplace is Marketplace.NOT_SPECIFIED
                else draft.marketplace.value.lower(),
            )
            if name
        ]
        known = self.resolve_label_ids()
        missing = [n for n in wanted if n not in known]
        if missing:
            # Creating labels silently would mean inventing project structure.
            log.warning("Plane labels not present, skipping: %s", missing)
        return [known[n] for n in wanted if n in known]


def _state_group(issue: dict[str, Any]) -> str:
    state = issue.get("state")
    if isinstance(state, dict):
        return str(state.get("group", "")).lower()
    return ""


def render_description(draft: TaskDraft, context: Any) -> str:
    """§5: full context and Slack provenance live in the task description."""
    rows = [
        ("Marketplace", draft.marketplace.value),
        ("Priority", draft.priority.value),
        ("Owner", draft.owner or "Not Specified"),
        ("Reference", draft.reference or "—"),
        ("Source", "Slack"),
        ("Slack channel", getattr(context, "channel_name", "") or context.channel_id),
        ("Slack sender", getattr(context, "sender_name", "") or context.sender_id),
    ]
    if draft.due_date_is_vague:
        rows.append(("Deadline", "Stated vaguely (e.g. ASAP) — no date set"))

    table = "".join(f"<li><b>{k}:</b> {v}</li>" for k, v in rows)
    permalink = getattr(context, "permalink", "")
    link = f'<p><a href="{permalink}">Original Slack message</a></p>' if permalink else ""
    details = f"<p>{draft.details}</p>" if draft.details else ""
    return f"{details}<ul>{table}</ul>{link}"
