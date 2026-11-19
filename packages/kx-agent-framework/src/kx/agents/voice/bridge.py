"""WP-C5 (D55, D57, D58): voice does everything chat does, over the LiveKit room's data channel.

A website call is the same visit as the chat (one thread, `web:{session_id}`), so a spoken turn runs the same
website turn as a typed one (`kx.agents.website.service.WebsiteTurns.turn`, handed in by the app as
`VisitTurn`): the instant resolver moves the page on the final speech-to-text words before the agent starts,
the agent's site tools move it again while it answers, every action is checked against the site manifest,
and the reply passes the price guard (and once more right before text-to-speech, `speakable`).

What travels on the room's data channel (JSON, reliable):

| Topic | Direction | Payload |
|---|---|---|
| `kx.site.actions` | agent -> browser | one site action, the same shape as `MessageOut.actions[]` |
| `kx.captions` | agent -> browser | `{role: "visitor" or "assistant", text, final}` (§9 accessibility) |
| `kx.chat.in` | browser -> agent | `{text, context?}`: typed text during the call, answered in the call |
| `kx.site.context` | browser -> agent | a `SiteContext`: the visitor moved (saved for the next turn) |

Typed text while the call is on goes through the call (`kx.chat.in`), not through the HTTP chat, so the two
never race: one ordered conversation. The recording notice plays once per visit (`notice_due`), so a page
reload that rejoins the room does not repeat it, and the agent never greets twice.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Final, Protocol

from pydantic import ValidationError

from kx.agents.site.models import SiteContext
from kx.core.logging import get_logger

log = get_logger(__name__)

TOPIC_ACTIONS: Final = "kx.site.actions"
TOPIC_CAPTIONS: Final = "kx.captions"
TOPIC_CHAT_IN: Final = "kx.chat.in"
TOPIC_CONTEXT: Final = "kx.site.context"
MAX_TYPED: Final = 2000

Emit = Callable[[str, dict[str, Any]], Awaitable[None]]
Publish = Callable[[str, dict[str, Any]], Awaitable[None]]  # (topic, payload)


class TurnOutcome(Protocol):
    @property
    def reply(self) -> str | None: ...

    @property
    def language(self) -> str: ...


# `WebsiteTurns.turn(session_id, deal_id, text, *, language, context, channel, emit)`: one turn of the visit,
# with its site actions. A plain callable type, not a Protocol: a Protocol with only `__call__` is matched by
# every class (`kx map` lists ports by structure).
VisitTurn = Callable[..., Awaitable[TurnOutcome]]


class VisitHooks(Protocol):
    """What the visit keeps between calls (`WebsiteTurns`)."""

    async def remember(
        self, session_id: uuid.UUID, context: SiteContext | None, *, engaged: bool = False
    ) -> SiteContext | None: ...

    async def recording_notice_due(self, session_id: uuid.UUID) -> bool: ...


@dataclass
class VoiceBridge:
    """One call's link between the speech session, the visit's turns and the browser."""

    session_id: uuid.UUID
    deal_id: uuid.UUID
    language: str
    visit_turn: VisitTurn
    hooks: VisitHooks
    publish: Publish
    pending: list[str] = field(default_factory=list)  # typed text waiting for the session to answer

    async def answer(self, text: str) -> str | None:
        """A spoken (or typed-in-call) turn: the visitor's caption, the page actions as they come, the
        reply's captions; returns the reply to speak (None: a salesperson has the conversation)."""
        await self._caption("visitor", text, final=True)
        out = await self.visit_turn(
            self.session_id,
            self.deal_id,
            text,
            language=self.language,
            context=None,  # the visitor's last place (sent with `kx.site.context` or the chat)
            channel="web_voice",
            emit=self._emit,
        )
        return out.reply

    async def _emit(self, name: str, data: dict[str, Any]) -> None:
        if name == "action":
            await self.publish(TOPIC_ACTIONS, data)
        elif name == "delta":
            await self._caption("assistant", str(data.get("text", "")), final=False)
        elif name == "done":
            await self.publish(TOPIC_CAPTIONS, {"role": "assistant", "text": "", "final": True} | data)

    async def _caption(self, role: str, text: str, *, final: bool) -> None:
        await self.publish(TOPIC_CAPTIONS, {"role": role, "text": text, "final": final})

    async def on_data(self, topic: str | None, raw: bytes) -> str | None:
        """A message from the browser. Returns typed text the session should answer, else None."""
        try:
            data = json.loads(raw.decode("utf-8"))
        except UnicodeDecodeError, json.JSONDecodeError:
            log.info("voice.data_ignored", topic=topic, why="not json")
            return None
        if not isinstance(data, dict):
            return None
        try:
            if topic == TOPIC_CONTEXT:
                await self.hooks.remember(self.session_id, SiteContext.model_validate(data))
                return None
            if topic == TOPIC_CHAT_IN:
                if isinstance(data.get("context"), dict):
                    await self.hooks.remember(self.session_id, SiteContext.model_validate(data["context"]))
                text = str(data.get("text") or "").strip()[:MAX_TYPED]
                return text or None
        except ValidationError:
            log.info("voice.data_ignored", topic=topic, why="bad context")
        return None

    async def notice_due(self) -> bool:
        """The recording notice plays once per visit (§6.1), not again after a reload or rejoin."""
        return await self.hooks.recording_notice_due(self.session_id)
