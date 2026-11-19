"""Indexes for the CRM's frequent screens (maintainability review 19 Nov, I3-I7).

Revision ID: crm_0016
Revises: crm_0015
Create Date: 2026-11-19

- I3 audit_events (action, at DESC): price-list history and the dashboard's action counts; audit_events is
  append-only and never partitioned, so it grows for ever.
- I4 two partial expression indexes on audit_events for the product price history (after / before
  product_id of price_tier rows; Postgres ORs them with a bitmap).
- I5 approvals (assigned_user_id) for open approvals: the salesperson's inbox on every load.
- I6 deals (updated_at DESC, id DESC): the unfiltered deals board keyset.
- I7 timeline_events (deal_id, id DESC): the deal page timeline keyset orders by id.
Additive; built CONCURRENTLY outside the migration transaction so writes never wait. Forward-only.
"""

from __future__ import annotations

from alembic import op

revision = "crm_0016"
down_revision = "crm_0015"
branch_labels = None
depends_on = None

STATEMENTS = (
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS audit_action_at_idx ON crm.audit_events (action, at DESC)",
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS audit_price_tier_after_idx ON crm.audit_events "
    "((after->>'product_id')) WHERE entity_type = 'price_tier'",
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS audit_price_tier_before_idx ON crm.audit_events "
    "((before->>'product_id')) WHERE entity_type = 'price_tier'",
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS approvals_assignee_open_idx ON crm.approvals (assigned_user_id) "
    "WHERE status IN ('pending', 'overdue')",
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS deals_updated_idx ON crm.deals (updated_at DESC, id DESC)",
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS timeline_deal_id_idx ON crm.timeline_events (deal_id, id DESC)",
)


def upgrade() -> None:
    with op.get_context().autocommit_block():  # CONCURRENTLY cannot run inside a transaction
        for statement in STATEMENTS:
            op.execute(statement)


def downgrade() -> None:
    raise NotImplementedError("forward-only; restore from snapshot")
