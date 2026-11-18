"""16 Nov v2 core pieces: OpenTelemetry metrics (D46), phone parsing without exceptions."""

from __future__ import annotations

import io
import sys
import uuid
from typing import Any

import pytest
from opentelemetry import metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from kx.core import metrics as kx_metrics
from kx.core.identity import try_normalise_phone


@pytest.fixture(scope="module")
def reader() -> InMemoryMetricReader:
    r = InMemoryMetricReader()
    metrics.set_meter_provider(MeterProvider(metric_readers=[r]))
    kx_metrics._instruments.clear()
    return r


def _points(reader: InMemoryMetricReader, name: str) -> list[tuple[float, dict[str, object]]]:
    data = reader.get_metrics_data()
    out: list[tuple[float, dict[str, object]]] = []
    for rm in data.resource_metrics if data else ():
        for sm in rm.scope_metrics:
            for m in sm.metrics:
                if m.name == name.lower():  # OpenTelemetry names are case-insensitive (stored lowercase)
                    out += [(p.value, dict(p.attributes or {})) for p in m.data.data_points]
    return out


def test_vendor_switch_is_a_counter_with_field_attributes(reader: InMemoryMetricReader) -> None:
    kx_metrics.emit_metric(
        "VendorSwitch", 1, company="pack", field="stt", from_vendor="deepgram", to_vendor="whisper"
    )
    kx_metrics.emit_metric(
        "VendorSwitch", 1, company="pack", field="stt", from_vendor="deepgram", to_vendor="whisper"
    )
    [(value, attrs)] = _points(reader, "VendorSwitch")
    assert value == 2
    assert attrs == {"company": "pack", "field": "stt", "from_vendor": "deepgram", "to_vendor": "whisper"}


def test_queue_lag_is_a_gauge(reader: InMemoryMetricReader) -> None:
    kx_metrics.emit_metric("QueueLagSeconds", 12.5, company="pack", unit="Seconds")
    kx_metrics.emit_metric("QueueLagSeconds", 3.0, company="pack", unit="Seconds")
    assert [v for v, _ in _points(reader, "QueueLagSeconds")] == [3.0]


def test_metrics_never_touch_stdout_so_celery_cannot_break_them(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bench R1: under Celery sys.stdout is a logging proxy without `.buffer`; the old EMF writer raised there,
    which killed the Beat heartbeat and every VendorSwitch metric inside workers."""

    class NoBuffer(io.StringIO):
        @property
        def buffer(self) -> object:
            raise AttributeError("buffer")

    monkeypatch.setattr(sys, "stdout", NoBuffer())
    kx_metrics.emit_metric("SpendCap", 80, company="pack", unit="Percent", cap="ai_usd")
    assert sys.stdout.getvalue() == ""


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("+971501234567", "+971501234567"), ("12345", None), ("", None), ("+1", None), ("abc", None)],
)
def test_try_normalise_phone(raw: str, expected: str | None) -> None:
    assert try_normalise_phone(raw) == expected


def test_app_logs_are_forwarded_after_masking_and_never_break_a_log_line() -> None:
    """17 Nov: structlog events also go to SigNoz. The forwarder sits after the PII mask (it only sees masked
    values), maps the level, flattens values to OTLP types and swallows its own errors."""
    from arifaa_pack.telemetry import log_forwarder
    from kx.core.logging import mask_pii

    sent: list[dict[str, Any]] = []

    class Otel:
        def emit(self, **kw: Any) -> None:
            sent.append(kw)

    event = {
        "event": "reply.sent",
        "level": "warning",
        "to": mask_pii("+971501234567"),
        "n": 3,
        "x": None,
        "deal": uuid.UUID(int=1),
        "timestamp": "t",
    }
    assert log_forwarder(Otel())(None, "warning", dict(event)) == event  # unchanged for the renderer
    (rec,) = sent
    assert (rec["body"], rec["severity_text"], rec["severity_number"].value) == ("reply.sent", "WARNING", 13)
    assert rec["attributes"] == {"to": "+971*****67", "n": 3, "deal": str(uuid.UUID(int=1))}

    class Broken:
        def emit(self, **kw: Any) -> None:
            raise RuntimeError("collector down")

    assert log_forwarder(Broken())(None, "info", {"event": "x"}) == {"event": "x"}


def test_log_time_is_not_masked_as_a_phone_but_phones_still_are(capsys: pytest.CaptureFixture[str]) -> None:
    """Found 17 Nov in the live SigNoz test: the PII mask ran after the timestamp and printed `20*****17T13:19`."""
    import json

    import structlog

    from kx.core.logging import configure_logging

    configure_logging("INFO", json=True)
    structlog.get_logger("t").info("x", phone="+971501234567")
    line = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert "*" not in line["timestamp"] and line["timestamp"][:2] == "20"
    assert line["phone"] == "+971*****67"


def test_celery_stdout_copies_and_exporter_errors_are_not_forwarded() -> None:
    import logging

    from arifaa_pack.telemetry import _library_record

    def rec(name: str) -> logging.LogRecord:
        return logging.LogRecord(name, logging.WARNING, "f", 1, "m", None, None)

    assert _library_record(rec("sqlalchemy.pool"))
    assert not _library_record(rec("celery.redirected"))
    assert not _library_record(rec("opentelemetry.exporter.otlp"))


def test_production_refuses_a_letterhead_with_placeholders() -> None:
    """D12(b): the letterhead prints on every quote PDF; a REPLACE value stops production, only warns elsewhere."""
    from arifaa_pack.config import Company
    from arifaa_pack.container import check_letterhead
    from kx.core.settings import Environment

    full = {"name": "ARIFA", "address": "Ajman", "trn": "100000000000003", "phone": "+971", "email": "a@b.ae"}
    check_letterhead(Company("pack", "Arifaa Pack", full, ("en",)), Environment.PRODUCTION)
    placeholder = Company("pack", "Arifaa Pack", {**full, "trn": "REPLACE — TRN"}, ("en",))
    check_letterhead(placeholder, Environment.LOCAL)  # warns only
    with pytest.raises(RuntimeError, match="trn"):
        check_letterhead(placeholder, Environment.PRODUCTION)
    # 18 Nov: staff may enter the TRN in the CRM company details instead of company.yaml
    check_letterhead(placeholder, Environment.PRODUCTION, {"trn": "100000000000003", "name": ""})


def test_voice_processes_install_the_otlp_exporter(monkeypatch: pytest.MonkeyPatch) -> None:
    """D46, found 16 Nov: only api and worker installed telemetry, so a mid-call STT / TTS fallback's
    `VendorSwitch` metric was dropped in voice processes. `prewarm` (once per LiveKit job process) installs it."""
    from livekit.plugins import silero

    from arifaa_pack import telemetry, voice_worker

    calls: list[dict[str, str]] = []

    def fake_init(_settings: object, *, component: str, company: str, environment: str) -> bool:
        calls.append({"component": component, "company": company, "environment": environment})
        return True

    monkeypatch.setattr(telemetry, "init_telemetry", fake_init)
    monkeypatch.setattr(silero.VAD, "load", staticmethod(lambda **_: "vad"))
    monkeypatch.setattr(voice_worker, "_STATE", {})
    voice_worker.prewarm(None)
    assert [c["component"] for c in calls] == ["voice"]
    assert voice_worker._STATE["vad"] == "vad"
    # the first call no longer pays for these imports on its own event loop (≈ 1.9 s measured 16 Nov)
    assert {"arifaa_pack.container", "livekit.plugins.openai", "livekit.plugins.deepgram"} <= set(sys.modules)


@pytest.mark.parametrize("as_json", [True, False])
def test_log_exception_works_in_both_formats(as_json: bool, capsys: pytest.CaptureFixture[str]) -> None:
    """Found 17 Nov: with console logs (local dev) `log.exception` raised TypeError and killed the request."""
    import structlog

    from kx.core.logging import configure_logging

    configure_logging("INFO", json=as_json)
    try:
        raise ValueError("boom")  # noqa: TRY301
    except ValueError:
        structlog.get_logger("t").exception("agents.all_models_failed")
    out = capsys.readouterr().out
    assert "agents.all_models_failed" in out and "boom" in out


@pytest.mark.parametrize(
    ("raw", "masked"),
    [
        ("deal 01a1e9b0-fa04-771d-b812-6543265c9cc2", "deal 01a1e9b0-fa04-771d-b812-6543265c9cc2"),
        ("due 2026-11-17T13:19:34.553043Z", "due 2026-11-17T13:19:34.553043Z"),
        ("call +971501234567 now", "call +971*****67 now"),
        ("0501-23-4567", "050*****67"),  # looks like a date to a loose pattern: still a phone
        ("+971-50-123-4567 / a.b@x.ae", "+971*****67 / a***@x.ae"),
    ],
)
def test_mask_keeps_ids_and_dates_but_still_masks_phones(raw: str, masked: str) -> None:
    """Found 17 Nov in SigNoz: deal ids and timestamps were half-masked as phone numbers."""
    from kx.core.logging import mask_pii

    assert mask_pii(raw) == masked


def test_forwarded_exceptions_carry_type_and_masked_stack() -> None:
    """Found 17 Nov: `log.exception` reached SigNoz as `exc_info=True` only, without the error or the stack."""
    from arifaa_pack.telemetry import log_forwarder

    sent: list[dict[str, Any]] = []

    class Otel:
        def emit(self, **kw: Any) -> None:
            sent.append(kw)

    try:
        raise LookupError("no manager for +971501234567")  # noqa: TRY301
    except LookupError:
        log_forwarder(Otel())(None, "error", {"event": "agents.handover_pause_failed", "exc_info": True})
    json_style = [
        {
            "exc_type": "ValueError",
            "exc_value": "bad",
            "frames": [{"filename": "a.py", "lineno": 3, "name": "f"}],
        }
    ]
    log_forwarder(Otel())(None, "error", {"event": "x", "exception": json_style})
    first, second = (s["attributes"] for s in sent)
    assert first["exception.type"] == "LookupError"
    assert first["exception.message"] == "no manager for +971*****67"
    assert "test_forwarded_exceptions" in first["exception.stacktrace"] and "exc_info" not in first
    assert second == {
        "exception.type": "ValueError",
        "exception.message": "bad",
        "exception.stacktrace": 'File "a.py", line 3, in f',
    }


def test_gemini_standby_joins_after_claude_only_when_switched_on() -> None:
    """18 Nov (client decision): Gemini 3.8 Flash answers when every Claude call fails; it needs a paid key."""
    from pydantic import SecretStr

    from arifaa_pack.config import AppSettings
    from arifaa_pack.container import _model_router
    from kx.agents.registry import ModelTier
    from kx.core.platform import resolve_modes

    keys = AppSettings(anthropic_api_key=SecretStr("a"), gemini_api_key=SecretStr("g"))
    on = _model_router(keys, resolve_modes("free", {}, llm_standby="gemini"), {"claude", "gemini"})
    chain = on.for_tier(ModelTier.FAST)
    from langchain_anthropic import ChatAnthropic

    assert isinstance(chain.primary, ChatAnthropic)  # our subclass with its own HTTP pool (threads pool)
    assert [type(m).__name__ for m in chain.fallbacks] == ["ChatGoogleGenerativeAI"]
    off = _model_router(keys, resolve_modes("free", {}), {"claude", "gemini"})
    assert off.for_tier(ModelTier.FAST).fallbacks == ()
    with pytest.raises(RuntimeError, match="KX_APP_GEMINI_API_KEY"):
        _model_router(
            AppSettings(anthropic_api_key=SecretStr("a")),
            resolve_modes("free", {}, llm_standby="gemini"),
            {"claude", "gemini"},
        )
