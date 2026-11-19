"""Per-run context injected into graphs (`context=`), tools (`ToolRuntime.context`) and middleware.

The deal id lives here, never in tool arguments, so the model cannot act on another buyer's deal.
`RunLog` collects tool calls, guard events and token usage in memory; `runs.RunStore` writes them
in one transaction when the run ends (no DB round-trips inside the agent loop).
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal

from kx.agents.knowledge.service import KnowledgeSearch
from kx.agents.llm.service import ModelSet
from kx.agents.roles.registry import AgentSpec, PromptStore
from kx.agents.site.models import SiteAction, SiteContext
from kx.core.contracts import AllowedPrice, Channel, CrmGateway, DealContext, SiteManifest

GuardKind = Literal["price_number", "injection", "disclosure", "cost_leak", "language"]
# (reply, allowed prices, run log) -> True when the reply states or implies a price not in the allowed list
type PriceCheck = Callable[[str, Sequence[AllowedPrice], RunLog], Awaitable[bool]]
GuardAction = Literal["blocked", "rewritten", "flagged"]


@dataclass(frozen=True, slots=True)
class ToolCallRecord:
    tool: str
    args: dict[str, Any]  # PII-masked
    status: Literal["ok", "error", "denied"]
    result_summary: str
    duration_ms: int


@dataclass(frozen=True, slots=True)
class GuardEventRecord:
    guard: GuardKind
    action: GuardAction
    detail: dict[str, Any]


@dataclass(slots=True)
class RunLog:
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    guard_events: list[GuardEventRecord] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    regenerations: int = 0  # the price guard regenerates at most once per run
    handoff: bool = False
    cost_usd: Decimal = Decimal(0)  # D34: summed per model call from the official price table
    site_actions: list[SiteAction] = field(default_factory=list)  # D55: what the website was asked to show

    @property
    def blocked(self) -> bool:
        return any(g.action == "blocked" for g in self.guard_events)


@dataclass(frozen=True, slots=True)
class AgentContext:
    deal: DealContext
    spec: AgentSpec
    channel: Channel
    language: str
    gateway: CrmGateway
    prompts: PromptStore
    log: RunLog = field(default_factory=RunLog)
    knowledge: KnowledgeSearch | None = None
    conversation_id: uuid.UUID | None = None
    summary: str | None = None  # rolling summary of turns older than the window
    # M7 layer 3: the FAST-model second opinion on a reply the deterministic guard found clean (None = off)
    price_check: PriceCheck | None = None
    # D55 website turns: where the visitor is, what the site has, and where to send an action at once (the
    # website starts moving while the agent is still writing its reply)
    site: SiteContext | None = None
    manifest: SiteManifest | None = None
    on_action: Callable[[SiteAction], Awaitable[None]] | None = None
    # D56: the fast model the product / manufacturing specialists answer with (None = specialists off)
    specialist_model: ModelSet | None = None

    @property
    def deal_id(self) -> uuid.UUID:
        return self.deal.deal_id
