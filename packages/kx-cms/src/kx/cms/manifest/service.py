"""D55: the site map the AI assistant drives the website with, built from published content.

The assistant never reads the website's code or HTML. It needs to know what exists (pages and their sections,
families, products, factory-tour chapters) and where each one lives. This is built from the same published
content the website renders, so the AI and the site always agree. About 20 KB; held in memory and rebuilt only
when `cms.state.revision` changed (every publish bumps it), checked at most every `check_seconds`.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from typing import Any

from kx.cms._shared.localized import localize
from kx.cms._shared.paths import ar, family_path, page_path, product_path
from kx.cms.content.reader import revision, rows_of
from kx.cms.content.service import SITE_KEY
from kx.core.contracts import (
    ManifestChapter,
    ManifestFamily,
    ManifestPage,
    ManifestProduct,
    ManifestSection,
    SiteManifest,
)
from kx.core.db import Databases
from kx.core.settings import LogicalDb


def _text(v: Any) -> str:
    return v if isinstance(v, str) else ""


async def build_manifest(databases: Databases) -> SiteManifest:
    async with databases.transaction(LogicalDb.CMS) as s:
        rev = await revision(s)
        rows = {
            c: await rows_of(s, c, None, drafts=False)
            for c in ("site", "pages", "families", "products", "tour_chapters")
        }
    site = next((v.data for d, v in rows["site"] if d.key == SITE_KEY), {})
    product_pages = bool(localize(site, "en").get("product_pages")) if site else False

    pages: list[ManifestPage] = []
    for d, v in rows["pages"]:
        en, arabic = localize(v.data, "en"), localize(v.data, "ar")
        path = page_path(d.key, en)
        ar_blocks = {b.get("section_id"): b for b in arabic.get("blocks", []) if isinstance(b, dict)}
        sections = tuple(
            ManifestSection(
                id=b["section_id"],
                design_id=b.get("design_id"),
                title=_text(b.get("heading")),
                purpose=_text(b.get("eyebrow")) or _text(b.get("kind")),
                # the words that lead the AI here, in both site languages (D55)
                keywords=tuple(
                    dict.fromkeys(
                        [
                            *(b.get("keywords") or ()),
                            *(ar_blocks.get(b["section_id"], {}).get("keywords") or ()),
                        ]
                    )
                ),
            )
            for b in en.get("blocks", [])
            if isinstance(b, dict) and b.get("section_id") and not b.get("hidden")
        )
        names = {lang: _text(t.get("title")) for lang, t in (("en", en), ("ar", arabic)) if t.get("title")}
        pages.append(
            ManifestPage(
                key=d.key,
                title=_text(en.get("title")),
                names=names,
                path_en=path,
                path_ar=ar(path),
                sections=sections,
            )
        )

    families: list[ManifestFamily] = []
    for d, v in rows["families"]:
        en, arabic = localize(v.data, "en"), localize(v.data, "ar")
        synonyms = {lang: tuple(words) for lang, words in (en.get("synonyms") or {}).items()}
        path = family_path(d.key)
        families.append(
            ManifestFamily(
                id=d.key,
                name=_text(en.get("name")),
                names={"en": _text(en.get("name")), "ar": _text(arabic.get("name"))},
                synonyms=synonyms,
                path_en=path,
                path_ar=ar(path),
            )
        )

    products: list[ManifestProduct] = []
    for d, v in rows["products"]:
        en = localize(v.data, "en")
        path = product_path(d.key, product_pages=product_pages)
        products.append(
            ManifestProduct(
                id=d.key,
                ref=_text(en.get("ref")),
                catalogue_code=en.get("code"),
                family=_text(en.get("family")),
                name=_text(en.get("name")),
                packing=en.get("qty"),
                range="self" if en.get("range") == "self" else "trade",
                path_en=path,
                path_ar=ar(path),
                has_photo=bool(en.get("photos")),
            )
        )

    chapters = [
        ManifestChapter(
            id=d.key,
            title=_text(localize(v.data, "en").get("title")),
            seconds=float(localize(v.data, "en").get("seconds") or 0),
            family=localize(v.data, "en").get("family"),
        )
        for d, v in rows["tour_chapters"]
    ]
    return SiteManifest(
        revision=rev,
        built_at=datetime.now(UTC),
        pages=tuple(pages),
        families=tuple(families),
        products=tuple(products),
        chapters=tuple(chapters),
        product_pages=product_pages,
    )


class ManifestCache:
    """`kx.core.contracts.SiteManifestSource`: the manifest, rebuilt only after a publish."""

    def __init__(self, databases: Databases, *, check_seconds: float = 3.0) -> None:
        self._db = databases
        self._check = check_seconds
        self._value: SiteManifest | None = None
        self._checked = 0.0
        self._lock = asyncio.Lock()

    async def manifest(self) -> SiteManifest:
        now = time.monotonic()
        if self._value is not None and now - self._checked < self._check:
            return self._value
        async with self._lock:
            if self._value is not None and time.monotonic() - self._checked < self._check:
                return self._value
            async with self._db.transaction(LogicalDb.CMS) as s:
                rev = await revision(s)
            if self._value is None or rev != self._value.revision:
                self._value = await build_manifest(self._db)
            self._checked = time.monotonic()
            return self._value
