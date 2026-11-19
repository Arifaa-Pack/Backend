"""What the website and the AI read: published content only (or the draft, with a preview token).

* `published(...)` / `published_list(...)`: one language, Arabic laid over English, with every media
  reference resolved to its public URL and alt text, plus a cache `etag` (the version numbers).
* `plain_document(...)`: the text the AI's knowledge base learns from (title + every text field, flattened),
  None once the document is no longer published (the indexer then removes it).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kx.cms._shared.localized import BASE_LOCALE, localize
from kx.cms._shared.paths import family_path, page_path, product_path
from kx.cms.content.schemas import COLLECTIONS
from kx.cms.content.service import SITE_KEY, check_collection
from kx.cms.models import Document, MediaAsset, State, Version
from kx.core.errors import NotFound

# Fields that are links, codes or switches, not text the AI should read
_NOT_TEXT: Final = frozenset(
    {
        "asset_id",
        "url",
        "href",
        "canonical",
        "path",
        "section_id",
        "design_id",
        "kind",
        "clip",
        "family",
        "range",
        "timezone",
        "opens",
        "closes",
        "map_url",
        "accent_color",
        "colors",
        "fonts",
        "legacy_paths",
        "share_image",
        "src",
        "content_type",
    }
)


@dataclass(frozen=True, slots=True)
class PublishedDoc:
    collection: str
    key: str
    version: int
    sort_order: int
    published_at: datetime | None
    data: dict[str, Any]  # one language, media resolved
    draft: bool = False  # True when served from a draft through a preview token


@dataclass(frozen=True, slots=True)
class PlainDocument:
    title: str
    text: str
    version: str
    published_at: datetime
    path: str | None


async def revision(s: AsyncSession) -> int:
    return int((await s.execute(select(State.revision).where(State.id == 1))).scalar_one_or_none() or 0)


async def rows_of(
    s: AsyncSession, collection: str, keys: Iterable[str] | None, *, drafts: bool
) -> list[tuple[Document, Version]]:
    number = Document.published_version
    q = select(Document, Version).join(Version, Version.document_id == Document.id)
    if drafts:  # preview: the open draft where there is one, else the live version
        q = q.where(
            ((Version.number == Document.draft_version) & Document.draft_version.is_not(None))
            | ((Version.number == number) & Document.draft_version.is_(None))
        )
    else:
        q = q.where(Version.number == number)
    q = q.where(Document.collection == collection)
    if keys is not None:
        q = q.where(Document.key.in_(list(keys)))
    rows = (await s.execute(q.order_by(Document.sort_order, Document.key))).all()
    return [(d, v) for d, v in rows]


def _asset_ids(value: Any, out: set[str]) -> None:
    if isinstance(value, dict):
        if isinstance(value.get("asset_id"), str):
            out.add(value["asset_id"])
        for v in value.values():
            _asset_ids(v, out)
    elif isinstance(value, list):
        for v in value:
            _asset_ids(v, out)


def _resolve(value: Any, assets: dict[str, MediaAsset], base_url: str, locale: str) -> Any:
    """Add `src`, `alt`, `width`, `height`, `content_type` to every media reference."""
    if isinstance(value, dict):
        out = {k: _resolve(v, assets, base_url, locale) for k, v in value.items()}
        asset_id = value.get("asset_id")
        if isinstance(asset_id, str):
            a = assets.get(asset_id)
            if a is not None:
                out["src"] = f"{base_url.rstrip('/')}/{a.object_key}"
                out["alt"] = value.get("alt") or a.alt.get(locale) or a.alt.get(BASE_LOCALE) or ""
                out["width"], out["height"], out["content_type"] = a.width, a.height, a.content_type
            else:
                out["src"] = None
        elif isinstance(value.get("url"), str) and "src" not in value and set(value) <= {"url", "alt"}:
            out["src"] = value["url"]
        return out
    if isinstance(value, list):
        return [_resolve(v, assets, base_url, locale) for v in value]
    return value


async def _assets(s: AsyncSession, docs: list[dict[str, Any]]) -> dict[str, MediaAsset]:
    ids: set[str] = set()
    for d in docs:
        _asset_ids(d, ids)
    if not ids:
        return {}
    parsed = []
    for i in ids:
        try:
            parsed.append(uuid.UUID(i))
        except ValueError:
            continue
    rows = (await s.execute(select(MediaAsset).where(MediaAsset.id.in_(parsed)))).scalars().all()
    return {str(a.id): a for a in rows}


async def published_list(
    s: AsyncSession,
    collection: str,
    *,
    locale: str = BASE_LOCALE,
    media_base_url: str = "",
    keys: Iterable[str] | None = None,
    drafts: bool = False,
) -> list[PublishedDoc]:
    check_collection(collection)
    rows = await rows_of(s, collection, keys, drafts=drafts)
    localized = [localize(v.data, locale) for _, v in rows]
    assets = await _assets(s, localized)
    return [
        PublishedDoc(
            collection=collection,
            key=d.key,
            version=v.number,
            sort_order=d.sort_order,
            published_at=v.published_at,
            data=_resolve(data, assets, media_base_url, locale),
            draft=v.status == "draft",
        )
        for (d, v), data in zip(rows, localized, strict=True)
    ]


async def published(
    s: AsyncSession,
    collection: str,
    key: str,
    *,
    locale: str = BASE_LOCALE,
    media_base_url: str = "",
    drafts: bool = False,
) -> PublishedDoc:
    found = await published_list(
        s, collection, locale=locale, media_base_url=media_base_url, keys=[key], drafts=drafts
    )
    if not found:
        raise NotFound(f"{collection}/{key} is not published")
    return found[0]


async def site_settings(
    s: AsyncSession, *, locale: str = BASE_LOCALE, drafts: bool = False
) -> dict[str, Any]:
    """The site settings, or {} before the first publish (the site then keeps its built-in values)."""
    found = await published_list(s, "site", locale=locale, keys=[SITE_KEY], drafts=drafts)
    return found[0].data if found else {}


def _flatten(value: Any, out: list[str], key: str = "") -> None:
    if key in _NOT_TEXT:
        return
    if isinstance(value, dict):
        for k, v in value.items():
            _flatten(v, out, k)
    elif isinstance(value, list):
        for v in value:
            _flatten(v, out, key)
    elif isinstance(value, str) and value.strip():
        out.append(f"{key}: {value}" if key in ("question", "answer") or not key else value)
    elif isinstance(value, bool) and key in ("oven", "microwave", "freezer", "custom_printing"):
        out.append(f"{key.replace('_', ' ')}: {'yes' if value else 'no'}")


def _title(collection: str, key: str, data: dict[str, Any]) -> str:
    for field in ("title", "name", "company_name", "question", "heading"):
        if isinstance(data.get(field), str) and data[field]:
            if collection == "products":
                return f"{data[field]} ({data.get('ref', key)})"
            return str(data[field])
    return f"{collection} {key}"


async def plain_document(s: AsyncSession, collection: str, key: str, locale: str) -> PlainDocument | None:
    """The published document as text for the AI's knowledge base (None = no longer published)."""
    if collection not in COLLECTIONS:
        return None
    rows = await rows_of(s, collection, [key], drafts=False)
    if not rows:
        return None
    doc, version = rows[0]
    data = localize(version.data, locale)
    lines: list[str] = []
    _flatten(data, lines)
    if collection == "products":  # codes the buyer may quote back
        lines.insert(
            0,
            f"Reference {data.get('ref', '')}; catalogue code {data.get('code') or '-'}; "
            f"family {data.get('family', '')}; packing {data.get('qty') or '-'}",
        )
    path: str | None = None
    if collection == "pages":
        path = page_path(key, data)
    elif collection == "products":
        path = product_path(key, product_pages=False)
    elif collection == "families":
        path = family_path(key)
    return PlainDocument(
        title=_title(collection, key, data),
        text="\n".join(lines),
        version=f"{version.number}",
        published_at=version.published_at or doc.updated_at,
        path=path,
    )
