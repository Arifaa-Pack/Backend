"""18 Nov editable message texts: the lookup rules, the edit checks, and that the AI, the quote message and the
staff alerts really use an edit (and fall back safely when an edit is broken)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from kx.agents.roles.registry import DefaultPromptStore, Phrase
from kx.core.texts import CATALOG, TEXTS, Texts, check_edit


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    TEXTS.replace({})
    yield
    TEXTS.replace({})


def test_lookup_order_language_then_builtin_then_english() -> None:
    t = Texts()
    assert t.raw("ai.handover_ack", "ar") == CATALOG["ai.handover_ack"].builtin["ar"]
    assert t.raw("ai.handoff", "hi") == CATALOG["ai.handoff"].en  # no Hindi yet -> English
    t.replace({("ai.handoff", "en"): "One of our team will answer you soon."})
    assert t.raw("ai.handoff", "hi") == "One of our team will answer you soon."  # the edit, not the built-in
    assert (
        t.raw("ai.handover_ack", "ar") == CATALOG["ai.handover_ack"].builtin["ar"]
    )  # Arabic kept until translated
    t.replace({("ai.handover_ack", "ar"): "شكراً — {name} سيرد صباحاً."})
    assert t.get("ai.handover_ack", "ar-AE", name="سارة") == "شكراً — سارة سيرد صباحاً."
    assert t.has("ai.handover_ack", "ar") and not t.has("ai.handoff", "ta")


def test_a_broken_edit_never_breaks_a_message() -> None:
    t = Texts()
    t.replace({("buyer.quote_subject", "en"): "Quotation {number from {seller}"})  # slipped past the checks
    assert t.get("buyer.quote_subject", "en", number="Q-1", seller="Arifa") == "Quotation Q-1 from Arifa"
    assert (
        t.get("staff.deal_won", "en", deal="D-7") == "Deal D-7 is WON by {name}."
    )  # a missing value stays visible


@pytest.mark.parametrize(
    ("key", "text", "problem"),
    [
        ("ai.change_request", "I've passed this to {name}.", "keep the placeholders {hours}"),
        ("ai.handoff", "Hi {buyer}, we'll reply.", "unknown placeholders {buyer}"),
        ("ai.price_fallback", "Pricing from AED 0.20, our team will confirm.", "no amounts with a currency"),
        ("ai.handoff", "Reply {soon", "not closed"),
        ("ai.handoff", "   ", "empty"),
        ("nope", "x", "unknown text"),
    ],
)
def test_edits_are_checked(key: str, text: str, problem: str) -> None:
    assert any(problem in p for p in check_edit(key, text))


def test_numbers_without_currency_and_every_builtin_pass() -> None:
    assert check_edit("ai.manager_60", "We'll reply within 45 minutes during working hours.") == []
    assert all(check_edit(k, d.en) == [] for k, d in CATALOG.items())
    assert all(check_edit(k, t) == [] for k, d in CATALOG.items() for t in d.builtin.values())


def test_the_ai_uses_the_edit_but_never_for_legal_wording() -> None:
    store = DefaultPromptStore()
    TEXTS.replace(
        {
            ("ai.safe_reply", "en"): "Let me check that with a colleague.",
            ("ai.recording_notice", "en"): "not editable",  # not in the catalogue: ignored
        }
    )
    assert store.phrase(Phrase.SAFE_REPLY, "en") == "Let me check that with a colleague."
    assert store.phrase(Phrase.RECORDING_NOTICE, "en").startswith("Hello, I'm the AI assistant")
    assert store.phrase(Phrase.OPTOUT_CHECK, "en") == "Just to check, should we stop messaging you?"
    assert "ai.optout_check" not in CATALOG and "ai.voice_stop_check" not in CATALOG


def test_staff_alert_and_summary_texts_come_from_the_catalogue() -> None:
    import uuid
    from datetime import UTC, datetime

    from kx.automation._shared.ladder import LadderTicket
    from kx.core.event_payloads import StaffContact

    TEXTS.replace({("staff.change_request_title", "en"): "Change request ({type}) on {deal}"})
    sara = StaffContact(user_ref=uuid.uuid4(), name="Sara", phone=None, email=None)
    now = datetime.now(UTC)
    p = type(
        "P",
        (),
        {
            "alert_ref": None,
            "change_request_ref": uuid.uuid4(),
            "deal_ref": uuid.uuid4(),
            "assigned_to": sara,
            "escalate_to": None,
            "remind_at": (now,),
            "escalate_at": now,
            "sla_due_at": now,
            "type": "delivery",
            "deal_number": "D-9",
        },
    )()
    assert LadderTicket.for_change(p).title == "Change request (delivery) on D-9"  # type: ignore[arg-type]
