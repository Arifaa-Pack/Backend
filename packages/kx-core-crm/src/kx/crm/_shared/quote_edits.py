"""What a salesperson changes on a quote (the approval screen, the change-request card): plain data.

The quotes slice applies it (`quotes/service.py`); the API schema builds it from the request body
(`_shared/schemas.py`), so it lives in `_shared/` and the schemas need no slice.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class LineEdit:
    line_no: int
    unit_price: Decimal | None = None
    quantity: Decimal | None = None


@dataclass(frozen=True, slots=True)
class QuoteEdits:
    """What the salesperson changed on the approval screen or the change-request card (§11.3, §13.3)."""

    lines: tuple[LineEdit, ...] = ()
    delivery_time_text: str | None = None
    delivery_place: str | None = None
    incoterm: str | None = None
    payment_terms_text: str | None = None
    note: str | None = None  # internal (approval card, timeline)
    buyer_note: str | None = None  # §13.3: what changed, sent to the buyer with the new version
    cover_note: str | None = None  # D9: the "open to discuss" line, message only
    currency: str | None = None  # the buyer asked for another currency; prices given are in it
