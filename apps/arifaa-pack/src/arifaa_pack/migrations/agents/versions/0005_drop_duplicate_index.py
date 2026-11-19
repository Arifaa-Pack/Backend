"""Drop a duplicate index (maintainability review 19 Nov, I2).

Revision ID: agents_0005
Revises: agents_0004
Create Date: 2026-11-19

`agent_runs_started_idx` (agents_0004) indexes the same column as the baseline `agent_runs_day_idx`
(`agent_runs (started_at)`, db/ddl/03_agents.sql), so every AI run paid for two identical index writes. The
baseline index stays. Dropped CONCURRENTLY outside the migration transaction. Forward-only.
"""

from __future__ import annotations

from alembic import op

revision = "agents_0005"
down_revision = "agents_0004"
branch_labels = None
depends_on = None


STATEMENTS = ("DROP INDEX CONCURRENTLY IF EXISTS agents.agent_runs_started_idx",)


def upgrade() -> None:
    with op.get_context().autocommit_block():  # CONCURRENTLY cannot run inside a transaction
        for statement in STATEMENTS:
            op.execute(statement)


def downgrade() -> None:
    raise NotImplementedError("forward-only; restore from snapshot")
