"""§20.11 admin settings in automation (11 Nov plan WP28): regional WhatsApp numbers and message templates."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from kx.automation.admin_api import build_router
from kx.core.auth import Principal, Role, current_principal
from kx.core.errors import install_problem_handlers

pytestmark = pytest.mark.db
ADMIN = Principal("admin", uuid.uuid4(), Role.ADMIN, mfa=True, is_human=True)


@pytest.fixture
async def client(auto_db: Any) -> AsyncIterator[tuple[httpx.AsyncClient, list[Principal]]]:
    who = [ADMIN]
    app = FastAPI()
    install_problem_handlers(app)
    app.include_router(build_router(auto_db))
    app.dependency_overrides[current_principal] = lambda: who[0]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        yield c, who


async def test_whatsapp_template_waits_for_meta_then_is_usable(
    client: tuple[httpx.AsyncClient, list[Principal]],
) -> None:
    c, _ = client
    body = {
        "channel": "whatsapp",
        "name": "staff_reopen",
        "language": "en",
        "body": "{{1}} from {{2}} here about your order - reply to continue.",
    }
    body = body | {"name": f"staff_reopen_{uuid.uuid4().hex[:6]}"}
    r = await c.put("/v1/admin/message-templates", json=body)
    assert r.status_code == 200 and r.json()["provider_status"] == "submitted" and r.json()["version"] == 1
    v1 = r.json()["id"]
    r = await c.put("/v1/admin/message-templates", json=body | {"body": "fixed typo {{1}} {{2}}"})
    assert r.json()["id"] == v1  # not approved yet: changed in place
    r = await c.post(f"/v1/admin/message-templates/{v1}/status", json={"provider_status": "approved"})
    assert r.json()["provider_status"] == "approved" and r.json()["live"]

    # 18 Nov: editing the live version never stops it: a new version waits for Meta under its own name
    r = await c.put("/v1/admin/message-templates", json=body | {"body": "changed text {{1}} {{2}}"})
    v2 = r.json()
    assert v2["provider_status"] == "submitted" and v2["version"] == 2 and not v2["live"]
    assert v2["provider_name"] == f"{body['name']}_v2"
    versions = {
        t["version"]: t
        for t in (await c.get("/v1/admin/message-templates")).json()
        if t["name"] == body["name"]
    }
    assert versions[1]["live"] and versions[1]["body"] == "fixed typo {{1}} {{2}}"  # still the one sent

    r = await c.post(f"/v1/admin/message-templates/{v2['id']}/status", json={"provider_status": "approved"})
    assert r.json()["live"]
    versions = {
        t["version"]: t
        for t in (await c.get("/v1/admin/message-templates")).json()
        if t["name"] == body["name"]
    }
    assert versions[1]["provider_status"] == "superseded" and versions[2]["live"]  # history kept
    current = [
        t
        for t in (await c.get("/v1/admin/message-templates?current_only=true")).json()
        if t["name"] == body["name"]
    ]
    assert [t["version"] for t in current] == [2]


async def test_email_template_edits_go_live_and_keep_history(
    client: tuple[httpx.AsyncClient, list[Principal]],
) -> None:
    c, who = client
    who[0] = Principal("mgr", uuid.uuid4(), Role.MANAGER, mfa=True, is_human=True)  # managers edit too
    body = {
        "channel": "email",
        "name": f"followup_{uuid.uuid4().hex[:6]}",
        "language": "en",
        "subject": "Your quotation",
        "body": "Hi {{1}}, any questions about the quotation?",
    }
    first = (await c.put("/v1/admin/message-templates", json=body)).json()
    second = (
        await c.put("/v1/admin/message-templates", json=body | {"body": "Hello {{1}}, shall we go ahead?"})
    ).json()
    assert first["live"] and second["live"] and second["version"] == 2 and second["provider_name"] is None
    versions = {
        t["version"]: t["provider_status"]
        for t in (await c.get("/v1/admin/message-templates")).json()
        if t["name"] == body["name"]
    }
    assert versions == {1: "superseded", 2: "approved"}
    who[0] = Principal("sales", uuid.uuid4(), Role.SALES, mfa=True, is_human=True)
    assert (await c.put("/v1/admin/message-templates", json=body)).status_code == 403


async def test_regional_numbers_and_admin_only(client: tuple[httpx.AsyncClient, list[Principal]]) -> None:
    c, who = client
    accounts = (await c.get("/v1/admin/channel-accounts")).json()
    assert accounts and "secret_ref" not in accounts[0]
    r = await c.patch(f"/v1/admin/channel-accounts/{accounts[0]['id']}", json={"countries": ["SA", "AE"]})
    assert r.status_code == 200 and r.json()["countries"] == ["SA", "AE"]
    who[0] = Principal("sales", uuid.uuid4(), Role.SALES, mfa=True, is_human=True)
    assert (
        await c.patch(f"/v1/admin/channel-accounts/{accounts[0]['id']}", json={"is_default": True})
    ).status_code == 403
