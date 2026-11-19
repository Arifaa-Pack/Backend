"""What other CRM slices may use from the `contacts` slice (ADR-0001 rule 5).

Another slice, or `kx.crm.deps`, imports `contacts` only through this module (import-linter enforces it):
everything else in `kx/crm/contacts/` is internal to the slice. Adding a name here widens the slice's
interface, so it is a reviewed change. Names resolve lazily (PEP 562): importing this module loads
nothing, so the slices that call each other (quotes and approvals) import in the same order as before.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from kx.crm._shared.surface import resolve

if TYPE_CHECKING:
    from kx.crm.contacts.consent import record_opt_out
    from kx.crm.contacts.phone import reachable, tell_salesperson

# public name -> the module that defines it
_EXPORTS: dict[str, str] = {
    "reachable": "kx.crm.contacts.phone",
    "record_opt_out": "kx.crm.contacts.consent",
    "tell_salesperson": "kx.crm.contacts.phone",
}

__all__ = [
    "reachable",
    "record_opt_out",
    "tell_salesperson",
]


def __getattr__(name: str) -> Any:
    return resolve(globals(), _EXPORTS, name)
