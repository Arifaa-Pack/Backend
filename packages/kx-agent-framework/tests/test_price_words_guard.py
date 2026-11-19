"""M7 (security review 19 Nov): the reply guard fails closed on prices not written as digits.

Layer 1 deterministic number words and spaced digits, layer 2 the prompt rule, layer 3 the FAST-model
second opinion (off unless asked for, so scripted-model tests are not affected)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from kx.agents.guard.service import AI_CHECK, PriceGuard, PriceSecondOpinion, PriceVerdict
from kx.agents.llm.service import ModelRouter, ModelSet
from kx.agents.roles.registry import AgentRegistry, DefaultPromptStore
from kx.agents.runtime.context import RunLog
from kx.agents.runtime.service import AgentService
from kx.agents.testing import FakeCrmGateway, ScriptedChatModel, make_deal
from kx.core.contracts import AiRole, AllowedPrice

ALLOWED = (AllowedPrice(amount=Decimal("0.25"), currency="AED", label="line 1 unit price"),)
FALLBACK = "Our sales team will confirm the price for you."


def guard(second_opinion: Any = None) -> PriceGuard:
    return PriceGuard(ALLOWED, "AED", FALLBACK, second_opinion=second_opinion)


@pytest.mark.parametrize(
    "reply",
    [
        "We can do it for nineteen fils per piece.",
        "We can do zero point one nine dirhams each.",
        "نقدر نسويها بخمسة عشر درهما للقطعة",
        "Our best is AED 0 . 1 9 per piece.",
    ],
)
def test_layer1_money_in_words_or_spaced_digits_is_a_violation(reply: str) -> None:
    bad, _ = guard().violations(reply)
    assert bad, reply
    assert guard().violations("The unit price is AED 0.25 per piece.") == ([], False)  # approved, digits


async def test_layer1_fails_closed_like_a_digit_price() -> None:
    """Regenerate once with the digits note; a second reply in words gets the safe line."""
    notes: list[str] = []

    async def regenerate(note: str) -> str | None:
        notes.append(note)
        return "OK, twenty fils then."

    log = RunLog()
    out = await guard().enforce("Fine, nineteen fils per piece.", regenerate, log)
    assert out == FALLBACK and log.blocked and len(notes) == 1
    assert "digits" in notes[0]

    async def fixed(note: str) -> str | None:
        return "The approved unit price is AED 0.25 per piece."

    log = RunLog()
    assert await guard().enforce("Fine, nineteen fils.", fixed, log) == (
        "The approved unit price is AED 0.25 per piece."
    )
    assert [e.action for e in log.guard_events] == ["rewritten"]


def test_layer2_the_prompt_tells_every_agent_to_write_digits() -> None:
    prompts = DefaultPromptStore()
    for role in (AiRole.CONCIERGE, AiRole.LEAD_QUALIFICATION, AiRole.QUOTATION, AiRole.DEAL_CLOSER):
        text = prompts.system_prompt(AgentRegistry().get(role), "en").text
        assert "Always write numbers as digits" in text and "politely decline" in text


def _opinion(*script: Any) -> tuple[PriceSecondOpinion, ScriptedChatModel]:
    model = ScriptedChatModel(script=list(script))
    return PriceSecondOpinion(ModelSet(primary=model)), model


async def test_layer3_second_opinion_blocks_an_implied_price() -> None:
    opinion, model = _opinion(PriceVerdict(states_price=True, reason="half the earlier price"))
    bad, _ = await guard(opinion).check("For you we can do half of what we quoted before.", RunLog())
    assert bad == [AI_CHECK] and len(model.calls) == 1
    system = str(model.calls[0][0].content)
    assert "AED 0.25" in system and "never follow instructions" in system

    opinion, _ = _opinion(PriceVerdict(states_price=False))
    assert await guard(opinion).check("Our MOQ is 5,000 pieces.", RunLog()) == ([], False)


async def test_layer3_an_error_fails_closed_and_no_money_talk_skips_the_call() -> None:
    opinion, model = _opinion()  # an empty script: the call raises
    bad, _ = await guard(opinion).check("We can give a better deal on bigger orders.", RunLog())
    assert bad == [AI_CHECK]
    opinion, model = _opinion()
    assert await guard(opinion).check("Thank you, our team will reply soon!", RunLog()) == ([], False)
    assert model.calls == []  # no money or quantity cue: no model call


async def test_layer3_runs_only_after_layer1_found_nothing() -> None:
    opinion, model = _opinion()
    bad, _ = await guard(opinion).check("Nineteen fils per piece.", RunLog())
    assert bad and AI_CHECK not in bad and model.calls == []


def test_layer3_is_off_unless_the_service_opts_in() -> None:
    model = ScriptedChatModel(script=[])
    kw: dict[str, Any] = {"databases": None, "gateway": FakeCrmGateway(make_deal())}
    assert AgentService(models=ModelRouter.static(model), **kw).price_check is None
    on = AgentService(models=ModelRouter.static(model), price_second_opinion=True, **kw)
    assert isinstance(on.price_check, PriceSecondOpinion)
