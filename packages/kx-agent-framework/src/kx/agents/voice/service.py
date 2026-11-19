"""Voice: speech vendor routing by language + a LiveKit Agents worker entrypoint.

Routing (16 Nov, D52; server plan v2 §2): the order per language comes from the platform modes
(`kx.core.platform`, D41). Paid version: Deepgram Nova-3 -> faster-whisper Small (our speech server) for STT,
ElevenLabs Flash -> Piper for TTS, inside one call (the app wraps them in LiveKit fallback adapters). Free
version: faster-whisper + Piper only. Piper has no Tamil voice, so Tamil uses ElevenLabs (both versions, when
its key is set); Sinhala has no live-voice option and stays text-only (the website offers chat). Last resorts
are the website's own: text chat, then WhatsApp click-to-chat.

The worker runs the SAME AgentService turn as chat (same graphs, same price guard) and re-checks the reply
against the deal's allowed prices right before TTS: speech cannot be recalled. The recording notice always
plays first (§6.1). On a call "stop" means "wait" (§8.1 step 10): the agent goes quiet and listens; it ends
the call only on a clear request, and only "stop calling me" / "don't call me" stops calls.
livekit-agents is an optional extra; the vendor plugins are installed with the app.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Collection, Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Final, Literal, Protocol, cast

from kx.agents.guard.service import PriceGuard
from kx.agents.roles.language import DEFAULT_LANGUAGE, normalise_language
from kx.agents.roles.registry import Phrase, PromptStore
from kx.agents.runtime.service import AgentService, InboundTurn
from kx.agents.site.models import visit_thread
from kx.agents.voice.bridge import VisitHooks, VisitTurn, VoiceBridge
from kx.core.contracts import AiRole, CrmGateway
from kx.core.optout import match_voice
from kx.core.platform import Field, Modes, order, resolve_modes

SttVendor = Literal["deepgram", "whisper"]
TtsVendor = Literal["elevenlabs", "piper"]
STT_ORDER: Final[tuple[SttVendor, ...]] = ("deepgram", "whisper")  # paid version: Deepgram -> faster-whisper
TTS_ORDER: Final[tuple[TtsVendor, ...]] = ("elevenlabs", "piper")  # paid version: ElevenLabs Flash -> Piper
VOICE_LANGUAGES: Final = ("en", "ar", "hi", "ta", "zh", "si")


@dataclass(frozen=True, slots=True)
class VoiceRoute:
    language: str
    stt: tuple[SttVendor, ...] = STT_ORDER  # first = primary; the app wraps them in a fallback adapter
    tts: tuple[TtsVendor, ...] = TTS_ORDER
    voice_id: str | None = None
    text_only: bool = False  # no live-voice vendor for this language: the website uses chat

    @property
    def voice_available(self) -> bool:
        return not self.text_only and bool(self.stt) and bool(self.tts)

    @property
    def free_stt(self) -> bool:
        """The call runs on our CPU speech-to-text only (counts against the free-version call cap, D52)."""
        return self.stt == ("whisper",)


def voice_routes(
    modes: Modes, configured: Collection[str], languages: Collection[str] = VOICE_LANGUAGES
) -> dict[str, VoiceRoute]:
    """One route per language from the paid / free version of the `stt` and `tts` fields."""
    routes: dict[str, VoiceRoute] = {}
    for lang in languages:
        stt = cast("tuple[SttVendor, ...]", order(Field.STT, modes, configured, language=lang))
        tts = cast("tuple[TtsVendor, ...]", order(Field.TTS, modes, configured, language=lang))
        routes[lang] = VoiceRoute(lang, stt=stt, tts=tts, text_only=not (stt and tts))
    return routes


DEFAULT_VOICE_MAP: Final[dict[str, VoiceRoute]] = voice_routes(
    resolve_modes("paid", {}), frozenset({"deepgram", "whisper", "elevenlabs", "piper"})
)


class VoiceRouter:
    def __init__(
        self, routes: Mapping[str, VoiceRoute] = DEFAULT_VOICE_MAP, default: str = DEFAULT_LANGUAGE
    ) -> None:
        self._routes = dict(routes)
        self._default = default

    def route(self, language: str | None) -> VoiceRoute:
        return self._routes.get(normalise_language(language, self._default)) or self._routes[self._default]


async def speakable(
    text: str, gateway: CrmGateway, deal_id: uuid.UUID, prompts: PromptStore, language: str
) -> str:
    """Last check before TTS against the fresh deal context (CRM adds issued offers to it)."""
    deal = await gateway.deal_context(deal_id)
    guard = PriceGuard(deal.allowed_prices, deal.currency, prompts.phrase(Phrase.PRICE_FALLBACK, language))
    bad, disclosure = guard.violations(text)
    return guard.fallback if bad or disclosure else text


async def voice_stop(service: AgentService, deal_id: uuid.UUID, text: str, language: str) -> str | None:
    """§8.1 step 10. None = an ordinary turn; "" = say nothing and listen; else the fixed line to speak.
    Only a clear "stop calling me" / "don't call me" records a stop, and only for calls (scope phone)."""
    kind = match_voice(text)
    prompts = service.prompts
    match kind:
        case "wait":
            return ""
        case "phone_stop":
            await service.gateway.record_opt_out(
                deal_id,
                scope="phone",
                exact_words=text,
                reason=None,
                decided_by="rule:voice_phone_stop",
                actor=AiRole.COMMUNICATION,
            )
            return prompts.phrase(Phrase.VOICE_GOODBYE, language)
        case "end_call":
            return prompts.phrase(Phrase.VOICE_GOODBYE, language)
        case "unclear":
            return prompts.phrase(Phrase.VOICE_STOP_CHECK, language)
        case _:
            return None


class VoicePlugins(Protocol):
    """App-supplied factory for LiveKit STT/TTS/VAD plugin objects (vendor SDKs are app deps)."""

    def stt(self, route: VoiceRoute) -> Any: ...
    def tts(self, route: VoiceRoute) -> Any: ...
    def vad(self) -> Any: ...


def graph_llm() -> Any:
    """An `llm.LLM` the session holds but never calls (bench R2b): livekit-agents skips every reply when the
    session has no LLM, before `llm_node` runs, so the caller heard only the recording notice. Our agent's
    `llm_node` answers through the Kairox graphs instead; if anything ever calls this model, it fails
    loudly."""
    from livekit import agents as lk  # noqa: PLC0415 - optional extra

    class GraphLLM(lk.llm.LLM):  # type: ignore[type-arg]
        @property
        def model(self) -> str:
            return "kairox-graphs"

        def chat(self, **_: Any) -> Any:
            raise RuntimeError("the Kairox voice agent answers in llm_node; this model is never called")

    return GraphLLM()


CallHook = Callable[["VoiceRoute", bool], Awaitable[object]]  # (route, started): start / end of a call


def make_livekit_entrypoint(
    service: AgentService,
    plugins: VoicePlugins,
    router: VoiceRouter | None = None,
    *,
    on_minutes: Callable[[Decimal], Awaitable[object]] | None = None,
    on_call: CallHook | None = None,
    visit_turn: VisitTurn | None = None,
    visit_hooks: VisitHooks | None = None,
) -> Callable[[Any], Any]:
    """Entrypoint for `livekit.agents.cli.run_app(WorkerOptions(entrypoint_fnc=...))`.
    Participant metadata must carry `deal_ref` and `language` (set by the token endpoint). `on_minutes`
    receives each call's length in minutes when the room closes (D34 daily voice-minute cap); `on_call` is
    told
    when a call starts and ends (the app counts free-version calls at once, D52). With `visit_turn` and
    `visit_hooks` (WP-C5) a call is the website visit's own turn: page actions and captions go to the browser
    on the room's data channel and typed text comes back on it (`kx.agents.voice.bridge`)."""
    from livekit import agents as lk  # noqa: PLC0415 - optional extra: imported only when the voice worker starts

    voice_router = router or VoiceRouter()

    class KairoxVoiceAgent(lk.Agent):
        def __init__(self, deal_id: uuid.UUID, language: str, room: str, bridge: VoiceBridge | None) -> None:
            super().__init__(instructions="Kairox sales assistant (replies come from the Kairox graphs).")
            self._deal_id, self._language, self._room = deal_id, language, room
            self._bridge = bridge

        async def on_enter(self) -> None:
            if self._bridge is not None and not await self._bridge.notice_due():
                return  # a rejoin after a page load: the notice was given, and no second greeting
            notice = service.prompts.phrase(Phrase.RECORDING_NOTICE, self._language)
            await self.session.say(notice, allow_interruptions=False)

        async def llm_node(self, chat_ctx: Any, tools: Any, model_settings: Any) -> AsyncIterator[str]:
            text = chat_ctx.items[-1].text_content or ""
            if (said := await voice_stop(service, self._deal_id, text, self._language)) is not None:
                if said:  # "" = stay quiet and listen ("stop" means "wait", §8.1 step 10)
                    yield said
                return
            if self._bridge is not None:  # WP-C5: the visit's turn (page actions and captions on the room)
                reply = await self._bridge.answer(text)
                if reply:
                    yield await speakable(
                        reply, service.gateway, self._deal_id, service.prompts, self._language
                    )
                return
            result = await service.handle_turn(
                InboundTurn(
                    channel="web_voice",
                    text=text,
                    deal_id=self._deal_id,
                    thread_key=visit_thread(self._room.removeprefix("web-")),  # D58: same as the chat
                    trigger_event="voice.utterance",
                    trigger_event_id=uuid.uuid7(),
                    language=self._language,
                )
            )
            if result.reply:
                yield await speakable(
                    result.reply, service.gateway, self._deal_id, service.prompts, result.language
                )

    async def entrypoint(ctx: Any) -> None:
        started = time.monotonic()

        async def count_minutes() -> None:
            if on_minutes is not None:
                await on_minutes(Decimal(str(round((time.monotonic() - started) / 60, 4))))

        ctx.add_shutdown_callback(count_minutes)
        await ctx.connect()
        participant = await ctx.wait_for_participant()
        meta = json.loads(participant.metadata or "{}")
        route = voice_router.route(meta.get("language"))
        if not route.voice_available:  # Sinhala: the token endpoint should not have issued a voice token
            await ctx.room.disconnect()
            return
        if on_call is not None:
            await on_call(route, True)

            async def call_ended() -> None:
                await on_call(route, False)

            ctx.add_shutdown_callback(call_ended)
        session: Any = lk.AgentSession(
            stt=plugins.stt(route), tts=plugins.tts(route), vad=plugins.vad(), llm=graph_llm()
        )
        deal_id = uuid.UUID(meta["deal_ref"])
        bridge = _bridge(ctx, meta, deal_id, language=route.language, turn=visit_turn, hooks=visit_hooks)
        await session.start(
            room=ctx.room, agent=KairoxVoiceAgent(deal_id, route.language, ctx.room.name, bridge)
        )
        if bridge is not None:
            _listen(ctx.room, bridge, session)

    return entrypoint


def _bridge(
    ctx: Any,
    meta: dict[str, Any],
    deal_id: uuid.UUID,
    *,
    language: str,
    turn: VisitTurn | None,
    hooks: VisitHooks | None,
) -> VoiceBridge | None:
    if turn is None or hooks is None:
        return None
    try:
        session_id = uuid.UUID(meta.get("session_id") or ctx.room.name.removeprefix("web-"))
    except ValueError:
        return None  # not a website visit's room

    async def publish(topic: str, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        await ctx.room.local_participant.publish_data(body, reliable=True, topic=topic)

    return VoiceBridge(session_id, deal_id, language, turn, hooks, publish)


_DATA_TASKS: set[asyncio.Task[None]] = set()


def _listen(room: Any, bridge: VoiceBridge, session: Any) -> None:
    """Typed text and page moves from the browser during the call (one ordered conversation)."""

    async def handle(topic: str | None, data: bytes) -> None:
        text = await bridge.on_data(topic, data)
        if text:
            session.generate_reply(user_input=text)  # answered through llm_node, like speech

    def on_data(packet: Any) -> None:
        task = asyncio.create_task(handle(packet.topic, bytes(packet.data)))
        _DATA_TASKS.add(task)
        task.add_done_callback(_DATA_TASKS.discard)

    room.on("data_received", on_data)
