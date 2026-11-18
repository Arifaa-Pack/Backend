"""Monitoring wiring (16 Nov, D46): OpenTelemetry → SigNoz on our server, UptimeRobot from outside.

* **Metrics, traces and logs:** `init_telemetry(component)` installs the OpenTelemetry SDK meter, tracer and
  logger providers with the OTLP/HTTP exporter (`KX_APP_OTLP_ENDPOINT`, e.g. http://signoz-otel-collector:4318).
  The platform writes metrics through the API only (`kx.core.metrics`). No-op without an endpoint.
* **Traces (17 Nov):** HTTP spans for every API route except the health checks (`instrument_api`) and Celery
  spans for publishing and running jobs (`instrument_celery`), so one trace follows a buyer's message from the
  webhook through the job to the reply.
* **Logs (17 Nov):** app logs stay PII-masked JSON on stdout (`kx.core.logging`) and are ALSO sent to SigNoz,
  after masking, with the trace id of the request or job they belong to; library warnings and errors (uvicorn,
  Celery, SQLAlchemy) go the same way. Host and container metrics and the other containers' logs are
  collected by the SigNoz collector itself (`infra/server/signoz/`).
* **AI audit trail** (Phoenix or Langfuse) has its own exporter in `kx.agents.tracing`.
* **Beat heartbeat:** every 30 s a worker writes the time and the queue lag to Redis; `GET /healthz/beat`
  answers 503 when the last beat is older than 2 minutes or the lag is too long, and UptimeRobot alerts on it.
  A fresh beat proves Beat, the broker and a worker are all alive (Fallback plan, "Background jobs").
"""

from __future__ import annotations

import logging
import time
from typing import Any, Final

import structlog

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
    _install_logs(base, resource)
    _installed["done"] = True
    log.info("telemetry.installed", component=component, endpoint=base)
    return True


def installed() -> bool:
    return bool(_installed.get("done"))


_SEVERITY: Final = {
    "debug": 5,
    "info": 9,
    "warning": 13,
    "warn": 13,
    "error": 17,
    "critical": 21,
    "exception": 17,
}
_SKIP_KEYS: Final = frozenset({"event", "level", "timestamp"})
_EXC_KEYS: Final = frozenset({"exc_info", "exception"})
_MAX_STACK: Final = 8000


def _exception_attrs(event: Any) -> dict[str, str]:
    """`log.exception(...)` / `exc_info=True` as the OpenTelemetry exception.* attributes, so SigNoz shows the
    error type and stack trace (found 17 Nov: only `exc_info=True` arrived). Masked like the rest of the line.
    Console logs carry `exc_info`; JSON logs carry the `exception` list of `dict_tracebacks`."""
    import sys  # noqa: PLC0415
    import traceback  # noqa: PLC0415

    from kx.core.logging import mask_pii  # noqa: PLC0415

    exc = event.get("exc_info")
    if exc is True:
        exc = sys.exc_info()[1]
    elif isinstance(exc, tuple):
        exc = exc[1]
    if isinstance(exc, BaseException):
        stack = "".join(traceback.format_exception(exc))
        kind, message = type(exc).__qualname__, str(exc)
    elif isinstance(dicts := event.get("exception"), list) and dicts and isinstance(dicts[-1], dict):
        last = dicts[-1]
        kind, message = str(last.get("exc_type", "")), str(last.get("exc_value", ""))
        stack = "\n".join(
            f'File "{f.get("filename")}", line {f.get("lineno")}, in {f.get("name")}'
            for d in dicts
            for f in d.get("frames", ())
        )
    else:
        return {}
    return {
        "exception.type": kind,
        "exception.message": mask_pii(message)[:_MAX_STACK],
        "exception.stacktrace": mask_pii(stack)[-_MAX_STACK:],
    }


def _install_logs(base: str, resource: Any) -> None:
    """App logs (structlog) and library warnings / errors (stdlib logging) to SigNoz over OTLP/HTTP."""
    from opentelemetry import _logs  # noqa: PLC0415
    from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter  # noqa: PLC0415
    from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler  # noqa: PLC0415
    from opentelemetry.sdk._logs.export import BatchLogRecordProcessor  # noqa: PLC0415

    provider = LoggerProvider(resource=resource)
    provider.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter(endpoint=f"{base}/v1/logs")))
    _logs.set_logger_provider(provider)
    handler = LoggingHandler(level=logging.WARNING, logger_provider=provider)
    handler.addFilter(_library_record)
    logging.getLogger().addHandler(handler)
    processors = list(structlog.get_config()["processors"])
    # just before the JSON / console renderer, i.e. after the PII mask: stdout is unchanged
    processors.insert(len(processors) - 1, log_forwarder(provider.get_logger("kx")))
    structlog.configure(processors=processors)


def _library_record(record: logging.LogRecord) -> bool:
    """Forward library records, but not the exporter's own complaints (a loop when the collector is down) and
    not Celery's copy of stdout (`celery.redirected`: our structlog lines again, already forwarded)."""
    return not (record.name.startswith("opentelemetry") or record.name == "celery.redirected")


def log_forwarder(otel: Any) -> Any:
    """A structlog processor that also emits each (already masked) event as an OpenTelemetry log record. The
    current span's trace id is attached by the SDK, so SigNoz links the line to its request or job."""
    from opentelemetry import _logs  # noqa: PLC0415

    def forward(_: Any, __: str, event: Any) -> Any:
        try:
            level = str(event.get("level", "info")).lower()
            attrs = {
                k: v if isinstance(v, str | int | float | bool) else str(v)
                for k, v in event.items()
                if k not in _SKIP_KEYS and k not in _EXC_KEYS and v is not None
            }
            attrs.update(_exception_attrs(event))
            otel.emit(
                severity_number=_logs.SeverityNumber(_SEVERITY.get(level, 9)),
                severity_text=level.upper(),
                body=str(event.get("event", "")),
                attributes=attrs,
            )
        except Exception:  # noqa: S110 - never let telemetry break a log line (and never log from here: a loop)
            pass
        return event

    return forward


def instrument_api(app: Any) -> None:
    """HTTP server spans for the API (health checks excluded: UptimeRobot calls them every minute) and Celery
    producer spans, so a request and the job it starts share one trace."""
    if not installed():
        return
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor  # noqa: PLC0415

    FastAPIInstrumentor.instrument_app(app, excluded_urls="healthz")
    instrument_celery()


def instrument_celery() -> None:
    """Celery spans: publishing (api, beat) and running (worker) `kx.run_job` / `kx.periodic`."""
    if not installed() or _installed.get("celery"):
        return
    from opentelemetry.instrumentation.celery import CeleryInstrumentor  # noqa: PLC0415

    CeleryInstrumentor().instrument()  # type: ignore[no-untyped-call]  # the package ships no annotations
    _installed["celery"] = True


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
