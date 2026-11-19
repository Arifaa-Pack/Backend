"""Language guard (live test 20 Nov): a visitor wrote in Arabic on the Arabic site and, after an English start
to the conversation, the model answered in English although the prompt said "Reply ONLY in Arabic".

For the languages with their own script (Arabic, Hindi, Tamil, Chinese, Sinhala) the reply's script is
checked: the wrong one gets one rewrite with a note naming the language. If the rewrite still misses, the
reply stays (an answer in English beats none) and the miss is recorded as the guard event `language`
(flagged), so a weak model shows up in the AI audit trail.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Final

from langchain.agents.middleware import AgentMiddleware, AgentState, ModelRequest, ModelResponse
from langchain_core.messages import AIMessage

from kx.agents.guard.service import with_note
from kx.agents.roles.language import detect_script_language, language_name
from kx.agents.runtime.context import AgentContext, GuardEventRecord

OWN_SCRIPT: Final = frozenset({"ar", "hi", "ta", "zh", "si"})


def wrong_language(text: str, language: str) -> bool:
    """The reply is not in the visitor's script (only checked for languages with their own script)."""
    return language in OWN_SCRIPT and detect_script_language(text) != language


class LanguageGuardMiddleware(AgentMiddleware[AgentState[Any], AgentContext]):
    async def awrap_model_call(
        self,
        request: ModelRequest[AgentContext],
        handler: Callable[[ModelRequest[AgentContext]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        response = await handler(request)
        msg = response.result[-1] if response.result else None
        ctx = request.runtime.context
        if not isinstance(msg, AIMessage) or msg.tool_calls or not wrong_language(msg.text, ctx.language):
            return response
        name = language_name(ctx.language)
        note = f"IMPORTANT: the visitor writes in {name}. Write your whole answer again in {name} only."
        retry = await handler(request.override(system_message=with_note(request.system_message, note)))
        again = retry.result[-1] if retry.result else None
        if (
            isinstance(again, AIMessage)
            and not again.tool_calls
            and not wrong_language(again.text, ctx.language)
        ):
            ctx.log.guard_events.append(GuardEventRecord("language", "rewritten", {"language": ctx.language}))
            return retry
        ctx.log.guard_events.append(GuardEventRecord("language", "flagged", {"language": ctx.language}))
        return response
