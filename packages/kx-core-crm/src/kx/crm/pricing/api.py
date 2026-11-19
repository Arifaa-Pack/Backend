"""What other CRM slices may use from the `pricing` slice (ADR-0001 rule 5).

Another slice, or `kx.crm.deps`, imports `pricing` only through this module (import-linter enforces it):
everything else in `kx/crm/pricing/` is internal to the slice. Adding a name here widens the slice's
interface, so it is a reviewed change. Names resolve lazily (PEP 562): importing this module loads
nothing, so the slices that call each other (quotes and approvals) import in the same order as before.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from kx.crm._shared.surface import resolve

if TYPE_CHECKING:
    from kx.crm.pricing.engine import (
        evaluate_counter,
        evaluate_ladder_counter,
        ladder_between,
        price_line,
        quote_totals,
        unit_cost_per_1000,
    )
    from kx.crm.pricing.fx import FxSource
    from kx.crm.pricing.price_list import PriceTier, TierPrice, convert_tier, min_price_for, resolve_tier
    from kx.crm.pricing.price_list_service import (
        RowError,
        add_tier,
        check_xlsx,
        delete_tier,
        export_xlsx,
        import_xlsx,
        parse_rows,
        tiers_for,
        update_tier,
    )
    from kx.crm.pricing.proposals import approve_rate, material_freshness, reject_rate
    from kx.crm.pricing.service import (
        ListPricingInputs,
        PricingInputs,
        build_delivery_estimate,
        category_min_pct,
        current_tiers,
        current_tiers_many,
        fx_to_aed,
        fx_with_date,
        lead_time_text,
        list_pricing_inputs,
        pricing_inputs,
        tiers_of_version,
        vat_pct,
    )
    from kx.crm.pricing.types import CostInputs, LadderState, MarginBand, NegotiationState, PricedLine

# public name -> the module that defines it
_EXPORTS: dict[str, str] = {
    "CostInputs": "kx.crm.pricing.types",
    "FxSource": "kx.crm.pricing.fx",
    "LadderState": "kx.crm.pricing.types",
    "ListPricingInputs": "kx.crm.pricing.service",
    "MarginBand": "kx.crm.pricing.types",
    "NegotiationState": "kx.crm.pricing.types",
    "PriceTier": "kx.crm.pricing.price_list",
    "PricedLine": "kx.crm.pricing.types",
    "PricingInputs": "kx.crm.pricing.service",
    "RowError": "kx.crm.pricing.price_list_service",
    "TierPrice": "kx.crm.pricing.price_list",
    "add_tier": "kx.crm.pricing.price_list_service",
    "approve_rate": "kx.crm.pricing.proposals",
    "build_delivery_estimate": "kx.crm.pricing.service",
    "category_min_pct": "kx.crm.pricing.service",
    "check_xlsx": "kx.crm.pricing.price_list_service",
    "convert_tier": "kx.crm.pricing.price_list",
    "current_tiers": "kx.crm.pricing.service",
    "current_tiers_many": "kx.crm.pricing.service",
    "delete_tier": "kx.crm.pricing.price_list_service",
    "evaluate_counter": "kx.crm.pricing.engine",
    "evaluate_ladder_counter": "kx.crm.pricing.engine",
    "export_xlsx": "kx.crm.pricing.price_list_service",
    "fx_to_aed": "kx.crm.pricing.service",
    "fx_with_date": "kx.crm.pricing.service",
    "import_xlsx": "kx.crm.pricing.price_list_service",
    "ladder_between": "kx.crm.pricing.engine",
    "lead_time_text": "kx.crm.pricing.service",
    "list_pricing_inputs": "kx.crm.pricing.service",
    "material_freshness": "kx.crm.pricing.proposals",
    "min_price_for": "kx.crm.pricing.price_list",
    "parse_rows": "kx.crm.pricing.price_list_service",
    "price_line": "kx.crm.pricing.engine",
    "pricing_inputs": "kx.crm.pricing.service",
    "quote_totals": "kx.crm.pricing.engine",
    "reject_rate": "kx.crm.pricing.proposals",
    "resolve_tier": "kx.crm.pricing.price_list",
    "tiers_for": "kx.crm.pricing.price_list_service",
    "tiers_of_version": "kx.crm.pricing.service",
    "unit_cost_per_1000": "kx.crm.pricing.engine",
    "update_tier": "kx.crm.pricing.price_list_service",
    "vat_pct": "kx.crm.pricing.service",
}

__all__ = [
    "CostInputs",
    "FxSource",
    "LadderState",
    "ListPricingInputs",
    "MarginBand",
    "NegotiationState",
    "PriceTier",
    "PricedLine",
    "PricingInputs",
    "RowError",
    "TierPrice",
    "add_tier",
    "approve_rate",
    "build_delivery_estimate",
    "category_min_pct",
    "check_xlsx",
    "convert_tier",
    "current_tiers",
    "current_tiers_many",
    "delete_tier",
    "evaluate_counter",
    "evaluate_ladder_counter",
    "export_xlsx",
    "fx_to_aed",
    "fx_with_date",
    "import_xlsx",
    "ladder_between",
    "lead_time_text",
    "list_pricing_inputs",
    "material_freshness",
    "min_price_for",
    "parse_rows",
    "price_line",
    "pricing_inputs",
    "quote_totals",
    "reject_rate",
    "resolve_tier",
    "tiers_for",
    "tiers_of_version",
    "unit_cost_per_1000",
    "update_tier",
    "vat_pct",
]


def __getattr__(name: str) -> Any:
    return resolve(globals(), _EXPORTS, name)
