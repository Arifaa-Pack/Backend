"""Live test 20 Nov: the model named certificates and a capacity no tool had given it. A claim stands only when
a tool result of the turn contains it; website replies are plain text."""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from kx.agents.guard.claims import facts_of, unsupported, without_claims
from kx.agents.guard.service import clip_for_channel, plain_text

LIVE_REPLY = (
    "Our factory meets international food-safety and quality standards. We hold ISO 9001 (quality management), "
    "ISO 14001, BRC Global Standard for Food Safety, HACCP certification, and CE marking for our equipment. "
    "We run high-speed forming lines that can handle up to 2,000,000 containers a month. "
    "Would you like our catalogue?"
)


def test_the_live_answer_loses_every_invented_claim() -> None:
    claims = unsupported(LIVE_REPLY, facts="")
    assert {"ISO 9001", "ISO 14001", "BRC", "HACCP", "CE marking"} <= set(claims)
    assert any("2,000,000" in c for c in claims)
    text = without_claims(LIVE_REPLY, "", "en")
    assert "ISO" not in text and "HACCP" not in text and "2,000,000" not in text
    assert text.startswith("Our factory meets") and "Would you like our catalogue?" in text
    assert text.endswith("Our team will confirm the certificates and production details for you.")


def test_a_claim_the_tools_gave_stands() -> None:
    facts = facts_of(
        [
            HumanMessage("do you have ISO?"),
            AIMessage(content="", tool_calls=[{"name": "search_knowledge", "args": {}, "id": "1"}]),
            ToolMessage("[Certificates] Arifa Pack holds ISO 22000 and HACCP.", tool_call_id="1"),
        ]
    )
    assert unsupported("Yes, we hold ISO 22000 and HACCP certification.", facts) == []
    assert unsupported("We also hold BRC.", facts) == ["BRC"]


def test_capacity_needs_its_number_in_the_facts() -> None:
    assert unsupported("Our monthly production capacity is 5,000,000 pieces.", "") != []
    assert (
        unsupported("Our monthly production capacity is 5,000,000 pieces.", "capacity: 5,000,000 a month")
        == []
    )
    assert unsupported("The 8325 holds 1000 ml and comes 1 x 1000 pcs.", "") == []  # not a capacity claim


def test_arabic_reply_gets_the_arabic_confirmation() -> None:
    assert without_claims("لدينا شهادة الأيزو.", "", "ar") == "سيؤكد فريقنا لك الشهادات وتفاصيل الإنتاج."


def test_website_replies_are_plain_text() -> None:
    md = "Here is our foil:\n- **Aluminium Foil 30 cm** for everyday use\n1. **45 cm** for trays\n## Next"
    assert plain_text(md) == "Here is our foil:\nAluminium Foil 30 cm for everyday use\n45 cm for trays\nNext"
    assert "**" not in clip_for_channel(md, "web_chat")
    assert "**" in clip_for_channel(md, "whatsapp")  # WhatsApp renders *bold*; left as the model wrote it


def test_the_reply_must_be_in_the_visitors_script() -> None:
    from kx.agents.guard.language import wrong_language

    assert wrong_language("The 8325 is our classic container.", "ar")
    assert not wrong_language("الحاوية 8325 هي الأكثر طلبا.", "ar")
    assert not wrong_language("The 8325 is our classic container.", "en")  # Latin languages are not checked


def test_the_buyers_own_words_may_be_repeated() -> None:
    """Live test 20 Nov: the guard cut "...for 20,000 pieces a month..." although the buyer had said it."""
    facts = facts_of([HumanMessage("We need about 20,000 pieces a month for our restaurants in Dubai.")])
    assert unsupported("Noted: 20,000 pieces a month for Dubai. May I have your name?", facts) == []
    assert unsupported("Noted: 20,000 pieces a month for Dubai.", "") == []  # demand, not our capacity
