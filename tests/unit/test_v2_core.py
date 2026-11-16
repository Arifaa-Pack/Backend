"""16 Nov v2 core pieces: OpenTelemetry metrics (D46), phone parsing without exceptions."""

from __future__ import annotations

import io
import sys

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
