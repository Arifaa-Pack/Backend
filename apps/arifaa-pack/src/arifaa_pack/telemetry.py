"""Monitoring wiring (16 Nov, D46): OpenTelemetry → SigNoz on our server, UptimeRobot from outside.

* **Metrics and traces:** `init_telemetry(component)` installs the OpenTelemetry SDK meter and tracer
providers
  with the OTLP/HTTP exporter (`KX_APP_OTLP_ENDPOINT`, e.g. http://signoz-otel-collector:4318). The platform
  writes metrics through the API only (`kx.core.metrics`), so nothing else changes. No-op without an endpoint.
* **Logs** stay PII-masked JSON on stdout (`kx.core.logging`); SigNoz's collector reads the container log
  files, so no log shipping code exists here.
* **AI audit trail** (Phoenix or Langfuse) has its own exporter in `kx.agents.tracing`.
* **Beat heartbeat:** every 30 s a worker writes the time and the queue lag to Redis; `GET /healthz/beat`
  answers 503 when the last beat is older than 2 minutes or the lag is too long, and UptimeRobot alerts on it.
  A fresh beat proves Beat, the broker and a worker are all alive (Fallback plan, "Background jobs").
"""

from __future__ import annotations

import time
from typing import Any, Final

from arifaa_pack.config import AppSettings
from kx.core.logging import get_logger
from kx.core.metrics import emit_metric

log = get_logger(__name__)

BEAT_STALE_SECONDS: Final = 120.0  # Fallback plan: Beat heartbeat missing 2 min = alert
_installed: dict[str, bool] = {}


def init_telemetry(s: AppSettings, *, component: str, company: str, environment: str) -> bool:
    """Install the OTel SDK providers once per process. Returns False (and does nothing) without an
    endpoint."""
    if not s.otlp_endpoint or _installed.get("done"):
        return False
    from opentelemetry import metrics, trace  # noqa: PLC0415
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter  # noqa: PLC0415
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter  # noqa: PLC0415
    from opentelemetry.sdk.metrics import MeterProvider  # noqa: PLC0415
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader  # noqa: PLC0415
    from opentelemetry.sdk.resources import Resource  # noqa: PLC0415
    from opentelemetry.sdk.trace import TracerProvider  # noqa: PLC0415
    from opentelemetry.sdk.trace.export import BatchSpanProcessor  # noqa: PLC0415

    base = s.otlp_endpoint.rstrip("/")
    resource = Resource.create(
        {
            "service.name": f"kx-{component}",
            "service.namespace": company,  # one SigNoz, many projects later (docs/MULTI_PROJECT.md)
            "deployment.environment": environment,
        }
    )
    reader = PeriodicExportingMetricReader(OTLPMetricExporter(endpoint=f"{base}/v1/metrics"), 30_000)
    metrics.set_meter_provider(MeterProvider(resource=resource, metric_readers=[reader]))
    tracer = TracerProvider(resource=resource)
    tracer.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{base}/v1/traces")))
    trace.set_tracer_provider(tracer)
    _installed["done"] = True
    log.info("telemetry.installed", component=component, endpoint=base)
    return True


def _beat_key(company: str) -> str:
    return f"kx:{company}:beat:last"


async def heartbeat(redis: Any, company: str, lag_seconds: float) -> None:
    """Beat's 30-second heartbeat, run on a worker: record it in Redis and publish `QueueLagSeconds`."""
    emit_metric("QueueLagSeconds", lag_seconds, company=company, unit="Seconds")
    try:
        await redis.hset(
            _beat_key(company), mapping={"at": f"{time.time():.3f}", "lag": f"{lag_seconds:.3f}"}
        )
    except Exception:
        log.warning(
            "heartbeat.redis_unavailable", exc_info=True
        )  # /healthz/beat goes stale: UptimeRobot alerts


async def beat_health(redis: Any, company: str, max_lag_seconds: float) -> tuple[bool, dict[str, float]]:
    """(healthy, detail) for `/healthz/beat`."""
    try:
        raw = await redis.hgetall(_beat_key(company))
    except Exception:
        return False, {}
    values = {(k.decode() if isinstance(k, bytes) else k): float(v) for k, v in raw.items()}
    age = time.time() - values.get("at", 0.0)
    lag = values.get("lag", 0.0)
    detail = {"age_seconds": round(age, 1), "lag_seconds": round(lag, 1)}
    return age <= BEAT_STALE_SECONDS and lag <= max_lag_seconds, detail
