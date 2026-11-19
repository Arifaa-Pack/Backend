"""The CMS admin API for content editors (`/v1/cms`, SRS §7). Staff sign in through the CRM's login
(`/v1/auth`); the content editor role (SRS §3, D54) edits and publishes content and has no CRM access. Admin,
manager and MD may do the same.

Every content type is described by `GET /v1/cms/collections` (its JSON Schema), so the admin screens can draw
a form for each collection without a code change when a field is added.
"""

# No `from __future__ import annotations` here: FastAPI resolves the dependencies' annotations at runtime.

from typing import Annotated, Final

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from kx.cms._shared.access import EDITORS, actor_of
from kx.cms._shared.localized import BASE_LOCALE
from kx.cms.content.reader import site_settings
from kx.cms.content.schemas import COLLECTIONS
from kx.cms.content.schemas_api import (
    CollectionInfo,
    DocumentOut,
    DocumentSummary,
    PreviewOut,
    PublishIn,
    SaveDraftIn,
    VersionOut,
    VersionSummary,
)
from kx.cms.content.service import DocumentView, VersionView
from kx.cms.deps import CmsDeps
from kx.cms.preview import service as preview
from kx.core.auth import Principal, require_role
from kx.core.settings import LogicalDb

TITLES: Final = {
    "site": "Site settings",
    "pages": "Pages",
    "families": "Product families",
    "products": "Products",
    "tour_chapters": "Factory tour chapters",
    "stations": "Quality line stations",
    "faqs": "FAQs",
    "certificates": "Certificates",
    "clients": "Client logos",
    "industries": "Industries",
    "stories": "Customer stories",
    "leadership": "Leadership",
}


def _version(v: VersionView | None) -> VersionOut | None:
    return VersionOut.model_validate(v) if v else None


def _document(d: DocumentView) -> DocumentOut:
    return DocumentOut(
        collection=d.collection,
        key=d.key,
        sort_order=d.sort_order,
        updated_at=d.updated_at,
        draft=_version(d.draft),
        published=_version(d.published),
    )


def _summary(d: DocumentView) -> DocumentSummary:
    latest = d.draft or d.published
    en = (latest.data.get(BASE_LOCALE, {}) if latest else {}) or {}
    title = next((str(en[f]) for f in ("title", "name", "company_name", "question") if en.get(f)), d.key)
    status = "draft" if d.published is None else ("changed" if d.draft else "published")
    return DocumentSummary(
        key=d.key,
        title=title,
        sort_order=d.sort_order,
        updated_at=d.updated_at,
        status=status,
        published_version=d.published.number if d.published else None,
        draft_revision=d.draft.revision if d.draft else None,
    )


def build_admin_router(deps: CmsDeps) -> APIRouter:
    r = APIRouter(prefix="/v1/cms", tags=["cms"])
    session = deps.databases.dependency(LogicalDb.CMS)
    Session = Annotated[AsyncSession, Depends(session)]  # noqa: N806 - a type alias
    Editor = Annotated[Principal, Depends(require_role(*EDITORS))]  # noqa: N806
    svc = deps.service

    async def product_pages(s: AsyncSession) -> bool:
        return bool((await site_settings(s)).get("product_pages"))

    @r.get("/collections", response_model=list[CollectionInfo])
    async def collections(_: Editor) -> list[CollectionInfo]:
        """Every content type with its JSON Schema (fields, types, limits), to draw the editor forms."""
        return [
            CollectionInfo(
                name=n, title=TITLES.get(n, n), single=n == "site", json_schema=m.model_json_schema()
            )
            for n, m in COLLECTIONS.items()
        ]

    @r.get("/collections/{collection}", response_model=list[DocumentSummary])
    async def list_documents(collection: str, s: Session, _: Editor) -> list[DocumentSummary]:
        """The documents of one content type, in display order, with their state (draft / published /
        changed = live with unpublished edits)."""
        return [_summary(d) for d in await svc.list_documents(s, collection)]

    @r.get("/collections/{collection}/{key}", response_model=DocumentOut)
    async def get_document(collection: str, key: str, s: Session, _: Editor) -> DocumentOut:
        """One document: its open draft (with `revision`, needed to save) and its live version."""
        return _document(await svc.get(s, collection, key))

    @r.put("/collections/{collection}/{key}", response_model=VersionOut)
    async def save_draft(collection: str, key: str, body: SaveDraftIn, s: Session, p: Editor) -> VersionOut:
        """Save the draft (creates the document when new). Send the `revision` you loaded; if a colleague
        saved meanwhile the answer is 409 and nothing is overwritten. Prices are refused (422 `cms_price`)."""
        v = await svc.save_draft(
            s,
            collection,
            key,
            body.data,
            actor=actor_of(p),
            revision=body.revision,
            sort_order=body.sort_order,
        )
        return VersionOut.model_validate(v)

    @r.post("/collections/{collection}/{key}/publish", response_model=VersionOut)
    async def publish(collection: str, key: str, body: PublishIn, s: Session, p: Editor) -> VersionOut:
        """Put the draft live. The website re-renders the affected pages within about a minute; the AI's
        knowledge and the CRM product record follow automatically."""
        v = await svc.publish(
            s, collection, key, actor=actor_of(p), note=body.note, product_pages=await product_pages(s)
        )
        return VersionOut.model_validate(v)

    @r.post("/collections/{collection}/{key}/unpublish", status_code=204)
    async def unpublish(collection: str, key: str, s: Session, p: Editor) -> Response:
        """Take the document off the website (versions are kept; publish again to bring it back)."""
        await svc.unpublish(s, collection, key, actor=actor_of(p), product_pages=await product_pages(s))
        return Response(status_code=204)

    @r.delete("/collections/{collection}/{key}/draft", status_code=204)
    async def discard_draft(collection: str, key: str, s: Session, p: Editor) -> Response:
        """Throw the open draft away (a document that was never published disappears with it)."""
        await svc.discard_draft(s, collection, key, actor=actor_of(p))
        return Response(status_code=204)

    @r.get("/collections/{collection}/{key}/versions", response_model=list[VersionSummary])
    async def versions(collection: str, key: str, s: Session, _: Editor) -> list[VersionSummary]:
        """Every version, newest first (each publish is kept)."""
        return [VersionSummary.model_validate(v) for v in await svc.versions(s, collection, key)]

    @r.post("/collections/{collection}/{key}/versions/{number}/restore", response_model=VersionOut)
    async def restore(collection: str, key: str, number: int, s: Session, p: Editor) -> VersionOut:
        """Copy an earlier version into the draft; publish it to go live again."""
        return VersionOut.model_validate(await svc.restore(s, collection, key, number, actor=actor_of(p)))

    @r.post("/preview-token", response_model=PreviewOut)
    async def preview_token(p: Editor) -> PreviewOut:
        """A 30-minute token that makes the website show drafts (Next.js draft mode)."""
        token, expires = preview.issue(deps.preview_secret, actor_of(p), deps.clock())
        url = f"{deps.website_url.rstrip('/')}/api/draft?token={token}" if deps.website_url else None
        return PreviewOut(token=token, expires_at=expires, url=url)

    return r
