"""What other CRM slices may use from the `orders` slice (ADR-0001 rule 5).

Another slice, or `kx.crm.deps`, imports `orders` only through this module (import-linter enforces it):
everything else in `kx/crm/orders/` is internal to the slice. Adding a name here widens the slice's
interface, so it is a reviewed change. Names resolve lazily (PEP 562): importing this module loads
nothing, so the slices that call each other (quotes and approvals) import in the same order as before.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from kx.crm._shared.surface import resolve

if TYPE_CHECKING:
    from kx.crm.orders.service import deal_of_order, mark_won, record_payment, set_stock, update_order_status

# public name -> the module that defines it
_EXPORTS: dict[str, str] = {
    "deal_of_order": "kx.crm.orders.service",
    "mark_won": "kx.crm.orders.service",
    "record_payment": "kx.crm.orders.service",
    "set_stock": "kx.crm.orders.service",
    "update_order_status": "kx.crm.orders.service",
}

__all__ = [
    "deal_of_order",
    "mark_won",
    "record_payment",
    "set_stock",
    "update_order_status",
]


def __getattr__(name: str) -> Any:
    return resolve(globals(), _EXPORTS, name)
