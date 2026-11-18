"""Security review fixes of 19 Nov (`live-test/results/review_security.md`, MEDIUM), pinned with no database."""

from __future__ import annotations

import httpx
from fastapi import FastAPI

from arifaa_pack.container import staff_auth
from arifaa_pack.cors import SplitCors
from kx.core.settings import AuthSettings

CRM, WWW, EVIL = "https://crm.arifapack.com", "https://www.arifapack.com", "https://evil.example"


def _app() -> FastAPI:
    app = FastAPI()

    @app.post("/v1/auth/refresh")
    async def refresh() -> dict[str, str]:
        return {"access_token": "staff"}

    @app.post("/v1/public/sessions")
    async def sessions() -> dict[str, str]:
        return {"session_id": "s"}

    app.add_middleware(SplitCors, crm_origins=(CRM,), public_origins=(WWW,))
    return app


async def test_cors_gives_credentials_only_to_the_crm_and_the_website_only_public_routes() -> None:
    """M2: the website may call /v1/public/* without credentials; it can never read a staff response."""
    transport = httpx.ASGITransport(app=_app())
    async with httpx.AsyncClient(transport=transport, base_url="https://api.arifapack.com") as c:
        crm = await c.post("/v1/auth/refresh", headers={"Origin": CRM})
        assert crm.headers["access-control-allow-origin"] == CRM
        assert crm.headers["access-control-allow-credentials"] == "true"

        www_staff = await c.post("/v1/auth/refresh", headers={"Origin": WWW})
        assert "access-control-allow-origin" not in www_staff.headers  # the browser hides the body
        preflight = await c.options(
            "/v1/auth/refresh",
            headers={"Origin": WWW, "Access-Control-Request-Method": "POST"},
        )
        assert "access-control-allow-origin" not in preflight.headers

        www_public = await c.post("/v1/public/sessions", headers={"Origin": WWW})
        assert www_public.headers["access-control-allow-origin"] == WWW
        assert "access-control-allow-credentials" not in www_public.headers
        public_preflight = await c.options(
            "/v1/public/sessions",
            headers={
                "Origin": WWW,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )
        assert public_preflight.status_code == 200
        assert "authorization" in public_preflight.headers["access-control-allow-headers"].lower()
        assert "access-control-allow-credentials" not in public_preflight.headers

        evil = await c.post("/v1/public/sessions", headers={"Origin": EVIL})
        assert "access-control-allow-origin" not in evil.headers


def test_auth_origins_default_to_the_crm_cors_origins() -> None:
    """M2: /v1/auth accepts the CRM origins from KX_APP_CORS_ORIGINS unless KX_AUTH__CRM_ORIGINS is set."""
    assert staff_auth(AuthSettings(), (CRM,)).crm_origins == (CRM,)
    pinned = AuthSettings(crm_origins=("https://crm2.example",))
    assert staff_auth(pinned, (CRM,)).crm_origins == ("https://crm2.example",)
