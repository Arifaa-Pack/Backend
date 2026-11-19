"""D55 / D57 / D58: one website turn, where the visitor is on the site, and the assistant's few nudges.

The router (`website/router.py`) checks the session token, the rate limits and the AI budget; this module
does the work:

* **A turn** (`WebsiteTurns.turn`). The instant resolver (`kx.agents.site.resolver`, layer 1) answers a clear
  "show me foil" in milliseconds: its action is sent before the agent starts. The agent (layer 2) may add
  actions with its site tools while it writes the reply; every action is checked against the site manifest,
  sent once (the same intent from both layers has the same id) and at most `MAX_ACTIONS` per turn. The reply
  has passed the price guard as a whole before any of it is sent.
* **One thread** (D58): chat and voice of one website visit share `web:{session_id}`, so a visitor who
  types after speaking (or the other way round) continues the same conversation.
* **The visitor's place** (`site_context`): saved with every message and every page / product change, so a
  voice turn or a nudge knows the page even when the browser did not send it again.
* **Nudges** (`WebsiteTurns.nudge`): the assistant speaks first only after the visitor has engaged
  (design doc AS-01), at most `MAX_NUDGES` per visit, never twice within `NUDGE_GAP`, and only on: a product
  open for `PRODUCT_DWELL_SECONDS`, an item added to the enquiry list, or `CONTACT_IDLE_SECONDS` idle on the
  Contact page (user decision 19 Nov). Nudges are fixed texts (no model call, no price, nothing to guard);
  they exist in English and Arabic, the two site languages, and are skipped in the other chat languages.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Final, Literal

from sqlalchemy import select

from kx.agents.memory.service import TurnStore
from kx.agents.orm import Visitor
from kx.agents.runtime.service import AgentService, InboundTurn
from kx.agents.site.actions import resolve_action
from kx.agents.site.models import AskContact, SiteAction, SiteContext, visit_thread
from kx.agents.site.resolver import instant_actions
from kx.core.contracts import AiRole, CrmGateway, SiteManifest, SiteManifestSource
from kx.core.db import Databases
from kx.core.logging import get_logger
from kx.core.settings import LogicalDb

log = get_logger(__name__)

TRIGGER: Final = "website.message"
MAX_ACTIONS: Final = 3
MAX_NUDGES: Final = 3
NUDGE_GAP: Final = timedelta(seconds=30)
PRODUCT_DWELL_SECONDS: Final = 20.0
CONTACT_IDLE_SECONDS: Final = 45.0
HANDED_OVER: Final = frozenset({"handoff", "acknowledged", "silent"})
_SENTENCE: Final = re.compile(r"(?<=[.!?؟。])\s+")

Emit = Callable[[str, dict[str, Any]], Awaitable[None]]
SiteEvent = Literal["page_view", "product_open", "product_dwell", "enquiry_add", "idle"]
NudgeKind = Literal["product", "enquiry", "contact"]


def sentences(text: str) -> list[str]:
    """The reply in sentence-sized pieces (the SSE `delta` events; voice speaks them one by one)."""
    return [s for s in _SENTENCE.split(text.strip()) if s]


class ActionSink:
    """Sends each action once (the same intent id = the same action, D57), at most `MAX_ACTIONS` a turn."""

    def __init__(self, emit: Emit | None) -> None:
        self._emit = emit
        self._ids: set[str] = set()
        self.sent: list[SiteAction] = []

    async def __call__(self, action: SiteAction) -> None:
        if action.id in self._ids or len(self.sent) >= MAX_ACTIONS:
            return
        self._ids.add(action.id)
        self.sent.append(action)
        if self._emit is not None:
            await self._emit("action", action.model_dump(mode="json"))


@dataclass(frozen=True, slots=True)
class WebTurn:
    reply: str | None
    language: str
    handed_over: bool
    actions: tuple[SiteAction, ...] = ()


@dataclass(frozen=True, slots=True)
class Nudge:
    kind: NudgeKind
    reply: str
    actions: tuple[SiteAction, ...] = ()


# fixed nudge texts (no model call). {name}: the product's name as the website shows it.
NUDGES: Final[dict[str, dict[NudgeKind, str]]] = {
    "en": {
        "product": "Would you like the sizes, packing and availability of the {name}? Just ask.",
        "enquiry": "Added to your list. When it is ready, I can pass it to our sales team for a quotation.",
        "contact": "Need a hand with the enquiry? Tell me what you need and I will fill it in for you.",
    },
    "ar": {
        "product": "هل تريد معرفة المقاسات والتعبئة والتوفر لـ {name}؟ اسألني.",
        "enquiry": "تمت الإضافة إلى قائمتك. عندما تكون جاهزة، أرسلها إلى فريق المبيعات لإعداد عرض السعر.",
        "contact": "هل تحتاج مساعدة في الاستفسار؟ أخبرني بما تحتاجه وسأملؤه لك.",
    },
}


@dataclass
class WebsiteTurns:
    databases: Databases
    service: AgentService
    gateway: CrmGateway
    manifest_source: SiteManifestSource | None
    clock: Callable[[], datetime]
    nudges: dict[str, dict[NudgeKind, str]] = field(default_factory=lambda: NUDGES)

    async def manifest(self) -> SiteManifest | None:
        """The site map, or None: the assistant still answers, it just cannot move the page."""
        if self.manifest_source is None:
            return None
        try:
            return await self.manifest_source.manifest()
        except Exception:
            log.warning("website.manifest_unavailable", exc_info=True)
            return None

    async def remember(
        self, session_id: uuid.UUID, context: SiteContext | None, *, engaged: bool = False
    ) -> SiteContext | None:
        """Save the visitor's place (when sent) and return the latest one known."""
        now = self.clock()
        async with self.databases.transaction(LogicalDb.AGENTS) as s:
            visitor = await s.get(Visitor, session_id)
            if visitor is None:
                return context
            if context is not None:
                visitor.site_context = context.model_dump(mode="json")
            if engaged and visitor.engaged_at is None:
                visitor.engaged_at = now
            visitor.last_seen_at = now
            saved = visitor.site_context
        if context is not None or saved is None:
            return context
        return SiteContext.model_validate(saved)

    async def turn(
        self,
        session_id: uuid.UUID,
        deal_id: uuid.UUID,
        text: str,
        *,
        language: str | None,
        context: SiteContext | None,
        channel: Literal["web_chat", "web_voice"] = "web_chat",
        emit: Emit | None = None,
    ) -> WebTurn:
        context = await self.remember(session_id, context, engaged=True)
        manifest = await self.manifest()
        sink = ActionSink(emit)
        if manifest is not None:  # layer 1: a clear request moves the page before the agent starts
            for match in instant_actions(text, manifest, context):
                await sink(match.action)
        result = await self.service.handle_turn(
            InboundTurn(
                channel=channel,
                text=text,
                deal_id=deal_id,
                thread_key=visit_thread(session_id),
                trigger_event=TRIGGER,
                trigger_event_id=uuid.uuid7(),
                language=language,
                site=context,
                manifest=manifest,
                on_action=sink,
                site_actions=tuple(sink.sent),
            )
        )
        if manifest is not None:
            for action in result.log.site_actions:  # anything the agent asked for and not sent yet
                if (checked := resolve_action(action, manifest, context)) is not None:
                    await sink(checked)
        handed_over = result.status in HANDED_OVER
        if emit is not None:
            for piece in sentences(result.reply or ""):
                await emit("delta", {"text": piece})
            await emit("done", {"language": result.language, "handed_over": handed_over})
        return WebTurn(result.reply, result.language, handed_over, tuple(sink.sent))

    async def nudge(
        self,
        session_id: uuid.UUID,
        event: SiteEvent,
        context: SiteContext,
        seconds: float | None,
    ) -> Nudge | None:
        """The assistant speaks first, within the limits in the module docstring; None = stay quiet."""
        kind = _nudge_kind(event, context, seconds)
        async with self.databases.transaction(LogicalDb.AGENTS) as s:
            visitor = await s.get(Visitor, session_id)
            if visitor is None:
                return None
            visitor.site_context = context.model_dump(mode="json")
            visitor.last_seen_at = self.clock()
            language = visitor.language or "en"
            allowed = kind is not None and language in self.nudges and self._allowed(visitor)
        if not allowed or kind is None:
            return None
        deal_id = await self.gateway.resolve_deal("web_chat", None, session_id)
        deal = await self.gateway.deal_context(deal_id)
        if not deal.ai_enabled or deal.ai_paused_reason is not None:
            return None  # a salesperson has the conversation: the assistant stays quiet
        manifest = await self.manifest()
        name = _product_name(manifest, context) or ""
        if (kind == "product" and not name) or not await self._take_nudge(session_id):
            return None
        reply = self.nudges[language][kind].format(name=name)
        actions: tuple[SiteAction, ...] = ()
        if kind == "enquiry" and not (deal.contact.has_phone or deal.contact.has_email):
            ask = resolve_action(AskContact(), manifest) if manifest is not None else None
            actions = (ask,) if ask is not None else ()
        async with self.databases.transaction(LogicalDb.AGENTS) as s:  # the agent sees it in the history
            conv = await TurnStore.open_conversation(
                s,
                thread_key=visit_thread(session_id),
                channel="web_chat",
                deal_ref=deal_id,
                contact_ref=None,
                language=language,
            )
            await TurnStore.append(s, conv, "ai", reply, role="nudge", language=language)
        await self.gateway.record_message(deal_id, "out", "web_chat", reply, AiRole.COMMUNICATION)
        log.info("website.nudge", kind=kind, session=str(session_id))
        return Nudge(kind, reply, actions)

    def _allowed(self, visitor: Visitor) -> bool:
        now = self.clock()
        return (
            visitor.engaged_at is not None  # AS-01: never before the visitor has spoken
            and visitor.proactive_count < MAX_NUDGES
            and (visitor.last_proactive_at is None or now - visitor.last_proactive_at >= NUDGE_GAP)
        )

    async def _take_nudge(self, session_id: uuid.UUID) -> bool:
        """Count the nudge under a row lock: two events at once cannot both nudge."""
        async with self.databases.transaction(LogicalDb.AGENTS) as s:
            stmt = select(Visitor).where(Visitor.id == session_id).with_for_update()
            visitor = (await s.execute(stmt)).scalar_one_or_none()
            if visitor is None or not self._allowed(visitor):
                return False
            visitor.proactive_count += 1
            visitor.last_proactive_at = self.clock()
            return True


def _nudge_kind(event: SiteEvent, context: SiteContext, seconds: float | None) -> NudgeKind | None:
    if event == "product_dwell" and (context.product_id or context.quick_view_id):
        return "product" if (seconds or 0) >= PRODUCT_DWELL_SECONDS else None
    if event == "enquiry_add" and context.enquiry_list:
        return "enquiry"
    if event == "idle" and context.page == "contact":
        return "contact" if (seconds or 0) >= CONTACT_IDLE_SECONDS else None
    return None


def _product_name(manifest: SiteManifest | None, context: SiteContext) -> str | None:
    pid = context.product_id or context.quick_view_id
    if manifest is None or pid is None:
        return None
    return next((p.name for p in manifest.products if p.id == pid), None)
