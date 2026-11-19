"""Request and response shapes of the CMS APIs (the content itself is described in `schemas.py`)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Locale = Literal["en", "ar"]


class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------- public (website)
class ContentDoc(_Out):
    """One published document in one language. `data` follows the collection's schema (CMS_API.md), with every
    media reference completed by `src`, `alt`, `width`, `height` and `content_type`."""

    collection: str
    key: str
    version: int
    published_at: datetime | None
    data: dict[str, Any]
    draft: bool = False  # True only in preview mode


class ContentList(_Out):
    collection: str
    locale: Locale
    revision: int  # grows on every publish anywhere in the CMS
    items: list[ContentDoc]


class ProductLink(_Out):
    key: str
    ref: str
    name: str


class ProductDetail(_Out):
    """PG-03: the product, its family, the products before and after it in the catalogue (PD-04) and up to
    four more from the same family (PD-05)."""

    product: ContentDoc
    family: ContentDoc | None
    previous: ProductLink | None
    next: ProductLink | None
    more_from_family: list[ContentDoc]


class SitemapEntry(_Out):
    path_en: str
    path_ar: str
    updated_at: datetime | None
    kind: Literal["page", "product", "family"]


class Redirect(_Out):
    source: str
    destination: str
    permanent: bool = True


# ---------------------------------------------------------------- admin (editors)
class VersionOut(_Out):
    number: int
    status: Literal["draft", "published", "archived"]
    revision: int
    data: dict[str, Any]
    author: str
    saved_at: datetime
    published_at: datetime | None
    published_by: str | None
    note: str | None


class VersionSummary(_Out):
    number: int
    status: Literal["draft", "published", "archived"]
    author: str
    saved_at: datetime
    published_at: datetime | None
    published_by: str | None
    note: str | None


class DocumentOut(_Out):
    collection: str
    key: str
    sort_order: int
    updated_at: datetime
    draft: VersionOut | None
    published: VersionOut | None


class DocumentSummary(_Out):
    key: str
    title: str
    sort_order: int
    updated_at: datetime
    status: Literal["draft", "published", "changed"]  # changed = live, with an unpublished draft
    published_version: int | None
    draft_revision: int | None


class CollectionInfo(_Out):
    name: str
    title: str
    single: bool  # one document only (site settings)
    json_schema: dict[str, Any]  # the content schema, for a generic editor form


class SaveDraftIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    data: dict[str, Any] = Field(description='Content per language: {"en": {...}, "ar": {...}}')
    revision: int | None = Field(
        default=None, ge=0, description="The draft revision you started from (null for a new draft)"
    )
    sort_order: int | None = Field(default=None, ge=0, le=100_000)


class PublishIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str | None = Field(default=None, max_length=500)


class PreviewOut(_Out):
    token: str
    expires_at: datetime
    url: str | None  # the website's draft-mode URL when the site address is configured


class UploadIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str = Field(min_length=1, max_length=300)
    content_type: str = Field(max_length=100)
    size: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    alt: dict[str, str] = Field(default_factory=dict)


class UploadOut(_Out):
    asset_id: str
    object_key: str
    upload_url: str | None = Field(
        description="PUT the file here (null: this file is already in the library)"
    )
    headers: dict[str, str]
    already_uploaded: bool


class ConfirmIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    width: int | None = Field(default=None, gt=0, le=20000)
    height: int | None = Field(default=None, gt=0, le=20000)


class AltIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    alt: dict[str, str]


class MediaOut(_Out):
    id: str
    src: str
    filename: str
    content_type: str
    size_bytes: int
    width: int | None
    height: int | None
    alt: dict[str, str]
    created_at: datetime
