"""What other CRM slices may use from the `deals` slice (ADR-0001 rule 5).

Another slice, or `kx.crm.deps`, imports `deals` only through this module (import-linter enforces it):
everything else in `kx/crm/deals/` is internal to the slice. Adding a name here widens the slice's
interface, so it is a reviewed change. Names resolve lazily (PEP 562): importing this module loads
nothing, so the slices that call each other (quotes and approvals) import in the same order as before.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from kx.crm._shared.surface import resolve

if TYPE_CHECKING:
    from kx.crm.deals.service import (
        add_note,
        contact_of,
        get_deal,
        hand_over,
        move_stage,
        patch_requirement,
        requirements_of,
        set_next_action,
    )
    from kx.crm.deals.views import deal_detail, visible_to

# public name -> the module that defines it
_EXPORTS: dict[str, str] = {
    "add_note": "kx.crm.deals.service",
    "contact_of": "kx.crm.deals.service",
    "deal_detail": "kx.crm.deals.views",
    "get_deal": "kx.crm.deals.service",
    "hand_over": "kx.crm.deals.service",
    "move_stage": "kx.crm.deals.service",
    "patch_requirement": "kx.crm.deals.service",
    "requirements_of": "kx.crm.deals.service",
    "set_next_action": "kx.crm.deals.service",
    "visible_to": "kx.crm.deals.views",
}

__all__ = [
    "add_note",
    "contact_of",
    "deal_detail",
    "get_deal",
    "hand_over",
    "move_stage",
    "patch_requirement",
    "requirements_of",
    "set_next_action",
    "visible_to",
]


def __getattr__(name: str) -> Any:
    return resolve(globals(), _EXPORTS, name)
