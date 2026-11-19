"""What other CRM slices may use from the `negotiation` slice (ADR-0001 rule 5).

Another slice, or `kx.crm.deps`, imports `negotiation` only through this module (import-linter enforces it):
everything else in `kx/crm/negotiation/` is internal to the slice. Adding a name here widens the slice's
interface, so it is a reviewed change. Names resolve lazily (PEP 562): importing this module loads
nothing, so the slices that call each other (quotes and approvals) import in the same order as before.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from kx.crm._shared.surface import resolve

if TYPE_CHECKING:
    from kx.crm.negotiation.service import manager_offer, pause_ai, resume_ai

# public name -> the module that defines it
_EXPORTS: dict[str, str] = {
    "manager_offer": "kx.crm.negotiation.service",
    "pause_ai": "kx.crm.negotiation.service",
    "resume_ai": "kx.crm.negotiation.service",
}

__all__ = [
    "manager_offer",
    "pause_ai",
    "resume_ai",
]


def __getattr__(name: str) -> Any:
    return resolve(globals(), _EXPORTS, name)
