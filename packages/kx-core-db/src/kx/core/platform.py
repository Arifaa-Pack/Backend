"""D41: the paid / free version of every field, as an ordered vendor list.

Source: *Accounts & Server Plan v2* §2 (main platform and its free alternative per field), §3 (paid version),
§4 (free version). Configuration only, no I/O: `KX_PLATFORM_PLAN=paid|free` presets every field and
`KX_PLATFORM__<FIELD>` overrides one (`CoreSettings.platform_plan` / `CoreSettings.platform`).

* **paid** = the paid platform first, then the free alternative on our server as the automatic fallback.
* **free** = the free alternative only.

Fields with no free platform (`llm`, `calls`, `whatsapp`) ignore the preset and refuse an explicit `free`
(`PlatformConfigError` at start-up): there is no free way to place a phone call, reach WhatsApp or run a
model good enough for price talk in five languages (Options by field §1, §5, §6).

The app passes `configured` (vendors whose key or URL is set): a vendor without one is left out of the order,
so paid STT without a Deepgram key runs faster-whisper only, with a warning in `resolved()`.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Literal

Plan = Literal["paid", "free"]


class PlatformConfigError(ValueError):
    """A field is set to a version it does not have (e.g. `KX_PLATFORM__LLM=free`)."""


class Field(StrEnum):
    LLM = "llm"
    STT = "stt"
    TTS = "tts"
    CALLS = "calls"
    WHATSAPP = "whatsapp"
    EMAIL = "email"


PAID_ONLY: Final = frozenset({Field.LLM, Field.CALLS, Field.WHATSAPP})

# v2 §2: main platform -> free alternative (vendor names are the adapters' keys in the app)
VENDORS: Final[dict[Field, dict[Plan, tuple[str, ...]]]] = {
    Field.LLM: {"paid": ("claude",)},
    Field.STT: {"paid": ("deepgram", "whisper"), "free": ("whisper",)},
    Field.TTS: {"paid": ("elevenlabs", "piper"), "free": ("piper",)},
    Field.CALLS: {"paid": ("telnyx",)},
    Field.WHATSAPP: {"paid": ("meta",)},
    # Stalwart is both versions' mailbox; SES is its relay and the app's backup sender (v2 §4 keeps SES)
    Field.EMAIL: {"paid": ("stalwart", "ses"), "free": ("stalwart", "ses")},
}
PAID_VENDORS: Final = frozenset({"claude", "gemini", "deepgram", "elevenlabs", "telnyx", "meta", "ses"})

PIPER_LANGUAGES: Final = frozenset({"en", "ar", "hi", "zh"})  # Piper voices exist (bench R2); no ta, no si
TEXT_ONLY_LANGUAGES: Final = frozenset({"si"})  # D36 / D52: Sinhala voice is text chat only
ELEVENLABS_ONLY_LANGUAGES: Final = frozenset({"ta"})  # D52: Tamil voice only through ElevenLabs

EMBEDDING_CHOICES: Final = ("bge-m3", "qwen3")
AI_TRACE_CHOICES: Final = ("phoenix", "langfuse")
LLM_STANDBY_CHOICES: Final = ("none", "gemini")


@dataclass(frozen=True, slots=True)
class Modes:
    """The resolved env switch: one plan per field (after the preset and the overrides)."""

    plan: Plan
    fields: dict[Field, Plan]
    llm_standby: str = "none"
    embeddings: str = "bge-m3"
    ai_trace: str = "phoenix"

    def mode(self, field: Field) -> Plan:
        return self.fields[field]


def resolve_modes(
    plan: Plan,
    overrides: dict[str, Plan | None],
    *,
    llm_standby: str = "none",
    embeddings: str = "bge-m3",
    ai_trace: str = "phoenix",
) -> Modes:
    """Preset + per-field overrides -> one plan per field. Paid-only fields ignore a `free` preset but refuse
    an explicit `free` override (a configuration error, not something to run with silently)."""
    fields: dict[Field, Plan] = {}
    for field in Field:
        override = overrides.get(field.value)
        if field in PAID_ONLY:
            if override == "free":
                raise PlatformConfigError(
                    f"KX_PLATFORM__{field.value.upper()}=free: {field} has no free version"
                )
            fields[field] = "paid"
        else:
            fields[field] = override or plan
    for name, value, allowed in (
        ("LLM_STANDBY", llm_standby, LLM_STANDBY_CHOICES),
        ("EMBEDDINGS", embeddings, EMBEDDING_CHOICES),
        ("AI_TRACE", ai_trace, AI_TRACE_CHOICES),
    ):
        if value not in allowed:
            raise PlatformConfigError(f"KX_PLATFORM__{name}={value!r}: choose one of {', '.join(allowed)}")
    return Modes(plan, fields, llm_standby, embeddings, ai_trace)


def order(
    field: Field, modes: Modes, configured: Collection[str], *, language: str | None = None
) -> tuple[str, ...]:
    """The vendors of `field` in the order to try, without those that are not configured. Empty = go straight
    to the field's last resort (text chat, safe reply, hand-over …)."""
    plan = modes.mode(field)
    vendors = VENDORS[field][plan]
    if field is Field.LLM and modes.llm_standby == "gemini":
        vendors = (*vendors, "gemini")
    if field in (Field.STT, Field.TTS) and language in TEXT_ONLY_LANGUAGES:
        return ()
    if field is Field.TTS and language is not None:
        vendors = _tts_for_language(vendors, language)
    return tuple(v for v in vendors if v in configured)


def _tts_for_language(vendors: tuple[str, ...], language: str) -> tuple[str, ...]:
    if language in ELEVENLABS_ONLY_LANGUAGES:
        return ("elevenlabs",)  # D52: also in the free version, when the key is set
    if language not in PIPER_LANGUAGES:
        return tuple(v for v in vendors if v != "piper")
    return vendors


@dataclass(frozen=True, slots=True)
class Resolved:
    field: Field
    mode: Plan
    order: tuple[str, ...]
    missing: tuple[str, ...]  # vendors of this mode that are not configured

    @property
    def lost_paid(self) -> bool:
        """The paid version was asked for, but no paid vendor is configured (it silently runs free)."""
        return self.mode == "paid" and not any(v in PAID_VENDORS for v in self.order)


def resolved(modes: Modes, configured: Collection[str]) -> tuple[Resolved, ...]:
    """The whole matrix, for the start-up log and `kx platform show`."""
    everything = _all_vendors()
    return tuple(
        Resolved(
            field,
            modes.mode(field),
            order(field, modes, configured),
            tuple(v for v in order(field, modes, everything) if v not in configured),
        )
        for field in Field
    )


def summary(modes: Modes, configured: Collection[str]) -> list[dict[str, str]]:
    """Rows for printing / logging: field, mode, order (a -> b), missing (ASCII: Windows consoles)."""
    rows = [
        {
            "field": r.field.value,
            "mode": r.mode,
            "order": " -> ".join(r.order) or "(last resort only)",
            "missing": ", ".join(r.missing),
        }
        for r in resolved(modes, configured)
    ]
    rows += [
        {"field": "llm_standby", "mode": modes.llm_standby, "order": "", "missing": ""},
        {"field": "embeddings", "mode": modes.embeddings, "order": "", "missing": ""},
        {"field": "ai_trace", "mode": modes.ai_trace, "order": "", "missing": ""},
    ]
    return rows


def _all_vendors() -> frozenset[str]:
    return frozenset({v for plans in VENDORS.values() for vs in plans.values() for v in vs} | {"gemini"})
