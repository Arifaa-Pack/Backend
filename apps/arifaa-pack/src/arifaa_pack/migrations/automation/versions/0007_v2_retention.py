"""D48 retention indexes for the nightly pruning job (automation database).

Revision ID: automation_0007
Revises: automation_0006
Create Date: 2026-11-16

Sources: docs/DATABASE.md, bench R1 (scheduled_jobs, outbox / inbox events and webhook receipts were never
pruned and are among the biggest growers). The job deletes in batches by age; these indexes keep each batch an
index scan. Autovacuum is made more eager on the tables that churn. Idempotent; forward-only.
"""

from __future__ import annotations

from arifaa_pack.migrations import execute_sql

revision = "automation_0007"
down_revision = "automation_0006"
branch_labels = None
depends_on = None

SQL = r"""
SET search_path = automation, public;

CREATE INDEX IF NOT EXISTS scheduled_jobs_finished_idx ON scheduled_jobs (finished_at)
    WHERE status IN ('done', 'cancelled', 'failed');
CREATE INDEX IF NOT EXISTS outbox_published_idx ON outbox_events (published_at) WHERE published_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS inbox_processed_idx ON inbox_events (processed_at);
CREATE INDEX IF NOT EXISTS webhook_receipts_received_idx ON webhook_receipts (received_at);

ALTER TABLE scheduled_jobs SET (autovacuum_vacuum_scale_factor = 0.05, autovacuum_analyze_scale_factor = 0.05);
ALTER TABLE outbox_events SET (autovacuum_vacuum_scale_factor = 0.05);
"""


def upgrade() -> None:
    execute_sql(SQL)


def downgrade() -> None:
    raise NotImplementedError("forward-only; restore from snapshot")
