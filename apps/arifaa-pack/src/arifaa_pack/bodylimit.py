"""L2 (security review 19 Nov): a cap on request body size before any route reads it.

Webhooks, the unsubscribe form and the CMS hook read the raw body before they can check a signature, so an
unauthenticated multi-hundred-MB POST used to be buffered in memory. Here a declared Content-Length over the
limit is refused at once, and a body sent without one (chunked) is counted and cut off when it passes the
limit. 1 MB everywhere; 10 MB for the two spreadsheet imports. The answer is RFC 9457 problem+json, 413.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Final

from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

DEFAULT_LIMIT: Final = 1_000_000
IMPORT_LIMIT: Final = 10 * 1024 * 1024
IMPORT_PREFIXES: Final = ("/v1/catalogue/workbook/", "/v1/price-list/import")


class BodyLimit:
    """ASGI middleware: 413 for a request body over the limit of its path."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        default: int = DEFAULT_LIMIT,
        large: int = IMPORT_LIMIT,
        large_prefixes: Sequence[str] = IMPORT_PREFIXES,
    ) -> None:
        self._app = app
        self._default = default
        self._large = large
        self._prefixes = tuple(large_prefixes)

    def limit_for(self, path: str) -> int:
        return self._large if path.startswith(self._prefixes) else self._default

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        limit = self.limit_for(str(scope["path"]))
        declared = dict(scope.get("headers") or ()).get(b"content-length")
        if declared is not None and (not declared.isdigit() or int(declared) > limit):
            await _refuse(send, limit)
            return
        seen = 0

        async def counted() -> Message:
            """A body without Content-Length (chunked) is counted as the route reads it. The 413 is raised as
            an HTTPException inside the route's read, so the app's own exception handling answers it."""
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > limit:
                    raise HTTPException(413, f"the request body is larger than {limit} bytes")
            return message

        await self._app(scope, counted, send)


async def _refuse(send: Send, limit: int) -> None:
    body = json.dumps(
        {
            "type": "https://errors.kairox.ai/payload_too_large",  # the kx.core.errors format
            "title": "Request body too large",
            "status": 413,
            "detail": f"the request body is larger than {limit} bytes",
        }
    ).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/problem+json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
