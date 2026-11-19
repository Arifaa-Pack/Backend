"""Celery worker tasks (11 Nov, D25). `celery -A arifaa_pack.celery_app worker` / `beat` (via `kx worker`,
`kx beat`).

Platform contest round 4 conditions, as built here:
* **one event loop per worker process or thread** — the async platform code runs on a loop created (with
  the container) on the first task and reused for every task after it. With `--pool threads` (the AI-turn
  worker, D-6) each thread has its own loop and container: model calls wait on the network, so threads serve
  many turns at once without a 250 MB process each;
* **idempotent tasks with acks_late** — `kx.run_job` claims its row atomically, so a redelivered message
  for a job already done, running or cancelled is a no-op;
* **exactly one Beat** — enforced by the deployment (one `beat` container, stop-then-start), not by code;
* **long waits in PostgreSQL** — Beat never schedules ETAs; it only sends `kx.periodic(<name>)`.
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
import uuid
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar, Final, TypeVar

from celery import signals

from arifaa_pack.config import app_settings
from arifaa_pack.jobqueue import RUN_JOB, celery_app
from arifaa_pack.schedule import PERIODIC_TASK, beat_schedule
from kx.core.logging import configure_logging, get_logger
from kx.core.settings import get_settings

T = TypeVar("T")
log = get_logger(__name__)
app = celery_app()
app.conf.beat_schedule = beat_schedule()


class _State:
    """One worker thread's loop and container (for the life of the thread; prefork: one per process)."""

    def __init__(self) -> None:
        from arifaa_pack.container import build  # noqa: PLC0415 - heavy; only in worker processes

        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.stack = AsyncExitStack()
        self.container = self.loop.run_until_complete(self.stack.enter_async_context(build()))

    def close(self) -> None:
        self.loop.run_until_complete(self.stack.aclose())
        self.loop.close()


class _Process:
    """Per-thread state (prefork has one thread per process, so it is the process's)."""

    _local = threading.local()
    _all: ClassVar[list[_State]] = []
    _lock = threading.Lock()

    @classmethod
    def run(cls, work: Callable[[Any], Awaitable[T]]) -> T:
        state: _State | None = getattr(cls._local, "state", None)
        if state is None:
            _configure()
            try:
                state = cls._local.state = _State()
            except Exception:
                _health_mark(RUNTIME_FAILED)  # the container health check turns unhealthy (D-1, 18 Nov)
                raise
            with cls._lock:
                cls._all.append(state)
        result = state.loop.run_until_complete(work(state.container))
        _health_mark(RUNTIME_OK)
        return result

    @classmethod
    def close(cls) -> None:
        with cls._lock:
            states, cls._all = cls._all, []
        for state in states:
            try:
                state.close()
            except Exception:  # shutting down: close the others anyway
                log.warning("worker.close_failed", exc_info=True)
        cls._local = threading.local()


# Live test 18 Nov (D-1): the worker reported healthy while every job failed because its runtime could not be
# built. docker/healthcheck.py reads these two files: the newer one wins.
HEALTH_DIR: Final = Path(os.environ.get("KX_HEALTH_DIR", "/tmp/kx-health"))  # noqa: S108 - tmpfs, ours only
RUNTIME_OK: Final = "runtime_ok"
RUNTIME_FAILED: Final = "runtime_failed"
OK_MARK_EVERY_SECONDS: Final = 10.0
_last_mark: dict[str, float] = {}


def _health_mark(name: str) -> None:
    """Write the time to HEALTH_DIR/name (a success at most every 10 s; never fails a task)."""
    now = time.time()
    if name == RUNTIME_OK and now - _last_mark.get(name, 0.0) < OK_MARK_EVERY_SECONDS:
        return
    _last_mark[name] = now
    try:
        HEALTH_DIR.mkdir(parents=True, exist_ok=True)
        (HEALTH_DIR / name).write_text(f"{now:.3f}")
    except OSError:
        log.warning("worker.health_mark_failed", name=name)


_configured = threading.Event()
_configure_lock = threading.Lock()


@signals.worker_process_init.connect
def _configure(**_: Any) -> None:
    """Logging and telemetry once per process: the prefork child signal, or the first task of a thread pool
    (which sends no process signal)."""
    with _configure_lock:
        if _configured.is_set():
            return
        core = get_settings()
        configure_logging(core.log_level, core.log_json)
        from arifaa_pack.telemetry import init_telemetry, instrument_celery  # noqa: PLC0415

        init_telemetry(
            app_settings(), component="worker", company=core.company_code, environment=core.env.value
        )
        instrument_celery()  # job spans, joined to the trace of the request that queued them
        _configured.set()


@signals.beat_init.connect
def _configure_beat(**_: Any) -> None:
    """Beat's own logs and spans (live test 18 Nov: Beat sent nothing to SigNoz). Each `kx.periodic` it sends
    becomes a producer span, joined to the worker span that runs it."""
    core = get_settings()
    configure_logging(core.log_level, core.log_json)
    from arifaa_pack.telemetry import init_telemetry, instrument_celery  # noqa: PLC0415

    init_telemetry(app_settings(), component="beat", company=core.company_code, environment=core.env.value)
    instrument_celery()
    log.info("beat.started", entries=len(app.conf.beat_schedule or {}))


@signals.worker_process_shutdown.connect
@signals.worker_shutdown.connect
def _shutdown(**_: Any) -> None:
    _Process.close()


@app.task(name=RUN_JOB, acks_late=True, ignore_result=True)
def run_job(job_id: str) -> str:
    """Run one row of automation.scheduled_jobs (claim -> handler -> finish / retry)."""
    return str(_Process.run(lambda c: c.runner.run(uuid.UUID(job_id))))


@app.task(name=PERIODIC_TASK, acks_late=True, ignore_result=True)
def periodic(name: str) -> None:
    """One Beat entry. Unknown names are logged, not retried (a renamed job must not loop)."""

    async def work(c: Any) -> None:
        fn = c.periodic.get(name)
        if fn is None:
            log.error("periodic.unknown", name=name)
            return
        await fn(datetime.now(UTC))

    _Process.run(work)
