"""WP-C5: a call is the visit's own turn. Page actions and captions go to the browser on the data channel,
typed text and page moves come back on it, and the recording notice is given once per visit."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

from kx.agents.site.models import SiteContext
from kx.agents.voice.bridge import (
    TOPIC_ACTIONS,
    TOPIC_CAPTIONS,
    TOPIC_CHAT_IN,
    TOPIC_CONTEXT,
    Emit,
    VoiceBridge,
)


@dataclass
class Out:
    reply: str | None
    language: str = "en"


@dataclass
class Visit:
    seen: list[dict[str, Any]] = field(default_factory=list)
    places: list[SiteContext | None] = field(default_factory=list)
    notice_given: bool = False

    async def __call__(
        self,
        session_id: uuid.UUID,
        deal_id: uuid.UUID,
        text: str,
        *,
        language: str | None,
        context: SiteContext | None,
        channel: Literal["web_chat", "web_voice"] = "web_chat",
        emit: Emit | None = None,
    ) -> Out:
        self.seen.append({"text": text, "channel": channel, "context": context})
        assert emit is not None
        await emit("action", {"type": "show_family", "family": "foil", "id": "f1"})
        await emit("delta", {"text": "Here is our foil."})
        await emit("done", {"language": "en", "handed_over": False})
        return Out("Here is our foil.")

    async def remember(
        self, session_id: uuid.UUID, context: SiteContext | None, *, engaged: bool = False
    ) -> SiteContext | None:
        self.places.append(context)
        return context

    async def recording_notice_due(self, session_id: uuid.UUID) -> bool:
        due, self.notice_given = not self.notice_given, True
        return due


def bridge() -> tuple[VoiceBridge, Visit, list[tuple[str, dict[str, Any]]]]:
    visit, sent = Visit(), []

    async def publish(topic: str, payload: dict[str, Any]) -> None:
        sent.append((topic, payload))

    return VoiceBridge(uuid.uuid4(), uuid.uuid4(), "en", visit, visit, publish), visit, sent


async def test_a_spoken_turn_moves_the_page_and_captions_both_sides() -> None:
    b, visit, sent = bridge()
    assert await b.answer("show me kitchen foil") == "Here is our foil."
    assert visit.seen[0]["channel"] == "web_voice"
    assert [t for t, _ in sent] == [TOPIC_CAPTIONS, TOPIC_ACTIONS, TOPIC_CAPTIONS, TOPIC_CAPTIONS]
    assert sent[0][1] == {"role": "visitor", "text": "show me kitchen foil", "final": True}
    assert sent[1][1]["family"] == "foil"
    assert sent[2][1] == {"role": "assistant", "text": "Here is our foil.", "final": False}
    assert sent[3][1]["final"] is True and sent[3][1]["handed_over"] is False


async def test_typed_text_and_page_moves_arrive_on_the_data_channel() -> None:
    b, visit, _ = bridge()
    move = json.dumps({"page": "products", "path": "/products?family=foil", "family": "foil"}).encode()
    assert await b.on_data(TOPIC_CONTEXT, move) is None
    assert visit.places[-1] is not None and visit.places[-1].family == "foil"
    typed = json.dumps({"text": " do you have 8325? ", "context": {"page": "contact", "path": "/contact"}})
    assert await b.on_data(TOPIC_CHAT_IN, typed.encode()) == "do you have 8325?"
    assert visit.places[-1] is not None and visit.places[-1].page == "contact"


async def test_rubbish_on_the_data_channel_is_ignored() -> None:
    b, visit, _ = bridge()
    assert await b.on_data(TOPIC_CHAT_IN, b"not json") is None
    assert await b.on_data(TOPIC_CONTEXT, json.dumps({"page": "nowhere"}).encode()) is None
    assert await b.on_data("other.topic", json.dumps({"text": "hi"}).encode()) is None
    assert await b.on_data(TOPIC_CHAT_IN, json.dumps({"text": "   "}).encode()) is None
    assert visit.places == []


async def test_the_recording_notice_is_given_once_per_visit() -> None:
    b, _, _ = bridge()
    assert await b.notice_due() is True
    assert await b.notice_due() is False  # a rejoin after a page load: no second notice, no second greeting
