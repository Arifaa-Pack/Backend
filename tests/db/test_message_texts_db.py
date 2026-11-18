"""18 Nov editable message texts against the database: edit English -> live -> AI translates -> history -> reset."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select

from arifaa_pack import texts_sync
from kx.core import events as ev
from kx.core.errors import RuleViolation
from kx.core.events import Event
from kx.core.settings import LogicalDb
from kx.core.texts import TEXTS
from kx.crm.models import MessageText, OutboxEvent
from kx.crm.services import texts
from kx.crm.services.journal import Actor

pytestmark = pytest.mark.db
MANAGER = Actor.system("test_manager")
KEY = "ai.change_request"


@pytest.fixture(autouse=True)
async def clean(databases: Any) -> Iterator[None]:
    async with databases.transaction(LogicalDb.CRM) as s:
        await s.execute(MessageText.__table__.delete())
    TEXTS.replace({})
    yield
    TEXTS.replace({})


class FakeModels:
    """ModelRouter stand-in: answers each translation from `answers` by target language."""

    def __init__(self, answers: dict[str, str]) -> None:
        self.answers, self.asked = answers, []

    def for_tier(self, _: Any) -> Any:
        return self

    def runnable(self) -> Any:
        return self

    async def ainvoke(self, messages: list[Any]) -> Any:
        system = messages[0].content
        lang = next(code for code, name in texts_sync.LANGUAGE_NAMES.items() if name in system)
        self.asked.append(lang)
        return type("Msg", (), {"text": self.answers.get(lang, "")})()


async def view(databases: Any) -> texts.TextView:
    async with databases.transaction(LogicalDb.CRM) as s:
        return await texts.get_text(s, KEY)


async def test_edit_goes_live_is_translated_and_kept_in_history(databases: Any) -> None:
    new = "Thanks — {name} will look at this and send an updated quotation within {hours} working hours."
    async with databases.transaction(LogicalDb.CRM) as s:
        v = await texts.update_english(s, KEY, new, actor=MANAGER, now=datetime.now(UTC))
        events = list(
            (
                await s.execute(
                    select(OutboxEvent.payload).where(OutboxEvent.event_type == ev.MESSAGE_TEXT_CHANGED)
                )
            ).scalars()
        )
    assert v.en == new and v.en_version == 1
    assert {lt.language: lt.status for lt in v.languages}[
        "ar"
    ] == "outdated"  # the old Arabic until translated
    assert events[-1] == {"key": KEY, "english": new, "en_version": 1}

    await texts_sync.refresh(databases)
    assert TEXTS.get(KEY, "en", name="Sara", hours=4).startswith("Thanks — Sara will look at this")

    models = FakeModels(
        {
            "ar": "شكراً — سيراجع {name} طلبك وسيرسل عرضاً محدّثاً خلال {hours} ساعات عمل.",
            "hi": "धन्यवाद — {name} इसे देखेंगे।",  # dropped {hours}: refused
            "zh": "谢谢 — {name} 会在 {hours} 个工作小时内发送更新报价，价格 AED 0.20。",  # added an amount: refused
        }
    )
    job = SimpleNamespace(
        args={
            "event": Event(
                event_type=ev.MESSAGE_TEXT_CHANGED,
                aggregate_type="message_text",
                aggregate_id=KEY,
                payload={"key": KEY, "english": new, "en_version": 1},
            ).model_dump(mode="json")
        }
    )
    await texts_sync.translate_text(models, databases, job)  # type: ignore[arg-type]
    assert sorted(models.asked) == ["ar", "hi", "si", "ta", "zh"]
    status = {lt.language: lt.status for lt in (await view(databases)).languages}
    assert status["ar"] == "translated" and status["hi"] == "missing"
    assert TEXTS.get(KEY, "ar", name="سارة", hours=4).startswith("شكراً — سيراجع سارة")

    async with databases.transaction(LogicalDb.CRM) as s:
        actions = [e.action for e in await texts.history(s, KEY)]
        reset = await texts.reset(s, KEY, actor=MANAGER)
    assert actions[:2] == ["message_text.translated", "message_text.updated"]
    assert reset.en == reset.builtin_en and reset.en_version == 0


async def test_bad_edits_are_refused_and_an_old_translation_is_not_stored(databases: Any) -> None:
    async with databases.transaction(LogicalDb.CRM) as s:
        with pytest.raises(RuleViolation):
            await texts.update_english(
                s, KEY, "Passed to {name}. Price from USD 5.", actor=MANAGER, now=datetime.now(UTC)
            )
    async with databases.transaction(LogicalDb.CRM) as s:
        await texts.update_english(s, KEY, "v1 {name} {hours}", actor=MANAGER, now=datetime.now(UTC))
        await texts.update_english(
            s, KEY, "v2 {name} within {hours} hours", actor=MANAGER, now=datetime.now(UTC)
        )
        # a translation of v1 arrives after v2 was saved: not stored
        assert not await texts.save_translation(
            s, KEY, "ar", "ت1 {name} {hours}", en_version=1, now=datetime.now(UTC)
        )
        assert await texts.save_translation(
            s, KEY, "ar", "ت2 {name} {hours}", en_version=2, now=datetime.now(UTC)
        )


async def test_staff_texts_are_english_only_and_missing_translations_can_be_queued(databases: Any) -> None:
    async with databases.transaction(LogicalDb.CRM) as s:
        v = await texts.update_english(
            s, "staff.deal_assigned", "New deal {deal} is yours", actor=MANAGER, now=datetime.now(UTC)
        )
        assert [lt.language for lt in v.languages] == ["en"]
        queued = await texts.request_missing_translations(s, actor=MANAGER)
    assert queued > 10  # every buyer / AI text without Hindi, Tamil, Chinese or Sinhala yet
