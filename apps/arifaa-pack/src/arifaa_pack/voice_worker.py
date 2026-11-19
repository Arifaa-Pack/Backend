"""`kx voice`: the LiveKit Agents worker for website voice calls (D52).

Fixes the three bench findings (R2b) that stopped every call:

1. **Picklable entry.** LiveKit starts job processes with multiprocessing (spawn / forkserver), which pickles
   the entry function; the old entry was a closure inside the CLI command and no job process could start.
   `entry` and `prewarm` live at module level here.
2. **Warm processes.** `prewarm` loads the Silero VAD model and imports the heavy modules once per process,
   before any call is routed to it (`num_idle_processes` keep processes ready); the container (DB engines,
   HTTP client, agent graphs) is built once per process on its first call and reused (it needs the job's
   event loop, which does not exist yet in `prewarm`).
3. **An LLM on the session** (`kx.agents.voice.graph_llm`): without one LiveKit skipped every reply.

Jobs run in processes, not threads: the container's engines and HTTP client belong to one event loop.
"""

from __future__ import annotations

import sys
from contextlib import AsyncExitStack
from functools import partial
from typing import Any

from arifaa_pack.adapters import PlatformVoicePlugins, VoiceSlots
from arifaa_pack.config import app_settings
from kx.core.logging import get_logger

log = get_logger(__name__)

_STATE: dict[str, Any] = {}


class _Plugins(PlatformVoicePlugins):
    """PlatformVoicePlugins that hands out the VAD model loaded in `prewarm` instead of loading it per
    call."""

    def vad(self) -> Any:
        if "vad" not in _STATE:
            _STATE["vad"] = super().vad()
        return _STATE["vad"]


def prewarm(_proc: Any) -> None:
    """Runs once in every job process before it takes calls. Also installs the OpenTelemetry exporter (D46):
    without it the `VendorSwitch` metric of a mid-call STT / TTS fallback was dropped in voice processes."""
    from livekit.plugins import silero  # noqa: PLC0415 - voice process only

    from arifaa_pack.telemetry import init_telemetry  # noqa: PLC0415
    from kx.core.settings import get_settings  # noqa: PLC0415

    core = get_settings()
    init_telemetry(app_settings(), component="voice", company=core.company_code, environment=core.env.value)
    _STATE["vad"] = silero.VAD.load()
    _preload()
    log.info("voice.prewarmed")


def _preload() -> None:
    """Import what the first call would otherwise import on the call's own event loop: measured 16 Nov, the
    container modules (SQLAlchemy models, botocore) blocked it ≈ 1.2 s and the speech plug-ins ≈ 0.7 s,
    delaying the recording notice and the first answer. Only imports: nothing connects or starts here."""
    from livekit.plugins import deepgram, elevenlabs, openai  # noqa: F401, PLC0415

    import arifaa_pack.container  # noqa: F401, PLC0415 - warm the module cache


async def entry(ctx: Any) -> None:
    """One website voice call. Builds the container on the process's first call, then reuses it."""
    if "fn" not in _STATE:
        from arifaa_pack.container import build  # noqa: PLC0415 - heavy import, job process only
        from kx.agents.voice import VoiceRouter, make_livekit_entrypoint  # noqa: PLC0415

        stack = AsyncExitStack()
        c = await stack.enter_async_context(build())
        s = app_settings()
        slots = VoiceSlots(c.redis, c.core.company_code, s.voice_max_concurrent_free)
        _STATE.update(
            stack=stack,
            fn=make_livekit_entrypoint(
                c.agent_service,
                _Plugins(s.speech_url, on_switch=c.voice_switch),
                VoiceRouter(c.voice_routes),
                on_minutes=partial(c.spend.add, "voice_minutes"),  # D34 daily voice-minute cap
                on_call=slots.on_call,  # D52 free-version calls at once
            ),
        )
    await _STATE["fn"](ctx)


def run() -> None:
    from livekit.agents import JobExecutorType, WorkerOptions, cli  # noqa: PLC0415 - voice extra

    s = app_settings()
    sys.argv = [sys.argv[0], "start"]
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entry,
            prewarm_fnc=prewarm,
            job_executor_type=JobExecutorType.PROCESS,
            num_idle_processes=s.voice_idle_processes,
            ws_url=s.livekit_url,
            api_key=s.livekit_api_key,
            api_secret=s.livekit_api_secret.get_secret_value() if s.livekit_api_secret else None,
            # infra M5: let live calls finish on SIGTERM, but inside the stack's stop_grace_period (the
            # library default is 3600 s, so Docker would kill the calls mid-sentence anyway)
            drain_timeout=280,
        )
    )
