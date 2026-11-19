"""What other CRM slices may use from the `approvals` slice (ADR-0001 rule 5).

Another slice, or `kx.crm.deps`, imports `approvals` only through this module (import-linter enforces it):
everything else in `kx/crm/approvals/` is internal to the slice. Adding a name here widens the slice's
interface, so it is a reviewed change. Names resolve lazily (PEP 562): importing this module loads
nothing, so the slices that call each other (quotes and approvals) import in the same order as before.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from kx.crm._shared.surface import resolve

if TYPE_CHECKING:
    from kx.crm.approvals.service import OPEN, decide, initial_role, open_approval, reroute_open

# public name -> the module that defines it
_EXPORTS: dict[str, str] = {
    "OPEN": "kx.crm.approvals.service",
    "decide": "kx.crm.approvals.service",
    "initial_role": "kx.crm.approvals.service",
    "open_approval": "kx.crm.approvals.service",
    "reroute_open": "kx.crm.approvals.service",
}

__all__ = [
    "OPEN",
    "decide",
    "initial_role",
    "open_approval",
    "reroute_open",
]


def __getattr__(name: str) -> Any:
    return resolve(globals(), _EXPORTS, name)
