"""One product workbook for the whole catalogue (18 Nov, client decisions).

Staff download the current catalogue as one Excel file, change cells, and upload it back. One upload updates
both systems: **prices, tiers, MOQ and delivery days stay in the CRM; product details (never prices) go to the
CMS as drafts**, which a manager previews and publishes there (then the website and the AI's knowledge update
through the normal publish webhook).

Sheets: `Products` (one row per product), `Price tiers` (all tiers of each product listed), `Categories`
(optional) and a read-only `How to fill`. Rules, chosen so a half-filled or partly deleted sheet can never
lose data:

* **A blank cell keeps the current value; `-` clears it.** A missing column changes nothing.
* A product missing from the sheet is kept. To stop selling it: Active = No.
* A product that appears in `Price tiers` gets exactly those rows as its next price-list version (only when
  they differ from the current one); a product not in that sheet keeps its prices. Open quotes keep their own
  snapshot (§10).
* Product text goes to the website, so an amount with a currency in it ("AED 0.25", "$10") is an error.
* **Upload -> preview -> apply.** The preview lists every change and every error by sheet and row; nothing is
  saved until apply, and apply saves all of it or nothing. Apply needs the token of the preview: if the
  catalogue changed in between (or the file is another one), it refuses and asks for a new preview.

Who: manager, MD, admin (a person with MFA). Every change is in the audit trail (product and price-list
history); price changes need no approval (client decision) and the MD sees them in the history.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Final, Literal

from openpyxl import Workbook, load_workbook  # type: ignore[import-untyped]  # types-openpyxl not in uv.lock
from openpyxl.styles import Font, PatternFill  # type: ignore[import-untyped]
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from kx.core import events as ev
from kx.core.errors import Conflict, RuleViolation
from kx.core.event_payloads import ProductContentChanged
from kx.core.price_guard import extract_prices
from kx.crm._shared.journal import Actor, Emit, record
from kx.crm.models import Product, ProductCategory, ProductPriceTier
from kx.crm.pricing.api import RowError, check_xlsx, current_tiers, current_tiers_many, parse_rows

PRODUCTS: Final = "Products"
TIERS: Final = "Price tiers"
CATEGORIES: Final = "Categories"
HELP: Final = "How to fill"
CLEAR: Final = "-"
_CODE = re.compile(r"^[A-Z0-9][A-Z0-9._/-]{0,63}$")

Kind = Literal["code", "text", "int", "bool", "pct"]
Target = Literal["key", "crm", "spec", "category", "website"]


@dataclass(frozen=True, slots=True)
class Column:
    key: str
    header: str
    kind: Kind
    target: Target
    note: str


# Product details the CMS gets are `target="spec"` plus name, category, unit, MOQ and custom printing.
# The spec keys are the CMS field names (docs/handoff/CMS_API.md): the CRM copy and the CMS use the same names.
PRODUCT_COLUMNS: Final = (
    Column(
        "code",
        "Code",
        "code",
        "key",
        "Required. The product code; never change it (CRM and CMS match on it).",
    ),
    Column("name", "Name", "text", "crm", "Required for a new product."),
    Column("category_code", "Category code", "code", "crm", "Required for a new product. See Categories."),
    Column("category_name", "Category name", "text", "category", "Only to create a new category on the fly."),
    Column("unit", "Unit", "text", "crm", "pcs, kg, roll ... (blank for a new product = pcs)."),
    Column("moq", "Minimum order (MOQ)", "int", "crm", "Whole number of units."),
    Column("carton_qty", "Pieces per carton", "int", "crm", "Whole number."),
    Column("lead_min", "Delivery days from", "int", "crm", "Working days after advance payment."),
    Column("lead_max", "Delivery days to", "int", "crm", "Not below 'Delivery days from'."),
    Column(
        "custom_printing",
        "Custom printing (Yes/No)",
        "bool",
        "crm",
        "Printed variants are priced by the salesperson.",
    ),
    Column("active", "Active (Yes/No)", "bool", "crm", "No = stop selling it (it is kept for old quotes)."),
    Column(
        "website",
        "Send to website (Yes/No)",
        "bool",
        "website",
        "Yes = save as a CMS draft for the manager to publish.",
    ),
    Column("shortDescription", "Short description", "text", "spec", "Website text. No prices."),
    Column("descriptionText", "Description", "text", "spec", "Website text. No prices."),
    Column("material", "Material", "text", "spec", ""),
    Column("capacity", "Capacity", "text", "spec", "e.g. 750 ml"),
    Column("dimensions", "Dimensions", "text", "spec", "e.g. 120 x 80 x 45 mm"),
    Column("thickness", "Thickness", "text", "spec", ""),
    Column("colours", "Colours", "text", "spec", ""),
    Column("lidOptions", "Lid options", "text", "spec", ""),
)
TIER_COLUMNS: Final = (
    Column("code", "Code", "code", "key", "Required. One row per quantity tier."),
    Column("min_qty", "From quantity", "int", "crm", "Required."),
    Column("max_qty", "To quantity", "int", "crm", "Blank = no upper limit (last tier)."),
    Column("list_price", "List price (AED)", "pct", "crm", "Required. Per unit."),
    Column(
        "min_price", "Minimum price (AED)", "pct", "crm", "Lowest the team may accept. Never shown to buyers."
    ),
)
CATEGORY_COLUMNS: Final = (
    Column("code", "Code", "code", "key", "Required."),
    Column("name", "Name", "text", "crm", "Required for a new category."),
    Column(
        "min_discount_pct", "Minimum discount %", "pct", "crm", "Optional: minimum = list price less this %."
    ),
)
CMS_FIELDS: Final = frozenset({"name", "category_code", "unit", "moq", "custom_printing"}) | {
    c.key for c in PRODUCT_COLUMNS if c.target == "spec"
}
_YES: Final = frozenset({"yes", "y", "true", "1", "نعم"})
_NO: Final = frozenset({"no", "n", "false", "0", "لا"})


class _Clear:
    def __repr__(self) -> str:
        return "<clear>"


CLEARED: Final = _Clear()
Cell = str | int | bool | Decimal | _Clear


@dataclass(frozen=True, slots=True)
class Issue:
    sheet: str
    row: int
    column: str
    message: str


@dataclass(frozen=True, slots=True)
class FieldChange:
    field: str
    before: Any
    after: Any


@dataclass(frozen=True, slots=True)
class ProductPlan:
    code: str
    row: int
    is_new: bool
    changes: tuple[FieldChange, ...]
    website: bool
    push: bool  # save the content in the CMS as a draft


@dataclass(frozen=True, slots=True)
class TierPlan:
    code: str
    before: tuple[tuple[Decimal, Decimal | None, Decimal, Decimal | None], ...]
    after: tuple[tuple[Decimal, Decimal | None, Decimal, Decimal | None], ...]


@dataclass(frozen=True, slots=True)
class CategoryPlan:
    code: str
    is_new: bool
    changes: tuple[FieldChange, ...]


@dataclass(slots=True)
class Plan:
    products: list[ProductPlan] = field(default_factory=list)
    tiers: list[TierPlan] = field(default_factory=list)
    categories: list[CategoryPlan] = field(default_factory=list)
    errors: list[Issue] = field(default_factory=list)
    warnings: list[Issue] = field(default_factory=list)
    unchanged_products: int = 0
    # the parsed values, for apply
    product_values: dict[str, dict[str, Cell]] = field(default_factory=dict)
    category_values: dict[str, dict[str, Cell]] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass(frozen=True, slots=True)
class Snapshot:
    """What the CRM holds now (read once; the plan is computed against it)."""

    products: Mapping[str, Mapping[str, Any]]  # code -> CRM fields + spec keys + website
    tiers: Mapping[str, tuple[tuple[Decimal, Decimal | None, Decimal, Decimal | None], ...]]
    categories: Mapping[str, Mapping[str, Any]]


# ------------------------------------------------------------------------------------------- reading
def _norm_header(h: Any) -> str:
    return re.sub(r"\s+", " ", str(h or "")).strip().lower()


def read_sheets(data: bytes) -> dict[str, list[tuple[int, dict[str, Any]]]]:
    """{sheet: [(row number, {column key: raw cell})]} for the three data sheets (headers by name)."""
    check_xlsx(data)  # L2: no zip bomb reaches openpyxl
    try:
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:
        raise RuleViolation("the file is not a readable .xlsx workbook") from exc
    out: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    for name, columns in ((PRODUCTS, PRODUCT_COLUMNS), (TIERS, TIER_COLUMNS), (CATEGORIES, CATEGORY_COLUMNS)):
        sheet = next((wb[s] for s in wb.sheetnames if s.strip().lower() == name.lower()), None)
        if sheet is None:
            continue
        rows = sheet.iter_rows(values_only=True)
        header = next(rows, ())
        by_header = {_norm_header(c.header): c.key for c in columns}
        keys = [by_header.get(_norm_header(h)) for h in header]
        parsed: list[tuple[int, dict[str, Any]]] = []
        for n, raw in enumerate(rows, start=2):
            cells = {k: v for k, v in zip(keys, raw, strict=False) if k is not None}
            if any(v not in (None, "") for v in cells.values()):
                parsed.append((n, cells))
        out[name] = parsed
    wb.close()
    return out


def _cell(raw: Any, kind: Kind) -> Cell | None:
    """None = blank (keep); CLEARED = '-'; else the typed value. Raises ValueError with a message."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    if isinstance(raw, str) and raw.strip() == CLEAR:
        return CLEARED
    if kind == "code":  # the format is checked for new codes only (existing data may predate the rule)
        return str(raw).strip().upper()
    if kind == "text":
        return str(raw).strip()
    if kind == "bool":
        if isinstance(raw, bool):
            return raw
        word = str(raw).strip().lower()
        if word in _YES:
            return True
        if word in _NO:
            return False
        raise ValueError("write Yes or No")
    text = repr(raw) if isinstance(raw, float) else str(raw)  # spreadsheet floats: through their text form
    try:
        number = Decimal(text.replace(",", "").strip())
    except Exception as exc:
        raise ValueError(f"not a number: {raw!r}") from exc
    if kind == "int":
        if number != number.to_integral_value() or number < 0:
            raise ValueError("a whole number, 0 or more")
        return int(number)
    return number


# ------------------------------------------------------------------------------------------- planning
def _money_in(text: str) -> bool:
    return any(m.currency is not None and not m.is_percentage for m in extract_prices(text))


def plan(sheets: Mapping[str, list[tuple[int, dict[str, Any]]]], now: Snapshot) -> Plan:  # noqa: PLR0915
    """Pure: validate the sheets and compute every change against `now`."""
    p = Plan()
    if not any(k in sheets for k in (PRODUCTS, TIERS, CATEGORIES)):
        p.errors.append(Issue("", 0, "", f"no sheet named {PRODUCTS!r}, {TIERS!r} or {CATEGORIES!r}"))
        return p

    # ---- categories
    sheet_categories: dict[str, dict[str, Cell]] = {}
    for n, raw in sheets.get(CATEGORIES, []):
        values = _typed(CATEGORIES, n, raw, CATEGORY_COLUMNS, p)
        code = values.get("code")
        if not isinstance(code, str):
            p.errors.append(Issue(CATEGORIES, n, "Code", "required"))
            continue
        if code in sheet_categories:
            p.errors.append(Issue(CATEGORIES, n, "Code", f"{code} is listed twice"))
            continue
        pct = values.get("min_discount_pct")
        if isinstance(pct, Decimal) and not Decimal(0) <= pct <= Decimal(100):
            p.errors.append(Issue(CATEGORIES, n, "Minimum discount %", "between 0 and 100"))
        current = now.categories.get(code)
        if current is None and not _CODE.match(code):
            p.errors.append(Issue(CATEGORIES, n, "Code", "letters, digits and . _ / - only (max 64)"))
        if current is None and not isinstance(values.get("name"), str):
            p.errors.append(Issue(CATEGORIES, n, "Name", "required for a new category"))
        sheet_categories[code] = values
        changes = _diff(current or {}, values, ("name", "min_discount_pct"))
        if current is None or changes:
            p.categories.append(CategoryPlan(code, current is None, changes))
    p.category_values = sheet_categories

    # ---- products
    seen: dict[str, int] = {}
    for n, raw in sheets.get(PRODUCTS, []):
        values = _typed(PRODUCTS, n, raw, PRODUCT_COLUMNS, p)
        code = values.get("code")
        if not isinstance(code, str):
            p.errors.append(Issue(PRODUCTS, n, "Code", "required"))
            continue
        if code in seen:
            p.errors.append(Issue(PRODUCTS, n, "Code", f"{code} is already on row {seen[code]}"))
            continue
        seen[code] = n
        current = now.products.get(code)
        is_new = current is None
        if is_new and not _CODE.match(code):
            p.errors.append(Issue(PRODUCTS, n, "Code", "letters, digits and . _ / - only (max 64)"))
        if is_new and not isinstance(values.get("name"), str):
            p.errors.append(Issue(PRODUCTS, n, "Name", "required for a new product"))
        for key in ("name", "category_code", "code", "unit"):
            if values.get(key) is CLEARED:
                p.errors.append(Issue(PRODUCTS, n, _header(key), "cannot be cleared"))
        category = values.get("category_code")
        if is_new and not isinstance(category, str):
            p.errors.append(Issue(PRODUCTS, n, "Category code", "required for a new product"))
        if (
            isinstance(category, str)
            and category not in now.categories
            and category not in sheet_categories
            and category != (current or {}).get("category_code")
        ):
            if isinstance(values.get("category_name"), str):
                sheet_categories[category] = {"code": category, "name": values["category_name"]}
                p.categories.append(
                    CategoryPlan(category, True, (FieldChange("name", None, values["category_name"]),))
                )
            else:
                p.errors.append(
                    Issue(
                        PRODUCTS,
                        n,
                        "Category code",
                        f"unknown category {category}: add it to the Categories sheet or fill Category name",
                    )
                )
        compare = tuple(
            k for k in (c.key for c in PRODUCT_COLUMNS) if k not in ("code", "category_name", "website")
        )
        changed = {c.field for c in _diff(current or {}, values, compare)}
        lo = _resolved(values, current, "lead_min")
        hi = _resolved(values, current, "lead_max")
        if {"lead_min", "lead_max"} & changed and isinstance(lo, int) and isinstance(hi, int) and hi < lo:
            p.errors.append(Issue(PRODUCTS, n, "Delivery days to", "below 'Delivery days from'"))
        for key in ("moq", "carton_qty"):
            if key in changed and values.get(key) == 0:
                p.errors.append(Issue(PRODUCTS, n, _header(key), "must be at least 1"))
        for col in PRODUCT_COLUMNS:
            v = values.get(col.key)
            if col.kind == "text" and col.key in changed and isinstance(v, str) and _money_in(v):
                p.errors.append(
                    Issue(PRODUCTS, n, col.header, "contains a price; prices never go to the website")
                )
        if is_new and values.get("unit") is None:
            values["unit"] = "pcs"
        website = values.get("website")
        if website is None or website is CLEARED:
            website = bool(current and current.get("website")) if not is_new else True
        assert isinstance(website, bool)
        changes = _diff(current or {}, values, compare)
        content_changed = is_new or any(c.field in CMS_FIELDS for c in changes)
        newly_on_site = website and not (current or {}).get("website")
        push = website and (content_changed or newly_on_site)
        if is_new or changes or push:
            p.products.append(ProductPlan(code, n, is_new, changes, website, push))
        else:
            p.unchanged_products += 1
        p.product_values[code] = values

    # ---- price tiers (the existing price-list rules: positive, minimum <= list, no overlap)
    known = set(now.products) | set(p.product_values)
    tier_rows = sheets.get(TIERS, [])
    if tier_rows:
        tuples: list[tuple[Any, ...]] = []
        numbers: list[int] = []
        for n, raw in tier_rows:
            code = str(raw.get("code") or "").strip().upper()
            tuples.append(
                (
                    code,
                    raw.get("min_qty"),
                    raw.get("max_qty"),
                    raw.get("list_price"),
                    raw.get("min_price"),
                    None,
                )
            )
            numbers.append(n)
        parsed, errors = parse_rows(tuples, known)
        index = {i + 2: numbers[i] for i in range(len(numbers))}  # parse_rows counts from row 2 in list order
        for e in errors:
            p.errors.append(Issue(TIERS, index.get(e.row, e.row), _tier_header(e), e.error))
        by_code: dict[str, list[tuple[Decimal, Decimal | None, Decimal, Decimal | None]]] = {}
        for t in parsed:
            by_code.setdefault(t.sku, []).append((t.min_qty, t.max_qty, t.list_price, t.min_price))
        for code, rows in sorted(by_code.items()):
            after = tuple(sorted(rows, key=lambda r: r[0]))
            before = tuple(now.tiers.get(code, ()))
            if _norm_tiers(after) != _norm_tiers(before):
                p.tiers.append(TierPlan(code, before, after))
    priced = {t.code for t in p.tiers} | {c for c, t in now.tiers.items() if t}
    for prod in p.products:
        if prod.is_new and prod.code not in priced:
            p.warnings.append(
                Issue(PRODUCTS, prod.row, "Code", f"{prod.code} has no price tiers: it cannot be quoted yet")
            )
    return p


def _norm_tiers(
    rows: Iterable[tuple[Decimal, Decimal | None, Decimal, Decimal | None]],
) -> tuple[tuple[str, ...], ...]:
    return tuple(tuple("" if v is None else str(Decimal(v).normalize()) for v in r) for r in rows)


def _tier_header(e: RowError) -> str:
    return {c.key: c.header for c in TIER_COLUMNS}.get(e.field, "Code" if e.field == "sku" else e.field)


def _header(key: str) -> str:
    return next(c.header for c in PRODUCT_COLUMNS if c.key == key)


def _typed(sheet: str, n: int, raw: Mapping[str, Any], columns: Sequence[Column], p: Plan) -> dict[str, Cell]:
    out: dict[str, Cell] = {}
    for col in columns:
        if col.key not in raw:
            continue
        try:
            value = _cell(raw[col.key], col.kind)
        except ValueError as exc:
            p.errors.append(Issue(sheet, n, col.header, str(exc)))
            continue
        if value is not None:
            out[col.key] = value
    return out


def _resolved(values: Mapping[str, Cell], current: Mapping[str, Any] | None, key: str) -> Any:
    v = values.get(key)
    if v is CLEARED:
        return None
    return v if v is not None else (current or {}).get(key)


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, Decimal) or isinstance(b, Decimal):
        try:
            return Decimal(str(a)) == Decimal(str(b))
        except Exception:
            return False
    return bool(a == b)


def _diff(
    current: Mapping[str, Any], values: Mapping[str, Cell], keys: Iterable[str]
) -> tuple[FieldChange, ...]:
    out: list[FieldChange] = []
    for key in keys:
        if key not in values:
            continue
        new = None if values[key] is CLEARED else values[key]
        old = current.get(key)
        if old in ("", None) and new in ("", None):
            continue
        if not _same(old, new):
            out.append(FieldChange(key, old, new))
    return tuple(out)


# ------------------------------------------------------------------------------------------- database
async def snapshot(session: AsyncSession, now: datetime) -> Snapshot:
    products: dict[str, dict[str, Any]] = {}
    tiers: dict[str, tuple[tuple[Decimal, Decimal | None, Decimal, Decimal | None], ...]] = {}
    all_products = list((await session.execute(select(Product))).scalars())
    by_product = await current_tiers_many(session, [p.id for p in all_products], now)  # review N1: 1 query
    for prod in all_products:
        products[prod.sku.upper()] = _product_fields(prod)
        tiers[prod.sku.upper()] = tuple(
            (t.min_qty, t.max_qty, t.list_price, t.min_price) for t in by_product[prod.id]
        )
    categories = {
        c.code: {"name": c.name, "min_discount_pct": c.min_discount_pct}
        for c in (await session.execute(select(ProductCategory))).scalars()
    }
    return Snapshot(products, tiers, categories)


def _product_fields(prod: Product) -> dict[str, Any]:
    out: dict[str, Any] = {
        "name": prod.name,
        "category_code": prod.category_code,
        "unit": prod.unit,
        "moq": prod.moq,
        "carton_qty": prod.carton_qty,
        "lead_min": prod.default_lead_days_min,
        "lead_max": prod.default_lead_days_max,
        "custom_printing": prod.custom_printing,
        "active": prod.is_active,
        "website": prod.cms_product_id is not None or bool((prod.spec or {}).get("_website")),
    }
    for col in PRODUCT_COLUMNS:
        if col.target == "spec":
            value = (prod.spec or {}).get(col.key)
            out[col.key] = None if value in (None, "") else str(value)
    return out


async def _stamp(session: AsyncSession) -> str:
    """Changes whenever the catalogue changes (products, tiers, categories)."""
    row = (
        await session.execute(
            select(func.count(Product.id), func.max(Product.updated_at)).select_from(Product)
        )
    ).one()
    tier = (
        await session.execute(
            select(func.count(ProductPriceTier.id), func.max(ProductPriceTier.updated_at)).select_from(
                ProductPriceTier
            )
        )
    ).one()
    cats = (
        await session.execute(
            select(ProductCategory.code, ProductCategory.name, ProductCategory.min_discount_pct).order_by(
                ProductCategory.code
            )
        )
    ).all()
    digest = hashlib.sha256(repr([tuple(c) for c in cats]).encode()).hexdigest()[:16]
    return f"{row[0]}|{row[1]}|{tier[0]}|{tier[1]}|{digest}"


def _token(data: bytes, stamp: str) -> str:
    return hashlib.sha256(data + b"\x00" + stamp.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class Preview:
    token: str
    plan: Plan


async def preview(session: AsyncSession, data: bytes, now: datetime) -> Preview:
    sheets = await asyncio.to_thread(read_sheets, data)  # review B2: openpyxl parsing off the event loop
    return Preview(_token(data, await _stamp(session)), plan(sheets, await snapshot(session, now)))


@dataclass(frozen=True, slots=True)
class Applied:
    products_created: int
    products_updated: int
    price_lists_changed: int
    categories_changed: int
    website_drafts: int


async def apply(session: AsyncSession, data: bytes, token: str, *, actor: Actor, now: datetime) -> Applied:
    """Apply a previewed workbook in the caller's transaction: everything or nothing."""
    # serialise uploads: one apply at a time per company (the stamp check below then sees the latest state)
    await session.execute(select(func.pg_advisory_xact_lock(0x6B78776B62)))
    if _token(data, await _stamp(session)) != token:
        raise Conflict("the catalogue changed since the preview, or this is another file: preview it again")
    sheets = await asyncio.to_thread(read_sheets, data)  # review B2
    result = plan(sheets, await snapshot(session, now))
    if not result.ok:
        raise RuleViolation(f"the workbook has {len(result.errors)} error(s): preview it to see them")

    for cat in result.categories:
        values = result.category_values.get(cat.code, {})
        row = await session.get(ProductCategory, cat.code)
        before = None if row is None else {"name": row.name, "min_discount_pct": row.min_discount_pct}
        if row is None:
            row = ProductCategory(code=cat.code, name=str(values.get("name") or cat.code))
            session.add(row)
        for c in cat.changes:
            setattr(row, c.field, c.after)
        await session.flush()
        await record(
            session,
            actor,
            "catalogue.category_created" if before is None else "catalogue.category_updated",
            "product_category",
            cat.code,
            before=before,
            after={"name": row.name, "min_discount_pct": row.min_discount_pct},
        )

    existing = {
        p.sku.upper(): p for p in (await session.execute(select(Product).with_for_update())).scalars()
    }
    pushed = created = updated = 0
    for prod_plan in result.products:
        values = result.product_values[prod_plan.code]
        prod = existing.get(prod_plan.code)
        before = None if prod is None else _audit(prod)
        if prod is None:
            prod = Product(
                sku=prod_plan.code,
                name=str(values["name"]),
                category_code=str(values["category_code"]),
                is_quotable=False,
                spec={},
            )
            session.add(prod)
            existing[prod_plan.code] = prod
            created += 1
        else:
            updated += 1
        _set_fields(prod, values)
        if prod_plan.website and prod.cms_product_id is None:
            prod.spec = {**(prod.spec or {}), "_website": True}  # remembered until the CMS id comes back
        await session.flush()
        emit: tuple[Emit, ...] = ()
        if prod_plan.push:
            emit = (Emit(ev.CATALOGUE_PRODUCT_CONTENT, "product", prod.id, _content(prod, values, result)),)
            pushed += 1
        await record(
            session,
            actor,
            "product.created" if before is None else "product.updated",
            "product",
            prod.id,
            before=before,
            after={**_audit(prod), "source": "workbook"},
            emit=emit,
        )

    for tier_plan in result.tiers:
        prod = existing[tier_plan.code]
        for lo, hi, lp, mp in tier_plan.after:
            tier = ProductPriceTier(
                product_id=prod.id,
                min_qty=lo,
                max_qty=hi,
                list_price=lp,
                min_price=mp,
                currency="AED",
                valid_from=now,
                created_by=actor.user_id,
            )
            session.add(tier)
            await session.flush()
            await record(
                session,
                actor,
                "price_tier.created",
                "price_tier",
                tier.id,
                after={
                    "product_id": prod.id,
                    "min_qty": lo,
                    "max_qty": hi,
                    "list_price": lp,
                    "min_price": mp,
                    "valid_from": now,
                    "source": "workbook",
                },
            )
    priced = {t.code for t in result.tiers}
    for code in {pp.code for pp in result.products} | priced:
        prod = existing[code]
        has_tiers = code in priced or bool(await current_tiers(session, prod.id, now))
        prod.is_quotable = has_tiers and prod.is_active
    await session.flush()
    summary = Applied(created, updated, len(result.tiers), len(result.categories), pushed)
    await record(
        session,
        actor,
        "catalogue.workbook_applied",
        "catalogue",
        now.isoformat(),
        after={
            "products_created": created,
            "products_updated": updated,
            "price_lists_changed": len(result.tiers),
            "categories_changed": len(result.categories),
            "website_drafts": pushed,
            "file_sha256": hashlib.sha256(data).hexdigest(),
        },
    )
    return summary


_CRM_ATTR: Final = {
    "name": "name",
    "category_code": "category_code",
    "unit": "unit",
    "moq": "moq",
    "carton_qty": "carton_qty",
    "lead_min": "default_lead_days_min",
    "lead_max": "default_lead_days_max",
    "custom_printing": "custom_printing",
    "active": "is_active",
}


def _set_fields(prod: Product, values: Mapping[str, Cell]) -> None:
    spec = dict(prod.spec or {})
    for key, value in values.items():
        new = None if value is CLEARED else value
        if key in _CRM_ATTR:
            if key in ("custom_printing", "active") and new is None:
                continue
            setattr(prod, _CRM_ATTR[key], new)
        elif key in {c.key for c in PRODUCT_COLUMNS if c.target == "spec"}:
            if new is None:
                spec.pop(key, None)
            else:
                spec[key] = new
    prod.spec = spec


def _audit(prod: Product) -> dict[str, Any]:
    return {k: v for k, v in _product_fields(prod).items() if k != "website"}


def _content(prod: Product, values: Mapping[str, Cell], result: Plan) -> ProductContentChanged:
    """The CMS draft: descriptive fields only (this function never sees a price)."""
    fields: dict[str, str | int | bool] = {}
    cleared: list[str] = []
    for key in CMS_FIELDS - {"name", "category_code"}:
        v = values.get(key)
        if v is CLEARED:
            cleared.append(key)
        elif isinstance(v, str | int | bool) and not isinstance(v, Decimal):
            fields[key] = v
    category = result.category_values.get(prod.category_code, {})
    return ProductContentChanged(
        code=prod.sku,
        name=prod.name,
        category_code=prod.category_code,
        category_name=str(category.get("name")) if isinstance(category.get("name"), str) else None,
        fields=fields,
        cleared=sorted(cleared),
    )


# ------------------------------------------------------------------------------------------- download
_HEAD = Font(bold=True, color="FFFFFF")
_FILL = PatternFill("solid", fgColor="1F3864")


async def download(session: AsyncSession, now: datetime) -> bytes:
    """The current catalogue in the upload layout, so staff edit cells instead of typing from scratch."""
    snap = await snapshot(session, now)
    wb = Workbook()
    help_ws = wb.active
    assert help_ws is not None
    help_ws.title = HELP
    for line in _HELP_LINES:
        help_ws.append([line])
    help_ws.column_dimensions["A"].width = 120
    _sheet(wb, PRODUCTS, PRODUCT_COLUMNS, ({"code": code, **f} for code, f in sorted(snap.products.items())))
    _sheet(
        wb,
        TIERS,
        TIER_COLUMNS,
        (
            {"code": code, "min_qty": lo, "max_qty": hi, "list_price": lp, "min_price": mp}
            for code, rows in sorted(snap.tiers.items())
            for lo, hi, lp, mp in rows
        ),
    )
    _sheet(
        wb, CATEGORIES, CATEGORY_COLUMNS, ({"code": code, **c} for code, c in sorted(snap.categories.items()))
    )
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def _sheet(wb: Workbook, title: str, columns: Sequence[Column], rows: Iterable[Mapping[str, Any]]) -> None:
    ws = wb.create_sheet(title)
    ws.append([c.header for c in columns])
    for i, c in enumerate(columns, start=1):
        cell = ws.cell(row=1, column=i)
        cell.font, cell.fill = _HEAD, _FILL
        ws.column_dimensions[cell.column_letter].width = max(14, min(40, len(c.header) + 4))
    ws.freeze_panes = "B2"
    for r in rows:
        ws.append([_out(r.get(c.key), c.kind) for c in columns])


def _out(value: Any, kind: Kind) -> Any:
    if value is None:
        return None
    if kind == "bool":
        return "Yes" if value else "No"
    if isinstance(value, Decimal):  # exact text, never a float (money rule); whole quantities as numbers
        if kind == "int" and value == value.to_integral_value():
            return int(value)
        return format(value.normalize(), "f")
    return value


_HELP_LINES: Final = (
    "PRODUCT WORKBOOK: how to fill",
    "",
    "1. Change cells in the sheets Products, Price tiers and Categories, then upload the file in the CRM.",
    "2. The CRM shows a preview of every change and every error (sheet + row).",
    "   Nothing is saved until you press Apply.",
    "3. Apply saves everything or nothing. Prices stay in the CRM; product details go to the website",
    "   as DRAFTS:",
    "   a manager opens them in the CMS, checks the preview and publishes.",
    "",
    "RULES",
    "- A blank cell keeps the current value. Type - (a dash) to clear a value.",
    "- Never change a Code: the CRM and the website match products by code. A new code = a new product.",
    "- A product missing from the sheet is kept. To stop selling it, set Active = No.",
    "- Price tiers: list ALL tiers of a product (one row each).",
    "  A product not in this sheet keeps its prices.",
    "  Tiers may not overlap; the minimum price may not be above the list price; prices are in AED per unit.",
    "- No prices in any text column (they go to the website).",
    "- Yes / No columns accept Yes, No, Y, N.",
    "- Photos and videos are added in the CMS, never here.",
    "",
    "COLUMNS",
    *(f"- {c.header}: {c.note}" for c in PRODUCT_COLUMNS + TIER_COLUMNS + CATEGORY_COLUMNS if c.note),
)
