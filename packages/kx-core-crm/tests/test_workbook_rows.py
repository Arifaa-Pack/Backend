"""18 Nov product workbook: the planning rules (pure, no database). Staff fill cells; the rules make sure a
half-filled sheet can never lose data and that no price reaches the website."""

from __future__ import annotations

import io
from decimal import Decimal
from typing import Any

from openpyxl import Workbook  # type: ignore[import-untyped]

from kx.crm.catalogue import workbook as wbk

D = Decimal
NOW = wbk.Snapshot(
    products={
        "CUP-8": {
            "name": "8 oz paper cup",
            "category_code": "CUPS",
            "unit": "pcs",
            "moq": 10000,
            "carton_qty": 1000,
            "lead_min": 10,
            "lead_max": 12,
            "custom_printing": False,
            "active": True,
            "website": True,
            "shortDescription": "Single wall, 240 ml",
            "descriptionText": None,
            "material": "Paper",
            "capacity": "240 ml",
            "dimensions": None,
            "thickness": None,
            "colours": "White",
            "lidOptions": None,
        }
    },
    tiers={"CUP-8": ((D(10000), D(49999), D("0.25"), D("0.22")), (D(50000), None, D("0.21"), D("0.19")))},
    categories={"CUPS": {"name": "Paper cups", "min_discount_pct": None}},
)
P_HEAD = [c.header for c in wbk.PRODUCT_COLUMNS]
T_HEAD = [c.header for c in wbk.TIER_COLUMNS]


def book(
    products: list[list[Any]] | None = None,
    tiers: list[list[Any]] | None = None,
    categories: list[list[Any]] | None = None,
    p_head: list[str] | None = None,
) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "How to fill"
    for title, head, rows in (
        (wbk.PRODUCTS, p_head or P_HEAD, products),
        (wbk.TIERS, T_HEAD, tiers),
        (wbk.CATEGORIES, [c.header for c in wbk.CATEGORY_COLUMNS], categories),
    ):
        if rows is None:
            continue
        sheet = wb.create_sheet(title)
        sheet.append(head)
        for r in rows:
            sheet.append(r)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def plan(data: bytes) -> wbk.Plan:
    return wbk.plan(wbk.read_sheets(data), NOW)


def row(code: str, **cells: Any) -> list[Any]:
    by_header = {c.key: c.header for c in wbk.PRODUCT_COLUMNS}
    values = {by_header["code"]: code, **{by_header[k]: v for k, v in cells.items()}}
    return [values.get(h) for h in P_HEAD]


def test_blank_cells_keep_values_and_a_dash_clears() -> None:
    p = plan(book(products=[row("cup-8", capacity="250 ml", colours="-")]))  # code case is ignored
    assert p.ok and len(p.products) == 1
    changes = {c.field: (c.before, c.after) for c in p.products[0].changes}
    assert changes == {"capacity": ("240 ml", "250 ml"), "colours": ("White", None)}
    assert p.products[0].push  # website content changed -> a CMS draft


def test_an_unchanged_row_and_a_partial_sheet_change_nothing() -> None:
    only_two_columns = ["Code", "Name"]
    p = plan(book(products=[["CUP-8", "8 oz paper cup"]], p_head=only_two_columns))
    assert p.ok and p.products == [] and p.unchanged_products == 1 and p.tiers == []


def test_a_crm_only_change_is_not_sent_to_the_website() -> None:
    p = plan(book(products=[row("CUP-8", moq=12000, lead_max=14)]))
    assert p.ok and p.products[0].push is True  # MOQ is shown on the website (not a price)
    p = plan(book(products=[row("CUP-8", lead_max=14, carton_qty=800)]))
    assert p.ok and p.products[0].push is False


def test_prices_never_go_into_website_text() -> None:
    p = plan(book(products=[row("CUP-8", shortDescription="Only AED 0.25 per cup!")]))
    assert [(e.column, "price" in e.message) for e in p.errors] == [("Short description", True)]
    # numbers without a currency are product facts, not prices
    assert plan(
        book(products=[row("CUP-8", descriptionText="Oven safe up to 250 °C, 2 lb, 10% recycled")])
    ).ok


def test_a_new_product_needs_name_and_a_known_or_named_category() -> None:
    p = plan(book(products=[row("TRAY-2")]))
    assert {e.column for e in p.errors} == {"Name", "Category code"}
    p = plan(book(products=[row("TRAY-2", name="Foil tray", category_code="TRAYS")]))
    assert [e.column for e in p.errors] == ["Category code"]  # unknown category, no name to create it
    p = plan(
        book(products=[row("TRAY-2", name="Foil tray", category_code="TRAYS", category_name="Foil trays")])
    )
    assert p.ok and [c.code for c in p.categories] == ["TRAYS"] and p.products[0].is_new
    assert [w.message for w in p.warnings] == ["TRAY-2 has no price tiers: it cannot be quoted yet"]
    assert p.product_values["TRAY-2"]["unit"] == "pcs"


def test_typing_mistakes_are_reported_by_row_and_column() -> None:
    p = plan(
        book(
            products=[
                row("CUP-8", custom_printing="maybe", moq="ten", lead_min=12, lead_max=5),
                row("CUP-8", name="again"),
                row("CUP-8 X", name="bad code"),
            ]
        )
    )
    found = {(e.row, e.column) for e in p.errors}
    assert {(2, "Custom printing (Yes/No)"), (2, "Minimum order (MOQ)"), (3, "Code"), (4, "Code")} <= found
    assert not p.ok


def test_price_tiers_become_a_new_version_only_when_they_differ() -> None:
    same = [["CUP-8", 10000, 49999, "0.25", "0.22"], ["CUP-8", 50000, None, "0.21", "0.19"]]
    assert plan(book(tiers=same)).tiers == []
    cheaper = [["CUP-8", 10000, 49999, "0.24", "0.22"], ["CUP-8", 50000, None, "0.21", "0.19"]]
    p = plan(book(tiers=cheaper))
    assert p.ok and len(p.tiers) == 1 and p.tiers[0].after[0][2] == D("0.24")


def test_price_tier_errors_point_at_the_sheet_row() -> None:
    p = plan(
        book(
            tiers=[
                ["CUP-8", 10000, 49999, "0.25", "0.30"],  # minimum above list
                [None, None, None, None, None],  # blank row in between: rows keep their sheet numbers
                ["CUP-8", 40000, None, "0.21", None],  # overlaps row 2
                ["NOPE", 1, None, "1", None],
            ]
        )
    )
    found = {(e.sheet, e.row, e.column) for e in p.errors}
    assert ("Price tiers", 2, "Minimum price (AED)") in found
    assert ("Price tiers", 4, "From quantity") in found
    assert ("Price tiers", 5, "Code") in found


def test_tiers_for_a_new_product_in_the_same_file_are_accepted() -> None:
    p = plan(
        book(
            products=[row("BOX-1", name="Kraft box", category_code="CUPS")],
            tiers=[["BOX-1", 8000, None, "0.40", "0.35"]],
        )
    )
    assert p.ok and p.warnings == [] and [t.code for t in p.tiers] == ["BOX-1"]


def test_the_cms_draft_carries_no_price() -> None:
    p = plan(
        book(
            products=[row("CUP-8", capacity="250 ml", colours="-")],
            tiers=[["CUP-8", 10000, None, "0.20", "0.18"]],
        )
    )
    assert wbk.CMS_FIELDS.isdisjoint({"list_price", "min_price", "min_qty", "max_qty"})
    assert all("price" not in f for f in wbk.CMS_FIELDS) and p.ok


def test_a_file_without_the_sheets_is_refused() -> None:
    wb = Workbook()
    wb.active.title = "Sheet1"
    out = io.BytesIO()
    wb.save(out)
    assert not plan(out.getvalue()).ok
