#!/usr/bin/env python3
"""Check every Plane assumption in plane.py against a real workspace.

Reads PLANE_* from .env, then walks the same calls the service makes:
metadata, create, update, delete. Cleans up after itself.

    python scripts/verify_plane.py

Exit code 0 means the Plane layer is sound. Anything else prints what broke
and where, so the fix goes into src/catalogbot/plane.py.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent

OK, BAD, WARN, INFO = "  ok ", " FAIL", " warn", "     "


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


class Plane:
    def __init__(self, base: str, key: str) -> None:
        self._base = base.rstrip("/")
        self._key = key

    def call(
        self, method: str, path: str, body: dict | None = None
    ) -> tuple[int, object]:
        request = urllib.request.Request(
            f"{self._base}{path}",
            method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"X-API-Key": self._key, "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read()
                if not raw:
                    return response.status, None
                try:
                    return response.status, json.loads(raw)
                except json.JSONDecodeError:
                    # A 200 of HTML means this host serves the web app, not the API.
                    return response.status, "<non-JSON body: not an API endpoint>"
        except urllib.error.HTTPError as error:
            raw = error.read()
            try:
                return error.code, json.loads(raw)
            except Exception:
                return error.code, raw.decode(errors="replace")[:300]
        except Exception as error:  # DNS, TLS, timeout
            return 0, f"{type(error).__name__}: {error}"


def diagnose(key: str, slug: str, project: str, base: str) -> None:
    """Isolate a 403: is it the token, the workspace, the path, or the host?

    Each rung narrows it down. A token rejected everywhere is a bad token; one
    that passes /users/me but not the workspace is a permission or plan wall;
    one that passes the workspace but not the project is a wrong id or path.
    """
    print(f"\n{INFO} ---- diagnosing ----")

    raw_len = len(key)
    if key != key.strip() or any(c.isspace() for c in key):
        print(f"{WARN} the token contains whitespace — likely a bad copy/paste")
    print(f"{INFO} token length {raw_len} chars")

    hosts = [base.rstrip("/")]
    for alternative in ("https://api.plane.so", "https://app.plane.so"):
        if alternative not in hosts:
            hosts.append(alternative)

    rungs = [
        ("token itself   ", "/api/v1/users/me/"),
        ("workspace      ", f"/api/v1/workspaces/{slug}/projects/"),
        ("project states ", f"/api/v1/workspaces/{slug}/projects/{project}/states/"),
    ]

    for host in hosts:
        print(f"\n{INFO} host {host}")
        plane = Plane(host, key)
        for label, path in rungs:
            status, payload = plane.call("GET", path)
            detail = ""
            if status not in (200, 201) and payload:
                detail = f"  {str(payload)[:120]}"
            print(f"{INFO}   {label} {status}{detail}")

    print(
        f"\n{INFO} reading: 401/403 everywhere = the token is not being accepted;"
        f"\n{INFO} token ok but workspace 403 = permission or plan wall;"
        f"\n{INFO} workspace ok but states 403/404 = wrong project id or path;"
        f"\n{INFO} one host works and the other does not = wrong PLANE_BASE_URL."
    )


def main() -> int:
    env = load_env()
    key = env.get("PLANE_API_KEY", "")
    slug = env.get("PLANE_WORKSPACE_SLUG", "")
    project = env.get("PLANE_PROJECT_ID", "")
    base = env.get("PLANE_BASE_URL", "https://api.plane.so")

    missing = [
        name
        for name, value in (
            ("PLANE_API_KEY", key),
            ("PLANE_WORKSPACE_SLUG", slug),
            ("PLANE_PROJECT_ID", project),
        )
        if not value
    ]
    if missing:
        print(f"{BAD} set these in .env first: {', '.join(missing)}")
        return 2

    plane = Plane(base, key)
    root = f"/api/v1/workspaces/{slug}/projects/{project}"
    failures: list[str] = []

    print(f"{INFO} workspace {slug} · project {project[:8]}… · {base}\n")

    # 1. Auth and states -------------------------------------------------
    status, payload = plane.call("GET", f"{root}/states/")
    if status == 200:
        states = payload.get("results", []) if isinstance(payload, dict) else []
        print(f"{OK} auth accepted · {len(states)} states")
        print(f"{INFO} copy these into STATUS_TO_STATE_NAME in plane.py:")
        for state in states:
            print(f"{INFO}   {state.get('name'):<16} group={state.get('group')}")
    else:
        hint = {
            401: "token rejected — wrong key, or it was regenerated",
            403: "authenticated but forbidden — likely a plan restriction",
            404: "workspace slug or project id is wrong",
        }.get(status, "")
        print(f"{BAD} GET states → {status} {hint}\n{INFO} {payload}")
        diagnose(key, slug, project, base)
        return 1

    # 2. Labels ----------------------------------------------------------
    status, payload = plane.call("GET", f"{root}/labels/")
    if status == 200:
        names = [l.get("name") for l in payload.get("results", [])]
        print(f"\n{OK} labels readable · {names or 'none defined yet'}")
        for needed in ("waiting:me", "waiting:team", "waiting:marketplace"):
            if needed not in names:
                print(f"{WARN} label {needed!r} missing — create it before Phase 2")
    else:
        failures.append(f"GET labels → {status}")
        print(f"\n{BAD} GET labels → {status}: {payload}")

    # 3. Create ----------------------------------------------------------
    print()
    status, created = plane.call(
        "POST",
        f"{root}/issues/",
        {
            "name": "[verify] delete me",
            "description_html": "<p>Written by scripts/verify_plane.py</p>",
            "priority": "low",
        },
    )
    if status not in (200, 201) or not isinstance(created, dict):
        failures.append(f"POST issue → {status}")
        print(f"{BAD} POST issue → {status}: {created}")
        _report(failures)
        return 1

    issue_id = created.get("id")
    print(f"{OK} created work item {created.get('sequence_id')} ({issue_id})")
    for field in ("target_date", "priority", "state", "labels"):
        if field not in created:
            print(f"{WARN} response has no {field!r} — check the field name")

    # 4. Update ----------------------------------------------------------
    status, _ = plane.call(
        "PATCH", f"{root}/issues/{issue_id}/", {"priority": "urgent"}
    )
    if status in (200, 204):
        print(f"{OK} updated it")
    else:
        failures.append(f"PATCH issue → {status}")
        print(f"{BAD} PATCH issue → {status}")

    # 5. List ------------------------------------------------------------
    status, listing = plane.call("GET", f"{root}/issues/")
    if status == 200:
        count = len(listing.get("results", []) if isinstance(listing, dict) else listing)
        print(f"{OK} listed work items ({count})")
    else:
        failures.append(f"GET issues → {status}")
        print(f"{BAD} GET issues → {status}")

    # 6. Clean up --------------------------------------------------------
    status, _ = plane.call("DELETE", f"{root}/issues/{issue_id}/")
    if status in (200, 202, 204):
        print(f"{OK} deleted the test item")
    else:
        print(f"{WARN} could not delete {issue_id} → {status}; remove it by hand")

    # 7. Webhooks probe (answers the plan question) -----------------------
    print()
    status, _ = plane.call("GET", f"/api/v1/workspaces/{slug}/webhooks/")
    if status == 200:
        print(f"{OK} webhooks available — Phase 2 can use push, not polling")
    elif status in (402, 403):
        print(f"{WARN} webhooks refused ({status}) — likely plan-gated. Phase 2 polls.")
    else:
        print(f"{WARN} webhooks probe → {status} (path may differ; confirm manually)")

    _report(failures)
    return 1 if failures else 0


def _report(failures: list[str]) -> None:
    print()
    if failures:
        print(f"{BAD} {len(failures)} call(s) failed: {'; '.join(failures)}")
    else:
        print(f"{OK} Plane layer verified.")


if __name__ == "__main__":
    sys.exit(main())
