"""A small JSON-over-HTTPS client built on the standard library.

Deliberately not a third-party HTTP library. Two reasons:

1. Header case is preserved exactly as given. Plane matches `X-API-Key`
   case-sensitively and answers a normalised `X-Api-Key` with 403 "Given API
   token is not valid" - an error that accuses the credential rather than the
   transport, and costs hours to track down. `urllib` title-cases header
   names, so it cannot be used here at all.
2. The service makes a few dozen JSON calls a day. That does not justify a
   dependency, and a dependency that misbehaves on one contributor's machine
   costs more than the convenience is worth.
"""

from __future__ import annotations

import gzip
import http.client
import json
import urllib.parse
from typing import Any

DEFAULT_TIMEOUT = 60.0


class HttpError(RuntimeError):
    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"HTTP {status}: {body[:400]}")
        self.status = status
        self.body = body


class JsonClient:
    def __init__(
        self,
        base_url: str,
        headers: dict[str, str] | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        parsed = urllib.parse.urlsplit(base_url.rstrip("/"))
        if parsed.scheme not in {"http", "https"}:
            raise ValueError(f"base_url must be http(s): {base_url!r}")
        self._scheme = parsed.scheme
        self._host = parsed.netloc
        self._prefix = parsed.path
        self._headers = dict(headers or {})
        self._timeout = timeout

    def request(
        self,
        method: str,
        path: str,
        body: Any = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Return the decoded JSON body, or raise HttpError."""
        status, payload = self.try_request(method, path, body, params)
        if status >= 400:
            raise HttpError(status, payload if isinstance(payload, str) else str(payload))
        return payload

    def try_request(
        self,
        method: str,
        path: str,
        body: Any = None,
        params: dict[str, Any] | None = None,
    ) -> tuple[int, Any]:
        """Like `request`, but returns the status instead of raising."""
        target = f"{self._prefix}{path}"
        if params:
            target += "?" + urllib.parse.urlencode(
                {k: v for k, v in params.items() if v is not None}
            )

        headers = dict(self._headers)
        headers.setdefault("Accept", "application/json")
        headers.setdefault("Accept-Encoding", "gzip")
        encoded = None
        if body is not None:
            encoded = json.dumps(body).encode()
            headers.setdefault("Content-Type", "application/json")

        connection = self._connect()
        try:
            connection.request(method, target, encoded, headers)
            response = connection.getresponse()
            raw = response.read()
            if response.getheader("Content-Encoding", "").lower() == "gzip" and raw:
                raw = gzip.decompress(raw)
            if not raw:
                return response.status, None
            try:
                return response.status, json.loads(raw)
            except json.JSONDecodeError:
                return response.status, raw.decode(errors="replace")
        finally:
            connection.close()

    def _connect(self) -> http.client.HTTPConnection:
        if self._scheme == "http":
            return http.client.HTTPConnection(self._host, timeout=self._timeout)
        return http.client.HTTPSConnection(self._host, timeout=self._timeout)

    def close(self) -> None:
        """Present for symmetry; connections are per-request and self-closing."""
