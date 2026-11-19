"""Security review fixes of 19 Nov (`live-test/results/review_security.md`, MEDIUM), pinned with no database."""

from __future__ import annotations

import io
import zipfile
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any

import httpx
import pydantic
import pytest
from fastapi import FastAPI, Request
from openpyxl import Workbook  # type: ignore[import-untyped]  # types-openpyxl not in uv.lock

from arifaa_pack.adapters import RedisAuthThrottle
from arifaa_pack.bodylimit import BodyLimit
from arifaa_pack.config import AppSettings
from arifaa_pack.container import public_buckets, staff_auth
from arifaa_pack.cors import SplitCors
from kx.automation.settings import AutomationSettings
from kx.core.contracts import ContactPatch
from kx.core.errors import RuleViolation
from kx.core.settings import AuthSettings, RateLimit
from kx.core.timewindow import language_tag, valid_timezone
from kx.crm.api.records import ContactEditIn
from kx.crm.services.price_lists import check_xlsx
from kx.crm.throttle import MemoryThrottle

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


class _RedisDown:
    """Every Redis call fails, as while Redis restarts."""

    def __getattr__(self, name: str) -> Any:
        def fail(*_: Any, **__: Any) -> Any:
            raise ConnectionError("redis down")

        return fail


async def test_staff_sign_in_limits_survive_a_redis_outage() -> None:
    """M3: the Redis throttle falls back to an in-process one: sign-in stays available and limited."""
    throttle = RedisAuthThrottle(_RedisDown(), _RedisDown(), "pack")
    limit = RateLimit(burst=2, per_minute=0.001)
    assert [await throttle.take("login_ip", "203.0.113.9", limit) for _ in range(3)] == [True, True, False]
    assert await throttle.bump("fail:x", 60) == 1 and await throttle.bump("fail:x", 60) == 2
    assert await throttle.count("fail:x") == 2
    await throttle.clear("fail:x")
    assert await throttle.count("fail:x") == 0
    await throttle.bump("fail:acct:203.0.113.9", 60)
    await throttle.clear_prefix("fail:acct:")  # admin unlock (19 Nov re-test): still works without Redis
    assert await throttle.count("fail:acct:203.0.113.9") == 0


async def test_memory_throttle_counters_expire_and_buckets_refill() -> None:
    now = [0.0]
    throttle = MemoryThrottle(clock=lambda: now[0])
    limit = RateLimit(burst=1, per_minute=1)
    assert await throttle.take("s", "k", limit) and not await throttle.take("s", "k", limit)
    now[0] += 61
    assert await throttle.take("s", "k", limit)
    assert await throttle.bump("c", 900) == 1
    now[0] += 901
    assert await throttle.count("c") == 0 and await throttle.bump("c", 900) == 1
    # an admin unlock clears one account's lockouts from every IP, and nobody else's
    for key in ("fail:a:1.1.1.1", "fail:a:2.2.2.2", "fail:ab:1.1.1.1"):
        await throttle.bump(key, 900)
    await throttle.clear_prefix("fail:a:")
    assert [await throttle.count(k) for k in ("fail:a:1.1.1.1", "fail:a:2.2.2.2", "fail:ab:1.1.1.1")] == [
        0,
        0,
        1,
    ]


def test_public_buckets_add_a_global_turn_limit_and_sessions_per_ip_per_day() -> None:
    """M6: defaults 60 website AI turns a minute in total and 20 new sessions per IP a day."""
    buckets = public_buckets(AppSettings())
    assert buckets["global"].capacity == 60 and buckets["global"].refill_per_minute == 60
    assert buckets["ip_sessions"].capacity == 20
    assert abs(buckets["ip_sessions"].refill_per_minute * 1440 - 20) < 1e-9  # 20 per 24 hours
    assert {"ip", "session"} <= buckets.keys()
    assert AppSettings().spend_public_ai_share == Decimal("0.30")


# ---------------------------------------------------------------- L8: contact time zone and language
def test_l8_time_zone_and_language_checks() -> None:
    assert valid_timezone("Asia/Dubai") and valid_timezone("UTC") and valid_timezone("Asia/Kolkata")
    for bad in ("Not/AZone", "", "../../etc/passwd", "/etc/localtime", "Asia/Dubai/x", "x" * 80):
        assert not valid_timezone(bad), bad
    assert (
        language_tag(" AR ") == "ar"
        and language_tag("zh_Hans") == "zh-hans"
        and language_tag("en-GB") == "en-gb"
    )
    for bad in ("", "english please", "e", "<script>", "a" * 20):
        assert language_tag(bad) is None, bad


def test_l8_the_ai_patch_drops_a_bad_zone_and_staff_get_a_422() -> None:

    patch = ContactPatch(timezone="Not/AZone", language="english please")
    assert (patch.timezone, patch.language) == (None, None)  # the AI turn goes on, nothing bad is stored
    good = ContactPatch(timezone=" Asia/Riyadh ", language="AR")
    assert (good.timezone, good.language) == ("Asia/Riyadh", "ar")
    with pytest.raises(pydantic.ValidationError, match="time zone"):
        ContactEditIn(timezone="Not/AZone")
    with pytest.raises(pydantic.ValidationError, match="language tag"):
        ContactEditIn(language="english please")
    assert ContactEditIn(timezone="Asia/Dubai", language="hi").timezone == "Asia/Dubai"
    # a zone stored before this check falls back to the default instead of failing every follow-up
    s = AutomationSettings()
    assert s.followup_window("Not/AZone").tz == s.followup.default_timezone
    assert s.followup_window("Asia/Riyadh").tz == "Asia/Riyadh"


# ---------------------------------------------------------------- L2: request body size, xlsx zip bombs
async def test_l2_body_limit_refuses_big_bodies_declared_or_streamed() -> None:
    app = FastAPI()

    @app.post("/webhooks/whatsapp")
    async def hook(request: Request) -> dict[str, int]:
        return {"n": len(await request.body())}

    @app.post("/v1/price-list/import")
    async def imp(request: Request) -> dict[str, int]:
        return {"n": len(await request.body())}

    limited = BodyLimit(app, default=1_000, large=5_000)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=limited), base_url="http://t") as c:
        assert (await c.post("/webhooks/whatsapp", content=b"x" * 900)).json() == {"n": 900}
        big = await c.post("/webhooks/whatsapp", content=b"x" * 1_001)
        assert big.status_code == 413 and big.headers["content-type"] == "application/problem+json"

        async def chunks() -> AsyncIterator[bytes]:  # no Content-Length: counted while it streams
            for _ in range(5):
                yield b"x" * 400

        assert (await c.post("/webhooks/whatsapp", content=chunks())).status_code == 413
        assert (await c.post("/v1/price-list/import", content=b"x" * 4_000)).json() == {"n": 4_000}
        assert (await c.post("/v1/price-list/import", content=b"x" * 5_001)).status_code == 413
        lying = await c.post("/webhooks/whatsapp", content=b"x", headers={"content-length": "abc"})
        assert lying.status_code in (400, 413)


def test_l2_a_zip_bomb_never_reaches_openpyxl() -> None:
    real = io.BytesIO()
    wb = Workbook()
    wb.active.append(["sku", "tier", "price"])
    wb.save(real)
    check_xlsx(real.getvalue())  # a real workbook passes
    bomb = io.BytesIO()
    with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("xl/sharedStrings.xml", b"\0" * 50_000_000)  # 50 MB of zeros packs into ~50 KB
    with pytest.raises(RuleViolation, match="unpacks"):
        check_xlsx(bomb.getvalue())
    with pytest.raises(RuleViolation, match="readable"):
        check_xlsx(b"not a zip at all")
