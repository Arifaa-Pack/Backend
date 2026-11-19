"""Defects found by the live test of 18 Nov (`live-test/TEST_REPORT.md`), pinned with no database."""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from typing import Any

import pytest
from pydantic import SecretStr

from arifaa_pack.container import check_secrets, quote_pdf_store
from kx.automation._shared.media import S3MediaStore
from kx.core.settings import Environment
from kx.crm.quotes.pdf import S3PdfUploader, pdf_key


class FakeS3:
    """In-memory S3 that fails like R2 does: an unknown key raises (NoSuchKey -> 404)."""

    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}

    async def put_object(self, *, Bucket: str, Key: str, Body: bytes, **_: Any) -> dict[str, Any]:  # noqa: N803
        self.objects[Bucket, Key] = Body
        return {}

    async def get_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:  # noqa: N803
        data = self.objects[Bucket, Key]

        class Body:
            async def read(self) -> bytes:
                return data

        @contextlib.asynccontextmanager
        async def body() -> AsyncIterator[Body]:
            yield Body()

        return {"Body": body()}


async def test_the_sender_reads_the_quote_pdf_the_crm_uploaded() -> None:
    """D-2: the CRM wrote `pack/quotes/...`, the sender read `media/pack/quotes/...` -> 404, so no approved
    quote was ever sent. Both sides must use the same key."""
    s3 = FakeS3()
    key = pdf_key("pack", "Q-2026-00001", 1, "ab" * 32)
    await S3PdfUploader(s3, "bucket").upload(key, b"%PDF-1.7 quote")
    assert await quote_pdf_store(s3, "bucket").get(key) == b"%PDF-1.7 quote"


async def test_inbound_media_keeps_its_own_prefix() -> None:
    s3 = FakeS3()
    await S3MediaStore(s3, "bucket", prefix="media/").put("wa/123.jpg", b"img", "image/jpeg")
    assert ("bucket", "media/wa/123.jpg") in s3.objects


@pytest.mark.parametrize("env", [Environment.STAGING, Environment.PRODUCTION])
@pytest.mark.parametrize("value", ["dev-only-change-me", "CHANGE", "short-but-random-1234"])
def test_production_refuses_default_or_short_signing_secrets(env: Environment, value: str) -> None:
    """M1: the code defaults let anyone forge website-session, unsubscribe and Telnyx callback tokens."""
    with pytest.raises(RuntimeError, match="KX_SERVICE_TOKEN_SECRET"):
        check_secrets(env, {"KX_SERVICE_TOKEN_SECRET": SecretStr(value)})


def test_strong_secrets_start_and_local_only_warns() -> None:
    check_secrets(Environment.PRODUCTION, {"KX_SERVICE_TOKEN_SECRET": SecretStr("a1" * 16)})
    check_secrets(Environment.LOCAL, {"KX_SERVICE_TOKEN_SECRET": SecretStr("dev-only-change-me")})


def test_ai_turns_have_their_own_queue() -> None:
    """D-6 (live test 18 Nov): one FIFO queue made a 30 ms send wait behind multi-second AI turns."""
    from arifaa_pack.container import AGENTS_EVENT, CRM_EVENT
    from arifaa_pack.jobqueue import AI_QUEUE, QUEUE, queue_for
    from kx.automation.jobs import EVENT as AUTOMATION_EVENT

    assert queue_for(AGENTS_EVENT) == AI_QUEUE
    assert {queue_for(t) for t in (CRM_EVENT, AUTOMATION_EVENT, "automation.send")} == {QUEUE}


def test_a_resend_is_skipped_while_its_queue_still_holds_messages() -> None:
    """D-7: the sweep resent every unclaimed job each 30 s under a backlog, piling up duplicates."""
    import uuid

    from arifaa_pack.jobqueue import AI_QUEUE, CeleryKicker
    from kx.core.jobs import Kick

    class App:
        def __init__(self) -> None:
            self.sent: list[tuple[str, str]] = []

        def send_task(self, name: str, *, args: list[str], queue: str, **_: Any) -> None:
            self.sent.append((queue, args[0]))

    class Kicker(CeleryKicker):
        def waiting(self, queue: str) -> int:
            return 3 if queue == AI_QUEUE else 0

    app = App()
    kicker = Kicker(app)  # type: ignore[arg-type]
    ai, send = Kick(uuid.uuid4(), "agents.handle_event"), Kick(uuid.uuid4(), "automation.send")
    assert kicker._send([ai, send], resend=True) == [send.job_id]  # the AI queue is busy, not lost
    assert kicker._send([ai], resend=False) == [ai.job_id]  # a first kick always goes
    assert ("kx.ai", str(ai.job_id)) in app.sent and ("kx", str(send.job_id)) in app.sent


def test_an_empty_broker_queue_counts_as_empty_not_as_an_error() -> None:
    """Live test 19 Nov: Redis deletes an empty list and the passive declare raised NOT_FOUND, so every resend
    of a lost job failed and the job stayed due for hours. A missing queue is an empty queue."""
    from celery import Celery

    from arifaa_pack.jobqueue import CeleryKicker

    app = Celery("t", broker="memory://")
    assert CeleryKicker(app).waiting("kx-never-declared") == 0
