"""The media library (SRS §7): photos, MP4 videos, PDFs and 3D model files in Cloudflare R2 (D45).

Uploads go straight from the editor's browser to R2 with a short-lived signed URL, so large files never pass
through the API (its body limit stays small). Two steps:

1. `POST /v1/cms/media/uploads` with the file's name, type, size and SHA-256: the API answers the signed URL.
   The object key is content-addressed (`cms/media/<sha256>.<ext>`), so the same file uploaded twice is one
   asset, and a changed file is a new URL (no cache can ever serve a stale photo).
2. `POST /v1/cms/media/{id}/confirm` once the browser's PUT succeeded: the API checks the object is there.

Large factory films can also be a YouTube / Vimeo link (`MediaRef.url`) instead of a file.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Final, Protocol, runtime_checkable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kx.cms._shared.journal import record
from kx.cms.models import MediaAsset
from kx.core.errors import NotFound, RuleViolation, Unavailable
from kx.core.identity import new_id

ALLOWED: Final[dict[str, str]] = {
    "image/webp": "webp",
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/avif": "avif",
    "image/svg+xml": "svg",
    "video/mp4": "mp4",
    "video/webm": "webm",
    "application/pdf": "pdf",
    "model/gltf-binary": "glb",
}
MAX_BYTES: Final[dict[str, int]] = {
    "image": 15_000_000,
    "video": 300_000_000,
    "application": 30_000_000,
    "model": 50_000_000,
}
_SHA256: Final = re.compile(r"^[0-9a-f]{64}$")


@runtime_checkable
class MediaStorage(Protocol):
    """Implemented by the app over the R2 (S3 API) client."""

    async def presign_put(self, key: str, content_type: str, size: int) -> tuple[str, dict[str, str]]: ...
    async def exists(self, key: str) -> bool: ...


@dataclass(frozen=True, slots=True)
class UploadTicket:
    asset_id: str
    object_key: str
    upload_url: str | None  # None: this file is already in the library
    headers: dict[str, str]
    already_uploaded: bool


def object_key(sha256: str, content_type: str) -> str:
    return f"cms/media/{sha256}.{ALLOWED[content_type]}"


def check_file(content_type: str, size: int, sha256: str) -> None:
    if content_type not in ALLOWED:
        raise RuleViolation(f"file type {content_type} is not accepted", reason="cms_media_type")
    limit = MAX_BYTES[content_type.split("/", 1)[0]]
    if not 0 < size <= limit:
        raise RuleViolation(
            f"files of this type may be at most {limit // 1_000_000} MB", reason="cms_media_size"
        )
    if not _SHA256.match(sha256):
        raise RuleViolation("sha256 must be 64 lower-case hex characters", reason="cms_media_hash")


async def start_upload(
    s: AsyncSession,
    storage: MediaStorage | None,
    *,
    filename: str,
    content_type: str,
    size: int,
    sha256: str,
    alt: dict[str, str],
    actor: str,
) -> UploadTicket:
    check_file(content_type, size, sha256)
    existing = (await s.execute(select(MediaAsset).where(MediaAsset.sha256 == sha256))).scalar_one_or_none()
    if existing is not None and existing.confirmed:
        return UploadTicket(str(existing.id), existing.object_key, None, {}, already_uploaded=True)
    if storage is None:
        raise Unavailable("media storage is not configured (KX_STORAGE__*)")
    key = object_key(sha256, content_type)
    url, headers = await storage.presign_put(key, content_type, size)
    if existing is None:
        existing = MediaAsset(
            id=new_id(),
            object_key=key,
            sha256=sha256,
            filename=filename[:300],
            content_type=content_type,
            size_bytes=size,
            alt=alt,
            created_by=actor,
        )
        s.add(existing)
        await s.flush()
    return UploadTicket(str(existing.id), key, url, headers, already_uploaded=False)


async def _asset(s: AsyncSession, asset_id: str) -> MediaAsset:
    try:
        row = await s.get(MediaAsset, uuid.UUID(asset_id))
    except ValueError:
        row = None
    if row is None:
        raise NotFound("media asset not found")
    return row


async def confirm(
    s: AsyncSession,
    storage: MediaStorage | None,
    asset_id: str,
    *,
    width: int | None,
    height: int | None,
    actor: str,
) -> MediaAsset:
    asset = await _asset(s, asset_id)
    if not asset.confirmed:
        if storage is None:
            raise Unavailable("media storage is not configured (KX_STORAGE__*)")
        if not await storage.exists(asset.object_key):
            raise RuleViolation(
                "the file has not arrived in storage yet; upload it, then confirm", reason="cms_media_missing"
            )
        asset.confirmed = True
    asset.width, asset.height = width or asset.width, height or asset.height
    await record(s, actor=actor, action="media_confirmed", detail={"asset": asset_id, "file": asset.filename})
    return asset


async def update_alt(s: AsyncSession, asset_id: str, alt: dict[str, str], *, actor: str) -> MediaAsset:
    asset = await _asset(s, asset_id)
    asset.alt = {**asset.alt, **{k: v for k, v in alt.items() if v}}
    await record(s, actor=actor, action="media_alt", detail={"asset": asset_id})
    return asset


async def list_assets(s: AsyncSession, *, kind: str | None = None, limit: int = 200) -> list[MediaAsset]:
    q = select(MediaAsset).where(MediaAsset.confirmed.is_(True))
    if kind:
        q = q.where(MediaAsset.content_type.startswith(f"{kind}/"))
    return list((await s.execute(q.order_by(MediaAsset.created_at.desc()).limit(limit))).scalars().all())
