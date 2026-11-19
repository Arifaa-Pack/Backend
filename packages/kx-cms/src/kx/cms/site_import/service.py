"""WP-C2: load the current website's content into the CMS, so the client edits it from day one and the AI's
site map and knowledge base are filled (they are empty until something is published).

Inputs are the website's own data files, unchanged (github.com/rizwankairoxaitech/Arifa-Pack `src/data/`):

* `catalogue.json`: `{families: [{id, name, short, clip, blurb}], products: [{id, family, ref, name, code,
  qty, range?, href}]}` (8 families, 78 products);
* `clients.json` (optional): industries, stories and client logos, all marked "sample" until Arifa approves;
* a `seed` (the client app's JSON): site settings, the page layouts with their section ids (= the site's DOM
  ids), the factory-tour chapters, the quality-line stations, family synonyms per language, which products
  have a photo, the photo base URL;
* `arabic` (optional): the website's English -> Arabic dictionary (`src/i18n/ar.ts` as JSON), used to fill the
  Arabic names and texts.

Idempotent: a document whose published content is already the same is left alone; a changed one gets a new
draft that is published (the history keeps the old version). Nothing here ever carries a price.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Final

from sqlalchemy.ext.asyncio import AsyncSession

from kx.cms.content.reader import site_settings
from kx.cms.content.service import ContentService
from kx.core.db import Databases
from kx.core.errors import Conflict, NotFound, RuleViolation
from kx.core.settings import LogicalDb

ACTOR: Final = "system:import-site"


@dataclass(slots=True)
class ImportReport:
    published: dict[str, int] = field(default_factory=dict)
    unchanged: dict[str, int] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)

    def count(self, bucket: dict[str, int], collection: str) -> None:
        bucket[collection] = bucket.get(collection, 0) + 1


def _ar(dictionary: Mapping[str, str], text: Any) -> Any:
    return dictionary.get(text.strip()) if isinstance(text, str) and text.strip() else None


def _family_docs(
    catalogue: Mapping[str, Any], seed: Mapping[str, Any], arabic: Mapping[str, str]
) -> list[tuple[str, dict[str, Any]]]:
    synonyms = seed.get("family_synonyms", {})
    out = []
    for f in catalogue.get("families", []):
        en = {
            "name": f["name"],
            "short": f.get("short") or f["name"],
            "blurb": f.get("blurb"),
            "clip": f.get("clip"),
            "synonyms": synonyms.get(f["id"], {}),
        }
        ar = {
            k: v
            for k, v in {
                "name": _ar(arabic, f["name"]),
                "short": _ar(arabic, f.get("short")),
                "blurb": _ar(arabic, f.get("blurb")),
            }.items()
            if v
        }
        out.append(
            (f["id"], {"en": {k: v for k, v in en.items() if v is not None}, **({"ar": ar} if ar else {})})
        )
    return out


def _product_docs(
    catalogue: Mapping[str, Any], seed: Mapping[str, Any], arabic: Mapping[str, str]
) -> list[tuple[str, dict[str, Any]]]:
    with_photo = set(seed.get("photos", []))
    base = str(seed.get("photo_base_url", "")).rstrip("/")
    erp_match = {k: v for k, v in seed.get("erp_match", {}).items() if not k.startswith("_")}
    out = []
    for p in catalogue.get("products", []):
        href = str(p.get("href") or "")
        en: dict[str, Any] = {
            "family": p["family"],
            "ref": p["ref"],
            "code": p.get("code") if p.get("code") not in (None, "", "—", "-") else None,
            "name": p["name"],
            "qty": p.get("qty"),
            "range": p.get("range", "trade"),
            "legacy_paths": [href if href.startswith("/") else f"/{href}"] if href else [],
            "erp_match": erp_match.get(p["id"]),  # D61: how its ERP items are recognised
        }
        if base and p["id"] in with_photo:
            en["photos"] = [{"url": f"{base}/{p['id']}.webp", "alt": f"{p['name']} {p['ref']}"}]
        ar = {k: v for k, v in {"name": _ar(arabic, p["name"])}.items() if v}
        out.append(
            (p["id"], {"en": {k: v for k, v in en.items() if v is not None}, **({"ar": ar} if ar else {})})
        )
    return out


def _client_docs(clients: Mapping[str, Any]) -> dict[str, list[tuple[str, dict[str, Any]]]]:
    industries = [
        (
            i["id"],
            {
                "en": {
                    k: v
                    for k, v in {
                        "name": i["name"],
                        "label": i.get("label"),
                        "short": i.get("short"),
                        "trade": i.get("trade"),
                        "use": i.get("use"),
                        "kit": i.get("families", []),
                    }.items()
                    if v is not None
                }
            },
        )
        for i in clients.get("industries", [])
    ]
    stories = [
        (
            s["id"],
            {
                "en": {
                    k: v
                    for k, v in {
                        "title": s.get("summary") or s.get("sector") or s["id"],
                        "industry": s.get("industry"),
                        "trade": s.get("sector"),
                        "scene": s.get("scene"),
                        "quote": s.get("quote"),
                        "author": s.get("attribution") or s.get("persona"),
                        "families": s.get("families", []),
                        "packaging": s.get("products", []),
                        "sample": bool(s.get("sample", True)),
                    }.items()
                    if v not in (None, [])
                }
            },
        )
        for s in clients.get("stories", [])
    ]
    logos = [
        (
            f"client-{n + 1:02d}",
            {
                "en": {
                    k: v
                    for k, v in {
                        "name": c["name"],
                        "industry": c.get("industry"),
                        "sample": bool(c.get("sample", True)),
                    }.items()
                    if v is not None
                }
            },
        )
        for n, c in enumerate(clients.get("clients", []))
    ]
    return {"industries": industries, "stories": stories, "clients": logos}


async def _put(  # noqa: PLR0917 - private helper
    s: AsyncSession,
    svc: ContentService,
    report: ImportReport,
    collection: str,
    key: str,
    data: dict[str, Any],
    sort_order: int,
    *,
    product_pages: bool,
) -> None:
    try:
        current = await svc.get(s, collection, key)
    except NotFound:
        current = None
    if current and current.published and current.published.data == data and current.draft is None:
        report.count(report.unchanged, collection)
        return
    revision = current.draft.revision if current and current.draft else None
    await svc.save_draft(s, collection, key, data, actor=ACTOR, revision=revision, sort_order=sort_order)
    await svc.publish(
        s, collection, key, actor=ACTOR, note="imported from the website", product_pages=product_pages
    )
    report.count(report.published, collection)


async def import_site(
    databases: Databases,
    *,
    catalogue: Mapping[str, Any],
    seed: Mapping[str, Any],
    clients: Mapping[str, Any] | None = None,
    arabic: Mapping[str, str] | None = None,
    service: ContentService | None = None,
) -> ImportReport:
    svc = service or ContentService()
    report = ImportReport()
    arabic = arabic or {}
    batches: list[tuple[str, list[tuple[str, dict[str, Any]]]]] = [
        ("site", [("site", seed["site"])] if "site" in seed else []),
        ("families", _family_docs(catalogue, seed, arabic)),
        ("products", _product_docs(catalogue, seed, arabic)),
        ("pages", [(k, v) for k, v in seed.get("pages", {}).items()]),
        ("tour_chapters", [(k, v) for k, v in seed.get("tour_chapters", {}).items()]),
        ("stations", [(k, v) for k, v in seed.get("stations", {}).items()]),
    ]
    if clients:
        batches += list(_client_docs(clients).items())
    for collection, docs in batches:
        async with databases.transaction(LogicalDb.CMS) as s:
            product_pages = bool((await site_settings(s)).get("product_pages"))
            for order, (key, data) in enumerate(docs):
                try:
                    async with s.begin_nested():  # one bad document is reported; the rest still import
                        await _put(
                            s, svc, report, collection, key, data, order * 10, product_pages=product_pages
                        )
                except (RuleViolation, Conflict) as exc:
                    report.problems.append(f"{collection}/{key}: {exc}")
    return report
