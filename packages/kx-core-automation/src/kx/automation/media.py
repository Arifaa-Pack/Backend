"""Object store for quote PDFs, inbound WhatsApp media and raw inbound email (D45: Cloudflare R2 through the
S3 API; R2 encrypts at rest, so no SSE / KMS parameters are sent)."""

from __future__ import annotations

from typing import Any, Protocol


class S3Client(Protocol):
    """The slice of an aioboto3 `s3` client we use (created once in the app lifespan, `endpoint_url` = R2)."""

    async def get_object(self, **kwargs: Any) -> dict[str, Any]: ...
    async def put_object(self, **kwargs: Any) -> dict[str, Any]: ...


class S3MediaStore:
    """`MediaStore` over one bucket; `prefix` keeps uses apart in that bucket (`media/`, `inbound-mail/`)."""

    def __init__(self, s3: S3Client, bucket: str, prefix: str = "") -> None:
        self._s3 = s3
        self.bucket = bucket
        self._prefix = prefix

    def _key(self, key: str) -> str:
        return f"{self._prefix}{key}" if self._prefix and not key.startswith(self._prefix) else key

    async def get(self, key: str) -> bytes:
        response = await self._s3.get_object(Bucket=self.bucket, Key=self._key(key))
        async with response["Body"] as stream:
            data: bytes = await stream.read()
        return data

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        await self._s3.put_object(Bucket=self.bucket, Key=self._key(key), Body=data, ContentType=content_type)
