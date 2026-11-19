"""D61 / WP-C9: the ERP items as the CRM's products (the sellable variants of the website products).

Revision ID: crm_0018
Revises: crm_0017
Create Date: 2026-11-19

Arifa's ERP has no item codes: an item is its description + unit ("45 CM ALUMINIUM FOIL" "1*1.400 KG"). The
import keeps every category (packing material, raw material and scrap too) but only sellable items can be
quoted or shown to buyers. A variant links to its website product through the existing `cms_product_id`;
`website_products` keeps what the CMS published so a later ERP import can link its new items too.
The AI may say only an availability word for stock (below `stock.limited_below` units = "limited"). Additive;
forward-only.
"""

from __future__ import annotations

from arifaa_pack.migrations import execute_sql

revision = "crm_0018"
down_revision = "crm_0017"
branch_labels = None
depends_on = None

SQL = r"""
SET search_path = crm, public;

ALTER TABLE products ADD COLUMN IF NOT EXISTS sellable boolean NOT NULL DEFAULT true;
ALTER TABLE products ADD COLUMN IF NOT EXISTS erp_category text;
ALTER TABLE products ADD COLUMN IF NOT EXISTS erp_description text;
ALTER TABLE products ADD COLUMN IF NOT EXISTS erp_packing text;     -- the ERP "Unit" column: 1*1000 PCS, CARTON
ALTER TABLE products ADD COLUMN IF NOT EXISTS brand text;
CREATE INDEX IF NOT EXISTS products_cms_product_idx ON products (cms_product_id) WHERE cms_product_id IS NOT NULL;

-- the website products the CMS published (catalogue.product_published.v1): what an ERP item is matched against
CREATE TABLE IF NOT EXISTS website_products (
    cms_id       text PRIMARY KEY,               -- the website id, e.g. ac-8325
    ref          text,                           -- what buyers say: 8325, 30 cm, Spoon
    family       text,                           -- containers, foil, ...
    name         text NOT NULL,
    range        text NOT NULL DEFAULT 'trade' CHECK (range IN ('trade', 'self')),
    match_words  jsonb NOT NULL DEFAULT '[]'::jsonb,  -- the editors' own ERP words (CMS erp_match)
    published    boolean NOT NULL DEFAULT true,
    updated_at   timestamptz NOT NULL DEFAULT now()
);

INSERT INTO settings (key, value, description) VALUES
 ('stock.limited_below', '10', 'Stock below this (in the item''s own unit) is "limited stock" to buyers; 0 = made to order (D61)')
ON CONFLICT (key) DO NOTHING;

-- D5: the read-only reporting role may read it (classified ALLOW in crm_0013)
DO $$
DECLARE
  prefix text := regexp_replace(current_database(), 'crm$', '');
  r text;
BEGIN
  FOR r IN SELECT rolname FROM pg_roles WHERE rolname IN (prefix || 'reporting_ro', 'reporting_ro') LOOP
    EXECUTE format('GRANT SELECT ON crm.website_products TO %I', r);
  END LOOP;
END
$$;
"""


def upgrade() -> None:
    execute_sql(SQL)


def downgrade() -> None:
    raise NotImplementedError("forward-only; restore from snapshot")
