"""HTTP API (Coolify `api` service): CRM routes under /v1, channel webhooks, health checks."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import orjson
from fastapi import Depends, FastAPI, Request, Response

from arifaa_pack.bodylimit import BodyLimit
from arifaa_pack.config import app_settings
from arifaa_pack.container import build
from arifaa_pack.cors import SplitCors
from arifaa_pack.errorlog import LogUnhandled
from arifaa_pack.inputguard import RejectNul, SecurityHeaders
from arifaa_pack.telemetry import beat_health, init_telemetry, instrument_api
from kx.agents.website import build_router as website_router
from kx.automation import admin_router as channels_admin_router
from kx.automation import cms_router, unsubscribe_router, webhook_router
from kx.core.errors import Unavailable, install_problem_handlers
from kx.core.logging import configure_logging
from kx.core.settings import Environment, get_settings
from kx.crm import SessionGuard
from kx.crm import build_router as crm_router


def create_app() -> FastAPI:
    core = get_settings()
    configure_logging(core.log_level, core.log_json)
    init_telemetry(app_settings(), component="api", company=core.company_code, environment=core.env.value)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with build() as c:
            app.include_router(crm_router(c.databases, c.crm_deps), prefix="/v1")
            app.include_router(webhook_router(c.webhook_deps))
            app.include_router(unsubscribe_router(c.unsubscribe_deps))  # §8.1 step 10
            cms_secret = c.app.cms_webhook_secret.get_secret_value() if c.app.cms_webhook_secret else None
            app.include_router(cms_router(c.databases, cms_secret))  # §7 / D22 publish webhook
            app.include_router(website_router(c.website_deps))  # §6 website chat and voice
            app.include_router(  # §20.11 numbers and templates; staff only, so the L1 live-session guard too
                channels_admin_router(c.databases),
                dependencies=[Depends(SessionGuard(c.databases, c.crm_deps.now).dependency())],
            )
            app.state.container = c
            yield

    # 19 Nov re-test: the API description is public only where people test against it (not in production)
    public_docs = core.env is not Environment.PRODUCTION
    app = FastAPI(
        title="Kairox CRM API",
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/docs" if public_docs else None,
        redoc_url="/redoc" if public_docs else None,
        openapi_url="/openapi.json" if public_docs else None,
    )
    instrument_api(app)  # HTTP + Celery producer spans to SigNoz (no-op without KX_APP_OTLP_ENDPOINT)
    install_problem_handlers(app)  # also turns bad webhook signatures into 401s
    # L2: 1 MB bodies (10 MB for the spreadsheet imports), before any route reads. Added first so it sits
    # inside CORS (the last one added is the outermost): a browser then sees the 413, not a CORS error.
    app.add_middleware(
        LogUnhandled
    )  # innermost: logs a crash inside the request's trace (log <-> trace link)
    app.add_middleware(BodyLimit)
    app.add_middleware(RejectNul)  # 19 Nov re-test: a NUL in a search reached PostgreSQL (500); now a 400
    app.add_middleware(SecurityHeaders)  # nosniff, no framing, no referrer, CSP (outside the 400 / 413 above)
    app.add_middleware(  # M2: credentials only for the CRM; the website only reaches /v1/public/*
        SplitCors,
        crm_origins=app_settings().cors_origins,
        public_origins=app_settings().cors_public_origins,
    )

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/healthz/beat", include_in_schema=False)
    async def healthz_beat(request: Request) -> Response:
        """D46: UptimeRobot checks this from outside; 503 = Beat, the broker or the workers stopped (or the
        oldest due job is waiting too long)."""
        c = request.app.state.container
        ok, detail = await beat_health(c.redis, c.core.company_code, c.app.beat_max_lag_seconds)
        body = {"status": "ok" if ok else "stale", **detail}
        return Response(orjson.dumps(body), status_code=200 if ok else 503, media_type="application/json")

    @app.get("/.well-known/jwks.json", tags=["auth"])
    async def jwks(request: Request) -> dict[str, Any]:
        """The public key that verifies staff access tokens (RS256, D26)."""
        tokens = request.app.state.container.crm_deps.tokens
        if tokens is None:
            raise Unavailable("token signing is not configured")
        result: dict[str, Any] = tokens.jwks()
        return result

    return app
