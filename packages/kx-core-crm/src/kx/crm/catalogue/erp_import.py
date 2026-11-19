"""WP-C9 / D61: load Arifa's ERP stock list into the CRM.

The file is the ERP's own export (CSV, or an .xlsx with the same columns), unchanged:

    Category | Item Description | Brand | Unit | OpnStk | In | Out | ClosStk

Every row is kept, whatever its category: finished products, but also packing material (cartons, inners), raw
material (duplex board, foil coil) and scrap, so the CRM's stock is complete. Only **sellable** items (the
`... PRODUCT` categories, not scrap or cartons) can be quoted, linked to the website or shown to buyers.

The ERP has no item codes, so each item's CRM code is made from its description and unit
(`8325 ALUMINIUM CONTAINER` + `1*1000 PCS` -> `8325-ALUMINIUM-CONTAINER-1X1000-PCS`; a short hash when longer
than 64 characters). The same file imported again updates the same items. New items are not quotable until a
manager prices them (the product workbook); the import never touches prices. The closing stock becomes the
item's stock (`ClosStk`, in the ERP unit); then new sellable items are linked to their website product where
it is safe (`variants.link_unlinked`).
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Final

from openpyxl import load_workbook  # type: ignore[import-untyped]  # types-openpyxl not in uv.lock
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from kx.core.errors import RuleViolation
from kx.crm._shared.journal import Actor, record
from kx.crm.catalogue.variants import link_unlinked
from kx.crm.models import Product, ProductCategory, StockItem

COLUMNS: Final = ("Category", "Item Description", "Brand", "Unit", "OpnStk", "In", "Out", "ClosStk")
MAX_CODE: Final = 64
_NOT_SELLABLE_WORDS: Final = ("SCRAP", "OUTER CTN", " INNER ", "RM ")
_MEASURES: Final = (
    (re.compile(r"\b(PCS|PIECES|PC|NOS)\b"), "pcs"),
    (re.compile(r"\b(KG|KILO ?GRAM|KGS)\b"), "kg"),
    (re.compile(r"\bCARTONS?\b|\bCTN\b"), "carton"),
    (re.compile(r"\bPACKETS?\b|\bPKT\b"), "packet"),
    (re.compile(r"\bROLLS?\b"), "roll"),
)


@dataclass(frozen=True, slots=True)
class ErpRow:
    line: int
    category: str
    description: str
    brand: str | None
    unit: str  # the ERP "Unit": 1*1000 PCS, 1*1.400 KG, CARTON, KILO GRAM
    closing: Decimal


@dataclass(slots=True)
class ErpReport:
    rows: int = 0
    created: int = 0
    updated: int = 0
    sellable: int = 0
    not_sellable: int = 0
    linked: int = 0
    ambiguous: int = 0
    errors: list[str] = field(default_factory=list)


def item_code(description: str, unit: str) -> str:
    """A stable CRM code for an ERP item (the ERP has none)."""
    raw = f"{description} {unit}".upper().replace("*", "X")
    code = re.sub(r"[^A-Z0-9.]+", "-", raw).strip("-")
    if len(code) <= MAX_CODE:
        return code
    digest = hashlib.sha256(raw.encode()).hexdigest()[:8]
    return f"{code[: MAX_CODE - 9].rstrip('-')}-{digest}"


def measure(unit: str) -> str:
    """The base unit a buyer orders in (pcs, kg, carton, packet, roll); pcs when the ERP says nothing."""
    u = unit.upper()
    for pattern, name in _MEASURES:
        if pattern.search(u):
            return name
    return "pcs"


def pieces_per_pack(unit: str) -> int | None:
    """`4*50 PCS` -> 200 pieces in the ERP unit; None for weights or plain units."""
    m = re.match(r"^\s*(\d+)\s*[*X]\s*(\d+)\s*(PCS|PIECES|PC)\b", unit.upper())
    return int(m.group(1)) * int(m.group(2)) if m else None


def is_sellable(category: str, description: str) -> bool:
    """Finished goods are sellable; packing material, raw material and scrap are kept but never sold."""
    cat, desc = category.upper().strip(), f" {description.upper()} "
    if not cat.endswith("PRODUCT"):
        return False
    return not any(w in desc for w in _NOT_SELLABLE_WORDS)


def category_code(category: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", category.upper()).strip("_") or "ERP_OTHER"


def _number(text: object) -> Decimal:
    value = str(text or "").replace(",", "").strip()
    if not value:
        return Decimal(0)
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"not a number: {value!r}") from exc


def _records(data: bytes) -> list[list[str]]:
    if data[:2] == b"PK":  # an .xlsx
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        return [
            ["" if c is None else str(c) for c in row]
            for ws in wb.worksheets
            for row in ws.iter_rows(values_only=True)
        ]
    text = data.decode("utf-8-sig", errors="replace")
    dialect = csv.Sniffer().sniff(text[:4096], delimiters=",\t;") if text.strip() else csv.excel
    return list(csv.reader(io.StringIO(text), dialect))


def parse(data: bytes) -> tuple[list[ErpRow], list[str]]:
    """The ERP rows of the file and the problems (the header repeats on every ERP page: it is skipped)."""
    rows: list[ErpRow] = []
    errors: list[str] = []
    header: dict[str, int] | None = None
    for n, rec in enumerate(_records(data), start=1):
        cells = [c.strip() for c in rec]
        if not any(cells):
            continue
        if "Item Description" in cells and "Category" in cells:  # the header (repeated on every ERP page)
            header = {name: cells.index(name) for name in COLUMNS if name in cells}
            continue
        if header is None or len(cells) <= header.get("Item Description", 0):
            continue
        cols = header

        def get(name: str, cells: list[str] = cells, cols: dict[str, int] = cols) -> str:
            i = cols.get(name)
            return cells[i] if i is not None and i < len(cells) else ""

        description, unit = get("Item Description"), get("Unit")
        if not description:
            continue
        try:
            closing = _number(get("ClosStk"))
        except ValueError as exc:
            errors.append(f"line {n}: {exc}")
            continue
        category = get("Category") or "ERP OTHER"
        rows.append(ErpRow(n, category, description, get("Brand") or None, unit or "PCS", closing))
    if header is None:
        errors.append("no header row: the file needs the columns " + ", ".join(COLUMNS))
    return rows, errors


async def _category(session: AsyncSession, erp_category: str) -> str:
    code = category_code(erp_category)
    if await session.get(ProductCategory, code) is None:
        session.add(ProductCategory(code=code, name=erp_category.strip().title()))
        await session.flush()
    return code


async def import_erp(session: AsyncSession, data: bytes, *, actor: Actor, now: datetime) -> ErpReport:
    """Create or update every ERP item and its stock, then link new sellable items to the website."""
    rows, errors = parse(data)
    report = ErpReport(rows=len(rows), errors=errors)
    if not rows:
        problems = "; ".join(errors or ["empty file"])
        raise RuleViolation(f"the file has no ERP rows: {problems}", reason="erp_file")
    touched: list[uuid.UUID] = []
    for row in rows:
        code = item_code(row.description, row.unit)
        product = (
            await session.execute(select(Product).where(func.upper(Product.sku) == code).with_for_update())
        ).scalar_one_or_none()
        sellable = is_sellable(row.category, row.description)
        if product is None:
            product = Product(
                sku=code,
                name=row.description.strip(),
                category_code=await _category(session, row.category),
                unit=measure(row.unit),
                carton_qty=pieces_per_pack(row.unit),
                is_quotable=False,  # D8: no approved price yet; the manager prices it in the workbook
                is_active=sellable,
                spec={},
            )
            session.add(product)
            report.created += 1
        else:
            report.updated += 1
        product.sellable = sellable
        product.erp_category, product.erp_description = row.category.strip(), row.description.strip()
        product.erp_packing, product.brand = row.unit.strip(), row.brand
        await session.flush()
        touched.append(product.id)
        await _stock(session, product, row, now)
        report.sellable += sellable
        report.not_sellable += not sellable
    report.linked, report.ambiguous = await link_unlinked(session, touched)
    after = {k: v for k, v in summary(report).items() if k != "errors"}
    await record(session, actor, "catalogue.erp_imported", "catalogue", "erp", after=after)
    return report


async def _stock(session: AsyncSession, product: Product, row: ErpRow, now: datetime) -> None:
    item = (
        await session.execute(select(StockItem).where(StockItem.product_id == product.id).with_for_update())
    ).scalar_one_or_none()
    if item is None:
        item = StockItem(
            item_type="finished",  # a product row (raw rows are materials in the cost model, v2)
            product_id=product.id,
            quantity=row.closing,
            unit=row.unit.strip() or product.unit,
            source="erp",
        )
        session.add(item)
    else:
        item.quantity, item.source, item.updated_at = row.closing, "erp", now
        item.unit = row.unit.strip() or item.unit


def summary(report: ErpReport) -> dict[str, object]:
    return {
        "rows": report.rows,
        "created": report.created,
        "updated": report.updated,
        "sellable": report.sellable,
        "not_sellable": report.not_sellable,
        "linked_to_website": report.linked,
        "ambiguous_links": report.ambiguous,
        "errors": report.errors,
    }
