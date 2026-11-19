"""§6 website API (11 Nov plan WP19, D34, D39): Turnstile, session tokens, one chat turn through the same
agents, LiveKit token with deal_ref + language, voice consent, identify without silent recognition."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import httpx
import jwt
import pytest
from fastapi import FastAPI

from kx.agents.llm.service import ModelRouter
from kx.agents.orm import Visitor
from kx.agents.runtime.service import AgentService
from kx.agents.testing import FakeCrmGateway, ScriptedChatModel, make_deal
from kx.agents.website.router import LiveKitConfig, WebsiteDeps, build_router
from kx.core.contracts import ContactPatch
from kx.core.errors import install_problem_handlers
from kx.core.settings import LogicalDb

pytestmark = pytest.mark.db
LIVEKIT = LiveKitConfig("wss://kx.livekit.cloud", "APIkey", "lk-secret-0123456789abcdef0123456789")


@dataclass
class Human:
    ok: bool = True
    seen: list[str] = field(default_factory=list)

    async def verify(self, token: str, remote_ip: str | None) -> bool:
        self.seen.append(token)
        return self.ok


@dataclass
class Limits:
    ok: bool = True
    refuse: set[str] = field(default_factory=set)  # scopes that are over their limit
    seen: list[tuple[str, str]] = field(default_factory=list)

    async def allow(self, scope: str, key: str) -> bool:
        self.seen.append((scope, key))
        return self.ok and scope not in self.refuse


@dataclass
class Env:
    client: httpx.AsyncClient
    gateway: FakeCrmGateway
    human: Human
    limits: Limits
    budget: list[bool]
    databases: Any


@pytest.fixture
async def env(databases: Any) -> AsyncIterator[Env]:
    gateway = FakeCrmGateway(make_deal())
    model = ScriptedChatModel(script=["Welcome to Arifa Pack! What are you looking for?"])
    service = AgentService(
        databases=databases, gateway=gateway, models=ModelRouter.static(model), deal_summaries=False
    )
    human, limits, budget = Human(), Limits(), [True]

    async def budget_left() -> bool:
        return budget[0]

    deps = WebsiteDeps(
        databases=databases,
        service=service,
        gateway=gateway,
        secret="website-test-secret",
        human_check=human,
        limits=limits,
        ai_budget_left=budget_left,
        livekit=LIVEKIT,
    )
    app = FastAPI()
    install_problem_handlers(app)
    app.include_router(build_router(deps))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
        yield Env(client, gateway, human, limits, budget, databases)


async def start(env: Env, **body: Any) -> dict[str, Any]:
    r = await env.client.post("/v1/public/sessions", json={"turnstile_token": "tok"} | body)
    assert r.status_code == 201, r.text
    out: dict[str, Any] = r.json()
    return out


def auth(session: dict[str, Any]) -> dict[str, str]:
    return {"Authorization": f"Bearer {session['session_token']}"}


async def test_a_chat_turn_goes_through_the_same_agents(env: Env) -> None:
    session = await start(env, language="en", voice_consent=True)
    assert session["voice_available"] is True and env.human.seen == ["tok"]
    r = await env.client.post(
        f"/v1/public/sessions/{session['session_id']}/messages",
        json={"text": "hello"},
        headers=auth(session),
    )
    assert r.status_code == 200, r.text
    assert r.json()["reply"] == "Welcome to Arifa Pack! What are you looking for?"
    async with env.databases.transaction(LogicalDb.AGENTS) as s:
        visitor = await s.get(Visitor, uuid.UUID(session["session_id"]))
    assert visitor is not None and visitor.voice_consent_at is not None and visitor.language == "en"
    # D-3 (live test 18 Nov): website chat and AI replies were missing from the deal timeline
    assert ("record_message", "in") in env.gateway.calls and ("record_message", "out") in env.gateway.calls


async def test_a_failed_human_check_starts_nothing(env: Env) -> None:
    env.human.ok = False
    r = await env.client.post("/v1/public/sessions", json={"turnstile_token": "bot"})
    assert r.status_code == 403


async def test_tokens_are_bound_to_their_session(env: Env) -> None:
    mine, other = await start(env), await start(env)
    r = await env.client.post(
        f"/v1/public/sessions/{other['session_id']}/messages", json={"text": "hi"}, headers=auth(mine)
    )
    assert r.status_code == 403
    r = await env.client.post(f"/v1/public/sessions/{mine['session_id']}/messages", json={"text": "hi"})
    assert r.status_code == 401


async def test_spend_cap_and_rate_limit_refuse_the_turn(env: Env) -> None:
    session = await start(env)
    url = f"/v1/public/sessions/{session['session_id']}/messages"
    env.budget[0] = False
    assert (await env.client.post(url, json={"text": "hi"}, headers=auth(session))).status_code == 503
    env.budget[0], env.limits.ok = True, False
    assert (await env.client.post(url, json={"text": "hi"}, headers=auth(session))).status_code == 429


async def test_voice_token_carries_the_deal_and_language_for_the_worker(env: Env) -> None:
    session = await start(env, language="ar", voice_consent=True)
    r = await env.client.post(f"/v1/public/sessions/{session['session_id']}/voice", headers=auth(session))
    assert r.status_code == 200, r.text
    body = r.json()
    claims = jwt.decode(body["token"], LIVEKIT.api_secret, algorithms=["HS256"])
    assert (
        claims["iss"] == "APIkey" and claims["video"]["room"] == body["room"] and claims["video"]["roomJoin"]
    )
    import json

    assert json.loads(claims["metadata"]) == {"deal_ref": str(env.gateway.deal.deal_id), "language": "ar"}


@pytest.mark.parametrize(
    ("language", "consent", "reason"), [("si", True, "text_only"), ("en", False, "no_voice_consent")]
)
async def test_no_voice_for_text_only_languages_or_without_consent(
    env: Env, language: str, consent: bool, reason: str
) -> None:
    session = await start(env, language=language, voice_consent=consent)
    r = await env.client.post(f"/v1/public/sessions/{session['session_id']}/voice", headers=auth(session))
    assert r.status_code == 422 and r.json()["reason"] == reason


async def test_identify_hands_the_details_to_the_crm_and_reveals_nothing(env: Env) -> None:
    session = await start(env)
    r = await env.client.post(
        f"/v1/public/sessions/{session['session_id']}/identify",
        json={"phone": "+971501234567", "name": "Omar"},
        headers=auth(session),
    )
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert ("set_contact", ContactPatch(phone="+971501234567", name="Omar")) in env.gateway.calls


async def test_sessions_per_ip_and_global_turns_are_limited(env: Env) -> None:
    """M6: new sessions per IP per day and AI turns per minute from every visitor together."""
    session = await start(env)
    assert ("ip_sessions", "127.0.0.1") in env.limits.seen
    url = f"/v1/public/sessions/{session['session_id']}/messages"
    env.limits.refuse = {"global"}
    assert (await env.client.post(url, json={"text": "hi"}, headers=auth(session))).status_code == 429
    assert ("global", "all") in env.limits.seen
    env.limits.refuse = {"ip_sessions"}
    r = await env.client.post("/v1/public/sessions", json={"turnstile_token": "tok"})
    assert r.status_code == 429
