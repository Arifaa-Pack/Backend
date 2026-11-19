"""§6 website conversation API (11 Nov plan WP19; D39 public-endpoint protection; D55 / D57 / D59).

`/v1/public/...`, called by the Next.js site (no staff login):

* `POST /sessions`: start a conversation after a Cloudflare Turnstile check. Pressing "Enter the factory" is
  the visitor's consent to voice (§6 step 1). Returns a short-lived session token (send it as
  `Authorization: Bearer`) and whether live voice exists for the language (Sinhala is text-only, D36).
* `POST /sessions/{id}/messages`: one chat turn through the same `AgentService` as WhatsApp and email (same
  router, graphs and price guard). The CRM deal is created on the first message, not when the page opens.
  The message carries the visitor's place on the site (`context`, D55); the answer carries site actions
  (show a family or a product, go to a page or section, the factory film, the enquiry list and desk, the
  contact card). With `Accept: text/event-stream` the answer streams (D57): `action` events first (the page
  moves while the assistant is still writing), then the reply in sentence `delta`s, then `done`; an `error`
  event replaces `done` when the turn fails. Without that header the answer is one JSON object.
* `POST /sessions/{id}/voice`: a LiveKit room token; its participant metadata carries `deal_ref`, `language`
  and `session_id`, which is what the voice worker reads.
* `POST /sessions/{id}/identify`: the phone or email the visitor typed. "Returning visitors are recognised by
  phone or email after they identify themselves. They are never recognised silently" (§6 step 7): the CRM
  merges the records; the answer is the same whether or not the buyer was known.
* `POST /sessions/{id}/contact`: the chat's contact card (name, company, phone, email) with the WhatsApp and
  email opt-ins and the words the visitor saw (D59, SRS §8 / §22). Same rules and same answer as identify.
* `POST /sessions/{id}/events`: the visitor moved (page, product, enquiry list, idle). The place is saved; the
  answer is a nudge (`reply` + `actions`) or nothing (`website/service.py` has the limits).
* `POST /enquiries`: the enquiry desk on the Contact page (CT-01) sends its own fields here instead of
  `mailto:`. A session token or a Turnstile token; the CRM opens a `web_form` deal, applies the contact rules
  and alerts the sales team.

Every call is rate-limited per IP and per session; messages and voice are refused once the daily AI spend
cap is reached (D34), and the site then shows WhatsApp click-to-chat (the documented last resort).
"""

# No `from __future__ import annotations` here: FastAPI resolves the closure-local annotations.

import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Final, Literal, Protocol

import jwt
from fastapi import APIRouter, Header, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from kx.agents.orm import Visitor
from kx.agents.roles.language import normalise_language
from kx.agents.runtime.service import AgentService
from kx.agents.site.models import SiteAction, SiteContext
from kx.agents.voice.service import VoiceRouter
from kx.agents.website.enquiry import EnquiryIn, to_enquiry
from kx.agents.website.service import Emit, SiteEvent, WebsiteTurns
from kx.core.clientip import client_ip as _ip
from kx.core.contracts import AiRole, ContactPatch, CrmGateway, SiteManifestSource
from kx.core.db import Databases
from kx.core.errors import DomainError, Forbidden, RuleViolation, TooManyRequests, Unauthorized, Unavailable
from kx.core.logging import get_logger
from kx.core.settings import LogicalDb
from kx.core.signed import sign, verify

log = get_logger(__name__)
# the visitor typed it into the site; the CRM records the change under the contact-capture role
WEBSITE_ROLE: Final = AiRole.CONTACT_CAPTURE
Clock = Callable[[], datetime]


class HumanCheck(Protocol):
    """Cloudflare Turnstile (D39): the token the widget produced, checked server-side."""

    async def verify(self, token: str, remote_ip: str | None) -> bool: ...


# "ip" / "session": requests per minute (D39). M6: "global" = AI turns per minute from every visitor together,
# "ip_sessions" = new sessions per client IP per day.
PublicScope = Literal["ip", "session", "global", "ip_sessions"]


class PublicLimits(Protocol):
    async def allow(self, scope: PublicScope, key: str) -> bool: ...


@dataclass(frozen=True, slots=True)
class LiveKitConfig:
    url: str
    api_key: str
    api_secret: str
    token_minutes: int = 30


@dataclass
class WebsiteDeps:
    databases: Databases
    service: AgentService
    gateway: CrmGateway
    secret: str  # KX_SERVICE_TOKEN_SECRET
    human_check: HumanCheck | None  # None only for local development (no Turnstile key)
    limits: PublicLimits
    ai_budget_left: Callable[[], Awaitable[bool]]  # D34 daily AI spend cap
    # D34 voice-minute cap and D35 kill switch at `text_only`: False -> the site uses chat instead
    voice_allowed: Callable[[], Awaitable[bool]] | None = None
    voice: VoiceRouter = field(default_factory=VoiceRouter)
    livekit: LiveKitConfig | None = None
    trusted_ip_header: str | None = None  # D53: "CF-Connecting-IP" on the server
    session_hours: int = 2
    clock: Clock = field(default=lambda: datetime.now(UTC))
    site_manifest: SiteManifestSource | None = None  # D55: None = the assistant answers but cannot move pages


# ---------------------------------------------------------------- request / response models
class SessionIn(BaseModel):
    turnstile_token: str = Field(default="", max_length=4096)
    language: str | None = Field(default=None, max_length=16)  # the site's or the browser's language
    voice_consent: bool = False  # "Enter the factory" pressed (§6 step 1); voice is refused without it
    cookie_consent: bool = False  # the site's cookie banner answer
    country_code: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    context: SiteContext | None = None  # D55: the page the visit starts on


class SessionOut(BaseModel):
    session_id: uuid.UUID
    session_token: str
    expires_at: datetime
    language: str
    voice_available: bool  # false for Sinhala (text only) or when live voice is not configured


class MessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    language: str | None = Field(default=None, max_length=16)  # the visitor switched language (§6 step 3)
    context: SiteContext | None = None  # D55: where the visitor is (omitted: the last one sent is used)


class MessageOut(BaseModel):
    reply: str | None  # None: the conversation is with a salesperson now; nothing to show
    language: str
    handed_over: bool  # the sales team takes over (show "you'll receive it on WhatsApp shortly")
    actions: list[SiteAction] = Field(default_factory=list, max_length=3)  # D55: do them in order, each once


class VoiceOut(BaseModel):
    livekit_url: str
    token: str
    room: str
    language: str
    expires_at: datetime


class IdentifyIn(BaseModel):
    phone: str | None = Field(default=None, max_length=32)
    email: str | None = Field(default=None, max_length=320)
    name: str | None = Field(default=None, max_length=200)


class IdentifyOut(BaseModel):
    ok: bool = True  # same answer whether or not the buyer was known (§6 step 7)


class ContactIn(BaseModel):
    """The chat's contact card. The opt-ins are separate ticks (never pre-ticked on the site)."""

    name: str | None = Field(default=None, max_length=200)
    company: str | None = Field(default=None, max_length=200)
    phone: str | None = Field(default=None, max_length=32)
    email: str | None = Field(default=None, max_length=320)
    whatsapp_opt_in: bool = False
    email_opt_in: bool = False
    consent_wording: str | None = Field(default=None, max_length=2000)  # the words next to the ticks


class EventIn(BaseModel):
    type: SiteEvent
    context: SiteContext
    seconds: float | None = Field(default=None, ge=0, le=86_400)  # how long on the product, or idle


class EventOut(BaseModel):
    reply: str | None = None  # a nudge from the assistant (show it as an assistant message), or nothing
    actions: list[SiteAction] = Field(default_factory=list, max_length=3)


class EnquiryOut(BaseModel):
    ok: bool = True
    reference: str  # a short reference to show: "We have your enquiry (W-3F9A2C)"


# ---------------------------------------------------------------- helpers
def livekit_token(
    cfg: LiveKitConfig, *, identity: str, room: str, metadata: dict[str, str], now: datetime
) -> tuple[str, datetime]:
    """A LiveKit access token (HS256 JWT, LiveKit's documented claims): join this one room only."""
    expires = now + timedelta(minutes=cfg.token_minutes)
    claims: dict[str, Any] = {
        "iss": cfg.api_key,
        "sub": identity,
        "nbf": int(now.timestamp()) - 5,
        "exp": int(expires.timestamp()),
        "name": "Website visitor",
        "metadata": json.dumps(metadata, separators=(",", ":")),  # read by the voice worker
        "video": {
            "room": room,
            "roomJoin": True,
            "canPublish": True,
            "canSubscribe": True,
            "canPublishData": True,
        },
    }
    return jwt.encode(claims, cfg.api_secret, algorithm="HS256"), expires


_RUNNING: set[asyncio.Task[None]] = set()  # streamed turns run to the end even when the browser goes away


def sse_event(name: str, data: dict[str, Any]) -> str:
    return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n"


async def stream_turn(run: Callable[[Emit], Awaitable[object]]) -> AsyncIterator[str]:
    """The turn's events as Server-Sent Events. The turn is recorded either way, so it is not cancelled
    when the browser disconnects; a failure becomes one `error` event with the code the JSON answer has."""
    queue: asyncio.Queue[tuple[str, dict[str, Any]] | None] = asyncio.Queue()

    async def emit(name: str, data: dict[str, Any]) -> None:
        await queue.put((name, data))

    async def work() -> None:
        try:
            await run(emit)
        except DomainError as exc:
            await queue.put(("error", {"status": exc.status, "code": exc.code, "message": exc.detail}))
        except Exception:
            log.exception("website.stream_failed")
            await queue.put(
                ("error", {"status": 503, "code": "unavailable", "message": "the assistant is busy"})
            )
        finally:
            await queue.put(None)

    task = asyncio.create_task(work())
    _RUNNING.add(task)
    task.add_done_callback(_RUNNING.discard)
    while (item := await queue.get()) is not None:
        yield sse_event(*item)


def build_router(deps: WebsiteDeps) -> APIRouter:  # noqa: PLR0915 - one small handler per step
    r = APIRouter(prefix="/v1/public", tags=["website"])
    turns = WebsiteTurns(deps.databases, deps.service, deps.gateway, deps.site_manifest, deps.clock)

    async def limit(scope: PublicScope, key: str) -> None:
        if not await deps.limits.allow(scope, key):
            raise TooManyRequests("too many requests; slow down")

    def claims_for(session_id: uuid.UUID, authorization: str | None) -> dict[str, Any]:
        token = (authorization or "").removeprefix("Bearer ").strip()
        claims = verify(deps.secret, token, now=deps.clock()) if token else None
        if claims is None:
            raise Unauthorized("session expired; start again")
        if claims.get("sid") != str(session_id):
            raise Forbidden("this token belongs to another session")
        return claims

    async def deal_of(session_id: uuid.UUID) -> uuid.UUID:
        """The visitor's open deal (created on first need; the CRM keys it on the visitor id)."""
        return await deps.gateway.resolve_deal("web_chat", None, session_id)

    async def human(token: str, ip: str) -> None:
        if deps.human_check is not None and not await deps.human_check.verify(token, ip):
            raise Forbidden("the human check failed; reload the page")

    @r.post("/sessions", response_model=SessionOut, status_code=201)
    async def start(body: SessionIn, request: Request) -> SessionOut:
        """§6 step 1: start a website conversation (Turnstile-checked)."""
        ip = _ip(request, deps.trusted_ip_header)
        await limit("ip", ip)
        await human(body.turnstile_token, ip)
        await limit("ip_sessions", ip)  # M6: a few new sessions per IP per day (each one solved Turnstile)
        language = normalise_language(body.language)
        now = deps.clock()
        expires = now + timedelta(hours=deps.session_hours)
        async with deps.databases.transaction(LogicalDb.AGENTS) as s:
            visitor = Visitor(
                cookie_consent=body.cookie_consent,
                voice_consent_at=now if body.voice_consent else None,  # OPEN_QUESTIONS #57
                country_code=body.country_code,
                language=language,
                site_context=body.context.model_dump(mode="json") if body.context else None,
            )
            s.add(visitor)
            await s.flush()
            session_id = visitor.id
        route = deps.voice.route(language)
        token = sign(
            deps.secret,
            {"sid": str(session_id), "lang": language, "vc": body.voice_consent},
            expires_at=expires,
        )
        return SessionOut(
            session_id=session_id,
            session_token=token,
            expires_at=expires,
            language=language,
            voice_available=route.voice_available and deps.livekit is not None,
        )

    @r.post(
        "/sessions/{session_id}/messages",
        response_model=MessageOut,
        responses={200: {"content": {"text/event-stream": {}}}},
    )
    async def message(
        session_id: uuid.UUID,
        body: MessageIn,
        request: Request,
        authorization: Annotated[str | None, Header()] = None,
    ) -> Any:
        """One chat turn (§6 steps 2-6) through the same agents, guard and router as WhatsApp; the page
        moves with it (D55). `Accept: text/event-stream` streams `action`, `delta`, `done` / `error`."""
        claims = claims_for(session_id, authorization)
        await limit("ip", _ip(request, deps.trusted_ip_header))
        await limit("session", str(session_id))
        await limit("global", "all")  # M6: AI turns per minute from all visitors together
        if not await deps.ai_budget_left():
            raise Unavailable("the assistant is busy; please continue on WhatsApp")
        deal_id = await deal_of(session_id)
        language = body.language or claims.get("lang")

        async def run(emit: Emit | None = None) -> MessageOut:
            started = time.perf_counter()
            out = await turns.turn(
                session_id, deal_id, body.text, language=language, context=body.context, emit=emit
            )
            log.info("website.turn", ms=int((time.perf_counter() - started) * 1000), actions=len(out.actions))
            return MessageOut(
                reply=out.reply, language=out.language, handed_over=out.handed_over, actions=list(out.actions)
            )

        if "text/event-stream" not in request.headers.get("accept", ""):
            return await run()
        return StreamingResponse(
            stream_turn(run),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    @r.post("/sessions/{session_id}/voice", response_model=VoiceOut)
    async def voice(
        session_id: uuid.UUID,
        request: Request,
        authorization: Annotated[str | None, Header()] = None,
    ) -> VoiceOut:
        """A LiveKit room token for live voice (participant metadata: deal_ref, language, session_id)."""
        claims = claims_for(session_id, authorization)
        await limit("ip", _ip(request, deps.trusted_ip_header))
        await limit("session", str(session_id))
        await limit("global", "all")  # M6
        if not claims.get("vc"):
            raise RuleViolation("press Enter the factory to start voice", reason="no_voice_consent")
        route = deps.voice.route(claims.get("lang"))
        if deps.livekit is None or not route.voice_available:
            raise RuleViolation(
                "live voice is not available in this language; use the chat", reason="text_only"
            )
        if not await deps.ai_budget_left() or (
            deps.voice_allowed is not None and not await deps.voice_allowed()
        ):
            raise Unavailable("voice is busy; please use the chat")
        deal_id = await deal_of(session_id)
        room = f"web-{session_id}"
        token, expires = livekit_token(
            deps.livekit,
            identity=f"visitor-{session_id}",
            room=room,
            metadata={"deal_ref": str(deal_id), "language": route.language, "session_id": str(session_id)},
            now=deps.clock(),
        )
        return VoiceOut(
            livekit_url=deps.livekit.url, token=token, room=room, language=route.language, expires_at=expires
        )

    async def save_contact(session_id: uuid.UUID, patch: ContactPatch) -> None:
        deal_id = await deal_of(session_id)
        try:
            await deps.gateway.set_contact(deal_id, patch, WEBSITE_ROLE)
        except ValueError as exc:
            raise RuleViolation("that phone number or email does not look right", reason="invalid") from exc

    @r.post("/sessions/{session_id}/identify", response_model=IdentifyOut)
    async def identify(
        session_id: uuid.UUID,
        body: IdentifyIn,
        request: Request,
        authorization: Annotated[str | None, Header()] = None,
    ) -> IdentifyOut:
        """§6 step 7: the visitor typed their phone or email; a returning buyer is recognised now, never
        before. The answer does not say whether the buyer was known."""
        claims_for(session_id, authorization)
        await limit("ip", _ip(request, deps.trusted_ip_header))
        await limit("session", str(session_id))
        if not (body.phone or body.email):
            raise RuleViolation("give a phone number or an email", reason="empty")
        await save_contact(session_id, ContactPatch(phone=body.phone, email=body.email, name=body.name))
        return IdentifyOut()

    @r.post("/sessions/{session_id}/contact", response_model=IdentifyOut)
    async def contact(
        session_id: uuid.UUID,
        body: ContactIn,
        request: Request,
        authorization: Annotated[str | None, Header()] = None,
    ) -> IdentifyOut:
        """The chat's contact card (D59): details plus the WhatsApp / email opt-ins with their wording. The
        same rules as identify (a typed phone or email is a claim, D-4) and the same answer."""
        claims_for(session_id, authorization)
        await limit("ip", _ip(request, deps.trusted_ip_header))
        await limit("session", str(session_id))
        if not (body.phone or body.email):
            raise RuleViolation("give a phone number or an email", reason="empty")
        patch = ContactPatch(
            name=body.name,
            company=body.company,
            phone=body.phone,
            email=body.email,
            whatsapp_opt_in=body.whatsapp_opt_in,
            email_opt_in=body.email_opt_in,
            consent_wording=body.consent_wording,
            consent_via="web_chat",
        )
        await save_contact(session_id, patch)
        return IdentifyOut()

    @r.post("/sessions/{session_id}/events", response_model=EventOut)
    async def event(
        session_id: uuid.UUID,
        body: EventIn,
        request: Request,
        authorization: Annotated[str | None, Header()] = None,
    ) -> EventOut:
        """The visitor moved on the site. Their place is saved; the assistant may nudge (rarely, and only
        after the visitor has spoken: `website/service.py`)."""
        claims_for(session_id, authorization)
        await limit("ip", _ip(request, deps.trusted_ip_header))
        await limit("session", str(session_id))
        if body.type in ("page_view", "product_open") or not await deps.ai_budget_left():
            await turns.remember(session_id, body.context)
            return EventOut()
        nudge = await turns.nudge(session_id, body.type, body.context, body.seconds)
        if nudge is None:
            return EventOut()
        return EventOut(reply=nudge.reply, actions=list(nudge.actions))

    @r.post("/enquiries", response_model=EnquiryOut, status_code=201)
    async def enquiry(
        body: EnquiryIn,
        request: Request,
        authorization: Annotated[str | None, Header()] = None,
    ) -> EnquiryOut:
        """CT-01 enquiry desk (replaces `mailto:`). With a session token the enquiry joins that visit's deal;
        without one a Turnstile token is required. The CRM gets English labels (design doc §6)."""
        ip = _ip(request, deps.trusted_ip_header)
        await limit("ip", ip)
        if body.session_id is not None:
            claims_for(body.session_id, authorization)
            await limit("session", str(body.session_id))
            visitor_ref = body.session_id
        else:
            await human(body.turnstile_token, ip)
            await limit("ip_sessions", ip)  # an enquiry without a session counts like a new session
            visitor_ref = uuid.uuid7()
        if not (body.phone or body.email):
            raise RuleViolation("give a phone number or an email", reason="empty")
        manifest = await turns.manifest()
        try:
            deal_id = await deps.gateway.website_enquiry(visitor_ref, to_enquiry(body, manifest))
        except ValueError as exc:
            raise RuleViolation("that phone number or email does not look right", reason="invalid") from exc
        return EnquiryOut(reference=f"W-{deal_id.hex[-6:].upper()}")

    return r
