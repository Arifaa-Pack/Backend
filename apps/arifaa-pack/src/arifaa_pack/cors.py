"""M2: CORS split by caller.

* CRM origins (`KX_APP_CORS_ORIGINS`): every route, with credentials (the staff refresh cookie).
* Public website origins (`KX_APP_CORS_PUBLIC_ORIGINS`): only `/v1/public/*` and `/v1/content/*`, never with
  credentials, so a script on the website cannot read a staff response even though it is the same site as the
  API.
* Any other origin gets no CORS headers (the browser keeps the response from the page).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

from starlette.datastructures import Headers
from starlette.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

PUBLIC_PREFIXES: Final = ("/v1/public/", "/v1/content/")  # the website's chat API and content reads (D54)


class SplitCors:
    """ASGI middleware: picks the CRM or the public CORS policy by the request's Origin and path."""

    def __init__(self, app: ASGIApp, *, crm_origins: Sequence[str], public_origins: Sequence[str]) -> None:
        self._app = app
        self._crm_origins = frozenset(crm_origins)
        self._public_origins = frozenset(public_origins) - self._crm_origins
        self._crm = CORSMiddleware(
            app,
            allow_origins=sorted(self._crm_origins),
            allow_methods=["*"],
            allow_headers=["*"],
            allow_credentials=True,
        )
        self._public = CORSMiddleware(
            app,
            allow_origins=sorted(self._public_origins),
            allow_methods=["*"],
            allow_headers=["*"],
            allow_credentials=False,
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            origin = Headers(scope=scope).get("origin")
            if origin in self._crm_origins:
                await self._crm(scope, receive, send)
                return
            if origin in self._public_origins and str(scope["path"]).startswith(PUBLIC_PREFIXES):
                await self._public(scope, receive, send)
                return
        await self._app(scope, receive, send)
