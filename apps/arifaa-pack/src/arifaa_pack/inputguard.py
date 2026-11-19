"""Request hygiene found in the 19 Nov live re-test (security probes):

* **NUL bytes** (`\\x00`, `%00`, JSON `\\u0000`) are refused with 400 before any route runs. PostgreSQL text
  cannot hold a NUL, so a search such as `?q=%00` reached the database and failed with a 500 (probe: High).
  Spreadsheet uploads (multipart) and other binary bodies are not inspected: they are not text.
* **Security headers** on every answer: no MIME sniffing, no framing, no referrer to other sites, and a CSP
  that allows nothing (the API returns JSON, never pages). A PDF gets no CSP (a browser's PDF viewer may not
  open under `default-src 'none'`); the Swagger page (not served in production) gets one that lets its own
  script and the CDN it loads from run.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Final

from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

_JSON_NUL: Final = b"\\u0000"
_DOCS_PATHS: Final = ("/docs", "/redoc")
SECURITY_HEADERS: Final[tuple[tuple[bytes, bytes], ...]] = (
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
)
API_CSP: Final = b"default-src 'none'; frame-ancestors 'none'"
DOCS_CSP: Final = (
    b"default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    b"style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    b"img-src 'self' data: https://fastapi.tiangolo.com; "
    b"frame-ancestors 'none'"
)


def has_nul(raw: bytes) -> bool:
    """A NUL in a path or query string, raw or percent-encoded (any case)."""
    return b"\x00" in raw or b"%00" in raw


class RejectNul:
    """ASGI middleware: 400 for a NUL in the path, the query string or a JSON / form body."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        raw_path = scope.get("raw_path") or str(scope.get("path", "")).encode()
        if has_nul(raw_path) or has_nul(scope.get("query_string") or b"") or "\x00" in str(scope.get("path")):
            await _bad_request(send)
            return
        ctype = dict(scope.get("headers") or ()).get(b"content-type", b"").split(b";")[0].strip().lower()
        if ctype not in (b"application/json", b"application/x-www-form-urlencoded") and not ctype.endswith(
            b"+json"
        ):
            await self._app(scope, receive, send)
            return
        tail = b""

        async def checked() -> Message:
            """Scan each chunk as the route reads it (with the end of the last one: an escape split across two
            chunks still counts); the 400 is raised inside the read so the app's handlers answer it."""
            nonlocal tail
            message = await receive()
            if message["type"] == "http.request":
                chunk = tail + message.get("body", b"")
                if (
                    b"\x00" in chunk
                    or _JSON_NUL in chunk
                    or (ctype.endswith(b"form-urlencoded") and has_nul(chunk))
                ):
                    raise HTTPException(400, "the request contains a NUL character")
                tail = chunk[-(len(_JSON_NUL) - 1) :]
            return message

        await self._app(scope, checked, send)


class SecurityHeaders:
    """ASGI middleware: the security headers above on every HTTP answer (a header a route set is kept)."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        csp = DOCS_CSP if str(scope.get("path", "")).startswith(_DOCS_PATHS) else API_CSP

        async def with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers") or [])
                present = {k.lower(): v for k, v in headers}
                pdf = present.get(b"content-type", b"").startswith(b"application/pdf")
                headers += [(k, v) for k, v in _wanted(None if pdf else csp) if k not in present]
                message = {**message, "headers": headers}
            await send(message)

        await self._app(scope, receive, with_headers)


def _wanted(csp: bytes | None) -> Iterable[tuple[bytes, bytes]]:
    yield from SECURITY_HEADERS
    if csp is not None:
        yield b"content-security-policy", csp


async def _bad_request(send: Send) -> None:
    body = json.dumps(
        {
            "type": "https://errors.kairox.ai/bad_request",  # the kx.core.errors format
            "title": "Bad request",
            "status": 400,
            "detail": "the request contains a NUL character",
        }
    ).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 400,
            "headers": [
                (b"content-type", b"application/problem+json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
