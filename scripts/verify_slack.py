#!/usr/bin/env python3
"""Check the Slack side the way verify_plane.py checks Plane.

Proves each capability §3 depends on, one at a time, and says which scope is
missing when one fails. Finishes by posting the real confirmation card to the
owner's DM, so the human-in-the-loop UI can be seen rather than imagined.

    python scripts/verify_slack.py            # check, and post the test card
    python scripts/verify_slack.py --quiet    # check without posting anything

Exit code 0 means the Slack layer is sound.
"""

from __future__ import annotations

import http.client
import json
import os
import pathlib
import sys
import urllib.parse

ROOT = pathlib.Path(__file__).resolve().parent.parent
OK, BAD, WARN, INFO = "  ok ", " FAIL", " warn", "     "

# Bot scopes the service actually uses, and what breaks without each.
REQUIRED_BOT_SCOPES = {
    "app_mentions:read": "capture @mentions (§3)",
    "channels:history": "read public-channel messages (§3)",
    "channels:read": "resolve channel names for the task description (§5)",
    "chat:write": "post questions, confirmations and the daily digest (§9)",
    "groups:history": "read private channels the bot is invited to (§3)",
    "im:history": "read DMs with the bot (§3)",
    "im:write": "open a DM to ask the owner a question (§10)",
    "users:read": "resolve sender names (§4)",
}

# Reading the owner's own DMs needs a user token - open question Q1.
REQUIRED_USER_SCOPES = {
    "im:history": "read the owner's direct messages (§3)",
    "channels:history": "read the owner's public channels (§3)",
}


def is_placeholder(value: str) -> bool:
    """.env.example ships values like `xoxb-...`, which are truthy but useless.

    Without this, a copied-but-unedited .env sails past every "is it set?"
    check and fails later with a confusing auth error.
    """
    return not value or value.endswith("...") or value in {"xoxb-", "xoxp-", "xapp-"}


def load_env() -> dict[str, str]:
    env = dict(os.environ)
    dotenv = ROOT / ".env"
    if dotenv.exists():
        for line in dotenv.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                env.setdefault(key.strip(), value.strip().strip("\"'"))
    return env


def call(
    token: str, method: str, params: dict | None = None, as_json: bool = False
) -> tuple[dict, dict[str, str]]:
    """One Slack Web API call. Returns (payload, response headers)."""
    connection = http.client.HTTPSConnection("slack.com", timeout=30)
    try:
        headers = {"Authorization": f"Bearer {token}"}
        if as_json:
            headers["Content-Type"] = "application/json; charset=utf-8"
            body = json.dumps(params or {})
        else:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            body = urllib.parse.urlencode(params or {})
        connection.request("POST", f"/api/{method}", body, headers)
        response = connection.getresponse()
        raw = response.read()
        received = {k.lower(): v for k, v in response.getheaders()}
        try:
            return json.loads(raw), received
        except json.JSONDecodeError:
            # Usually a proxy or firewall answering instead of Slack.
            text = raw.decode(errors="replace")[:160]
            return {"ok": False, "error": f"cannot reach slack.com — {text}"}, received
    except Exception as error:
        return {"ok": False, "error": f"{type(error).__name__}: {error}"}, {}
    finally:
        connection.close()


def check_scopes(
    granted: str, required: dict[str, str], label: str
) -> list[str]:
    have = {scope.strip() for scope in granted.split(",") if scope.strip()}
    missing = [scope for scope in required if scope not in have]
    if not have:
        print(f"{WARN} {label}: Slack did not report granted scopes")
    elif missing:
        print(f"{BAD} {label}: {len(missing)} scope(s) missing")
        for scope in missing:
            print(f"{INFO}   {scope:<20} needed to {required[scope]}")
    else:
        print(f"{OK} {label}: all {len(required)} required scopes granted")
    return missing


def confirmation_blocks() -> list[dict]:
    """The real card from slack_app.py, so the UI can be reviewed for real."""
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    "*Fix the bullet points on B08XYZ1234*\n"
                    "Priya asked for this in #catalog-ops. No date was given "
                    "(“asap”), so I have not set one.\n"
                    "_This is a test card from verify_slack.py._"
                ),
            },
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": (
                        "Marketplace *Amazon* · Priority *Not Specified* · "
                        "Reference *B08XYZ1234*"
                    ),
                }
            ],
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "action_id": "confirm_task",
                    "style": "primary",
                    "text": {"type": "plain_text", "text": "Create task"},
                    "value": "verify",
                },
                {
                    "type": "button",
                    "action_id": "ignore_task",
                    "text": {"type": "plain_text", "text": "Not a task"},
                    "value": "verify",
                },
            ],
        },
    ]


def main() -> int:
    env = load_env()
    bot = env.get("SLACK_BOT_TOKEN", "")
    user = env.get("SLACK_USER_TOKEN", "")
    app_token = env.get("SLACK_APP_TOKEN", "")
    owner = env.get("SLACK_OWNER_USER_ID", "")

    for name, value in (
        ("SLACK_BOT_TOKEN", bot),
        ("SLACK_USER_TOKEN", user),
        ("SLACK_APP_TOKEN", app_token),
        ("SLACK_OWNER_USER_ID", owner),
    ):
        if is_placeholder(value):
            env[name] = ""
    bot, user = env["SLACK_BOT_TOKEN"], env["SLACK_USER_TOKEN"]
    app_token, owner = env["SLACK_APP_TOKEN"], env["SLACK_OWNER_USER_ID"]

    if not bot:
        print(f"{BAD} set a real SLACK_BOT_TOKEN in .env first (starts xoxb-)")
        return 2

    failures: list[str] = []

    # 1. Who are we? --------------------------------------------------------
    identity, headers = call(bot, "auth.test")
    if not identity.get("ok"):
        error = identity.get("error")
        hint = {
            "invalid_auth": "token is wrong, revoked, or from another workspace",
            "account_inactive": "the app was uninstalled from this workspace",
        }.get(str(error), "")
        print(f"{BAD} auth.test → {error} {hint}")
        return 1

    print(
        f"{OK} bot authenticated as {identity.get('user')} "
        f"in workspace {identity.get('team')}"
    )
    failures += check_scopes(
        headers.get("x-oauth-scopes", ""), REQUIRED_BOT_SCOPES, "bot scopes"
    )

    # 2. The user token - the §3 / Q1 question -------------------------------
    print()
    if user:
        who, user_headers = call(user, "auth.test")
        if who.get("ok"):
            print(f"{OK} user token works — DM capture (§3) is available")
            check_scopes(
                user_headers.get("x-oauth-scopes", ""),
                REQUIRED_USER_SCOPES,
                "user scopes",
            )
        else:
            print(f"{BAD} user token rejected → {who.get('error')}")
            failures.append("user token")
    else:
        print(
            f"{WARN} no SLACK_USER_TOKEN — capture is limited to channels the bot"
            f"\n{INFO}   is in, plus @mentions. This is open question Q1."
        )

    # 3. Socket Mode --------------------------------------------------------
    print()
    if app_token.startswith("xapp-"):
        print(f"{OK} app-level token present — Socket Mode can connect")
    else:
        print(
            f"{WARN} no SLACK_APP_TOKEN (xapp-…): Socket Mode is off, so the"
            f"\n{INFO}   service would need a public HTTPS URL for events."
        )

    # 4. What can we see? ---------------------------------------------------
    print()
    listing, _ = call(
        bot,
        "conversations.list",
        {"types": "public_channel,private_channel", "limit": 200},
    )
    if listing.get("ok"):
        joined = [c["name"] for c in listing.get("channels", []) if c.get("is_member")]
        total = len(listing.get("channels", []))
        print(f"{OK} sees {total} channel(s); is a member of {len(joined)}")
        if joined:
            print(f"{INFO}   member of: {', '.join(joined)}")
        else:
            print(
                f"{WARN} not in any channel yet — invite it with /invite @Catalogbot"
                f"\n{INFO}   or it will only ever see DMs and @mentions."
            )
    else:
        print(f"{BAD} conversations.list → {listing.get('error')}")
        failures.append("conversations.list")

    # 5. The owner, and the confirmation card -------------------------------
    print()
    if not owner:
        # Not optional: without it the bot cannot ask its §10 confirmation
        # questions or send the §9 digest, so it has no way to reach anyone.
        failures.append("SLACK_OWNER_USER_ID unset")
        print(
            f"{BAD} SLACK_OWNER_USER_ID not set — the bot has nobody to ask."
            f"\n{INFO}   In Slack: your avatar → Profile → ⋯ → Copy member ID."
        )
        _report(failures)
        return 1

    profile, _ = call(bot, "users.info", {"user": owner})
    if profile.get("ok"):
        name = profile["user"].get("real_name") or profile["user"].get("name")
        print(f"{OK} owner resolves to {name}")
    else:
        print(f"{BAD} users.info → {profile.get('error')}")
        failures.append("users.info")

    if "--quiet" not in sys.argv:
        posted, _ = call(
            bot,
            "chat.postMessage",
            {
                "channel": owner,
                "text": "Catalogbot test: is this a task?",
                "blocks": confirmation_blocks(),
            },
            as_json=True,
        )
        if posted.get("ok"):
            print(f"{OK} posted the confirmation card to your DM — go look at it")
            print(
                f"{INFO}   the buttons do nothing until the service is running;"
                f"\n{INFO}   this proves the card renders, not that it responds."
            )
        else:
            error = posted.get("error")
            hint = "needs the im:write scope" if error == "channel_not_found" else ""
            print(f"{BAD} chat.postMessage → {error} {hint}")
            failures.append("chat.postMessage")

    _report(failures)
    return 1 if failures else 0


def _report(failures: list[str]) -> None:
    print()
    if failures:
        print(f"{BAD} {len(failures)} problem(s): {', '.join(failures)}")
        print(f"{INFO} fix scopes at api.slack.com/apps → OAuth & Permissions,")
        print(f"{INFO} then Reinstall to Workspace — new scopes need a reinstall.")
    else:
        print(f"{OK} Slack layer verified.")


if __name__ == "__main__":
    sys.exit(main())
