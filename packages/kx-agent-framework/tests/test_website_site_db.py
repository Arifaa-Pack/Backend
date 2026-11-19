"""WP-C3 (D55 / D57 / D58 / D59): the Website API moves the page, streams, nudges a little, takes the contact
card and the enquiry desk."""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select

from kx.agents.llm.service import ModelRouter
from kx.agents.orm import Conversation, Visitor
from kx.agents.runtime.service import AgentService
from kx.agents.testing import FakeCrmGateway, ScriptedChatModel, make_deal, tool_call
from kx.agents.website.enquiry import quantity_of
from kx.agents.website.router import WebsiteDeps, build_router
from kx.core.contracts import (
    ContactPatch,
    ContactView,
    ManifestFamily,
    ManifestPage,
    ManifestProduct,
    ManifestSection,
    SiteManifest,
    WebEnquiry,
)
from kx.core.errors import install_problem_handlers
from kx.core.settings import LogicalDb

MANIFEST = SiteManifest(
    revision=1,
    built_at=datetime(2026, 11, 20, tzinfo=UTC),
    pages=(
        ManifestPage(key="home", title="Home", path_en="/", path_ar="/ar"),
        ManifestPage(
            key="contact",
            title="Contact",
            path_en="/contact",
            path_ar="/ar/contact",
            sections=(ManifestSection(id="enquiry", keywords=("enquiry",)),),
        ),
    ),
    families=(
        ManifestFamily(
            id="foil",
            name="Aluminium Foil",
            synonyms={"en": ("kitchen foil",)},
            path_en="/products?family=foil",
            path_ar="/ar/products?family=foil",
        ),
    ),
    products=(
        ManifestProduct(
            id="ac-8325",
            ref="8325",
            family="foil",
            name="Aluminium Container & Lid",
            path_en="/products?p=ac-8325",
            path_ar="/ar/products?p=ac-8325",
        ),
    ),
)


@dataclass
class Manifests:
    async def manifest(self) -> SiteManifest:
        return MANIFEST


@dataclass
class Human:
    ok: bool = True
    seen: list[str] = field(default_factory=list)

    async def verify(self, token: str, remote_ip: str | None) -> bool:
        self.seen.append(token)
        return self.ok


@dataclass
class Limits:
    async def allow(self, scope: str, key: str) -> bool:
        return True


@dataclass
class Clock:
    now: datetime = field(default_factory=lambda: datetime(2026, 11, 20, 9, tzinfo=UTC))

    def __call__(self) -> datetime:
        return self.now


@dataclass
class Env:
    client: httpx.AsyncClient
    gateway: FakeCrmGateway
    human: Human
    clock: Clock
    databases: Any


@pytest.fixture
async def env(databases: Any, request: pytest.FixtureRequest) -> AsyncIterator[Env]:
    deal = make_deal()
    no_contact = ContactView(**(deal.contact.model_dump() | {"has_phone": False, "has_email": False}))
    gateway = FakeCrmGateway(deal.model_copy(update={"contact": no_contact}))
    script = getattr(request, "param", None) or ["Here is our foil range."] * 6  # a test may script its own
    model = ScriptedChatModel(script=list(script))
    service = AgentService(
        databases=databases, gateway=gateway, models=ModelRouter.static(model), deal_summaries=False
    )
    human, clock = Human(), Clock()

    async def budget_left() -> bool:
        return True

    deps = WebsiteDeps(
        databases=databases,
        service=service,
        gateway=gateway,
        secret="website-test-secret",
        human_check=human,
        limits=Limits(),
        ai_budget_left=budget_left,
        clock=clock,
        site_manifest=Manifests(),
    )
    app = FastAPI()
    install_problem_handlers(app)
    app.include_router(build_router(deps))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
        yield Env(client, gateway, human, clock, databases)


async def start(env: Env) -> tuple[str, dict[str, str]]:
    r = await env.client.post("/v1/public/sessions", json={"turnstile_token": "tok", "language": "en"})
    assert r.status_code == 201, r.text
    return r.json()["session_id"], {"Authorization": f"Bearer {r.json()['session_token']}"}


PRODUCTS = {"path": "/products", "page": "products"}


@pytest.mark.db
async def test_a_clear_request_moves_the_page_and_the_place_is_kept(env: Env) -> None:
    sid, auth = await start(env)
    r = await env.client.post(
        f"/v1/public/sessions/{sid}/messages",
        json={"text": "show me kitchen foil", "context": PRODUCTS},
        headers=auth,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["reply"] == "Here is our foil range."
    (action,) = body["actions"]
    assert action["type"] == "show_family" and action["family"] == "foil" and action["layer"] == "instant"
    assert action["resolved"]["path_ar"] == "/ar/products?family=foil" and action["id"]
    async with env.databases.transaction(LogicalDb.AGENTS) as s:
        visitor = await s.get(Visitor, uuid.UUID(sid))
        assert visitor is not None and visitor.engaged_at is not None
        assert visitor.site_context is not None and visitor.site_context["page"] == "products"
        thread = (
            await s.execute(select(Conversation).where(Conversation.langgraph_thread_id == f"web:{sid}"))
        ).scalar_one_or_none()
    assert thread is not None  # D58: the visit's one thread (voice uses the same key)


@pytest.mark.db
async def test_the_stream_sends_the_action_before_the_words(env: Env) -> None:
    sid, auth = await start(env)
    async with env.client.stream(
        "POST",
        f"/v1/public/sessions/{sid}/messages",
        json={"text": "kitchen foil please", "context": PRODUCTS},
        headers=auth | {"Accept": "text/event-stream"},
    ) as r:
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
        raw = "".join([chunk async for chunk in r.aiter_text()])
    events = [
        (
            block.split("\n")[0].removeprefix("event: "),
            json.loads(block.split("\n")[1].removeprefix("data: ")),
        )
        for block in raw.strip().split("\n\n")
    ]
    names = [n for n, _ in events]
    assert names[0] == "action" and names[-1] == "done" and "delta" in names
    assert events[0][1]["family"] == "foil"
    assert "".join(d["text"] for n, d in events if n == "delta") == "Here is our foil range."


@pytest.mark.db
async def test_nudges_wait_for_the_visitor_and_stay_rare(env: Env) -> None:
    sid, auth = await start(env)
    on_product = {"page": "products", "path": "/products?p=ac-8325", "quick_view_id": "ac-8325"}

    async def dwell(seconds: float) -> dict[str, Any]:
        r = await env.client.post(
            f"/v1/public/sessions/{sid}/events",
            json={"type": "product_dwell", "context": on_product, "seconds": seconds},
            headers=auth,
        )
        assert r.status_code == 200, r.text
        out: dict[str, Any] = r.json()
        return out

    assert (await dwell(30))["reply"] is None  # AS-01: not before the visitor has spoken
    await env.client.post(f"/v1/public/sessions/{sid}/messages", json={"text": "hello"}, headers=auth)
    assert (await dwell(5))["reply"] is None  # not after 5 s
    first = await dwell(25)
    assert first["reply"] and "Aluminium Container & Lid" in first["reply"]
    assert (await dwell(25))["reply"] is None  # never twice within 30 s
    for _ in range(2):
        env.clock.now += timedelta(minutes=1)
        r = await env.client.post(
            f"/v1/public/sessions/{sid}/events",
            json={"type": "enquiry_add", "context": on_product | {"enquiry_list": ["ac-8325"]}},
            headers=auth,
        )
        assert r.json()["reply"]
        assert [a["type"] for a in r.json()["actions"]] == ["ask_contact"]  # no phone or email yet
    env.clock.now += timedelta(minutes=1)
    assert (await dwell(25))["reply"] is None  # at most 3 nudges a visit


@pytest.mark.db
async def test_the_contact_card_records_the_opt_ins(env: Env) -> None:
    sid, auth = await start(env)
    r = await env.client.post(
        f"/v1/public/sessions/{sid}/contact",
        json={"name": "Omar", "phone": "+971501234567", "whatsapp_opt_in": True, "consent_wording": "Yes"},
        headers=auth,
    )
    assert r.status_code == 200 and r.json() == {"ok": True}
    (patch,) = [p for name, p in env.gateway.calls if name == "set_contact"]
    assert isinstance(patch, ContactPatch)
    assert (patch.whatsapp_opt_in, patch.email_opt_in, patch.consent_via) == (True, False, "web_chat")
    empty = await env.client.post(f"/v1/public/sessions/{sid}/contact", json={"name": "x"}, headers=auth)
    assert empty.status_code == 422


@pytest.mark.db
async def test_the_enquiry_desk_reaches_the_crm_with_english_labels(env: Env) -> None:
    desk = {
        "family": ["foil"],
        "products": ["ac-8325", "nope"],
        "quantity": "20,000 a month",
        "city": "Dubai",
        "name": "Omar",
        "phone": "+971501234567",
        "channel": "whatsapp",
        "message": "تسليم شهري",
        "turnstile_token": "tok",
    }
    r = await env.client.post("/v1/public/enquiries", json=desk)
    assert r.status_code == 201, r.text
    assert r.json()["reference"].startswith("W-") and env.human.seen == ["tok"]
    (enquiry,) = [e for name, e in env.gateway.calls if name == "website_enquiry"]
    assert isinstance(enquiry, WebEnquiry)
    assert enquiry.website_products == ("ac-8325",)
    assert "Products: Aluminium Container & Lid (8325)" in enquiry.summary
    assert "Reply by: WhatsApp" in enquiry.summary and "Message: تسليم شهري" in enquiry.summary
    assert enquiry.requirement is not None and enquiry.requirement.quantity == Decimal(20_000)
    assert enquiry.contact.consent_via == "web_form" and enquiry.contact.preferred_channel == "whatsapp"
    no_way_back = await env.client.post("/v1/public/enquiries", json=desk | {"phone": None})
    assert no_way_back.status_code == 422
    env.human.ok = False
    assert (await env.client.post("/v1/public/enquiries", json=desk)).status_code == 403


@pytest.mark.db
async def test_an_enquiry_in_a_chat_uses_the_session_not_turnstile(env: Env) -> None:
    sid, auth = await start(env)
    env.human.seen.clear()
    desk = {"phone": "+971501234567", "session_id": sid}
    assert (await env.client.post("/v1/public/enquiries", json=desk, headers=auth)).status_code == 201
    assert env.human.seen == []
    other = {"phone": "+971501234567", "session_id": str(uuid.uuid4())}
    assert (await env.client.post("/v1/public/enquiries", json=other, headers=auth)).status_code == 403


@pytest.mark.parametrize(
    ("text", "qty", "unit"),
    [
        ("20,000 a month", Decimal(20_000), "pcs"),
        ("50k pcs", Decimal(50_000), "pcs"),
        ("2 cartons", Decimal(2), "carton"),
        ("about 300 kg", None, None),  # not sure: the words reach the salesperson anyway
        ("", None, None),
    ],
)
def test_the_quantity_a_buyer_types(text: str, qty: Decimal | None, unit: str | None) -> None:
    assert quantity_of(text) == (qty, unit)


@pytest.mark.db
@pytest.mark.parametrize(
    "env",
    [[tool_call("show_product", {"product_id": "ac-8325"}), "This is our 8325 container."]],
    indirect=True,
)
async def test_the_agent_moves_the_page_while_it_answers(env: Env) -> None:
    sid, auth = await start(env)
    r = await env.client.post(
        f"/v1/public/sessions/{sid}/messages",
        json={"text": "I need something for biryani takeaway", "context": PRODUCTS},
        headers=auth,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["reply"] == "This is our 8325 container."
    (action,) = body["actions"]
    assert (action["type"], action["product_id"], action["layer"]) == ("show_product", "ac-8325", "agent")


@pytest.mark.db
async def test_the_voice_recording_notice_is_due_once_per_visit(env: Env, databases: Any) -> None:
    from kx.agents.website.service import WebsiteTurns

    sid, _ = await start(env)
    turns = WebsiteTurns(databases, None, env.gateway, Manifests(), env.clock)  # type: ignore[arg-type]
    assert await turns.recording_notice_due(uuid.UUID(sid)) is True
    assert await turns.recording_notice_due(uuid.UUID(sid)) is False
