"""Defects found by the live test of 18 Nov (`live-test/TEST_REPORT.md`), pinned with no database."""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from typing import Any

from arifaa_pack.container import quote_pdf_store
from kx.automation.media import S3MediaStore
from kx.crm.pdf import S3PdfUploader, pdf_key


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
