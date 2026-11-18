"""D5 (architecture guide): the read-only reporting role must not read credentials or cost / minimum-price data.

Revision ID: crm_0013
Revises: crm_0012
Create Date: 2026-11-17

Before: `db_bootstrap.sh` granted SELECT on ALL crm tables, and by default on every future one, to
`<prefix>reporting_ro`: password hashes, encrypted TOTP seeds, refresh-token hashes and the MD-only cost and minimum
prices were all readable by any reporting tool.

Now, for the reporting role of this database (`<prefix>reporting_ro`, where the prefix comes from the database name,
e.g. pack_crm -> pack_reporting_ro; also a plain `reporting_ro` if present):
* every table grant and the default grant for future tables are removed: a NEW table is not readable until a later
  migration grants it on purpose (rule in docs/DATABASE.md §6);
* tables in ALLOW are readable; tables in LIMITED are readable except the listed columns; DENY tables are not.
A test checks that every crm table is classified here, so a new table forces a decision. Idempotent; forward-only.
"""

from __future__ import annotations

from arifaa_pack.migrations import execute_sql

revision = "crm_0013"
down_revision = "crm_0012"
branch_labels = None
depends_on = None

# credentials and security tokens; cost, markup and freight cost (MD only); plumbing
DENY: tuple[str, ...] = (
    "staff_credentials",
    "auth_sessions",
    "auth_challenges",
    "quote_send_grants",
    "product_costings",
    "quote_line_costs",
    "margin_rules",
    "material_rates",
    "freight_lanes",
    "outbox_events",
    "inbox_events",
    "idempotency_keys",
)
# readable except the columns that hold the minimum price, the concession ladder or edit history of them
LIMITED: dict[str, tuple[str, ...]] = {
    "product_price_tiers": ("min_price",),
    "quote_line_tiers": ("min_price", "max_steps"),
    "product_categories": ("min_discount_pct",),
    "audit_events": ("before", "after"),  # price-list edits record the old and new minimum here
}
ALLOW: tuple[str, ...] = (
    "approval_events",
    "approvals",
    "change_requests",
    "companies",
    "consents",
    "contacts",
    "deal_requirements",
    "deals",
    "delivery_estimates",
    "factory_visits",
    "message_texts",  # 18 Nov (crm_0014 grants it): wording only, no personal or price data
    "feature_flags",
    "fx_rates",
    "lane_risk_history",
    "materials",
    "negotiation_rounds",
    "order_status_history",
    "orders",
    "payments",
    "price_sources",
    "products",
    "quote_lines",
    "quote_versions",
    "quotes",
    "reorder_opportunities",
    "rpt_daily_metrics",
    "settings",
    "settings_history",
    "stage_history",
    "stock_items",
    "stock_movements",
    "supplier_lead_times",
    "tasks",
    "timeline_events",
    "users",
    "visit_slots",
)


def _array(names: tuple[str, ...]) -> str:
    return "ARRAY[" + ", ".join(f"'{n}'" for n in names) + "]::text[]"


def _limited_json() -> str:
    pairs = ", ".join(f"'{t}', {_array(cols)}" for t, cols in LIMITED.items())
    return f"jsonb_build_object({pairs})"


# Built only from the constant table / column lists above (no input): not an injection vector.
SQL = rf"""
DO $$
DECLARE
  prefix text := regexp_replace(current_database(), 'crm$', '');
  r text;
  t text;
  cols text;
  limited jsonb := {_limited_json()};
BEGIN
  FOR r IN SELECT rolname FROM pg_roles WHERE rolname IN (prefix || 'reporting_ro', 'reporting_ro') LOOP
    EXECUTE format('REVOKE ALL ON ALL TABLES IN SCHEMA crm FROM %I', r);
    -- default privileges of the role running the migrations (the schema owner on the server)
    EXECUTE format('ALTER DEFAULT PRIVILEGES IN SCHEMA crm REVOKE SELECT ON TABLES FROM %I', r);
    FOREACH t IN ARRAY {_array(ALLOW)} LOOP
      IF to_regclass('crm.' || t) IS NOT NULL THEN
        EXECUTE format('GRANT SELECT ON crm.%I TO %I', t, r);
      END IF;
    END LOOP;
    FOR t IN SELECT jsonb_object_keys(limited) LOOP
      IF to_regclass('crm.' || t) IS NOT NULL THEN
        SELECT string_agg(quote_ident(column_name), ', ' ORDER BY ordinal_position) INTO cols
          FROM information_schema.columns
         WHERE table_schema = 'crm' AND table_name = t
           AND column_name <> ALL (ARRAY(SELECT jsonb_array_elements_text(limited -> t)));
        EXECUTE format('GRANT SELECT (%s) ON crm.%I TO %I', cols, t, r);
      END IF;
    END LOOP;
  END LOOP;
END
$$;
"""  # noqa: S608


def upgrade() -> None:
    execute_sql(SQL)


def downgrade() -> None:
    raise NotImplementedError("forward-only; restore from snapshot")
