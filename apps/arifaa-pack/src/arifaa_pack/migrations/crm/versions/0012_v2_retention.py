"""D48 retention indexes for the nightly pruning job (crm database: plumbing tables only).

Revision ID: crm_0012
Revises: crm_0011
Create Date: 2026-11-16

Sources: docs/DATABASE.md. Only outbox / inbox events and idempotency keys are pruned in the Business tier; deals,
quotes, audit and timeline are kept (the §22 rules anonymise, they do not delete). Idempotent; forward-only.
"""

from __future__ import annotations

from arifaa_pack.migrations import execute_sql

revision = "crm_0012"
down_revision = "crm_0011"
branch_labels = None
depends_on = None

SQL = r"""
SET search_path = crm, public;

CREATE INDEX IF NOT EXISTS outbox_published_idx ON outbox_events (published_at) WHERE published_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS inbox_processed_idx ON inbox_events (processed_at);
CREATE INDEX IF NOT EXISTS idempotency_keys_created_idx ON idempotency_keys (created_at);
"""


def upgrade() -> None:
    execute_sql(SQL)


def downgrade() -> None:
    raise NotImplementedError("forward-only; restore from snapshot")
