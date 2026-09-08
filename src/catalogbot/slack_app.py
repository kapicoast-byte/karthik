"""Slack intake and the human-in-the-loop confirmation UI (§3, §8, §10).

NOTE (docs/open-questions.md Q1): reading the owner's DMs requires a Slack
*user* token with `im:history`, which usually needs workspace-admin approval.
Without it, only channels the bot is invited to plus @mentions are visible.
`capture_scope()` reports which mode we are actually running in.
"""

from __future__ import annotations

import json
import logging

from slack_bolt import App
from slack_sdk import WebClient

from .config import settings
from .models import Classification, SlackContext
from .pipeline import Decision, Outcome, Pipeline
from .store import PendingConfirmation, SessionLocal

log = logging.getLogger(__name__)


def capture_scope() -> str:
    return "dms+channels" if settings.slack_user_token else "channels+mentions only"


def build_app(pipeline: Pipeline) -> App:
    app = App(
        token=settings.slack_bot_token,
        signing_secret=settings.slack_signing_secret or None,
    )

    @app.event("message")
    def on_message(event: dict, client: WebClient) -> None:
        if not _is_capturable(event):
            return
        _process(pipeline, client, event)

    @app.event("app_mention")
    def on_mention(event: dict, client: WebClient) -> None:
        _process(pipeline, client, event)

    @app.action("confirm_task")
    def on_confirm(ack, body: dict, client: WebClient) -> None:
        ack()
        _resolve_confirmation(pipeline, client, body, confirmed=True)

    @app.action("ignore_task")
    def on_ignore(ack, body: dict, client: WebClient) -> None:
        ack()
        _resolve_confirmation(pipeline, client, body, confirmed=False)

    return app


def _is_capturable(event: dict) -> bool:
    """§3: only real human messages, only from channels we are allowed to read."""
    if event.get("bot_id") or event.get("subtype") in {"message_changed", "bot_message"}:
        return False
    if event.get("user") == settings.slack_owner_user_id:
        return False  # the owner's own messages are not requests to themselves
    allowlist = settings.channel_allowlist
    return not allowlist or event.get("channel") in allowlist


def _process(pipeline: Pipeline, client: WebClient, event: dict) -> None:
    context = _context_from_event(client, event)
    thread = _thread_history(client, event)

    with SessionLocal() as session:
        try:
            decision = pipeline.handle_message(
                session, event.get("text", ""), context, thread
            )
        except Exception:
            log.exception("intake failed for %s", context.message_ts)
            return

    _report(client, decision, context)


def _context_from_event(client: WebClient, event: dict) -> SlackContext:
    channel_id = event["channel"]
    ts = event["ts"]
    permalink = ""
    try:
        permalink = client.chat_getPermalink(channel=channel_id, message_ts=ts)[
            "permalink"
        ]
    except Exception:  # a missing permalink must not block intake
        log.debug("no permalink for %s", ts)
    return SlackContext(
        channel_id=channel_id,
        channel_name=_channel_name(client, channel_id),
        sender_id=event.get("user", ""),
        sender_name=_user_name(client, event.get("user", "")),
        message_ts=ts,
        thread_ts=event.get("thread_ts"),
        permalink=permalink,
    )


def _thread_history(client: WebClient, event: dict) -> list[str] | None:
    """§8: pull the thread so the latest instruction wins."""
    thread_ts = event.get("thread_ts")
    if not thread_ts or thread_ts == event.get("ts"):
        return None
    try:
        replies = client.conversations_replies(
            channel=event["channel"], ts=thread_ts, limit=50
        )
    except Exception:
        log.warning("could not read thread %s", thread_ts)
        return None
    return [
        m.get("text", "")
        for m in replies.get("messages", [])
        if m.get("ts") != event.get("ts") and m.get("text")
    ]


def _report(client: WebClient, decision: Decision, context: SlackContext) -> None:
    """Everything the bot says goes to the owner, never to the source channel."""
    owner = settings.slack_owner_user_id
    if not owner or decision.outcome is Outcome.IGNORED:
        return

    if decision.outcome is Outcome.ASKED:
        client.chat_postMessage(
            channel=owner,
            text=decision.message,
            blocks=_confirmation_blocks(decision, context),
        )
    elif decision.outcome is Outcome.FILED:
        client.chat_postMessage(
            channel=owner,
            text=f":white_check_mark: Filed in Plane: {decision.message}",
        )


def _confirmation_blocks(decision: Decision, context: SlackContext) -> list[dict]:
    value = json.dumps(
        {"channel": context.channel_id, "ts": context.message_ts}
    )
    draft = decision.classification.draft if decision.classification else None
    summary = f"*{draft.task}*\n" if draft else ""
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"{summary}{decision.message}\n<{context.permalink}|View in Slack>",
            },
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "action_id": "confirm_task",
                    "style": "primary",
                    "text": {"type": "plain_text", "text": "Create task"},
                    "value": value,
                },
                {
                    "type": "button",
                    "action_id": "ignore_task",
                    "text": {"type": "plain_text", "text": "Not a task"},
                    "value": value,
                },
            ],
        },
    ]


def _resolve_confirmation(
    pipeline: Pipeline, client: WebClient, body: dict, *, confirmed: bool
) -> None:
    payload = json.loads(body["actions"][0]["value"])

    with SessionLocal() as session:
        pending = (
            session.query(PendingConfirmation)
            .filter_by(
                slack_channel_id=payload["channel"],
                slack_message_ts=payload["ts"],
                resolved=False,
            )
            .one_or_none()
        )
        if pending is None:
            return

        pending.resolved = True
        if not confirmed:
            session.commit()
            return

        result = Classification.model_validate_json(pending.payload_json)
        context = SlackContext(
            channel_id=payload["channel"],
            sender_id=body["user"]["id"],
            message_ts=payload["ts"],
        )
        decision = pipeline.file(session, result, context)

    client.chat_postMessage(
        channel=body["user"]["id"],
        text=f":white_check_mark: {decision.message}",
    )


def _channel_name(client: WebClient, channel_id: str) -> str:
    try:
        return client.conversations_info(channel=channel_id)["channel"].get("name", "")
    except Exception:
        return ""


def _user_name(client: WebClient, user_id: str) -> str:
    if not user_id:
        return ""
    try:
        profile = client.users_info(user=user_id)["user"]
        return profile.get("real_name") or profile.get("name", "")
    except Exception:
        return ""
