"""Agent middleware for the role-switching conversation agent.

RoleMiddleware: one compiled agent serves every conversation role; per model call it installs the
role's system prompt (static part cache-tagged for Anthropic prompt caching) plus the live deal facts,
and narrows the tool list to the role's subset.
ToolAuditMiddleware: denies tools outside the role (defence against hallucinated calls), turns CRM
rule refusals into tool errors the model can explain, and records every call (PII-masked).
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Final, cast

from langchain.agents.middleware import AgentMiddleware, AgentState, ModelRequest, ModelResponse
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import SystemMessage, ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from kx.agents.roles.language import language_name
from kx.agents.roles.registry import CATALOGUE_TOOLS, SITE_TOOLS, SPECIALIST_TOOLS
from kx.agents.runtime.context import AgentContext, ToolCallRecord
from kx.core.contracts import AllowedPrice
from kx.core.errors import DomainError
from kx.core.logging import mask_pii

_CHANNEL_STYLE = {
    "web_voice": "You are speaking aloud: two or three short sentences, no lists, no markdown.",
    "phone": "You are speaking aloud: two or three short sentences, no lists, no markdown.",
    "whatsapp": "WhatsApp: short paragraphs, no markdown tables.",
    "email": "Email: complete, polite, structured; no subject line.",
    "web_chat": "Website chat: concise and friendly.",
}


WEBSITE_CHANNELS: Final = frozenset({"web_chat", "web_voice"})
WEBSITE_GUIDE: Final = (
    "You are on Arifa Pack's website and can move the visitor's page: show_family, show_product, go_to_page, "
    "start_factory_tour, add_to_enquiry_list, fill_enquiry_desk. Show what you talk about, once, and refer "
    "to it in a few words; never describe the page. Get product ids from find_products; never invent one. "
    "For a question that needs product and factory knowledge, ask both specialists at once. Give real help "
    "first; when the visitor wants a quotation, details or a call (or after two or three useful answers), "
    "call ask_for_contact once. WhatsApp or email only with the visitor's own yes (the ticks on the card)."
)


def available_tools(ctx: AgentContext) -> frozenset[str]:
    """The role's tools this turn: the site tools only on the website with a site map, the catalogue only with
    a site map, the specialists only when the fast model is wired for them."""
    names = set(ctx.spec.tools)
    if ctx.channel not in WEBSITE_CHANNELS or ctx.manifest is None:
        names -= SITE_TOOLS
    if ctx.manifest is None:
        names -= CATALOGUE_TOOLS
    if ctx.specialist_model is None:
        names -= SPECIALIST_TOOLS
    return frozenset(names)


def _site_lines(ctx: AgentContext) -> list[str]:
    if ctx.channel not in WEBSITE_CHANNELS:
        return []
    lines = [ctx.site.summary()] if ctx.site is not None else []
    if ctx.manifest is not None:
        lines.append(WEBSITE_GUIDE)
    if ctx.log.site_actions:
        shown = ", ".join(
            f"{a.type} {a.resolved.path_en if a.resolved else ''}".strip() for a in ctx.log.site_actions
        )
        lines.append(f"Already on the visitor's screen this turn: {shown}. Do not show it again.")
    return lines


def deal_facts(ctx: AgentContext, allowed: Sequence[AllowedPrice] | None = None) -> str:
    """The per-turn, uncached part of the system prompt. `allowed` narrows the prices (deal graph)."""
    d = ctx.deal
    prices = (d.allowed_prices if ctx.spec.may_quote_prices else ()) if allowed is None else tuple(allowed)
    lines = [
        f"Deal {d.number}, stage {d.stage.value}. Reply ONLY in {language_name(ctx.language)}.",
        _CHANNEL_STYLE.get(ctx.channel, ""),
    ]
    if d.missing_fields:
        lines.append("Still missing before qualification: " + ", ".join(d.missing_fields) + ".")
    lines.extend(
        f"Requirement: {r.product_text or r.product_sku or '?'}, qty {r.quantity or '?'} {r.unit}, "
        f"to {r.delivery_country or '?'}."
        for r in d.requirements
    )
    if prices:
        lines.append(
            "ALLOWED PRICES (state no other amount): "
            + "; ".join(f"{p.currency} {p.amount} ({p.label})" for p in prices)
        )
    else:
        lines.append("ALLOWED PRICES: none. Do not state any price or amount.")
    if d.estimated_delivery:
        lines.append(
            f"Estimated delivery (always say 'estimated, confirmed by our team'): {d.estimated_delivery}."
        )
    if ctx.summary:
        lines.append(f"Earlier in this conversation: {ctx.summary}")
    lines.extend(_site_lines(ctx))
    return "\n".join(x for x in lines if x)


class RoleMiddleware(AgentMiddleware[AgentState[Any], AgentContext]):
    async def awrap_model_call(
        self,
        request: ModelRequest[AgentContext],
        handler: Callable[[ModelRequest[AgentContext]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        ctx = request.runtime.context
        static: dict[str, Any] = {
            "type": "text",
            "text": ctx.prompts.system_prompt(ctx.spec, ctx.language).text,
        }
        if isinstance(request.model, ChatAnthropic):
            static["cache_control"] = {"type": "ephemeral"}  # caches tools + role prompt across turns
        system = SystemMessage(content=[static, {"type": "text", "text": deal_facts(ctx)}])
        allowed = available_tools(ctx)
        tools = [t for t in request.tools if isinstance(t, dict) or t.name in allowed]
        return await handler(request.override(system_message=system, tools=tools))


class ToolAuditMiddleware(AgentMiddleware[AgentState[Any], AgentContext]):
    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        ctx = cast("AgentContext", request.runtime.context)
        call = request.tool_call
        name, call_id = call["name"], call["id"] or ""
        args = {k: mask_pii(v) if isinstance(v, str) else v for k, v in call["args"].items()}
        started = time.perf_counter()

        def record(status: Any, summary: str) -> None:
            ctx.log.tool_calls.append(
                ToolCallRecord(
                    name, args, status, mask_pii(summary)[:500], int((time.perf_counter() - started) * 1000)
                )
            )

        if name not in available_tools(ctx):
            record("denied", "tool not allowed for role")
            return ToolMessage(content=f"Tool {name} is not available.", tool_call_id=call_id, status="error")
        try:
            result = await handler(request)
        except DomainError as exc:  # CRM refused (rule, conflict, not found): let the model explain
            record("error", exc.detail)
            return ToolMessage(content=f"Not possible: {exc.detail}", tool_call_id=call_id, status="error")
        if isinstance(result, ToolMessage):
            record("error" if result.status == "error" else "ok", result.text)
        else:
            record("ok", "command")
        return result
