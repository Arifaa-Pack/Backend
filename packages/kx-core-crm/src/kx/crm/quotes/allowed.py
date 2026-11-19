"""The closed list of amounts that may be stated to a buyer on a deal (§11.1): the figures of the issued
quote and the unit prices offered on it in negotiation, all in the quote currency.

One definition for both checks: the AI's `DealContext.allowed_prices` (built by `kx.crm.gateway`) and a
salesperson's own reply to the buyer (`kx.crm.handover`). Moved unchanged from the gateway (19 Nov).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kx.core.contracts import AllowedPrice
from kx.core.money import MONEY_PLACES, q
from kx.crm.models import NegotiationRound, Quote, QuoteLine, QuoteVersion
from kx.crm.quotes import service as quotes
from kx.crm.quotes.issuing import lines_of


@dataclass(frozen=True, slots=True)
class IssuedOffer:
    """The deal's latest issued quote version with its lines, and the amounts that may be stated."""

    number: str
    version: QuoteVersion
    lines: list[QuoteLine]
    prices: tuple[AllowedPrice, ...]


def allowed_prices(
    v: QuoteVersion, lines: list[QuoteLine], offers: list[NegotiationRound]
) -> tuple[AllowedPrice, ...]:
    """Closed list of amounts the AI may state, all in the quote currency."""
    cur = v.currency
    out = [
        AllowedPrice(amount=v.subtotal, currency=cur, label="subtotal"),
        AllowedPrice(amount=v.total, currency=cur, label="total"),
    ]
    if v.vat_amount:
        out.append(AllowedPrice(amount=v.vat_amount, currency=cur, label="vat"))
    for ln in lines:
        out += [
            AllowedPrice(amount=ln.unit_price, currency=cur, label=f"line {ln.line_no} unit price"),
            AllowedPrice(amount=ln.line_total, currency=cur, label=f"line {ln.line_no} total"),
        ]
    qty = lines[0].quantity if lines else None
    for r in offers:
        if r.offered_unit_price is None:
            continue
        out.append(AllowedPrice(amount=r.offered_unit_price, currency=cur, label=f"offer round {r.round_no}"))
        if (n := r.buyer_ask_quantity or qty) is not None:
            out.append(
                AllowedPrice(
                    amount=q(r.offered_unit_price * n, MONEY_PLACES),
                    currency=cur,
                    label=f"offer round {r.round_no} total",
                )
            )
    unique: dict[tuple[Decimal, str], AllowedPrice] = {}
    for p in out:
        unique.setdefault((p.amount.normalize(), p.currency), p)
    return tuple(unique.values())


async def issued_offer(s: AsyncSession, deal_id: uuid.UUID) -> IssuedOffer | None:
    """None until a quote was issued: then nothing may be stated."""
    issued = await quotes.latest_version(s, deal_id, quotes.ISSUED)
    if issued is None:
        return None
    number = await s.scalar(select(Quote.number).where(Quote.id == issued.quote_id))
    lines = await lines_of(s, issued.id)
    offers = list(
        (
            await s.execute(
                select(NegotiationRound)
                .where(NegotiationRound.quote_version_id == issued.id)
                .order_by(NegotiationRound.round_no)
            )
        ).scalars()
    )
    return IssuedOffer(number or "", issued, lines, allowed_prices(issued, lines, offers))


async def allowed_prices_for(s: AsyncSession, deal_id: uuid.UUID) -> tuple[AllowedPrice, ...]:
    offer = await issued_offer(s, deal_id)
    return offer.prices if offer is not None else ()
