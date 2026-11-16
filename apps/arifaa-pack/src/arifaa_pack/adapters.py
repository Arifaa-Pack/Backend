"""App-side implementations of package Protocols that need more than one package or an external service."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any, Literal, Protocol, cast

import httpx
from sqlalchemy import text

from kx.agents.handlers import ReplyAddress
from kx.agents.knowledge import KbSource
from kx.agents.voice import VoiceRoute
from kx.core.db import Databases
from kx.core.errors import Unavailable
from kx.core.logging import get_logger
from kx.core.settings import LogicalDb

log = get_logger(__name__)

_ADDRESS_SQL = """
    SELECT ca.channel, t.external_key, t.contact_ref
    FROM automation.threads t JOIN automation.channel_accounts ca ON ca.id = t.channel_account_id
    WHERE t.{column} = :ref AND ca.channel IN ('whatsapp', 'email')
    ORDER BY t.last_inbound_at DESC NULLS LAST LIMIT 1
"""
_THREAD_SQL = text(_ADDRESS_SQL.format(column="id"))
_DEAL_SQL = text(_ADDRESS_SQL.format(column="deal_ref"))


class ThreadAddressBook:
    """kx.agents ReplyAddressBook over automation.threads: agents never see raw addresses, the app
    resolves them here, from the automation DB, when a reply is about to be queued."""

    def __init__(self, databases: Databases) -> None:
        self._db = databases

    async def _one(self, sql: Any, ref: uuid.UUID) -> ReplyAddress | None:
        async with self._db.transaction(LogicalDb.AUTOMATION) as s:
            row = (await s.execute(sql, {"ref": ref})).first()
        if row is None:
            return None
        channel = cast("Literal['whatsapp', 'email']", row[0])
        return ReplyAddress(channel=channel, address=row[1], contact_ref=row[2])

    async def for_thread(self, thread_ref: uuid.UUID) -> ReplyAddress | None:
        return await self._one(_THREAD_SQL, thread_ref)

    async def for_deal(self, deal_ref: uuid.UUID) -> ReplyAddress | None:
        return await self._one(_DEAL_SQL, deal_ref)


# Qwen3-Embedding model card: queries carry a one-line task instruction, documents do not. BGE-M3 takes plain
# text on both sides (no prefix, no instruction).
QWEN3_QUERY_INSTRUCTION = "Given a web search query, retrieve relevant passages that answer the query"

# D47: KX_PLATFORM__EMBEDDINGS -> (Hugging Face model id served by TEI, query instruction)
EMBEDDING_MODELS: dict[str, tuple[str, str | None]] = {
    "bge-m3": ("BAAI/bge-m3", None),
    "qwen3": ("Qwen/Qwen3-Embedding-0.6B", QWEN3_QUERY_INSTRUCTION),
}


class HttpEmbedder:
    """D47: BGE-M3 (or Qwen3-Embedding-0.6B) on Hugging Face Text Embeddings Inference, over the
    OpenAI-compatible `POST /v1/embeddings`. 1,024 dimensions (`kb_chunks.vector(1024)` stays); vectors are
    L2-normalised so pgvector cosine distance == 1 - dot product. The model name is stored on every chunk: a
    model change means `kx reindex`. The HTTP timeout is the *indexing* one (CPU re-index requests took ≈ 26 s
    under load in the bench); the 2 s query cut-off is applied by `KnowledgeBase`."""

    dimensions = 1024

    def __init__(
        self,
        http: httpx.AsyncClient,
        base_url: str,
        model: str,
        *,
        api_key: str | None = None,
        timeout_seconds: float = 120.0,
        query_instruction: str | None = None,
        batch: int = 8,
    ) -> None:
        self._http = http
        self._url = base_url.rstrip("/") + "/v1/embeddings"
        self.model = model
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._timeout = timeout_seconds
        self._instruction = query_instruction
        self._batch = batch

    async def _embed(self, texts: Sequence[str]) -> list[list[float]]:
        r = await self._http.post(
            self._url,
            json={"model": self.model, "input": list(texts)},
            headers=self._headers,
            timeout=self._timeout,
        )
        r.raise_for_status()
        rows = sorted(r.json()["data"], key=lambda d: int(d["index"]))
        vectors = [_normalise([float(x) for x in row["embedding"]]) for row in rows]
        if any(len(v) != self.dimensions for v in vectors):
            raise ValueError(f"{self.model} returned vectors that are not {self.dimensions}-dimensional")
        return vectors

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), self._batch):
            out += await self._embed(texts[i : i + self._batch])
        return out

    async def embed_query(self, text_: str) -> list[float]:
        query = f"Instruct: {self._instruction}\nQuery:{text_}" if self._instruction else text_
        return (await self._embed([query]))[0]


def _normalise(v: list[float]) -> list[float]:
    norm = sum(x * x for x in v) ** 0.5
    return [x / norm for x in v] if norm else v


class PayloadContentSource:
    """kx.agents ContentSource over the Payload CMS REST API (only published documents are indexed)."""

    def __init__(self, http: httpx.AsyncClient, base_url: str, api_key: str | None) -> None:
        self._http = http
        self._base = base_url.rstrip("/")
        self._headers = {"Authorization": f"users API-Key {api_key}"} if api_key else {}

    async def fetch(self, collection: str, cms_id: str, language: str) -> KbSource | None:
        r = await self._http.get(
            f"{self._base}/api/{collection}/{cms_id}",
            params={"locale": language, "depth": 0},
            headers=self._headers,
        )
        if r.status_code == httpx.codes.NOT_FOUND:
            return None
        r.raise_for_status()
        doc = r.json()
        if doc.get("_status", "published") != "published":
            return None
        updated = doc.get("updatedAt")
        return KbSource(
            collection=collection,
            cms_id=cms_id,
            cms_version=str(doc.get("version") or updated or ""),
            language=language,
            title=str(doc.get("title") or ""),
            body=str(doc.get("plainText") or doc.get("body") or ""),
            approved_at=datetime.fromisoformat(updated) if updated else datetime.now(UTC),
            url_path=doc.get("slug"),
        )


class PlatformVoicePlugins:
    """kx.agents VoicePlugins (D52). Each route's vendors (from the paid / free version, `kx.core.platform`)
    are
    wrapped in LiveKit's fallback adapter, so a vendor outage switches inside a live call: Deepgram ->
    faster-whisper when no transcript comes, ElevenLabs -> Piper when no audio comes. The free engines are our
    speech server (`services/speech`, OpenAI-compatible) reached through `livekit-plugins-openai`. Paid
    plug-ins
    read their keys from the environment (DEEPGRAM_API_KEY, ELEVEN_API_KEY). A vendor missing from the route
    is never built; every switch is reported to `on_switch` (logged, counted, ops alerted)."""

    def __init__(
        self, speech_url: str | None, on_switch: Callable[[str, str, str], None] | None = None
    ) -> None:
        self._speech = (speech_url or "http://speech:8100").rstrip("/") + "/v1"
        self._on_switch = on_switch

    def stt(self, route: VoiceRoute) -> Any:
        from livekit.agents import stt as lk_stt  # noqa: PLC0415 - voice process only

        engines = [self._stt(v, route.language) for v in route.stt]
        return engines[0] if len(engines) == 1 else self._watch(lk_stt.FallbackAdapter(engines), "stt", route)

    def tts(self, route: VoiceRoute) -> Any:
        from livekit.agents import tts as lk_tts  # noqa: PLC0415 - voice process only

        engines = [self._tts(v, route) for v in route.tts]
        return engines[0] if len(engines) == 1 else self._watch(lk_tts.FallbackAdapter(engines), "tts", route)

    def vad(self) -> Any:
        from livekit.plugins import silero  # noqa: PLC0415

        return silero.VAD.load()

    def _stt(self, vendor: str, language: str) -> Any:
        if vendor == "deepgram":  # Nova-3 Multilingual (en, ar, hi, ta, zh; no Sinhala)
            from livekit.plugins import deepgram  # noqa: PLC0415

            return deepgram.STT(model="nova-3", language="multi")
        from livekit.plugins import openai  # noqa: PLC0415

        # faster-whisper Small on our server; VAD-segmented (non-streaming) recognition
        return openai.STT(
            base_url=self._speech, api_key="local", model="small", language=language, use_realtime=False
        )

    def _tts(self, vendor: str, route: VoiceRoute) -> Any:
        if vendor == "elevenlabs":  # Flash: the low-latency model the plan prices
            from livekit.plugins import elevenlabs  # noqa: PLC0415

            if route.voice_id:
                return elevenlabs.TTS(model="eleven_flash_v2_5", voice_id=route.voice_id)
            return elevenlabs.TTS(model="eleven_flash_v2_5")
        from livekit.plugins import openai  # noqa: PLC0415

        return openai.TTS(
            base_url=self._speech, api_key="local", model="piper", voice=route.language, response_format="pcm"
        )

    def _watch(self, adapter: Any, field: str, route: VoiceRoute) -> Any:
        """Report a vendor going unavailable mid-call (LiveKit emits `availability_changed`)."""
        notify = self._on_switch
        if notify is not None and hasattr(adapter, "on"):
            vendors = route.stt if field == "stt" else route.tts

            def changed(event: Any) -> None:
                if not getattr(event, "available", True):
                    notify(field, vendors[0], vendors[-1])

            adapter.on("availability_changed", changed)
        return adapter


class PlainMailer(Protocol):
    async def send(self, *, to_address: str, subject: str, body: str) -> str: ...


class StaffCodeMailer:
    """kx.core StaffMailer (D26): a sign-in or recovery code to a staff work address, straight through the
    email field's vendors in order (Stalwart, then the SES API). Never through the buyer outbox, so a code is
    never
    stored in a message table. Every vendor failing is a 503 for the login screen ("use your
    authenticator")."""

    def __init__(self, senders: Sequence[PlainMailer]) -> None:
        self._senders = tuple(senders)

    async def send_code(self, to_email: str, subject: str, body: str) -> None:
        for index, sender in enumerate(self._senders):
            try:
                await sender.send(to_address=to_email, subject=subject, body=body)
            except Exception as exc:
                log.warning("staff_code_mail_failed", vendor_index=index, error=type(exc).__name__)
                continue
            if index:
                log.warning("vendor_switch", field="email", purpose="staff_code", vendor_index=index)
            return
        raise Unavailable("the sign-in code could not be emailed; use the authenticator app")


TURNSTILE_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


class TurnstileVerifier:
    """kx.agents.website.HumanCheck over Cloudflare Turnstile's server-side check (D39)."""

    def __init__(self, http: httpx.AsyncClient, secret: str) -> None:
        self._http = http
        self._secret = secret

    async def verify(self, token: str, remote_ip: str | None) -> bool:
        if not token:
            return False
        data = {"secret": self._secret, "response": token}
        if remote_ip:
            data["remoteip"] = remote_ip
        try:
            r = await self._http.post(TURNSTILE_URL, data=data, timeout=5.0)
            r.raise_for_status()
        except httpx.HTTPError:
            log.warning("turnstile.unreachable")
            return False  # fail closed: the public AI costs money (D34, D39)
        return bool(r.json().get("success"))


class PublicLimiter:
    """kx.agents.website.PublicLimits over automation's Redis token buckets (D39 app rate limits). Values per
    OPEN_QUESTIONS #61 until Arifa sets them: 30 requests per minute per IP, 12 per minute per session."""

    def __init__(self, limiter: Any, buckets: dict[str, Any]) -> None:
        self._limiter = limiter
        self._buckets = buckets

    async def allow(self, scope: Literal["ip", "session"], key: str) -> bool:
        decision = await self._limiter.take(f"public_{scope}", key, self._buckets[scope])
        return bool(decision.allowed)


class OpsNotifier:
    """D41 / D30 alert hook for `FallbackChain` and the voice fallback adapters: a vendor switch is logged and
    emailed to the ops address list, at most once per field every 15 minutes (Redis key with TTL), so an
    outage pages someone without flooding the inbox. The `VendorSwitch` metric is written by the chain itself
    and SigNoz alerts on it too."""

    def __init__(
        self,
        redis: Any,
        company: str,
        mailer: PlainMailer | None,
        to: tuple[str, ...],
        quiet_seconds: int = 900,
    ) -> None:
        self._redis = redis
        self._company = company
        self._mailer = mailer
        self._to = to
        self._quiet = quiet_seconds

    async def __call__(self, field: str, from_vendor: str, to_vendor: str) -> None:
        log.warning("vendor_switch", field=field, from_vendor=from_vendor, to_vendor=to_vendor)
        if self._mailer is None or not self._to:
            return
        try:
            first = await self._redis.set(
                f"kx:{self._company}:ops:switch:{field}", "1", nx=True, ex=self._quiet
            )
        except Exception:
            log.warning("ops_alert.redis_unavailable", field=field)
            first = True
        if not first:
            return
        subject = f"[{self._company}] {field}: switched from {from_vendor} to {to_vendor}"
        body = (
            f"The {field} field of {self._company} switched from {from_vendor} to {to_vendor} at "
            f"{datetime.now(UTC).isoformat(timespec='seconds')}.\n"
            "Check the vendor's status page and SigNoz (VendorSwitch). Further switches of this field are "
            "not emailed for 15 minutes."
        )
        for address in self._to:
            try:
                await self._mailer.send(to_address=address, subject=subject, body=body)
            except Exception:
                log.warning("ops_alert.mail_failed", field=field)


class VoiceSlots:
    """D52: free-version voice calls at once (CPU speech-to-text) are capped. A Redis counter is raised when a
    call starts and lowered when it ends (the key expires after an hour, so a crashed worker cannot hold
    slots forever); the token endpoint refuses voice when the cap is reached and the visitor gets chat."""

    def __init__(self, redis: Any, company: str, cap: int) -> None:
        self._redis = redis
        self._key = f"kx:{company}:voice:free_calls"
        self._cap = cap

    async def available(self) -> bool:
        try:
            current = int(await self._redis.get(self._key) or 0)
        except Exception:
            return True  # the cap protects CPU, not money: never block voice on a Redis hiccup
        return current < self._cap

    async def on_call(self, route: VoiceRoute, started: bool) -> None:
        if not route.free_stt:
            return
        try:
            if started:
                await self._redis.incr(self._key)
                await self._redis.expire(self._key, 3600)
            elif int(await self._redis.decr(self._key)) < 0:
                await self._redis.set(self._key, 0)
        except Exception:
            log.warning("voice_slots.redis_unavailable")
