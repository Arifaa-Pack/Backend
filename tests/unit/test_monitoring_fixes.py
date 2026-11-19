"""Monitoring gaps of the live test (18 Nov, TEST_REPORT §10), pinned with no database: picklable
ChannelError, worker health that sees a broken runtime, ERROR logs inside the trace, Beat telemetry."""

from __future__ import annotations

import importlib.util
import pathlib
import pickle
from types import ModuleType
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from opentelemetry.sdk.trace import TracerProvider
from structlog.testing import capture_logs

import arifaa_pack.celery_app as worker
from arifaa_pack.errorlog import LogUnhandled
from kx.automation.channels import ChannelError
from kx.core.logging import _trace_processor

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _healthcheck() -> ModuleType:
    spec = importlib.util.spec_from_file_location("kx_healthcheck", ROOT / "docker" / "healthcheck.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_channel_error_survives_pickling_with_its_cause() -> None:
    err = ChannelError("stalwart", "mailbox still in bootstrap", retryable=True, status=503)
    back = pickle.loads(pickle.dumps(err))  # what Celery does with a task's exception
    assert isinstance(back, ChannelError) and str(back) == "stalwart: mailbox still in bootstrap"
    assert (back.provider, back.detail, back.retryable, back.status) == ("stalwart", err.detail, True, 503)


def test_worker_health_follows_the_newest_mark(tmp_path: pathlib.Path) -> None:
    hc = _healthcheck()
    assert hc.worker_health(tmp_path) == 0  # nothing yet (idle, just started): healthy
    (tmp_path / "runtime_failed").write_text("100.0")
    assert hc.worker_health(tmp_path) == 1  # the runtime could not be built (D-1): unhealthy
    (tmp_path / "runtime_ok").write_text("200.0")
    assert hc.worker_health(tmp_path) == 0  # a task finished after that: healthy again


def test_the_worker_marks_a_runtime_that_cannot_be_built(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(worker, "HEALTH_DIR", tmp_path)
    monkeypatch.setattr(worker, "_configure", lambda **_: None)
    monkeypatch.setattr(worker._Process, "_local", type(worker._Process._local)())

    class Broken:
        def __init__(self) -> None:
            raise RuntimeError("no database")

    monkeypatch.setattr(worker, "_State", Broken)

    async def work(_: Any) -> None:
        return None

    with pytest.raises(RuntimeError, match="no database"):
        worker._Process.run(work)
    assert (tmp_path / "runtime_failed").exists() and not (tmp_path / "runtime_ok").exists()
    assert _healthcheck().worker_health(tmp_path) == 1


def test_a_log_line_inside_a_span_carries_its_trace() -> None:
    tracer = TracerProvider().get_tracer("test")
    with tracer.start_as_current_span("request") as span:
        event = _trace_processor(None, "error", {"event": "api.unhandled"})
        ctx = span.get_span_context()
    assert event["trace_id"] == format(ctx.trace_id, "032x") and event["span_id"] == format(
        ctx.span_id, "016x"
    )
    assert "trace_id" not in _trace_processor(None, "info", {"event": "outside"})


async def test_an_unhandled_error_is_logged_and_answered_as_a_plain_500() -> None:
    app = FastAPI()
    app.add_middleware(LogUnhandled)  # as in arifaa_pack.api: inside FastAPI's own error middleware

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("secret detail that must not leave the server")

    with capture_logs() as logs:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            r = await c.get("/boom")
    assert r.status_code == 500 and r.headers["content-type"] == "application/problem+json"
    assert "secret" not in r.text
    assert [entry["event"] for entry in logs] == ["api.unhandled"] and logs[0]["path"] == "/boom"


def test_beat_sets_up_its_own_logging_and_telemetry() -> None:
    worker._configure_beat()  # no OTLP endpoint in tests: telemetry is a no-op, nothing raises
