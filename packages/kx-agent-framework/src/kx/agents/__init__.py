"""Kairox agents: the AI sales conversation. A deterministic router picks the role, LangGraph graphs run
it, the tools read the CRM through `kx.core.contracts`, and every reply passes the reply guard (price guard,
fails closed) before it can reach a buyer. Also the knowledge base, memory, the voice worker and the website
API.

Public surface (ADR-0001, rule 3): the names an app (`arifaa_pack`) imports. Everything else is internal to
its feature slice (`kx/agents/<slice>/`, map in `docs/arch/AUTOMATION_AGENTS_SLICES.md`). Other packages
never import `kx.agents`: they use `kx.core.contracts` and events. The names are resolved lazily (PEP 562),
so importing one agents module does not load the whole package (LangGraph, LiveKit ...).
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from kx.agents import retention
    from kx.agents.evals.harness import run_suite
    from kx.agents.handlers import AgentHandlers, ReplyAddress
    from kx.agents.knowledge.service import KbSource, KnowledgeBase, KnowledgeIndexer
    from kx.agents.llm.service import (
        AnthropicApiProvider,
        Effort,
        GeminiApiProvider,
        ModelProvider,
        ModelRouter,
        tier_options,
    )
    from kx.agents.memory.service import open_checkpointer
    from kx.agents.roles.registry import ModelTier
    from kx.agents.router.service import TopicWords
    from kx.agents.runtime.service import AgentService
    from kx.agents.tracing.service import Tracer
    from kx.agents.voice.service import VoiceRoute, VoiceRouter, make_livekit_entrypoint, voice_routes
    from kx.agents.website.router import LiveKitConfig, PublicScope, WebsiteDeps
    from kx.agents.website.router import build_router as website_router

# public name -> (module, attribute); an empty attribute means the module itself
_EXPORTS: dict[str, tuple[str, str]] = {
    # wiring
    "retention": ("kx.agents.retention", ""),
    "AgentHandlers": ("kx.agents.handlers", "AgentHandlers"),
    "ReplyAddress": ("kx.agents.handlers", "ReplyAddress"),
    "AgentService": ("kx.agents.runtime.service", "AgentService"),
    "TopicWords": ("kx.agents.router.service", "TopicWords"),
    "Tracer": ("kx.agents.tracing.service", "Tracer"),
    "open_checkpointer": ("kx.agents.memory.service", "open_checkpointer"),
    # models
    "AnthropicApiProvider": ("kx.agents.llm.service", "AnthropicApiProvider"),
    "GeminiApiProvider": ("kx.agents.llm.service", "GeminiApiProvider"),
    "ModelProvider": ("kx.agents.llm.service", "ModelProvider"),
    "ModelRouter": ("kx.agents.llm.service", "ModelRouter"),
    "Effort": ("kx.agents.llm.service", "Effort"),
    "tier_options": ("kx.agents.llm.service", "tier_options"),
    "ModelTier": ("kx.agents.roles.registry", "ModelTier"),
    "run_suite": ("kx.agents.evals.harness", "run_suite"),
    # knowledge
    "KbSource": ("kx.agents.knowledge.service", "KbSource"),
    "KnowledgeBase": ("kx.agents.knowledge.service", "KnowledgeBase"),
    "KnowledgeIndexer": ("kx.agents.knowledge.service", "KnowledgeIndexer"),
    # voice and the website
    "VoiceRoute": ("kx.agents.voice.service", "VoiceRoute"),
    "VoiceRouter": ("kx.agents.voice.service", "VoiceRouter"),
    "voice_routes": ("kx.agents.voice.service", "voice_routes"),
    "make_livekit_entrypoint": ("kx.agents.voice.service", "make_livekit_entrypoint"),
    "LiveKitConfig": ("kx.agents.website.router", "LiveKitConfig"),
    "PublicScope": ("kx.agents.website.router", "PublicScope"),
    "WebsiteDeps": ("kx.agents.website.router", "WebsiteDeps"),
    "website_router": ("kx.agents.website.router", "build_router"),
}

__all__ = [
    "AgentHandlers",
    "AgentService",
    "AnthropicApiProvider",
    "Effort",
    "GeminiApiProvider",
    "KbSource",
    "KnowledgeBase",
    "KnowledgeIndexer",
    "LiveKitConfig",
    "ModelProvider",
    "ModelRouter",
    "ModelTier",
    "PublicScope",
    "ReplyAddress",
    "TopicWords",
    "Tracer",
    "VoiceRoute",
    "VoiceRouter",
    "WebsiteDeps",
    "make_livekit_entrypoint",
    "open_checkpointer",
    "retention",
    "run_suite",
    "tier_options",
    "voice_routes",
    "website_router",
]


def __getattr__(name: str) -> Any:
    try:
        module_name, attr = _EXPORTS[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    value: Any = importlib.import_module(module_name)
    if attr:
        value = getattr(value, attr)
    globals()[name] = value
    return value
