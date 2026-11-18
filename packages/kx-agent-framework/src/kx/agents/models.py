"""Model factory: tier -> chat model, with provider fallback.

D27 (11 Nov): Claude Sonnet 5.5 on money turns (SMART) and Haiku 5.5 on the rest (FAST), called directly
through the Claude API (langchain-anthropic `ChatAnthropic`); Amazon Bedrock was dropped. Backup providers
(16 Nov: Gemini 3.8 Flash standby, `KX_PLATFORM__LLM_STANDBY`) are appended to the router's list only once
they pass the eval suite and the client agrees.

D51 (16 Nov): Claude Sonnet 5.5 and Haiku 5.5 reject non-default `temperature` / `top_p` / `top_k` with
HTTP 400 (the bench found every Sonnet call failing on `temperature=0.2`), so no sampling parameter is ever
sent. Depth and cost are steered with `output_config.effort` per tier; thinking stays adaptive (it cannot be
disabled on Sonnet 5.5) and `max_tokens` leaves room for it so replies are not cut short.

Live agents (`create_agent`) get fallbacks through `ModelFallbackMiddleware` (a `.with_fallbacks()`
runnable is not a chat model and cannot bind tools); plain node calls use `.with_fallbacks()`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final, Literal, Protocol, runtime_checkable

from langchain.agents.middleware import AgentMiddleware, ModelFallbackMiddleware
from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import BaseChatModel, LanguageModelInput
from langchain_core.messages import BaseMessage
from langchain_core.runnables import Runnable
from pydantic import BaseModel, SecretStr

from kx.agents.registry import ModelTier

ANTHROPIC_MODEL_IDS: Final[dict[ModelTier, str]] = {
    ModelTier.FAST: "claude-haiku-5-5",
    ModelTier.SMART: "claude-sonnet-5-5",
    ModelTier.JUDGE: "claude-opus-5-5",
}

Effort = Literal["low", "medium", "high", "xhigh", "max"]


@dataclass(frozen=True, slots=True)
class ModelOptions:
    """Per-tier request options. No sampling parameters (D51): consistency comes from the prompts, the
    fixed phrases and the price guard, not from temperature."""

    effort: Effort = "medium"
    max_tokens: int = 4096  # room for adaptive thinking + the reply; replies themselves are short
    timeout_s: float = 30.0
    max_retries: int = 2


# OPEN_QUESTIONS #76: FAST answers routine turns quickly; SMART (money turns) thinks more; the judge most.
DEFAULT_OPTIONS: Final[dict[ModelTier, ModelOptions]] = {
    ModelTier.FAST: ModelOptions(effort="low", max_tokens=2048),
    ModelTier.SMART: ModelOptions(effort="medium", max_tokens=4096),
    ModelTier.JUDGE: ModelOptions(effort="high", max_tokens=8192, timeout_s=120.0),
}


def tier_options(
    fast: Effort | None = None, smart: Effort | None = None, judge: Effort | None = None
) -> dict[ModelTier, ModelOptions]:
    """DEFAULT_OPTIONS with the effort of some tiers replaced (settings `KX_APP_LLM_EFFORT_*`)."""
    out = dict(DEFAULT_OPTIONS)
    for tier, effort in ((ModelTier.FAST, fast), (ModelTier.SMART, smart), (ModelTier.JUDGE, judge)):
        if effort is not None:
            base = out[tier]
            out[tier] = ModelOptions(effort, base.max_tokens, base.timeout_s, base.max_retries)
    return out


@runtime_checkable
class ModelProvider(Protocol):
    name: str

    def chat_model(self, tier: ModelTier, options: ModelOptions) -> BaseChatModel: ...


class AnthropicApiProvider:
    name = "anthropic"

    def __init__(
        self, api_key: SecretStr | None = None, model_ids: Mapping[ModelTier, str] = ANTHROPIC_MODEL_IDS
    ) -> None:
        self._key = api_key
        self._ids = dict(model_ids)

    def chat_model(self, tier: ModelTier, options: ModelOptions) -> BaseChatModel:
        kwargs: dict[str, Any] = {"api_key": self._key} if self._key is not None else {}
        return ChatAnthropic(
            model_name=self._ids[tier],
            max_tokens_to_sample=options.max_tokens,
            timeout=options.timeout_s,
            max_retries=options.max_retries,
            stop=None,
            # effort via output_config, not `reasoning_effort`: that field would also switch the thinking
            # display to "summarized" (more output tokens for nothing a buyer sees).
            thinking={"type": "adaptive"},
            output_config={"effort": options.effort},
            **kwargs,
        )


GEMINI_MODEL_IDS: Final[dict[ModelTier, str]] = {
    ModelTier.FAST: "gemini-3.8-flash",
    ModelTier.SMART: "gemini-3.8-flash",
    ModelTier.JUDGE: "gemini-3.8-flash",
}
_GEMINI_THINKING: Final[dict[str, str]] = {
    "low": "low",
    "medium": "medium",
    "high": "high",
    "xhigh": "high",
    "max": "high",
}


class GeminiApiProvider:
    """18 Nov (client decision): Gemini 3.8 Flash as the standby when every Claude call fails
    (`KX_PLATFORM__LLM_STANDBY=gemini`, a PAID key: the free tier allows 20 requests a day). It only ever
    answers inside the same guards (price guard, fixed phrases, hand-over rules) as Claude."""

    name = "gemini"

    def __init__(self, api_key: SecretStr, model_ids: Mapping[ModelTier, str] = GEMINI_MODEL_IDS) -> None:
        self._key = api_key
        self._ids = dict(model_ids)

    def chat_model(self, tier: ModelTier, options: ModelOptions) -> BaseChatModel:
        from langchain_google_genai import ChatGoogleGenerativeAI  # noqa: PLC0415 - only with the standby on

        model: BaseChatModel = ChatGoogleGenerativeAI(
            model=self._ids[tier],
            google_api_key=self._key,
            max_output_tokens=options.max_tokens,
            timeout=options.timeout_s,
            max_retries=options.max_retries,
            thinking_level=_GEMINI_THINKING[options.effort],
        )
        return model


@dataclass(frozen=True, slots=True)
class ModelSet:
    """A tier's primary model plus ordered fallbacks."""

    primary: BaseChatModel
    fallbacks: tuple[BaseChatModel, ...] = ()

    @property
    def name(self) -> str:
        return str(
            getattr(self.primary, "model", None)
            or getattr(self.primary, "model_name", None)
            or type(self.primary).__name__
        )

    def runnable(self) -> Runnable[LanguageModelInput, BaseMessage]:
        return self.primary.with_fallbacks(list(self.fallbacks)) if self.fallbacks else self.primary

    def structured[T: BaseModel](self, schema: type[T]) -> Runnable[LanguageModelInput, T]:
        """Native JSON-schema structured output (Claude 5.5 models refuse forced tool_choice)."""

        def build(m: BaseChatModel) -> Runnable[LanguageModelInput, T]:
            return m.with_structured_output(schema, method="json_schema")  # type: ignore[return-value]

        first = build(self.primary)
        return first.with_fallbacks([build(m) for m in self.fallbacks]) if self.fallbacks else first

    def middleware(self) -> list[AgentMiddleware[Any, Any]]:
        return [ModelFallbackMiddleware(*self.fallbacks)] if self.fallbacks else []


@dataclass
class ModelRouter:
    """Tier -> ModelSet. The first provider is primary (the Claude API), the rest fall back."""

    providers: Sequence[ModelProvider]
    options: Mapping[ModelTier, ModelOptions] = field(default_factory=dict)
    _cache: dict[ModelTier, ModelSet] = field(default_factory=dict, init=False)

    def for_tier(self, tier: ModelTier) -> ModelSet:
        if tier not in self._cache:
            opts = self.options.get(tier) or DEFAULT_OPTIONS.get(tier, ModelOptions())
            models = [p.chat_model(tier, opts) for p in self.providers]
            self._cache[tier] = ModelSet(models[0], tuple(models[1:]))
        return self._cache[tier]

    @classmethod
    def static(cls, model: BaseChatModel) -> ModelRouter:
        """Every tier answered by one model (tests, evals with a fake model)."""
        router = cls(providers=())
        for tier in ModelTier:
            router._cache[tier] = ModelSet(model)
        return router
