"""D61 / WP-C9: the ERP stock list in the CRM, and the variants of the website products.

The sample is Arifa's own ERP export (19 Nov, as pasted by the user): no item codes, the header repeated on
every page, packing and raw material and scrap mixed with finished goods, numbers with thousands commas."""

from __future__ import annotations

import pathlib
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select, text

from kx.core.contracts import ContactPatch
from kx.core.event_payloads import ProductPublished
from kx.core.settings import LogicalDb
from kx.crm._shared.journal import Actor
from kx.crm.catalogue import erp_import
from kx.crm.catalogue import service as catalogue
from kx.crm.catalogue.variants import Candidate, best_match, key_terms, variants_of
from kx.crm.models import Consent, Product, ProductPriceTier, StockItem

SAMPLE = (pathlib.Path(__file__).parent / "erp_sample.tsv").read_bytes()


# ---------------------------------------------------------------- pure rules
def test_codes_are_stable_readable_and_short() -> None:
    assert (
        erp_import.item_code("8325 ALUMINIUM CONTAINER", "1*1000 PCS")
        == "8325-ALUMINIUM-CONTAINER-1X1000-PCS"
    )
    assert erp_import.item_code("30 CM ALUMINIUM FOIL", "1*1.350KG") == "30-CM-ALUMINIUM-FOIL-1X1.350KG"
    long = erp_import.item_code(
        "RM DUPLEX BOARD 250 GSM 79.30/ 79 CM 8389/8367 FOR THE LIDS OF THE BIG TRAYS", "KILO GRAM"
    )
    assert len(long) <= 64 and long == erp_import.item_code(
        "RM DUPLEX BOARD 250 GSM 79.30/ 79 CM 8389/8367 FOR THE LIDS OF THE BIG TRAYS", "KILO GRAM"
    )


@pytest.mark.parametrize(
    ("unit", "measure", "pieces"),
    [
        ("1*1000 PCS", "pcs", 1000),
        ("4*50 PCS", "pcs", 200),
        ("1*1.400 KG", "kg", None),
        ("KILO GRAM", "kg", None),
        ("CARTON", "carton", None),
        ("PACKET", "packet", None),
        ("PIECES", "pcs", None),
    ],
)
def test_units_and_measures(unit: str, measure: str, pieces: int | None) -> None:
    assert erp_import.measure(unit) == measure
    assert erp_import.pieces_per_pack(unit) == pieces


@pytest.mark.parametrize(
    ("category", "description", "sellable"),
    [
        ("ALUMINIUM PRODUCT", "8325 ALUMINIUM CONTAINER", True),
        ("PAPER PRODUCT", "8325 LID", True),
        ("ALUMINIUM PRODUCT", "ALUMINIUM SCRAP", False),
        ("Packing Material", "OUTER CTN 3180 CON 400L*400W*235H", False),
        ("RAW MATERIAL PAPER", "RM DUPLEX BOARD 250 GSM", False),
    ],
)
def test_only_finished_goods_are_sellable(category: str, description: str, sellable: bool) -> None:
    assert erp_import.is_sellable(category, description) is sellable


def test_the_erp_export_parses_with_its_repeated_header() -> None:
    rows, errors = erp_import.parse(SAMPLE)
    assert not errors and len(rows) == 18
    scrap = next(r for r in rows if r.description == "ALUMINIUM SCRAP")
    assert scrap.closing == Decimal("2196.2")  # "2,196.2"


def _c(cms_id: str, ref: str, family: str, retail: bool = False) -> Candidate:
    return Candidate(
        cms_id,
        tuple(key_terms(ref)),
        tuple(t for t in cms_id.upper().split("-") if not t.isdigit()),
        family,
        retail,
    )


WEB = [
    _c("ac-8325", "8325", "containers"),
    _c("ac-8342", "8342", "containers"),
    _c("sr-con-8342", "8342", "containers", retail=True),
    _c("foil-30", "30 cm", "foil"),
    _c("foil-30-blue", "30 cm", "foil"),
    _c("cling-30", "30 cm", "cling-film"),
    _c("pot-22", "22 pot", "pots"),  # the site shows XS: the CMS `erp_match` says "22 pot"
    _c("sr-pot-22", "22 pot", "pots", retail=True),
    _c("tray-3180", "3180", "cups-trays"),
    _c("tray-5186", "5186", "cups-trays"),
]


@pytest.mark.parametrize(
    ("description", "expected"),
    [
        ("8325 ALUMINIUM CONTAINER", "ac-8325"),
        ("8325 LID", "ac-8325"),  # the lid is a variant too: container + lid quote as two lines
        ("8342 LID", "ac-8342"),  # not the retail pack
        ("30 CM ALUMINIUM FOIL", "foil-30"),  # not Blue Line, not cling film
        ("22 ALU POT ROUND", "pot-22"),
        ("22 ALU POT ROUND RETAIL", "sr-pot-22"),
        ("3180 ALUMINIUM CONTAINER ROUND", "tray-3180"),  # a model number wins over the ERP's family word
        ("5186 ALU ROUND PLATTER", "tray-5186"),
        ("1065 ALUMINIUM CONTAINER 1*1000", None),  # no website product for it
    ],
)
def test_erp_items_find_their_website_product(description: str, expected: str | None) -> None:
    assert best_match(description, WEB) == expected


def test_a_number_joins_only_a_real_unit() -> None:
    assert key_terms("30 cm") == ["30CM"] and key_terms("22 pot") == ["22", "POT"]


# ---------------------------------------------------------------- database
async def _clean(databases: Any) -> None:
    async with databases.transaction(LogicalDb.CRM) as s:
        await s.execute(text("DELETE FROM crm.website_products"))


@pytest.mark.db
async def test_import_keeps_everything_marks_sellable_and_saves_stock(databases: Any) -> None:
    await _clean(databases)
    actor = Actor.system("test")
    async with databases.transaction(LogicalDb.CRM) as s:
        report = await erp_import.import_erp(s, SAMPLE, actor=actor, now=datetime.now(UTC))
    assert report.rows == 18 and report.sellable == 13 and report.not_sellable == 5
    async with databases.transaction(LogicalDb.CRM) as s:
        again = await erp_import.import_erp(s, SAMPLE, actor=actor, now=datetime.now(UTC))
        assert again.created == 0 and again.updated == 18  # the same file updates the same items
        foil = (
            await s.execute(select(Product).where(Product.sku == "45-CM-ALUMINIUM-FOIL-1X1.500-KG"))
        ).scalar_one()
        assert (
            foil.sellable and foil.unit == "kg" and not foil.is_quotable and foil.erp_packing == "1*1.500 KG"
        )
        stock = (await s.execute(select(StockItem).where(StockItem.product_id == foil.id))).scalar_one()
        assert stock.quantity == Decimal(137) and stock.source == "erp"
        box = (await s.execute(select(Product).where(Product.sku.like("OUTER-CTN-3180%")))).scalar_one()
        assert not box.sellable and not box.is_active


@pytest.mark.db
async def test_publishing_a_website_product_links_and_describes_its_variants(databases: Any) -> None:
    await _clean(databases)
    async with databases.transaction(LogicalDb.CRM) as s:
        await erp_import.import_erp(s, SAMPLE, actor=Actor.system("test"), now=datetime.now(UTC))
    p = ProductPublished(
        code="AC-8325",
        cms_id="ac-8325",
        name="Aluminium Container & Lid",
        category_code="containers",
        ref="8325",
        range="trade",
        spec={"capacity": "1000 ml", "unit_price": "1.2"},
    )
    async with databases.transaction(LogicalDb.CRM) as s:
        variants = await catalogue.mirror_product(s, p, datetime.now(UTC))
        assert sorted(v.erp_description or "" for v in variants) == ["8325 ALUMINIUM CONTAINER", "8325 LID"]
        assert all(v.spec.get("capacity") == "1000 ml" and "unit_price" not in v.spec for v in variants)
        container = next(v for v in variants if "CONTAINER" in (v.erp_description or ""))
        for low, high, price in (
            (Decimal(1), Decimal(9999), Decimal("0.40")),
            (Decimal(10000), None, Decimal("0.35")),
        ):
            s.add(
                ProductPriceTier(
                    product_id=container.id,
                    min_qty=low,
                    max_qty=high,
                    list_price=price,
                    min_price=price,
                    currency="AED",
                )
            )
        container.is_quotable = True
    async with databases.transaction(LogicalDb.CRM) as s:
        views = await variants_of(s, "ac-8325", limited_below=Decimal(60), now=datetime.now(UTC))
    by_desc = {v.description: v for v in views}
    assert by_desc["8325 ALUMINIUM CONTAINER"].availability == "limited"  # 50 < 60
    assert (
        by_desc["8325 ALUMINIUM CONTAINER"].quantity_breaks == (Decimal(10000),)
        and by_desc["8325 ALUMINIUM CONTAINER"].quotable
    )
    assert by_desc["8325 LID"].availability == "in_stock" and not by_desc["8325 LID"].quotable
    assert "0.35" not in repr(views) and "0.40" not in repr(views)  # never an amount


@pytest.mark.db
async def test_opt_ins_are_recorded_with_their_wording(databases: Any) -> None:
    from kx.crm.deals import service as deal_svc

    async with databases.transaction(LogicalDb.CRM) as s:
        deal = await deal_svc.resolve_deal(s, "web_chat", None, uuid.uuid4(), Actor.system("test"))
        contact = await deal_svc.patch_contact(
            s,
            deal,
            ContactPatch(
                email=f"q{uuid.uuid4().hex[:6]}@example.com",
                whatsapp_opt_in=True,
                email_opt_in=False,
                consent_via="web_chat",
                consent_wording="Yes, WhatsApp is fine",
            ),
            Actor.system("test"),
        )
        rows = (await s.execute(select(Consent).where(Consent.contact_id == contact.id))).scalars().all()
    granted = [(r.channel, r.status, r.wording, r.captured_via) for r in rows if r.status == "granted"]
    assert granted == [("whatsapp", "granted", "Yes, WhatsApp is fine", "web_chat")]


@pytest.mark.db
async def test_the_ai_may_name_the_website_product_or_the_buyers_reference(databases: Any) -> None:
    """Live test 20 Nov: "8325" was refused as an unknown CRM code, so the AI told the buyer it did not exist."""
    from kx.core.contracts import RequirementPatch
    from kx.crm.deals import service as deal_svc

    await _clean(databases)
    async with databases.transaction(LogicalDb.CRM) as s:
        await erp_import.import_erp(s, SAMPLE, actor=Actor.system("test"), now=datetime.now(UTC))
        for p in (
            ProductPublished(
                code="AC-8325",
                cms_id="ac-8325",
                name="Aluminium Container & Lid",
                ref="8325",
                category_code="containers",
            ),
            ProductPublished(
                code="TRAY-3180",
                cms_id="tray-3180",
                name="Aluminium Tray",
                ref="3180",
                category_code="cups-trays",
            ),
        ):
            await catalogue.mirror_product(s, p, datetime.now(UTC))
    async with databases.transaction(LogicalDb.CRM) as s:
        deal = await deal_svc.resolve_deal(s, "web_chat", None, uuid.uuid4(), Actor.system("test"))
        one = await deal_svc.patch_requirement(
            s, deal, RequirementPatch(product_sku="3180"), Actor.system("t")
        )
        assert one.product_id is not None  # one ERP item: it is the product
        two = await deal_svc.patch_requirement(
            s, deal, RequirementPatch(product_sku="ac-8325"), Actor.system("t")
        )
        assert (
            two.product_text == "Aluminium Container & Lid (8325)"
            and two.spec["website_product"] == "ac-8325"
        )
        odd = await deal_svc.patch_requirement(
            s, deal, RequirementPatch(product_sku="9999", product_text=None), Actor.system("t")
        )
        assert odd.product_text == "Aluminium Container & Lid (8325)"  # an unknown code never overwrites
