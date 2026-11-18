"""The one Celery app (11 Nov, D25) and the `kx.core.jobs.Kicker` over it.

Imported by every process: the API and the worker kick due jobs through it; Beat sends the timers. It does
not import the container (the worker builds that lazily in `arifaa_pack.celery_app`), so there is no cycle.

Settings from the platform docs and Celery's own guides (Official sources, 11 Nov):
* `acks_late` + `task_reject_on_worker_lost`: a task is re-delivered if a worker dies, so every job body is
  idempotent and every send checks its idempotency key;
* prefetch 1: a long PDF job never holds short chat replies hostage;
* `visibility_timeout` above the longest task; long waits are rows in PostgreSQL, never ETAs;
* Redis `noeviction` is set on the ElastiCache parameter group (infra), not here;
* D-6 (live test 18 Nov): AI turns (`agents.handle_event`) have their own queue `kx.ai`, so a 30 ms send
  never waits behind multi-second model calls in one FIFO queue (head-of-line blocking);
* D-7: a job kicked over a minute ago is resent only when its queue is empty (the message was lost); while
  the queue still holds messages it is waiting, and resending it only piled up duplicates.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Sequence
from functools import lru_cache
from typing import Final

from celery import Celery

from arifaa_pack.config import app_settings
from kx.core.jobs import Kick
from kx.core.settings import get_settings

RUN_JOB: Final = "kx.run_job"
QUEUE: Final = "kx"
AI_QUEUE: Final = "kx.ai"
AI_TASKS: Final = frozenset({"agents.handle_event"})  # the buyer turns that call the model
ALL_QUEUES: Final = (QUEUE, AI_QUEUE)


def queue_for(task: str) -> str:
    return AI_QUEUE if task in AI_TASKS else QUEUE


@lru_cache(maxsize=1)
def celery_app() -> Celery:
    s = app_settings()
    broker = s.celery_broker_url or str(get_settings().redis_url)
    app = Celery("kairox", broker=broker)
    app.conf.update(
        task_default_queue=QUEUE,
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,
        task_serializer="json",
        accept_content=["json"],
        result_backend=None,
        task_ignore_result=True,
        timezone="UTC",
        enable_utc=True,
        broker_connection_retry_on_startup=True,
        broker_transport_options={"visibility_timeout": s.celery_visibility_timeout_seconds},
        worker_hijack_root_logger=False,
    )
    return app


class CeleryKicker:
    """Sends `kx.run_job(<id>)` for each due job on its task's queue. Runs the blocking publish in a thread
    so the API event loop never waits on Redis; a broker failure raises and the store leaves the rows for the
    next sweep."""

    def __init__(self, app: Celery | None = None) -> None:
        self._app = app or celery_app()

    async def kick(self, jobs: Sequence[Kick], *, resend: bool = False) -> list[uuid.UUID]:
        return await asyncio.to_thread(self._send, list(jobs), resend)

    def _send(self, jobs: list[Kick], resend: bool) -> list[uuid.UUID]:
        by_queue: dict[str, list[Kick]] = {}
        for job in jobs:
            by_queue.setdefault(queue_for(job.task), []).append(job)
        sent: list[uuid.UUID] = []
        for queue, batch in by_queue.items():
            if resend and self.waiting(queue) > 0:
                continue  # D-7: still in the queue, not lost
            for job in batch:
                self._app.send_task(
                    RUN_JOB,
                    args=[str(job.job_id)],
                    queue=queue,
                    retry=True,
                    retry_policy={"max_retries": 1, "interval_start": 0, "interval_step": 0.2},
                )
                sent.append(job.job_id)
        return sent

    def waiting(self, queue: str) -> int:
        """Messages waiting in `queue` (not yet taken by a worker)."""
        with self._app.connection_for_read() as conn:
            return int(conn.default_channel.queue_declare(queue=queue, passive=True).message_count)
