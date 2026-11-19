"""What other CRM slices may use from the `assignment` slice (ADR-0001 rule 5).

Another slice, or `kx.crm.deps`, imports `assignment` only through this module (import-linter enforces it):
everything else in `kx/crm/assignment/` is internal to the slice. Adding a name here widens the slice's
interface, so it is a reviewed change. Names resolve lazily (PEP 562): importing this module loads
nothing, so the slices that call each other (quotes and approvals) import in the same order as before.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from kx.crm._shared.surface import resolve

if TYPE_CHECKING:
    from kx.crm.assignment.service import assign, set_availability

# public name -> the module that defines it
_EXPORTS: dict[str, str] = {
    "assign": "kx.crm.assignment.service",
    "set_availability": "kx.crm.assignment.service",
}

__all__ = [
    "assign",
    "set_availability",
]


def __getattr__(name: str) -> Any:
    return resolve(globals(), _EXPORTS, name)
