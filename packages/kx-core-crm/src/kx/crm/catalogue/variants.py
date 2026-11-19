"""D61: website products and their sellable variants (the ERP items).

A website product (`ac-8325`, "Aluminium Container & Lid") is what a buyer browses. What Arifa sells, prices
and stocks are ERP items: "8325 ALUMINIUM CONTAINER" 1*1000 PCS, "8325 LID" 1*1000 PCS, "45 CM ALUMINIUM
FOIL" in twenty roll weights, retail packs. Each ERP item is a CRM product linked to its website product
(`products.cms_product_id`), so the AI can explain the variants and the quote carries the exact item (a
container with its lid is two quote lines).

Linking is automatic where it is safe: an ERP item is linked to a website product when every one of the
product's words is in the item's description (its reference, or the editors' own `erp_match` words), the item
is of the product's family, and the trade / retail range agrees. An item two website products fit equally is
left unlinked (reported) for a person to decide; staff can always set or change the link.

What the AI sees is `VariantView`: stock as a word, price tiers as the quantities where a better price starts,
never an amount (the price guard still blocks any number it should not say).
"""

from __future__ import annotations

import itertools
import re
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Final

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from kx.core.contracts import Availability, VariantView
from kx.core.event_payloads import ProductPublished
from kx.crm.models import Product, ProductPriceTier, StockItem, WebsiteProduct

# Words an ERP description uses for each website family (an item must name its family to be linked)
FAMILY_WORDS: Final[dict[str, tuple[str, ...]]] = {
    "containers": ("CONTAINER", "CON", "LID"),
    "foil": ("FOIL",),
    "platters": ("PLATTER",),
    "cups-trays": ("CUP", "TRAY"),
    "pots": ("POT",),
    "microwave": ("PLA", "MICROWAVE", "MWB", "MWC"),
    "cutlery": ("SPOON", "FORK", "KNIFE", "KNIEF", "CUTLERY"),
    "cling-film": ("FILM", "CLING"),
}
RETAIL_WORD: Final = "RETAIL"
UNITS: Final = frozenset({"CM", "MM", "M", "ML", "L", "LTR", "G", "GM", "GSM", "KG", "MIC", "OZ"})


def tokens(text: str) -> list[str]:
    """Upper-case words; "30 CM" also reads as "30CM" and "1*1000" as "1X1000" (descriptions write both)."""
    t = text.upper().replace("*", "X")
    words = re.findall(r"[A-Z0-9.]+", t)
    joined = [a + b for a, b in itertools.pairwise(words) if a.replace(".", "").isdigit() and b.isalpha()]
    return words + joined


def key_terms(text: str) -> list[str]:
    """The words a description must contain: a number with its unit counts once, joined ("30 cm" -> 30CM)."""
    words = re.findall(r"[A-Z0-9.]+", text.upper())
    out: list[str] = []
    i = 0
    while i < len(words):
        nxt = words[i + 1] if i + 1 < len(words) else ""
        if words[i].replace(".", "").isdigit() and nxt in UNITS:
            out.append(words[i] + nxt)
            i += 2
        else:
            out.append(words[i])
            i += 1
    return out


@dataclass(frozen=True, slots=True)
class Candidate:
    cms_id: str
    required: tuple[str, ...]  # every one must be in the description
    optional: tuple[str, ...]  # the more of these, the better the fit
    family: str | None
    retail: bool


def candidate(w: WebsiteProduct) -> Candidate:
    required = tuple(key_terms(" ".join(w.match_words) if w.match_words else w.ref or ""))
    optional = tuple(t for t in tokens(w.cms_id.replace("-", " ")) if t not in required and not t.isdigit())
    return Candidate(w.cms_id, required, optional, w.family, w.range == "self")


def fits(description: str, c: Candidate) -> float | None:
    """How well an ERP description fits a website product (None = it does not)."""
    words = set(tokens(description))
    if not c.required or not set(c.required) <= words:
        return None
    family_words = FAMILY_WORDS.get(c.family or "", ())
    model_number = any(t.isdigit() and len(t) >= 4 for t in c.required)  # 8325, 14082: names one product
    if family_words and not model_number and not any(f in words for f in family_words):
        return None  # "30 CM" alone fits foil and cling film: the family word decides
    if (RETAIL_WORD in words) != c.retail:
        return None
    if not c.optional:
        return 1.0
    family_ids = {f.upper() for f in FAMILY_WORDS} | {"CON", "SR", "AC", "AP", "MW", "CUT"}
    extra = [o for o in c.optional if o not in family_ids]
    return 1.0 if not extra else sum(o in words for o in extra) / len(extra)


def best_match(description: str, candidates: Sequence[Candidate]) -> str | None:
    """The one website product an ERP item belongs to, or None (no fit, or a tie between two)."""
    scored = [(score, c.cms_id) for c in candidates if (score := fits(description, c)) is not None]
    if not scored:
        return None
    top = max(s for s, _ in scored)
    winners = [cid for s, cid in scored if s == top]
    return winners[0] if len(winners) == 1 else None


async def remember_website_product(session: AsyncSession, p: ProductPublished) -> WebsiteProduct:
    """Keep what the CMS published, so later ERP imports can link their items to it."""
    row = await session.get(WebsiteProduct, p.cms_id)
    if row is None:
        row = WebsiteProduct(cms_id=p.cms_id, name=p.name)
        session.add(row)
    row.name, row.ref, row.family = p.name, p.ref, p.category_code
    row.range = p.range or "trade"
    row.match_words = list(p.match_words)
    row.published = p.published
    await session.flush()
    return row


async def link_unlinked(session: AsyncSession, only: Iterable[uuid.UUID] | None = None) -> tuple[int, int]:
    """Link sellable, unlinked ERP items to their website product. Returns (linked, ambiguous)."""
    cands = [candidate(w) for w in (await session.execute(select(WebsiteProduct))).scalars()]
    if not cands:
        return 0, 0
    q = select(Product).where(
        Product.cms_product_id.is_(None), Product.sellable.is_(True), Product.erp_description.is_not(None)
    )
    if only is not None:
        q = q.where(Product.id.in_(list(only)))
    linked = ambiguous = 0
    for product in (await session.execute(q.with_for_update())).scalars():
        target = best_match(product.erp_description or "", cands)
        if target is not None:
            product.cms_product_id = target
            linked += 1
        elif any(fits(product.erp_description or "", c) is not None for c in cands):
            ambiguous += 1
    await session.flush()
    return linked, ambiguous


def availability(quantity: Decimal | None, limited_below: Decimal) -> Availability:
    if quantity is None:
        return "unknown"
    if quantity <= 0:
        return "made_to_order"
    return "limited" if quantity < limited_below else "in_stock"


async def variants_of(
    session: AsyncSession, website_product_id: str, *, limited_below: Decimal, now: datetime
) -> tuple[VariantView, ...]:
    """The sellable variants of a website product, as the AI may describe them (no amounts)."""
    products = list(
        (
            await session.execute(
                select(Product)
                .where(
                    func.lower(Product.cms_product_id) == website_product_id.lower(),
                    Product.sellable.is_(True),
                    Product.is_active.is_(True),
                )
                .order_by(Product.erp_description, Product.sku)
            )
        ).scalars()
    )
    if not products:
        return ()
    ids = [p.id for p in products]
    stock: dict[object, Decimal] = {}
    for item in (await session.execute(select(StockItem).where(StockItem.product_id.in_(ids)))).scalars():
        free = item.quantity - item.reserved_quantity
        stock[item.product_id] = stock.get(item.product_id, Decimal(0)) + free
    breaks: dict[object, set[Decimal]] = {}
    tiers = await session.execute(
        select(ProductPriceTier.product_id, ProductPriceTier.min_qty).where(
            ProductPriceTier.product_id.in_(ids), ProductPriceTier.valid_from <= now
        )
    )
    for product_id, min_qty in tiers.all():
        breaks.setdefault(product_id, set()).add(min_qty)
    out = []
    for p in products:
        steps = sorted(breaks.get(p.id, set()))
        out.append(
            VariantView(
                sku=p.sku,
                description=p.erp_description or p.name,
                packing=p.erp_packing,
                unit=p.unit,
                availability=availability(stock.get(p.id), limited_below),
                moq=p.moq,
                quantity_breaks=tuple(steps[1:]) if len(steps) > 1 else (),  # the first band is the base
                quotable=p.is_quotable and bool(steps),
            )
        )
    return tuple(out)
