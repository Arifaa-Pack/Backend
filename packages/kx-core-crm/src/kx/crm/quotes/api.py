"""What other CRM slices may use from the `quotes` slice (ADR-0001 rule 5).

Another slice, or `kx.crm.deps`, imports `quotes` only through this module (import-linter enforces it):
everything else in `kx/crm/quotes/` is internal to the slice. Adding a name here widens the slice's
interface, so it is a reviewed change. Names resolve lazily (PEP 562): importing this module loads
nothing, so the slices that call each other (quotes and approvals) import in the same order as before.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from kx.crm._shared.surface import resolve

if TYPE_CHECKING:
    from kx.crm.quotes.allowed import allowed_prices_for
    from kx.crm.quotes.grants import GrantPolicy, SendChannel, recipient, recipient_on
    from kx.crm.quotes.issuing import buyer_country, issue_to_buyer, lines_of, local_today
    from kx.crm.quotes.pdf import (
        Letterhead,
        PdfUploader,
        QuoteRenderer,
        SummaryDocument,
        SummaryLine,
        summary_caption,
        summary_key,
        summary_phrase,
    )
    from kx.crm.quotes.service import (
        ISSUED,
        ManualLine,
        costs_of,
        create_final_version,
        latest_version,
        manual_quote,
        request_quote,
        revise_quote,
        tiers_of,
    )

# public name -> the module that defines it
_EXPORTS: dict[str, str] = {
    "GrantPolicy": "kx.crm.quotes.grants",
    "ISSUED": "kx.crm.quotes.service",
    "Letterhead": "kx.crm.quotes.pdf",
    "ManualLine": "kx.crm.quotes.service",
    "PdfUploader": "kx.crm.quotes.pdf",
    "QuoteRenderer": "kx.crm.quotes.pdf",
    "SendChannel": "kx.crm.quotes.grants",
    "SummaryDocument": "kx.crm.quotes.pdf",
    "SummaryLine": "kx.crm.quotes.pdf",
    "allowed_prices_for": "kx.crm.quotes.allowed",
    "buyer_country": "kx.crm.quotes.issuing",
    "costs_of": "kx.crm.quotes.service",
    "create_final_version": "kx.crm.quotes.service",
    "issue_to_buyer": "kx.crm.quotes.issuing",
    "latest_version": "kx.crm.quotes.service",
    "lines_of": "kx.crm.quotes.issuing",
    "local_today": "kx.crm.quotes.issuing",
    "manual_quote": "kx.crm.quotes.service",
    "recipient": "kx.crm.quotes.grants",
    "recipient_on": "kx.crm.quotes.grants",
    "request_quote": "kx.crm.quotes.service",
    "revise_quote": "kx.crm.quotes.service",
    "summary_caption": "kx.crm.quotes.pdf",
    "summary_key": "kx.crm.quotes.pdf",
    "summary_phrase": "kx.crm.quotes.pdf",
    "tiers_of": "kx.crm.quotes.service",
}

__all__ = [
    "ISSUED",
    "GrantPolicy",
    "Letterhead",
    "ManualLine",
    "PdfUploader",
    "QuoteRenderer",
    "SendChannel",
    "SummaryDocument",
    "SummaryLine",
    "allowed_prices_for",
    "buyer_country",
    "costs_of",
    "create_final_version",
    "issue_to_buyer",
    "latest_version",
    "lines_of",
    "local_today",
    "manual_quote",
    "recipient",
    "recipient_on",
    "request_quote",
    "revise_quote",
    "summary_caption",
    "summary_key",
    "summary_phrase",
    "tiers_of",
]


def __getattr__(name: str) -> Any:
    return resolve(globals(), _EXPORTS, name)
