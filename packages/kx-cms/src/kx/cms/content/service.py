"""Draft -> preview -> publish (SRS §7, D22, D54).

* **Save** edits the one open draft of a document (creating the document and the draft when needed). The
  caller sends the draft's `revision` it started from; if someone saved in between, the save is refused
  (409) instead of silently overwriting a colleague's work.
* **Publish** turns the draft into the live version and archives the previous live one (every published
  state is kept; `restore` copies an old version into a new draft). Publishing raises, in the same
  transaction: `content.published.v1` (the website re-renders the pages, within about a minute),
  `kb.content_published.v1` per language for content the AI may use (the knowledge base is re-indexed), and,
  for products, `catalogue.product_published.v1` (the CRM mirrors the product by its code; never a price).
* **No prices**: a price-like field name or a text stating money is refused on save (`kx.core.no_prices`).

Content is stored per language (`{"en": {...}, "ar": {...}}`): English is validated as the full schema, every
other language as "English with these fields translated" (`_shared.localized`).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from kx.cms._shared.journal import record
from kx.cms._shared.localized import BASE_LOCALE, locales_of, localize
from kx.cms._shared.paths import tags_and_paths
from kx.cms.content.schemas import COLLECTIONS, KB_COLLECTIONS, LOCALES, Product, schema_of
from kx.cms.models import Document, State, Version
from kx.core import events as ev
from kx.core.errors import Conflict, NotFound, RuleViolation
from kx.core.event_payloads import ContentPublished, EventPayload, KbContentPublished, ProductPublished
from kx.core.identity import new_id
from kx.core.no_prices import find_price

Clock = Callable[[], datetime]
SITE_KEY: Final = "site"  # the one document of the `site` collection


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class VersionView:
    number: int
    status: str
    revision: int
    data: dict[str, Any]
    author: str
    saved_at: datetime
    published_at: datetime | None
    published_by: str | None
    note: str | None


@dataclass(frozen=True, slots=True)
class DocumentView:
    collection: str
    key: str
    sort_order: int
    draft: VersionView | None
    published: VersionView | None
    updated_at: datetime


def check_collection(collection: str) -> None:
    if collection not in COLLECTIONS:
        raise NotFound(f"unknown collection {collection!r}")


def validate_content(collection: str, data: dict[str, Any]) -> dict[str, Any]:
    """The cleaned per-language content, or RuleViolation naming the first problem."""
    model = schema_of(collection)
    unknown = sorted(set(data) - set(LOCALES))
    if unknown:
        raise RuleViolation(f"unknown language(s): {', '.join(unknown)}", reason="cms_locale")
    if not isinstance(data.get(BASE_LOCALE), dict):
        raise RuleViolation("English content (`en`) is required", reason="cms_locale")
    found = find_price(data)
    if found is not None:
        raise RuleViolation(
            f"prices never appear in website content (found at {found}); prices live only in the CRM",
            reason="cms_price",
        )
    clean: dict[str, Any] = {}
    for locale in LOCALES:
        if locale not in data or data[locale] in (None, {}):
            continue
        if not isinstance(data[locale], dict):
            raise RuleViolation(f"`{locale}` must be an object", reason="cms_locale")
        try:
            model.model_validate(localize(data, locale))
        except ValidationError as exc:
            first = exc.errors()[0]
            where = ".".join(str(p) for p in first["loc"])
            raise RuleViolation(f"{locale}.{where}: {first['msg']}", reason="cms_invalid") from exc
        clean[locale] = data[locale]
    return clean


def _view(v: Version | None) -> VersionView | None:
    if v is None:
        return None
    return VersionView(
        number=v.number,
        status=v.status,
        revision=v.revision,
        data=v.data,
        author=v.author,
        saved_at=v.saved_at,
        published_at=v.published_at,
        published_by=v.published_by,
        note=v.note,
    )


class ContentService:
    """Editor operations on documents. Every method runs inside the caller's CMS transaction."""

    def __init__(self, clock: Clock = _now) -> None:
        self._clock = clock

    # ------------------------------------------------------------------ reads (editors)
    async def _document(self, s: AsyncSession, collection: str, key: str, *, lock: bool = False) -> Document:
        check_collection(collection)
        q = select(Document).where(Document.collection == collection, Document.key == key)
        doc = (await s.execute(q.with_for_update() if lock else q)).scalar_one_or_none()
        if doc is None:
            raise NotFound(f"{collection}/{key} not found")
        return doc

    async def _version(self, s: AsyncSession, doc: Document, number: int | None) -> Version | None:
        if number is None:
            return None
        return (
            await s.execute(select(Version).where(Version.document_id == doc.id, Version.number == number))
        ).scalar_one_or_none()

    async def get(self, s: AsyncSession, collection: str, key: str) -> DocumentView:
        doc = await self._document(s, collection, key)
        return DocumentView(
            collection=collection,
            key=key,
            sort_order=doc.sort_order,
            draft=_view(await self._version(s, doc, doc.draft_version)),
            published=_view(await self._version(s, doc, doc.published_version)),
            updated_at=doc.updated_at,
        )

    async def list_documents(self, s: AsyncSession, collection: str) -> list[DocumentView]:
        check_collection(collection)
        docs = (
            (
                await s.execute(
                    select(Document)
                    .where(Document.collection == collection)
                    .order_by(Document.sort_order, Document.key)
                )
            )
            .scalars()
            .all()
        )
        numbers = {n for d in docs for n in (d.draft_version, d.published_version) if n is not None}
        versions: dict[tuple[Any, int], Version] = {}
        if numbers:
            rows = (
                await s.execute(
                    select(Version).where(
                        Version.document_id.in_([d.id for d in docs]), Version.status != "archived"
                    )
                )
            ).scalars()
            versions = {(v.document_id, v.number): v for v in rows}
        return [
            DocumentView(
                collection=collection,
                key=d.key,
                sort_order=d.sort_order,
                draft=_view(versions.get((d.id, d.draft_version))) if d.draft_version else None,
                published=_view(versions.get((d.id, d.published_version))) if d.published_version else None,
                updated_at=d.updated_at,
            )
            for d in docs
        ]

    async def versions(self, s: AsyncSession, collection: str, key: str) -> list[VersionView]:
        doc = await self._document(s, collection, key)
        rows = (
            (
                await s.execute(
                    select(Version).where(Version.document_id == doc.id).order_by(Version.number.desc())
                )
            )
            .scalars()
            .all()
        )
        return [v for v in (_view(r) for r in rows) if v is not None]

    # ------------------------------------------------------------------ writes
    async def save_draft(
        self,
        s: AsyncSession,
        collection: str,
        key: str,
        data: dict[str, Any],
        *,
        actor: str,
        revision: int | None = None,
        sort_order: int | None = None,
    ) -> VersionView:
        """Create or update the document's open draft. `revision` = the draft revision the editor started
        from (None for a new draft); a mismatch is a 409 so nobody overwrites a colleague's save."""
        check_collection(collection)
        if collection == "site" and key != SITE_KEY:
            raise RuleViolation("the site settings document is site/site", reason="cms_key")
        clean = validate_content(collection, data)
        now = self._clock()
        doc = (
            await s.execute(
                select(Document)
                .where(Document.collection == collection, Document.key == key)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if doc is None:
            doc = Document(
                id=new_id(), collection=collection, key=key, sort_order=sort_order or 0, updated_at=now
            )
            s.add(doc)
            await s.flush()
        elif sort_order is not None:
            doc.sort_order = sort_order
        draft = await self._version(s, doc, doc.draft_version)
        if draft is None:
            if revision not in (None, 0):
                raise Conflict("this draft was published or discarded meanwhile; reload the document")
            number = await self._next_number(s, doc)
            draft = Version(
                id=new_id(),
                document_id=doc.id,
                number=number,
                status="draft",
                revision=1,
                data=clean,
                author=actor,
                saved_at=now,
            )
            s.add(draft)
            doc.draft_version = number
        else:
            if revision is not None and revision != draft.revision:
                raise Conflict(
                    f"someone else saved this draft (revision {draft.revision}, yours {revision}); reload it"
                )
            draft.data = clean
            draft.revision += 1
            draft.author = actor
            draft.saved_at = now
        doc.updated_at = now
        await s.flush()
        await record(
            s, actor=actor, action="draft_saved", collection=collection, key=key, version=draft.number
        )
        result = _view(draft)
        assert result is not None
        return result

    async def _next_number(self, s: AsyncSession, doc: Document) -> int:
        current = (
            (await s.execute(select(Version.number).where(Version.document_id == doc.id))).scalars().all()
        )
        return max(current, default=0) + 1

    async def discard_draft(self, s: AsyncSession, collection: str, key: str, *, actor: str) -> None:
        doc = await self._document(s, collection, key, lock=True)
        draft = await self._version(s, doc, doc.draft_version)
        if draft is None:
            raise NotFound(f"{collection}/{key} has no open draft")
        await s.delete(draft)
        doc.draft_version = None
        doc.updated_at = self._clock()
        await record(
            s, actor=actor, action="draft_discarded", collection=collection, key=key, version=draft.number
        )
        if doc.published_version is None:  # a document that never went live disappears with its draft
            await s.flush()
            await s.delete(doc)

    async def restore(
        self, s: AsyncSession, collection: str, key: str, number: int, *, actor: str
    ) -> VersionView:
        """Copy an earlier version into the open draft (a new draft if there is none). Publish to go live."""
        doc = await self._document(s, collection, key)
        old = await self._version(s, doc, number)
        if old is None:
            raise NotFound(f"{collection}/{key} has no version {number}")
        draft = await self._version(s, doc, doc.draft_version)
        view = await self.save_draft(
            s, collection, key, old.data, actor=actor, revision=draft.revision if draft else None
        )
        await record(s, actor=actor, action="restored", collection=collection, key=key, version=number)
        return view

    async def publish(
        self,
        s: AsyncSession,
        collection: str,
        key: str,
        *,
        actor: str,
        note: str | None = None,
        product_pages: bool = False,
    ) -> VersionView:
        """The open draft goes live; the previous live version is archived."""
        doc = await self._document(s, collection, key, lock=True)
        draft = await self._version(s, doc, doc.draft_version)
        if draft is None:
            raise RuleViolation("nothing to publish: save a draft first", reason="cms_no_draft")
        clean = validate_content(collection, draft.data)  # rules may have changed since the save
        family_name = await self._check_links(s, collection, key, clean)
        now = self._clock()
        live = await self._version(s, doc, doc.published_version)
        if live is not None:
            live.status = "archived"
            await s.flush()  # the one-live-version index needs the old row archived first
        draft.status, draft.published_at, draft.published_by, draft.note = "published", now, actor, note
        doc.published_version, doc.draft_version = draft.number, None
        doc.published_at = doc.updated_at = now
        await self._bump(s, now)
        await record(
            s,
            actor=actor,
            action="published",
            collection=collection,
            key=key,
            version=draft.number,
            detail={"note": note} if note else None,
            events=self._events(collection, key, clean, draft.number, "publish", product_pages, family_name),
        )
        result = _view(draft)
        assert result is not None
        return result

    async def unpublish(
        self, s: AsyncSession, collection: str, key: str, *, actor: str, product_pages: bool = False
    ) -> None:
        """Take the document off the website (its versions stay; publish again to bring it back)."""
        doc = await self._document(s, collection, key, lock=True)
        live = await self._version(s, doc, doc.published_version)
        if live is None:
            raise RuleViolation("this document is not published", reason="cms_not_published")
        live.status = "archived"
        doc.published_version = None
        doc.updated_at = now = self._clock()
        await self._bump(s, now)
        await record(
            s,
            actor=actor,
            action="unpublished",
            collection=collection,
            key=key,
            version=live.number,
            events=self._events(collection, key, live.data, None, "unpublish", product_pages),
        )

    async def _bump(self, s: AsyncSession, now: datetime) -> None:
        await s.execute(
            update(State).where(State.id == 1).values(revision=State.revision + 1, updated_at=now)
        )

    async def _check_links(
        self, s: AsyncSession, collection: str, key: str, data: dict[str, Any]
    ) -> str | None:
        """A product needs a published family. Returns the family's English name (the CRM files the product's
        variants under it)."""
        if collection != "products":
            return None
        product = Product.model_validate(localize(data, BASE_LOCALE))
        family = (
            await s.execute(
                select(Version.data)
                .join(
                    Document,
                    (Version.document_id == Document.id) & (Version.number == Document.published_version),
                )
                .where(Document.collection == "families", Document.key == product.family)
            )
        ).scalar_one_or_none()
        if family is None:
            raise RuleViolation(f"publish the family {product.family!r} first", reason="cms_family")
        name = family.get(BASE_LOCALE, {}).get("name")
        return str(name) if name else None

    def _events(  # noqa: PLR0917 - private, two call sites
        self,
        collection: str,
        key: str,
        data: dict[str, Any],
        version: int | None,
        operation: str,
        product_pages: bool,
        family_name: str | None = None,
    ) -> list[tuple[str, EventPayload]]:
        en = localize(data, BASE_LOCALE)
        locales = locales_of(data)
        tags, paths = tags_and_paths(collection, key, en, product_pages=product_pages)
        out: list[tuple[str, EventPayload]] = [
            (
                ev.CONTENT_PUBLISHED,
                ContentPublished(
                    collection=collection,
                    key=key,
                    version=version,
                    operation="publish" if operation == "publish" else "unpublish",
                    locales=locales,
                    tags=tags,
                    paths=paths,
                ),
            )
        ]
        if collection in KB_COLLECTIONS:
            out += [
                (ev.KB_CONTENT_PUBLISHED, KbContentPublished(collection=collection, doc_id=key, locale=loc))
                for loc in locales
            ]
        if collection == "products":
            payload = product_payload(key, en, published=operation == "publish", family_name=family_name)
            out.append((ev.CATALOGUE_PRODUCT_PUBLISHED, payload))
        return out


def product_payload(
    key: str, en: dict[str, Any], *, published: bool, family_name: str | None = None
) -> ProductPublished:
    """§7 / D61: the CRM updates the descriptive details of every variant linked to this website product
    (`code` = the website id, upper case). Never a price."""
    p = Product.model_validate(en)
    specs = p.specs
    spec: dict[str, object] = {
        k: v
        for k, v in {
            "shortDescription": p.short_description,
            "descriptionText": p.description,
            "material": specs.material,
            "capacity": specs.capacity,
            "dimensions": specs.dimensions,
            "thickness": specs.thickness,
            "colours": specs.colours,
            "lidOptions": specs.lid_options,
            "catalogueCode": p.code,
            "packing": p.qty,
        }.items()
        if v
    }
    return ProductPublished(
        code=key.upper(),
        cms_id=key,
        name=p.name,
        category_code=p.family,
        category_name=family_name,
        spec=spec,
        published=published,
        custom_printing=p.custom_printing,
    )
