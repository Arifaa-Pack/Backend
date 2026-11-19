"""Claims guard (live test 20 Nov; design doc §4 "no invented specs, certificates, delivery promises").

On the live stack the model, asked "where are your certificates?", named ISO 9001, ISO 14001, BRC, HACCP and a
production capacity that no approved content had given it. A buyer may rely on such a claim (a tender, a food
safety audit), so it is treated like a price: a sentence naming a certificate or standard, or stating a
production capacity, may stand only when a tool result of this very turn (the knowledge base, the catalogue,
the product variants, the specialists) contains it. Otherwise the model is asked once to answer without it;
if it still claims it, the unsupported sentences are removed (and the visitor is told the team will confirm).
Recorded as the guard event `citation`.

Deterministic and conservative: it only knows the certificate names below and capacity sentences with a
number. Everything else stays with the prompt rule "only state facts found with the tools".
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Final

from langchain.agents.middleware import AgentMiddleware, AgentState, ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage

from kx.agents.guard.service import with_note
from kx.agents.runtime.context import AgentContext, GuardEventRecord

CERTIFICATES: Final = re.compile(
    r"\b(ISO\s?\d{3,5}(?::\d{4})?|BRC(?:GS)?|HACCP|FSSC\s?22000|FDA|SGS|Intertek|GMP|halal|kosher|"
    r"CE[- ]mark(?:ed|ing)?)\b"
    r"|(?:شهادة|شهادات)\s+(?:الأيزو|ايزو|أيزو|حلال)",
    re.IGNORECASE,
)
CAPACITY: Final = re.compile(
    r"(?:capacity|capacities|produce|production|output|طاقة|إنتاج)[^.!?؟\n]{0,80}?(\d[\d,.\s]*\d|\d)"
    r"|(\d[\d,.]*)\s*(?:million\s+)?(?:pieces|pcs|containers|units|tons?|tonnes?)\s+(?:a|per|each)\s+"
    r"(?:day|week|month|year|hour|shift)",
    re.IGNORECASE,
)
_SENTENCE: Final = re.compile(r"(?<=[.!?؟。])\s+|\n+")
NOTE: Final = (
    "IMPORTANT: your last answer named certificates, standards or a production capacity that no tool result "
    "in this conversation gave you. Answer again without naming any certificate, standard or capacity; say "
    "our team will confirm them, and offer to send the documents."
)
CONFIRM: Final = {
    "en": "Our team will confirm the certificates and production details for you.",
    "ar": "سيؤكد فريقنا لك الشهادات وتفاصيل الإنتاج.",
}


def facts_of(messages: Sequence[BaseMessage]) -> str:
    """What the tools returned in this conversation (the only facts the model may cite)."""
    return "\n".join(m.text for m in messages if isinstance(m, ToolMessage)).lower()


def unsupported(text: str, facts: str) -> list[str]:
    """The claims in `text` that `facts` does not contain (lower-cased, spaces ignored)."""
    squash = facts.replace(" ", "")
    out = [
        m.group(0) for m in CERTIFICATES.finditer(text) if m.group(0).lower().replace(" ", "") not in squash
    ]
    for m in CAPACITY.finditer(text):
        number = (m.group(1) or m.group(2) or "").replace(",", "").replace(" ", "")
        if number and number not in squash.replace(",", ""):
            out.append(m.group(0).strip())
    return out


def without_claims(text: str, facts: str, language: str) -> str:
    """The reply minus every sentence with an unsupported claim, then the team-will-confirm line."""
    kept = [s for s in _SENTENCE.split(text) if s.strip() and not unsupported(s, facts)]
    confirm = CONFIRM.get(language, CONFIRM["en"])
    return " ".join([*kept, confirm]) if kept else confirm


class ClaimsGuardMiddleware(AgentMiddleware[AgentState[Any], AgentContext]):
    """Sits inside the price guard: the price guard then checks the claims-guarded text."""

    async def awrap_model_call(
        self,
        request: ModelRequest[AgentContext],
        handler: Callable[[ModelRequest[AgentContext]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        response = await handler(request)
        msg = response.result[-1] if response.result else None
        if not isinstance(msg, AIMessage) or msg.tool_calls or not msg.text:
            return response
        facts = facts_of(request.messages)
        claims = unsupported(msg.text, facts)
        if not claims:
            return response
        ctx = request.runtime.context
        retry = await handler(request.override(system_message=with_note(request.system_message, NOTE)))
        again = retry.result[-1] if retry.result else None
        clean = isinstance(again, AIMessage) and not again.tool_calls and bool(again.text)
        if clean and isinstance(again, AIMessage) and not unsupported(again.text, facts):
            ctx.log.guard_events.append(GuardEventRecord("citation", "rewritten", {"claims": claims[:10]}))
            return retry
        text = without_claims(msg.text, facts, ctx.language)
        ctx.log.guard_events.append(GuardEventRecord("citation", "blocked", {"claims": claims[:10]}))
        return ModelResponse(result=[AIMessage(content=text, id=msg.id)])
