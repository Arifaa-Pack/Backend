"""Platform metrics through the OpenTelemetry API (D46): exported to SigNoz by the app's meter provider.

`kx.core` depends only on `opentelemetry-api`; until the app installs a meter provider
(`arifaa_pack.telemetry`) every call is a cheap no-op, so tests and scripts need nothing. Nothing is written
to stdout (the old CloudWatch EMF lines crashed under Celery, whose stdout proxy has no `.buffer`: bench R1).

The metric names are unchanged (OpenTelemetry stores them lowercase: `vendorswitch` …); SigNoz alert rules
watch them:
* `QueueLagSeconds` — age of the oldest due job in the schedule table (D25), a gauge;
* `VendorSwitch` — a field fell back to its next vendor (D30 / D41), a counter;
* `SpendCap` — a daily spend cap crossed 80 % or 100 % (D34; the value is the percentage), a gauge.
Attributes: `company` plus the extra keyword `properties` (field, from_vendor, to_vendor …). No buyer data.
"""

from __future__ import annotations

from typing import Any, Final, Literal

from opentelemetry import metrics

METER_NAME: Final = "kairox"
Unit = Literal["Count", "Seconds", "Milliseconds", "None", "Percent"]
_UCUM: Final[dict[str, str]] = {
    "Count": "1",
    "Seconds": "s",
    "Milliseconds": "ms",
    "None": "1",
    "Percent": "%",
}

_instruments: dict[str, Any] = {}


def _instrument(name: str, unit: Unit) -> Any:
    """Counters for counts, gauges for levels (lag, percentages). Created lazily and cached: the global meter
    provider may be installed after import, and the API proxies to it once it is."""
    if (inst := _instruments.get(name)) is None:
        meter = metrics.get_meter(METER_NAME)
        if unit == "Count":
            inst = meter.create_counter(name, unit=_UCUM[unit])
        else:
            inst = meter.create_gauge(name, unit=_UCUM[unit])
        _instruments[name] = inst
    return inst


def emit_metric(name: str, value: float, *, company: str, unit: Unit = "Count", **properties: str) -> None:
    """Record one data point. Never raises: a metrics problem must not break a send or a job."""
    try:
        inst = _instrument(name, unit)
        attributes = {"company": company, **properties}
        if unit == "Count":
            inst.add(value, attributes)
        else:
            inst.set(value, attributes)
    except Exception:  # noqa: S110 - telemetry is best effort by design
        pass
