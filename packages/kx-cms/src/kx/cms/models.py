"""The CMS database (schema `cms`, D54). Its own logical database and DSN, like every package (D48).

* `documents`: one row per piece of content (`collection` + `key`, e.g. `products` / `ac-8325`), pointing at
  its published version and its open draft;
* `versions`: every saved state of a document. A save edits the one open draft (status `draft`, its
  `revision` counts saves so two editors cannot overwrite each other); publishing turns the draft into the
  `published` version and archives the previous one, so every published state is kept and can be restored;
* `media_assets`: photos, videos, PDFs and 3D files in R2 under a content-addressed key (a new upload is a new
  URL, so no cache ever serves a stale file);
* `state`: one row whose `revision` grows on every publish (the site manifest's cache key);
* `audit_log`, `outbox_events`, `inbox_events`: the platform's usual audit trail and transactional outbox.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any, Final

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from kx.core.db import make_base

SCHEMA: Final = "cms"
if TYPE_CHECKING:  # make_base builds the class at runtime; give mypy a static one with the same shape

    class Base(DeclarativeBase):
        """Static stand-in for mypy."""

else:
    Base = make_base(SCHEMA)


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("collection", "key"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    collection: Mapped[str] = mapped_column(String(64))
    key: Mapped[str] = mapped_column(String(128))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    published_version: Mapped[int | None] = mapped_column(Integer)
    draft_version: Mapped[int | None] = mapped_column(Integer)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Version(Base):
    __tablename__ = "versions"
    __table_args__ = (UniqueConstraint("document_id", "number"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.documents.id"))
    number: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16))  # draft | published | archived
    revision: Mapped[int] = mapped_column(Integer, default=1)  # saves of this draft (optimistic lock)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB)  # {"en": {...}, "ar": {...}}
    author: Mapped[str] = mapped_column(String(200))
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    saved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_by: Mapped[str | None] = mapped_column(String(200))


class MediaAsset(Base):
    __tablename__ = "media_assets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    object_key: Mapped[str] = mapped_column(String(300), unique=True)
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    filename: Mapped[str] = mapped_column(String(300))
    content_type: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    alt: Mapped[dict[str, str]] = mapped_column(JSONB, default=dict)  # language -> alt text
    confirmed: Mapped[bool] = mapped_column(default=False)  # the file is in R2
    created_by: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class State(Base):
    __tablename__ = "state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # always 1
    revision: Mapped[int] = mapped_column(BigInteger, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuditEntry(Base):
    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    actor: Mapped[str] = mapped_column(String(200))
    action: Mapped[str] = mapped_column(String(64))
    collection: Mapped[str | None] = mapped_column(String(64))
    key: Mapped[str | None] = mapped_column(String(128))
    version: Mapped[int | None] = mapped_column(Integer)
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
