"""Media library API for content editors (`/v1/cms/media`). See `service.py` for the two-step upload."""

# No `from __future__ import annotations` here: FastAPI resolves the dependencies' annotations at runtime.

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from kx.cms._shared.access import EDITORS, actor_of
from kx.cms.content.schemas_api import AltIn, ConfirmIn, MediaOut, UploadIn, UploadOut
from kx.cms.deps import CmsDeps
from kx.cms.media import service
from kx.cms.models import MediaAsset
from kx.core.auth import Principal, require_role
from kx.core.settings import LogicalDb


def _out(deps: CmsDeps, a: MediaAsset) -> MediaOut:
    return MediaOut(
        id=str(a.id),
        src=f"{deps.media_base_url.rstrip('/')}/{a.object_key}",
        filename=a.filename,
        content_type=a.content_type,
        size_bytes=a.size_bytes,
        width=a.width,
        height=a.height,
        alt=a.alt,
        created_at=a.created_at,
    )


def build_media_router(deps: CmsDeps) -> APIRouter:
    r = APIRouter(prefix="/v1/cms/media", tags=["cms"])
    Session = Annotated[AsyncSession, Depends(deps.databases.dependency(LogicalDb.CMS))]  # noqa: N806
    Editor = Annotated[Principal, Depends(require_role(*EDITORS))]  # noqa: N806

    @r.get("", response_model=list[MediaOut])
    async def list_media(
        s: Session,
        _: Editor,
        kind: Annotated[str | None, Query(pattern="^(image|video|application|model)$")] = None,
    ) -> list[MediaOut]:
        """The library, newest first (`kind` = image, video, application (PDF) or model (3D))."""
        return [_out(deps, a) for a in await service.list_assets(s, kind=kind)]

    @r.post("/uploads", response_model=UploadOut, status_code=201)
    async def start_upload(body: UploadIn, s: Session, p: Editor) -> UploadOut:
        """Step 1: ask where to upload. PUT the file to `upload_url` with `headers`, then confirm. Accepted:
        WebP, JPEG, PNG, AVIF, SVG (15 MB), MP4, WebM (300 MB), PDF (30 MB), GLB 3D models (50 MB)."""
        t = await service.start_upload(
            s,
            deps.storage,
            filename=body.filename,
            content_type=body.content_type,
            size=body.size,
            sha256=body.sha256,
            alt=body.alt,
            actor=actor_of(p),
        )
        return UploadOut(
            asset_id=t.asset_id,
            object_key=t.object_key,
            upload_url=t.upload_url,
            headers=t.headers,
            already_uploaded=t.already_uploaded,
        )

    @r.post("/{asset_id}/confirm", response_model=MediaOut)
    async def confirm(asset_id: str, body: ConfirmIn, s: Session, p: Editor) -> MediaOut:
        """Step 2: the browser's PUT succeeded; the file joins the library."""
        a = await service.confirm(
            s, deps.storage, asset_id, width=body.width, height=body.height, actor=actor_of(p)
        )
        return _out(deps, a)

    @r.patch("/{asset_id}", response_model=MediaOut)
    async def update_alt(asset_id: str, body: AltIn, s: Session, p: Editor) -> MediaOut:
        """Alt text per language (accessibility and SEO)."""
        return _out(deps, await service.update_alt(s, asset_id, body.alt, actor=actor_of(p)))

    return r
