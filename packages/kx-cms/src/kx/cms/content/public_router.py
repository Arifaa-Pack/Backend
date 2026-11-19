"""Public content API for the website (`/v1/content`, no login). Published content only; with a valid
`preview` token the open drafts instead (SRS §7 preview), never cached.

Caching: every answer carries an `ETag` (the CMS revision and the document versions) and
`Cache-Control: public, max-age=30, s-maxage=60, stale-while-revalidate=600`, so Cloudflare and the website's
own fetch cache absorb the traffic. After a publish the platform calls the website's `/api/revalidate`, so a
change shows within about a minute anyway (D54). `If-None-Match` answers 304.
"""

# No `from __future__ import annotations` here: FastAPI resolves the dependencies' annotations at runtime.

import re
from typing import Annotated, Any, Final

from fastapi import APIRouter, Query, Request, Response

from kx.cms._shared.paths import ar, family_path, page_path, product_path
from kx.cms.content import reader
from kx.cms.content.schemas_api import (
    ContentDoc,
    ContentList,
    Locale,
    ProductDetail,
    ProductLink,
    Redirect,
    SitemapEntry,
)
from kx.cms.content.service import SITE_KEY
from kx.cms.deps import CmsDeps
from kx.cms.manifest.service import ManifestCache
from kx.cms.preview import service as preview
from kx.core.contracts import SiteManifest
from kx.core.errors import NotFound
from kx.core.settings import LogicalDb

CACHE: Final = "public, max-age=30, s-maxage=60, stale-while-revalidate=600"
NO_STORE: Final = "private, no-store"
GENERIC: Final = frozenset(
    {"tour_chapters", "stations", "faqs", "certificates", "clients", "industries", "stories", "leadership"}
)
LocaleQ = Annotated[Locale, Query(description="en, or ar (Arabic laid over English)")]
PreviewQ = Annotated[
    str | None, Query(alias="preview", max_length=4096, description="Preview token: show the drafts")
]


def _doc(d: reader.PublishedDoc) -> ContentDoc:
    return ContentDoc(
        collection=d.collection,
        key=d.key,
        version=d.version,
        published_at=d.published_at,
        data=d.data,
        draft=d.draft,
    )


def _answer(request: Request, body: Any, etag: str, *, drafts: bool) -> Response:
    """JSON with ETag + cache headers, or 304 when the client already has it."""
    tag = f'W/"{etag}"'
    headers = {"ETag": tag, "Cache-Control": NO_STORE if drafts else CACHE, "Vary": "Accept-Encoding"}
    if not drafts and request.headers.get("if-none-match") == tag:
        return Response(status_code=304, headers=headers)
    content = body.model_dump_json() if hasattr(body, "model_dump_json") else body
    return Response(content=content, media_type="application/json", headers=headers)


def build_public_router(deps: CmsDeps, manifest: ManifestCache) -> APIRouter:  # noqa: PLR0915 - one small handler per route
    r = APIRouter(prefix="/v1/content", tags=["content"])

    def drafts_ok(token: str | None) -> bool:
        return preview.valid(deps.preview_secret, token, deps.clock())

    async def list_of(
        collection: str, locale: str, drafts: bool, keys: list[str] | None = None
    ) -> ContentList:
        async with deps.databases.transaction(LogicalDb.CMS) as s:
            rev = await reader.revision(s)
            docs = await reader.published_list(
                s, collection, locale=locale, media_base_url=deps.media_base_url, keys=keys, drafts=drafts
            )
        return ContentList(collection=collection, locale=locale, revision=rev, items=[_doc(d) for d in docs])

    @r.get("/site", response_model=ContentDoc)
    async def site(request: Request, locale: LocaleQ = "en", preview_token: PreviewQ = None) -> Any:
        """Site settings: company name, phones, emails, addresses, hours, social links, announcement bar,
        theme colours and fonts, the assistant's fixed texts, and whether product pages (PG-03) are live."""
        drafts = drafts_ok(preview_token)
        async with deps.databases.transaction(LogicalDb.CMS) as s:
            d = await reader.published(
                s, "site", SITE_KEY, locale=locale, media_base_url=deps.media_base_url, drafts=drafts
            )
        return _answer(request, _doc(d), f"site:{d.version}:{locale}:{int(d.draft)}", drafts=drafts)

    @r.get("/manifest", response_model=SiteManifest)
    async def site_manifest(request: Request) -> Any:
        """D55: the site map the AI assistant uses (pages and their section ids, families with their names and
        synonyms in six languages, products with their paths, factory-tour chapters). The website's instant
        navigation (layer 0) reads the same data."""
        m = await manifest.manifest()
        return _answer(request, m, f"manifest:{m.revision}", drafts=False)

    @r.get("/pages/{slug}", response_model=ContentDoc)
    async def page(
        request: Request, slug: str, locale: LocaleQ = "en", preview_token: PreviewQ = None
    ) -> Any:
        """One page with its section blocks (home, products, about, quality, clients, contact, privacy)."""
        drafts = drafts_ok(preview_token)
        async with deps.databases.transaction(LogicalDb.CMS) as s:
            d = await reader.published(
                s, "pages", slug, locale=locale, media_base_url=deps.media_base_url, drafts=drafts
            )
        return _answer(request, _doc(d), f"page:{slug}:{d.version}:{locale}:{int(d.draft)}", drafts=drafts)

    @r.get("/families", response_model=ContentList)
    async def families(request: Request, locale: LocaleQ = "en", preview_token: PreviewQ = None) -> Any:
        """The product families, in their display order (HM-01, HM-04, PR-02)."""
        drafts = drafts_ok(preview_token)
        out = await list_of("families", locale, drafts)
        return _answer(request, out, f"families:{out.revision}:{locale}:{int(drafts)}", drafts=drafts)

    @r.get("/products", response_model=ContentList)
    async def products(  # noqa: PLR0917 - FastAPI query parameters
        request: Request,
        locale: LocaleQ = "en",
        family: Annotated[str | None, Query(max_length=40)] = None,
        q: Annotated[str | None, Query(max_length=100, description="Search code, reference or name")] = None,
        range_: Annotated[str | None, Query(alias="range", pattern="^(trade|self)$")] = None,
        preview_token: PreviewQ = None,
    ) -> Any:
        """The catalogue (PR-03), in catalogue order; filters as on the site: `family`, `q`, `range`."""
        drafts = drafts_ok(preview_token)
        out = await list_of("products", locale, drafts)
        needle = re.sub(r"\s+", " ", q.strip().lower()) if q else None
        items = [
            d
            for d in out.items
            if (family is None or d.data.get("family") == family)
            and (range_ is None or d.data.get("range", "trade") == range_)
            and (
                needle is None
                or needle in " ".join(str(d.data.get(f, "")) for f in ("ref", "code", "name")).lower()
                or needle in d.key
            )
        ]
        body = out.model_copy(update={"items": items})
        etag = f"products:{out.revision}:{locale}:{family}:{q}:{range_}:{int(drafts)}"
        return _answer(request, body, etag, drafts=drafts)

    @r.get("/products/{product_id}", response_model=ProductDetail)
    async def product(
        request: Request, product_id: str, locale: LocaleQ = "en", preview_token: PreviewQ = None
    ) -> Any:
        """PG-03: one product with its family, previous / next in the catalogue (PD-04) and up to four more
        from the same family (PD-05)."""
        drafts = drafts_ok(preview_token)
        all_products = await list_of("products", locale, drafts)
        ids = [d.key for d in all_products.items]
        if product_id not in ids:
            raise NotFound(f"product {product_id} is not published")
        i = ids.index(product_id)
        this = all_products.items[i]

        def link(d: ContentDoc) -> ProductLink:
            return ProductLink(key=d.key, ref=str(d.data.get("ref", "")), name=str(d.data.get("name", "")))

        fam_id = this.data.get("family")
        fams = await list_of("families", locale, drafts, keys=[fam_id] if isinstance(fam_id, str) else [])
        same = [d for d in all_products.items if d.data.get("family") == fam_id and d.key != product_id]
        body = ProductDetail(
            product=this,
            family=fams.items[0] if fams.items else None,
            previous=link(all_products.items[i - 1]) if i > 0 else None,
            next=link(all_products.items[i + 1]) if i + 1 < len(ids) else None,
            more_from_family=same[:4],
        )
        etag = f"product:{product_id}:{all_products.revision}:{locale}:{int(drafts)}"
        return _answer(request, body, etag, drafts=drafts)

    @r.get("/collections/{collection}", response_model=ContentList)
    async def collection(
        request: Request, collection: str, locale: LocaleQ = "en", preview_token: PreviewQ = None
    ) -> Any:
        """Any other collection, in display order: `tour_chapters` (HM-05), `stations` (QA-02), `faqs`,
        `certificates` (QA-04), `clients` (CL-02), `industries` (CL-01), `stories` (CL-03), `leadership`
        (AB-05, AB-06)."""
        if collection not in GENERIC:
            raise NotFound(f"unknown collection {collection!r}")
        drafts = drafts_ok(preview_token)
        out = await list_of(collection, locale, drafts)
        return _answer(request, out, f"{collection}:{out.revision}:{locale}:{int(drafts)}", drafts=drafts)

    @r.get("/sitemap", response_model=list[SitemapEntry])
    async def sitemap(request: Request) -> Any:
        """Every published page, family and product, in English and Arabic, for `/sitemap.xml` (§9)."""
        async with deps.databases.transaction(LogicalDb.CMS) as s:
            rev = await reader.revision(s)
            site_data = await reader.site_settings(s)
            pages = await reader.published_list(s, "pages")
            families = await reader.published_list(s, "families")
            products = await reader.published_list(s, "products")
        product_pages = bool(site_data.get("product_pages"))
        out: list[SitemapEntry] = []
        for d in pages:
            if not (d.data.get("seo") or {}).get("in_sitemap", True) or (d.data.get("seo") or {}).get(
                "noindex"
            ):
                continue
            path = page_path(d.key, d.data)
            out.append(SitemapEntry(path_en=path, path_ar=ar(path), updated_at=d.published_at, kind="page"))
        for d in families:
            path = family_path(d.key)
            out.append(SitemapEntry(path_en=path, path_ar=ar(path), updated_at=d.published_at, kind="family"))
        if product_pages:
            for d in products:
                path = product_path(d.key, product_pages=True)
                out.append(
                    SitemapEntry(path_en=path, path_ar=ar(path), updated_at=d.published_at, kind="product")
                )
        body = "[" + ",".join(e.model_dump_json() for e in out) + "]"
        return _answer(request, body, f"sitemap:{rev}", drafts=False)

    @r.get("/redirects", response_model=list[Redirect])
    async def redirects(request: Request) -> Any:
        """Old-site addresses that move permanently to their new page (design doc §9): each product's
        `legacy_paths` (e.g. `/8325.php`) to its page (or its quick view until PG-03 is live)."""
        async with deps.databases.transaction(LogicalDb.CMS) as s:
            rev = await reader.revision(s)
            site_data = await reader.site_settings(s)
            products = await reader.published_list(s, "products")
        product_pages = bool(site_data.get("product_pages"))
        out = [
            Redirect(
                source=src if src.startswith("/") else f"/{src}",
                destination=product_path(d.key, product_pages=product_pages),
            )
            for d in products
            for src in d.data.get("legacy_paths") or []
            if isinstance(src, str) and src
        ]
        body = "[" + ",".join(e.model_dump_json() for e in out) + "]"
        return _answer(request, body, f"redirects:{rev}", drafts=False)

    return r
