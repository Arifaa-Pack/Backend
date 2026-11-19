"""The CMS against PostgreSQL: draft -> publish -> version -> restore, the events a publish raises, the
optimistic lock, the public content API, preview tokens, the site manifest and the website import."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import text

from kx.cms import CmsDeps, ContentService, ManifestCache, build_public_router, import_site, plain_document
from kx.cms.content.drafts import save_product_draft
from kx.cms.preview.service import issue
from kx.core.db import Databases
from kx.core.errors import Conflict, RuleViolation, install_problem_handlers
from kx.core.event_payloads import ProductContentChanged
from kx.core.settings import LogicalDb

pytestmark = pytest.mark.db
ACTOR = "user:test"
FAMILY = {
    "en": {"name": "Aluminium Containers", "short": "Containers", "synonyms": {"en": ["foil containers"]}},
    "ar": {"name": "حاويات ألمنيوم"},
}


def product(ref: str, family: str = "containers", **extra: Any) -> dict[str, Any]:
    return {
        "en": {
            "family": family,
            "ref": ref,
            "name": "Aluminium Container & Lid",
            "qty": "1 x 1000 pcs",
            **extra,
        }
    }


@pytest.fixture
async def dbs(databases: Databases) -> Databases:
    async with databases.transaction(LogicalDb.CMS) as s:  # every test starts from an empty CMS
        await s.execute(
            text(
                "TRUNCATE cms.versions, cms.documents, cms.media_assets, cms.audit_log, "
                "cms.outbox_events, cms.inbox_events"
            )
        )
        await s.execute(text("UPDATE cms.state SET revision = 0"))
    return databases


async def publish(dbs: Databases, collection: str, key: str, data: dict[str, Any]) -> int:
    svc = ContentService()
    async with dbs.transaction(LogicalDb.CMS) as s:
        doc = None
        try:
            doc = await svc.get(s, collection, key)
        except Exception:
            doc = None
        rev = doc.draft.revision if doc and doc.draft else None
        await svc.save_draft(s, collection, key, data, actor=ACTOR, revision=rev)
        return (await svc.publish(s, collection, key, actor=ACTOR)).number


async def events(dbs: Databases) -> list[tuple[str, dict[str, Any]]]:
    async with dbs.transaction(LogicalDb.CMS) as s:
        rows = await s.execute(text("SELECT event_type, payload FROM cms.outbox_events ORDER BY sequence"))
        return [(r[0], r[1]) for r in rows.all()]


async def test_draft_publish_restore_keeps_every_version(dbs: Databases) -> None:
    svc = ContentService()
    assert await publish(dbs, "families", "containers", FAMILY) == 1
    async with dbs.transaction(LogicalDb.CMS) as s:
        v2 = await svc.save_draft(
            s, "families", "containers", {"en": {**FAMILY["en"], "name": "Containers & lids"}}, actor=ACTOR
        )
        assert v2.number == 2 and v2.revision == 1
        await svc.publish(s, "families", "containers", actor=ACTOR, note="rename")
    async with dbs.transaction(LogicalDb.CMS) as s:
        history = await svc.versions(s, "families", "containers")
        assert [(v.number, v.status) for v in history] == [(2, "published"), (1, "archived")]
        restored = await svc.restore(s, "families", "containers", 1, actor=ACTOR)
        assert restored.number == 3 and restored.data["en"]["name"] == "Aluminium Containers"
        doc = await svc.get(s, "families", "containers")
        assert doc.published is not None and doc.published.number == 2  # live until published again
        assert doc.draft is not None and doc.draft.number == 3


async def test_two_editors_cannot_overwrite_each_other(dbs: Databases) -> None:
    svc = ContentService()
    async with dbs.transaction(LogicalDb.CMS) as s:
        first = await svc.save_draft(
            s, "families", "foil", {"en": {"name": "Foil", "short": "Foil"}}, actor="a"
        )
        await svc.save_draft(
            s,
            "families",
            "foil",
            {"en": {"name": "Foil rolls", "short": "Foil"}},
            actor="b",
            revision=first.revision,
        )
    with pytest.raises(Conflict):
        async with dbs.transaction(LogicalDb.CMS) as s:  # "a" saves again from the revision it loaded
            await svc.save_draft(
                s,
                "families",
                "foil",
                {"en": {"name": "Kitchen foil", "short": "Foil"}},
                actor="a",
                revision=first.revision,
            )


async def test_a_price_is_refused_and_nothing_is_saved(dbs: Databases) -> None:
    svc = ContentService()
    with pytest.raises(RuleViolation, match="prices never appear"):
        async with dbs.transaction(LogicalDb.CMS) as s:
            await svc.save_draft(
                s, "products", "ac-8325", product("8325", description="Only AED 0.45 each"), actor=ACTOR
            )
    async with dbs.transaction(LogicalDb.CMS) as s:
        assert (await s.execute(text("SELECT count(*) FROM cms.documents"))).scalar_one() == 0


async def test_product_needs_a_published_family(dbs: Databases) -> None:
    with pytest.raises(RuleViolation, match="publish the family"):
        await publish(dbs, "products", "ac-8325", product("8325"))


async def test_publish_raises_website_knowledge_and_crm_events(dbs: Databases) -> None:
    await publish(dbs, "families", "containers", FAMILY)
    await publish(
        dbs, "products", "ac-8325", {**product("8325", code="AC-1"), "ar": {"name": "حاوية مع غطاء"}}
    )
    found = await events(dbs)
    types = [t for t, _ in found]
    assert types.count("content.published.v1") == 2
    kb = [p for t, p in found if t == "kb.content_published.v1" and p["doc_id"] == "ac-8325"]
    assert sorted(p["locale"] for p in kb) == ["ar", "en"]
    crm = next(p for t, p in found if t == "catalogue.product_published.v1")
    assert crm["code"] == "AC-8325" and crm["cms_id"] == "ac-8325" and crm["published"] is True
    assert crm["category_code"] == "containers" and crm["category_name"] == "Aluminium Containers"
    assert crm["spec"]["catalogueCode"] == "AC-1"
    assert not any("price" in k for k in crm["spec"])
    site = next(p for t, p in found if t == "content.published.v1" and p["key"] == "ac-8325")
    assert "/products" in site["paths"] and "products:ac-8325" in site["tags"]


async def test_unpublish_takes_it_off_and_tells_everyone(dbs: Databases) -> None:
    svc = ContentService()
    await publish(dbs, "families", "containers", FAMILY)
    await publish(dbs, "products", "ac-8325", product("8325"))
    async with dbs.transaction(LogicalDb.CMS) as s:
        await svc.unpublish(s, "products", "ac-8325", actor=ACTOR)
        assert await plain_document(s, "products", "ac-8325", "en") is None
    crm = [p for t, p in await events(dbs) if t == "catalogue.product_published.v1"]
    assert crm[-1]["published"] is False


async def test_knowledge_text_has_codes_and_no_links(dbs: Databases) -> None:
    await publish(dbs, "families", "containers", FAMILY)
    await publish(
        dbs,
        "products",
        "ac-8325",
        product("8325", code="AC-1", specs={"capacity": "1000 ml"}, suitability={"oven": True}),
    )
    async with dbs.transaction(LogicalDb.CMS) as s:
        doc = await plain_document(s, "products", "ac-8325", "en")
    assert doc is not None
    assert doc.title == "Aluminium Container & Lid (8325)"
    assert "Reference 8325" in doc.text and "1000 ml" in doc.text and "oven: yes" in doc.text
    assert doc.path == "/products?p=ac-8325"


def _app(dbs: Databases, cache: ManifestCache) -> FastAPI:
    app = FastAPI()
    install_problem_handlers(app)
    deps = CmsDeps(databases=dbs, preview_secret="test-preview-secret", media_base_url="https://media.test")
    app.include_router(build_public_router(deps, cache))
    return app


async def test_public_api_published_only_with_etag_and_preview(dbs: Databases) -> None:
    await publish(dbs, "families", "containers", FAMILY)
    await publish(dbs, "products", "ac-8325", product("8325"))
    await publish(dbs, "products", "ac-8342", product("8342"))
    svc = ContentService()
    async with dbs.transaction(LogicalDb.CMS) as s:  # an unpublished edit must not show
        doc = await svc.get(s, "products", "ac-8325")
        await svc.save_draft(s, "products", "ac-8325", product("8325", name="DRAFT NAME"), actor=ACTOR)
        assert doc.published is not None
    cache = ManifestCache(dbs, check_seconds=0)
    transport = httpx.ASGITransport(app=_app(dbs, cache))
    async with httpx.AsyncClient(transport=transport, base_url="http://cms") as c:
        r = await c.get("/v1/content/products/ac-8325", params={"locale": "ar"})
        assert r.status_code == 200
        body = r.json()
        assert body["product"]["data"]["name"] == "Aluminium Container & Lid"
        assert body["family"]["data"]["name"] == "حاويات ألمنيوم"  # Arabic laid over English
        assert body["next"]["key"] == "ac-8342" and body["previous"] is None
        assert "s-maxage" in r.headers["cache-control"]
        again = await c.get(
            "/v1/content/products/ac-8325",
            params={"locale": "ar"},
            headers={"If-None-Match": r.headers["etag"]},
        )
        assert again.status_code == 304
        token, _ = issue("test-preview-secret", ACTOR, datetime.now(UTC))
        preview = await c.get("/v1/content/products/ac-8325", params={"preview": token})
        assert preview.json()["product"]["data"]["name"] == "DRAFT NAME"
        assert preview.json()["product"]["draft"] is True and "no-store" in preview.headers["cache-control"]
        bad = await c.get("/v1/content/products/ac-8325", params={"preview": "forged.token"})
        assert bad.json()["product"]["data"]["name"] == "Aluminium Container & Lid"
        found = await c.get("/v1/content/products", params={"q": "8342"})
        assert [d["key"] for d in found.json()["items"]] == ["ac-8342"]
        missing = await c.get("/v1/content/products/nope")
        assert missing.status_code == 404
        m = (await c.get("/v1/content/manifest")).json()
        assert [p["id"] for p in m["products"]] == ["ac-8325", "ac-8342"]
        assert m["products"][0]["path_en"] == "/products?p=ac-8325"
        assert m["families"][0]["synonyms"]["en"] == ["foil containers"]


async def test_manifest_follows_publishes(dbs: Databases) -> None:
    cache = ManifestCache(dbs, check_seconds=0)
    assert (await cache.manifest()).families == ()
    await publish(dbs, "families", "containers", FAMILY)
    m = await cache.manifest()
    assert [f.id for f in m.families] == ["containers"] and m.revision == 1


async def test_workbook_saves_a_product_draft_never_live(dbs: Databases) -> None:
    await publish(dbs, "families", "containers", FAMILY)
    await publish(dbs, "products", "ac-8325", product("8325"))
    svc = ContentService()
    change = ProductContentChanged(
        code="8325-ALUMINIUM-CONTAINER-1X1000-PCS",
        name="Aluminium Container & Lid",
        fields={"capacity": "1000 ml", "shortDescription": "Our 1-litre container"},
        cms_product_id="ac-8325",
    )
    async with dbs.transaction(LogicalDb.CMS) as s:
        key, created = await save_product_draft(s, change, svc)
        assert (key, created) == ("ac-8325", False)
        doc = await svc.get(s, "products", "ac-8325")
    assert doc.draft is not None and doc.draft.data["en"]["specs"]["capacity"] == "1000 ml"
    assert (
        doc.published is not None and "specs" not in doc.published.data["en"]
    )  # live until a manager publishes


async def test_import_site_is_idempotent(dbs: Databases) -> None:
    catalogue = {
        "families": [
            {
                "id": "containers",
                "name": "Aluminium Containers",
                "short": "Containers",
                "clip": "x",
                "blurb": "Containers and lids",
            }
        ],
        "products": [
            {
                "id": "ac-8325",
                "family": "containers",
                "ref": "8325",
                "name": "Aluminium Container & Lid",
                "code": "AC-1",
                "qty": "1 × 1000 pcs",
                "href": "8325.php",
            },
            {
                "id": "sr-con-8342",
                "family": "containers",
                "ref": "8342",
                "range": "self",
                "name": "Aluminium Containers & Lid Pack",
                "code": "ARF-8342",
                "qty": "10 pcs × 100 pkt",
                "href": "sr-alu-con-8342.php",
            },
        ],
    }
    seed = {
        "site": {"en": {"company_name": "Arifa Packing & Packaging LLC"}},
        "photos": ["ac-8325"],
        "photo_base_url": "https://www.example.test/images/catalogue",
        "pages": {"home": {"en": {"title": "Home", "path": "/", "blocks": [{"section_id": "floor"}]}}},
    }
    arabic = {"Aluminium Containers": "حاويات ألمنيوم"}
    first = await import_site(dbs, catalogue=catalogue, seed=seed, arabic=arabic)
    assert first.published == {"site": 1, "families": 1, "products": 2, "pages": 1} and not first.problems
    second = await import_site(dbs, catalogue=catalogue, seed=seed, arabic=arabic)
    assert second.published == {} and second.unchanged == {
        "site": 1,
        "families": 1,
        "products": 2,
        "pages": 1,
    }
    m = await ManifestCache(dbs, check_seconds=0).manifest()
    assert {p.id: p.range for p in m.products} == {"ac-8325": "trade", "sr-con-8342": "self"}
    assert m.pages[0].sections[0].id == "floor"
    async with dbs.transaction(LogicalDb.CMS) as s:
        row = (
            await s.execute(
                text(
                    "SELECT v.data FROM cms.versions v JOIN cms.documents d ON d.id = v.document_id "
                    "WHERE d.key = 'ac-8325' AND v.status = 'published'"
                )
            )
        ).scalar_one()
    assert row["en"]["legacy_paths"] == ["/8325.php"]
    assert row["en"]["photos"][0]["url"].endswith("/ac-8325.webp")
