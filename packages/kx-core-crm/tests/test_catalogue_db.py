"""§7 / D22 / D61: a CMS publish reaches the CRM variants of that website product, descriptive fields only.

Since D61 the CMS product is the website's marketing record; the CRM products are its variants (ERP items),
linked by `cms_product_id`. A publish never creates a CRM product and never touches prices, MOQ, lead days
or `is_quotable` (the manager owns those)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from conftest import Seed
from sqlalchemy import select

from kx.core.event_payloads import ProductPublished
from kx.core.settings import LogicalDb
from kx.crm.catalogue import service as catalogue
from kx.crm.models import Product, WebsiteProduct

pytestmark = pytest.mark.db


async def mirror(databases: Any, p: ProductPublished) -> list[Product]:
    async with databases.transaction(LogicalDb.CRM) as s:
        return await catalogue.mirror_product(s, p, datetime.now(UTC))


async def _variant(databases: Any, seed: Seed, cms_id: str) -> uuid.UUID:
    """An ERP item the salesperson linked by hand (`PUT /catalogue/products/{sku}/website-link`)."""
    async with databases.transaction(LogicalDb.CRM) as s:
        base = await s.get(Product, await _seed_id(s, seed))
        assert base is not None
        row = Product(
            sku=f"T-{uuid.uuid4().hex[:8]}".upper(),
            name="TRAY 1 1*500 PCS",
            category_code=base.category_code,
            cms_product_id=cms_id,
            moq=20_000,
            is_quotable=True,
            default_lead_days_max=14,
            spec={},
        )
        s.add(row)
        await s.flush()
        return row.id


async def _seed_id(s: Any, seed: Seed) -> uuid.UUID:
    found: uuid.UUID = (
        await s.execute(select(Product.id).where(Product.sku == seed.product_sku))
    ).scalar_one()
    return found


async def test_publish_updates_description_but_never_prices_moq_or_quotability(
    databases: Any, seed: Seed
) -> None:
    cms_id = f"tray-{uuid.uuid4().hex[:6]}"
    vid = await _variant(databases, seed, cms_id)
    p = ProductPublished(
        code=cms_id.upper(),
        cms_id=cms_id,
        name="Tray 1 (smooth wall)",
        spec={"capacity_ml": 750, "unit_price": "0.3"},
        custom_printing=True,
    )
    (product,) = await mirror(databases, p)
    assert product.id == vid and product.cms_published and product.custom_printing
    assert product.spec == {"capacity_ml": 750}  # the price key is dropped
    assert (product.moq, product.is_quotable, product.default_lead_days_max) == (20_000, True, 14)

    (product,) = await mirror(databases, p.model_copy(update={"published": False}))
    assert not product.cms_published and product.is_quotable  # kept: open quotes refer to it


async def test_a_website_product_without_erp_items_creates_nothing_but_is_remembered(databases: Any) -> None:
    cms_id = f"new-{uuid.uuid4().hex[:6]}"
    p = ProductPublished(code=cms_id.upper(), cms_id=cms_id, name="Round tray", ref="9901", range="self")
    assert await mirror(databases, p) == []
    async with databases.transaction(LogicalDb.CRM) as s:
        web = await s.get(WebsiteProduct, cms_id)
        assert web is not None and (web.ref, web.range, web.name) == ("9901", "self", "Round tray")
        linked = select(Product.id).where(Product.cms_product_id == cms_id)
        assert (await s.execute(linked)).first() is None  # no CRM product made up for it
