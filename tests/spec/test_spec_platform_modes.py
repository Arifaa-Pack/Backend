"""D41 spec: the paid / free version per field (server plan v2 §2-§4, docs/PLATFORM_MODES.md)."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from kx.core.platform import (
    PAID_ONLY,
    PAID_VENDORS,
    Field,
    PlatformConfigError,
    order,
    resolve_modes,
    resolved,
)
from kx.core.settings import CoreSettings

ALL_VENDORS = frozenset(
    {"claude", "gemini", "deepgram", "whisper", "elevenlabs", "piper", "telnyx", "meta", "stalwart", "ses"}
)
FREE_FIELDS = [f for f in Field if f not in PAID_ONLY]
plans = st.sampled_from(["paid", "free"])
overrides = st.fixed_dictionaries({f.value: st.none() | plans for f in FREE_FIELDS})
configured = st.frozensets(st.sampled_from(sorted(ALL_VENDORS)))
languages = st.sampled_from([None, "en", "ar", "hi", "ta", "zh", "si"])


def test_paid_version_is_paid_first_then_the_free_alternative() -> None:
    m = resolve_modes("paid", {})
    assert order(Field.STT, m, ALL_VENDORS) == ("deepgram", "whisper")
    assert order(Field.TTS, m, ALL_VENDORS, language="ar") == ("elevenlabs", "piper")
    assert order(Field.EMAIL, m, ALL_VENDORS) == ("stalwart", "ses")
    assert order(Field.LLM, m, ALL_VENDORS) == ("claude",)


def test_free_version_uses_only_the_free_alternative() -> None:
    m = resolve_modes("free", {})
    assert order(Field.STT, m, ALL_VENDORS) == ("whisper",)
    assert order(Field.TTS, m, ALL_VENDORS, language="en") == ("piper",)
    # fields with no free platform keep their paid one under a free preset
    assert order(Field.CALLS, m, ALL_VENDORS) == ("telnyx",)
    assert order(Field.WHATSAPP, m, ALL_VENDORS) == ("meta",)
    assert order(Field.LLM, m, ALL_VENDORS) == ("claude",)


def test_tamil_needs_elevenlabs_even_in_the_free_version_and_sinhala_is_text_only() -> None:
    free = resolve_modes("free", {})
    assert order(Field.TTS, free, ALL_VENDORS, language="ta") == ("elevenlabs",)
    assert order(Field.TTS, free, ALL_VENDORS - {"elevenlabs"}, language="ta") == ()
    for m in (free, resolve_modes("paid", {})):
        assert order(Field.TTS, m, ALL_VENDORS, language="si") == ()
        assert order(Field.STT, m, ALL_VENDORS, language="si") == ()


def test_gemini_standby_only_when_switched_on() -> None:
    assert order(Field.LLM, resolve_modes("paid", {}, llm_standby="gemini"), ALL_VENDORS) == (
        "claude",
        "gemini",
    )


@pytest.mark.parametrize("field", sorted(f.value for f in PAID_ONLY))
def test_an_explicit_free_for_a_paid_only_field_is_refused(field: str) -> None:
    with pytest.raises(PlatformConfigError):
        resolve_modes("paid", {field: "free"})


@pytest.mark.parametrize(("name", "value"), [("embeddings", "openai"), ("ai_trace", "datadog")])
def test_unknown_choices_are_refused(name: str, value: str) -> None:
    with pytest.raises(PlatformConfigError):
        resolve_modes("paid", {}, **{name: value})  # type: ignore[arg-type]


def test_a_paid_field_without_its_paid_key_is_reported() -> None:
    rows = {r.field: r for r in resolved(resolve_modes("paid", {}), ALL_VENDORS - {"deepgram"})}
    assert rows[Field.STT].order == ("whisper",)
    assert rows[Field.STT].missing == ("deepgram",)
    assert rows[Field.STT].lost_paid


def test_env_vars_preset_and_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KX_PLATFORM_PLAN", "free")
    monkeypatch.setenv("KX_PLATFORM__STT", "paid")
    m = CoreSettings().modes()
    assert m.mode(Field.STT) == "paid"
    assert m.mode(Field.TTS) == "free"


@given(plans, overrides, configured, languages)
def test_order_invariants(
    plan: str, over: dict[str, str | None], conf: frozenset[str], lang: str | None
) -> None:
    m = resolve_modes(plan, over)  # type: ignore[arg-type]
    for field in Field:
        got = order(field, m, conf, language=lang)
        assert set(got) <= conf  # never a vendor that is not configured
        assert len(got) == len(set(got))
        if m.mode(field) == "free" and field is not Field.EMAIL:
            # the free version never calls a paid vendor, except ElevenLabs for Tamil (D52)
            paid = {v for v in got if v in PAID_VENDORS} - ({"elevenlabs"} if lang == "ta" else set())
            assert not paid
        if field is Field.STT and m.mode(field) == "paid" and "deepgram" in got:
            assert got[0] == "deepgram"  # paid first, free alternative after
