"""18 Nov product workbook against the database: download -> edit cells -> preview -> apply."""

from __future__ import annotations

import io
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from conftest import MGR_ID, Seed
from openpyxl import load_workbook  # type: ignore[import-untyped]
from sqlalchemy import select

from kx.core import events as ev
from kx.core.errors import Conflict, RuleViolation
from kx.core.settings import LogicalDb
from kx.crm import models as m
from kx.crm.services import workbook as wbk
from kx.crm.services.journal import Actor
from kx.crm.services.pricing import current_tiers

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("price_list")]
ACTOR = Actor("user", str(MGR_ID), MGR_ID)


async def download(databases: Any) -> bytes:
    async with databases.transaction(LogicalDb.CRM) as s:
        return await wbk.download(s, datetime.now(UTC))


async def preview(databases: Any, data: bytes) -> wbk.Preview:
    async with databases.transaction(LogicalDb.CRM) as s:
        return await wbk.preview(s, data, datetime.now(UTC))


async def apply(databases: Any, data: bytes, token: str) -> wbk.Applied:
    async with databases.transaction(LogicalDb.CRM) as s:
        return await wbk.apply(s, data, token, actor=ACTOR, now=datetime.now(UTC))


def edit(
    data: bytes, sheet: str, rows: list[dict[str, Any]], *, change: dict[str, dict[str, Any]] | None = None
) -> bytes:
    """Append rows (by header) and change cells of existing rows (code -> {header: value})."""
    wb = load_workbook(io.BytesIO(data))
    ws = wb[sheet]
    head = [c.value for c in ws[1]]
    for line in ws.iter_rows(min_row=2):
        code = str(line[0].value or "")
        for header, value in (change or {}).get(code, {}).items():
            line[head.index(header)].value = value
    for r in rows:
        ws.append([r.get(h) for h in head])
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


async def test_a_downloaded_workbook_uploads_back_with_no_change(databases: Any) -> None:
    """The round trip staff rely on: download, upload unchanged -> nothing to do, no error."""
    pv = await preview(databases, await download(databases))
    assert pv.plan.errors == []
    assert (pv.plan.products, pv.plan.tiers, pv.plan.categories) == ([], [], [])


async def test_apply_updates_crm_prices_and_sends_only_details_to_the_cms(databases: Any, seed: Seed) -> None:
    code, category = f"WB-{uuid.uuid4().hex[:6]}".upper(), f"WBC_{uuid.uuid4().hex[:6]}".upper()
    data = edit(
        await download(databases),
        wbk.PRODUCTS,
        [
            {
                "Code": code,
                "Name": "Kraft box 1 l",
                "Category code": category,
                "Category name": "Kraft boxes",
                "Minimum order (MOQ)": 8000,
                "Delivery days from": 10,
                "Delivery days to": 12,
                "Short description": "Grease-resistant kraft box",
                "Capacity": "1000 ml",
            }
        ],
    )
    data = edit(
        data,
        wbk.TIERS,
        [
            {
                "Code": code,
                "From quantity": 8000,
                "To quantity": 49999,
                "List price (AED)": "0.40",
                "Minimum price (AED)": "0.35",
            },
            {"Code": code, "From quantity": 50000, "List price (AED)": "0.36", "Minimum price (AED)": "0.32"},
        ],
    )
    pv = await preview(databases, data)
    assert pv.plan.ok, pv.plan.errors
    assert [p.code for p in pv.plan.products] == [code] and pv.plan.products[0].push
    assert [t.code for t in pv.plan.tiers] == [code] and [c.code for c in pv.plan.categories] == [category]

    done = await apply(databases, data, pv.token)
    assert (done.products_created, done.price_lists_changed, done.website_drafts) == (1, 1, 1)
    async with databases.transaction(LogicalDb.CRM) as s:
        prod = (await s.execute(select(m.Product).where(m.Product.sku == code))).scalar_one()
        assert prod.is_quotable and prod.moq == 8000 and prod.spec["capacity"] == "1000 ml"
        tiers = await current_tiers(s, prod.id, datetime.now(UTC))
        assert [(t.min_qty, t.list_price, t.min_price) for t in tiers] == [
            (Decimal(8000), Decimal("0.40"), Decimal("0.35")),
            (Decimal(50000), Decimal("0.36"), Decimal("0.32")),
        ]
        events = list(
            (
                await s.execute(
                    select(m.OutboxEvent.payload).where(
                        m.OutboxEvent.event_type == ev.CATALOGUE_PRODUCT_CONTENT,
                        m.OutboxEvent.aggregate_id == str(prod.id),
                    )
                )
            ).scalars()
        )
        actions = set(
            (
                await s.execute(
                    select(m.AuditEvent.action).where(m.AuditEvent.entity_id.in_([str(prod.id), category]))
                )
            ).scalars()
        )
    assert len(events) == 1
    draft = events[0]
    assert (
        draft["code"] == code and draft["fields"]["capacity"] == "1000 ml" and draft["fields"]["moq"] == 8000
    )
    flat = repr(draft).lower()
    assert "0.40" not in flat and "0.35" not in flat and "price" not in flat  # never a price to the website
    assert {"product.created", "catalogue.category_created"} <= actions

    # a second edit: a dash clears, a blank keeps, the unchanged prices make no new version
    data2 = edit(
        await download(databases),
        wbk.PRODUCTS,
        [],
        change={code: {"Capacity": "-", "Minimum order (MOQ)": 9000}},
    )
    pv2 = await preview(databases, data2)
    assert pv2.plan.ok and pv2.plan.tiers == []
    assert {(c.field, c.after) for c in pv2.plan.products[0].changes} == {("capacity", None), ("moq", 9000)}
    await apply(databases, data2, pv2.token)
    async with databases.transaction(LogicalDb.CRM) as s:
        prod = (await s.execute(select(m.Product).where(m.Product.sku == code))).scalar_one()
        assert "capacity" not in prod.spec and prod.moq == 9000 and prod.spec["shortDescription"]


async def test_apply_refuses_a_stale_preview_or_a_file_with_errors(databases: Any, seed: Seed) -> None:
    data = edit(
        await download(databases), wbk.PRODUCTS, [], change={seed.product_sku: {"Thickness": "0.08 mm"}}
    )
    pv = await preview(databases, data)
    assert pv.plan.ok
    async with databases.transaction(LogicalDb.CRM) as s:  # someone else changes the catalogue meanwhile
        prod = (await s.execute(select(m.Product).where(m.Product.sku == seed.product_sku))).scalar_one()
        prod.carton_qty = (prod.carton_qty or 0) + 1
    with pytest.raises(Conflict):
        await apply(databases, data, pv.token)

    bad = edit(
        await download(databases),
        wbk.PRODUCTS,
        [],
        change={seed.product_sku: {"Description": "Now USD 5 only"}},
    )
    pv = await preview(databases, bad)
    assert [e.column for e in pv.plan.errors] == ["Description"]
    with pytest.raises(RuleViolation):
        await apply(databases, bad, pv.token)
