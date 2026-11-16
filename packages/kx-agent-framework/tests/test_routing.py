from __future__ import annotations

import pytest

from kx.agents.evals.harness import load_scenarios, run_scenario
from kx.agents.language import detect_script_language, reply_language
from kx.agents.models import AnthropicApiProvider, ModelRouter
from kx.agents.orchestrator import route
from kx.agents.registry import AgentRegistry, ModelTier
from kx.agents.testing import make_deal
from kx.agents.voice import VoiceRouter
from kx.core.contracts import AiRole, DealStage

ISSUED = [("0.25", "line 1 unit price")]


@pytest.mark.parametrize(
    ("stage", "kw", "text", "first", "graph", "role"),
    [
        (DealStage.NEW, {"ai_enabled": False}, "hi", False, None, AiRole.ORCHESTRATOR),
        (DealStage.NEW, {}, "Hello", True, "conversation", AiRole.CONCIERGE),
        (DealStage.NEW, {"missing": ["quantity"]}, "I need jars", False, "conversation", AiRole.PRODUCT),
        (
            DealStage.NEW,
            {"missing": ["quantity"]},
            "what is your price?",
            False,
            "conversation",
            AiRole.LEAD_QUALIFICATION,
        ),
        (DealStage.NEW, {"missing": ["contact_name"]}, "ok", False, "conversation", AiRole.CONTACT_CAPTURE),
        (DealStage.NEW, {}, "what is your factory capacity?", False, "conversation", AiRole.MANUFACTURING),
        (DealStage.QUALIFIED, {}, "how much for these?", False, "quotation", AiRole.QUOTATION),
        (DealStage.QUALIFIED, {}, "do you ship to Oman?", False, "conversation", AiRole.PRODUCT),
        (DealStage.QUOTATION, {}, "price?", False, "conversation", AiRole.QUOTATION),  # quote not issued yet
        (DealStage.QUOTATION, {"allowed": ISSUED}, "Can you do AED 0.20?", False, "deal", AiRole.DEAL_CLOSER),
        (DealStage.NEGOTIATION, {"allowed": ISSUED}, "كم السعر الأفضل؟", False, "deal", AiRole.DEAL_CLOSER),
        (
            DealStage.NEGOTIATION,
            {"allowed": ISSUED},
            "when can you deliver?",
            False,
            "conversation",
            AiRole.COMMUNICATION,
        ),
        (DealStage.WON, {}, "reorder please", False, "conversation", AiRole.REPEAT_SALES),
    ],
)
def test_routing_table(
    stage: DealStage, kw: dict[str, object], text: str, first: bool, graph: str | None, role: AiRole
) -> None:
    rt = route(text, make_deal(stage, **kw), first_turn=first)  # type: ignore[arg-type]
    assert (rt.graph.value if rt.graph else None, rt.role) == (graph, role)
    assert (rt.action == "silent") == (graph is None)


def test_registry_has_all_twelve_roles_and_only_price_roles_quote() -> None:
    reg = AgentRegistry()
    assert len(reg) == 12
    assert (
        not reg.get(AiRole.LEAD_QUALIFICATION).may_quote_prices
        and reg.get(AiRole.DEAL_CLOSER).may_quote_prices
    )


@pytest.mark.parametrize("lang", ["en", "ar-AE", "zh", "hi", "fr"])
def test_paid_voice_is_deepgram_and_elevenlabs_with_the_free_engines_as_fallback(lang: str) -> None:
    """D52 (server plan v2 §2): Deepgram Nova-3 -> faster-whisper; ElevenLabs Flash -> Piper."""
    r = VoiceRouter().route(lang)
    assert (r.stt, r.tts, r.voice_available) == (("deepgram", "whisper"), ("elevenlabs", "piper"), True)


def test_free_voice_uses_our_speech_server_and_tamil_needs_elevenlabs() -> None:
    from kx.agents.voice import voice_routes
    from kx.core.platform import resolve_modes

    free = resolve_modes("free", {})
    routes = voice_routes(free, {"whisper", "piper", "elevenlabs"})
    assert (routes["ar"].stt, routes["ar"].tts) == (("whisper",), ("piper",))
    assert routes["ar"].free_stt
    assert routes["ta"].tts == ("elevenlabs",)
    assert not voice_routes(free, {"whisper", "piper"})["ta"].voice_available  # no key: Tamil goes to chat
    assert not routes["si"].voice_available


def test_the_voice_session_gets_an_llm_so_llm_node_runs() -> None:
    """Bench R2b: without an LLM on the session livekit-agents skipped every reply (only the recording
    notice was heard). The placeholder must be a real LiveKit LLM that is never called."""
    from livekit.agents import llm as lk_llm

    from kx.agents.voice import graph_llm

    model = graph_llm()
    assert isinstance(model, lk_llm.LLM)
    with pytest.raises(RuntimeError):
        model.chat(chat_ctx=None)


def test_sinhala_is_text_only() -> None:
    """Platform contest round 1: "No option covers Sinhala in live voice; Sinhala stays text-only"."""
    assert not VoiceRouter().route("si").voice_available


def test_language_detection() -> None:
    assert detect_script_language("أريد زجاجات PET") == "ar"
    assert detect_script_language("我要500个瓶子") == "zh"
    assert detect_script_language("எனக்கு பாட்டில்கள் வேண்டும்") == "ta"
    assert reply_language("hello", "si") == "si"


def test_claude_api_direct_haiku_on_the_rest_sonnet_on_money_turns() -> None:
    """D27: both through the Claude API (Bedrock dropped 11 Nov); no backup until the suite passes."""
    from pydantic import SecretStr

    router = ModelRouter([AnthropicApiProvider(SecretStr("test-key"))])
    assert router.for_tier(ModelTier.FAST).name == "claude-haiku-5-5"
    assert router.for_tier(ModelTier.SMART).name == "claude-sonnet-5-5"
    assert router.for_tier(ModelTier.SMART).fallbacks == ()


@pytest.mark.parametrize("scenario", load_scenarios(), ids=lambda s: s.id)
async def test_eval_scenarios_scripted(scenario: object) -> None:
    result = await run_scenario(scenario)  # type: ignore[arg-type]
    assert result.passed, result.failures


def test_handover_routes_to_the_salesperson_and_acknowledges_only_after_hours() -> None:
    from kx.agents.orchestrator import route as route_

    deal = make_deal(DealStage.READY_TO_CLOSE).model_copy(
        update={"ai_enabled": False, "ai_paused_reason": "handover"}
    )
    assert route_("Can you do 0.20?", deal).action == "silent"
    after = deal.model_copy(update={"team_available": False})
    assert (route_("hello?", after).action, route_("hello?", after).reason) == (
        "acknowledge",
        "handover_after_hours",
    )
    other = deal.model_copy(update={"ai_paused_reason": "below_range", "team_available": False})
    assert route_("hello?", other).action == "silent"  # only the handover gets the morning acknowledgement


def test_repeat_sales_is_v2_and_off() -> None:
    """§18 / D14: won customers are answered by the salesperson in v1."""
    from kx.agents.orchestrator import route as route_
    from kx.agents.registry import AgentRegistry

    registry = AgentRegistry()
    assert registry.disabled() == frozenset({AiRole.REPEAT_SALES})
    won = make_deal(DealStage.WON)
    assert route_("I want to reorder", won, disabled=registry.disabled()).action == "silent"
    assert route_("I want to reorder", won).role is AiRole.REPEAT_SALES  # v2: switched back on


@pytest.mark.parametrize("tier", [ModelTier.FAST, ModelTier.SMART, ModelTier.JUDGE])
def test_claude_request_has_no_sampling_parameters_and_sets_effort(tier: ModelTier) -> None:
    """D51 / bench R1: Sonnet 5.5 and Haiku 5.5 return HTTP 400 for a non-default temperature, top_p or top_k,
    so every Sonnet (money-turn) call used to fail. The real request payload must carry none of them, must
    keep thinking adaptive and must set the tier's effort."""
    from langchain_core.messages import HumanMessage
    from pydantic import SecretStr

    from kx.agents.models import DEFAULT_OPTIONS

    model = ModelRouter([AnthropicApiProvider(SecretStr("test-key"))]).for_tier(tier).primary
    payload = model._get_request_payload([HumanMessage("hello")])  # type: ignore[attr-defined]
    assert not {"temperature", "top_p", "top_k"} & set(payload)
    assert payload["thinking"] == {"type": "adaptive"}
    assert payload["output_config"]["effort"] == DEFAULT_OPTIONS[tier].effort
    assert payload["max_tokens"] == DEFAULT_OPTIONS[tier].max_tokens


def test_effort_settings_override_one_tier_only() -> None:
    from kx.agents.models import DEFAULT_OPTIONS, tier_options

    opts = tier_options(smart="high")
    assert opts[ModelTier.SMART].effort == "high"
    assert opts[ModelTier.SMART].max_tokens == DEFAULT_OPTIONS[ModelTier.SMART].max_tokens
    assert opts[ModelTier.FAST] == DEFAULT_OPTIONS[ModelTier.FAST]


def test_a_claude_refusal_is_no_answer_so_the_buyer_gets_the_hand_over() -> None:
    from langchain_core.messages import AIMessage

    from kx.agents.graphs.conversation import final_reply

    declined = AIMessage("I can't help with", response_metadata={"stop_reason": "refusal"})
    assert final_reply([declined]) is None
    assert final_reply([AIMessage("Hello!", response_metadata={"stop_reason": "end_turn"})]) == "Hello!"
