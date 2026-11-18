"""D6 (architecture guide): know when a message started sending, so a stuck one is noticed.

Revision ID: automation_0008
Revises: automation_0007
Create Date: 2026-11-17

`OutboundService._claim` commits `sending` before calling the provider. A worker that dies in between left the row
in `sending` for ever (nothing read that status). `sending_at` is set at the claim; the 5-minute sweep
(`expire_stuck_sending`) marks rows sending for more than 15 minutes as failed with `unknown:stuck_sending` and
tells the salesperson to check before resending (never an automatic resend: the provider may have delivered it).
Additive and idempotent; forward-only.
"""

from __future__ import annotations

from arifaa_pack.migrations import execute_sql

revision = "automation_0008"
down_revision = "automation_0007"
branch_labels = None
depends_on = None

SQL = r"""
SET search_path = automation, public;

ALTER TABLE outbound_messages ADD COLUMN IF NOT EXISTS sending_at timestamptz;
CREATE INDEX IF NOT EXISTS outbound_sending_idx ON outbound_messages (sending_at) WHERE status = 'sending';
"""


def upgrade() -> None:
    execute_sql(SQL)


def downgrade() -> None:
    raise NotImplementedError("forward-only; restore from snapshot")
