"""Unhandled API errors logged inside the request's trace (live test 18 Nov: ERROR lines had an empty
trace_id because uvicorn logged the exception after the request span had ended, so SigNoz could not link the
log to its trace).

This middleware sits inside the OpenTelemetry request span: it logs the exception with the span's trace and
span ids (kx.core.logging adds them), records it on the span, and answers a plain RFC 9457 500 (nothing about
the cause leaves the server). If the response had already started it re-raises instead.
"""

from __future__ import annotations

import json

from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from kx.core.logging import get_logger

log = get_logger(__name__)
BODY = json.dumps(
    {
        "type": "https://errors.kairox.ai/internal_error",
        "title": "Internal error",
        "status": 500,
        "detail": "the request could not be completed; it has been logged",
    }
).encode()


class LogUnhandled:
    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        started = False

        async def tracked(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self._app(scope, receive, tracked)
        except Exception as exc:
            span = trace.get_current_span()
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
            log.exception("api.unhandled", method=scope.get("method"), path=scope.get("path"))
            if started:
                raise
            await send(
                {
                    "type": "http.response.start",
                    "status": 500,
                    "headers": [
                        (b"content-type", b"application/problem+json"),
                        (b"content-length", str(len(BODY)).encode()),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": BODY})
