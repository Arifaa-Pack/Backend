"""Index for the hottest webhook lookup (maintainability review 19 Nov, I1).

Revision ID: automation_0010
Revises: automation_0009
Create Date: 2026-11-19

Every WhatsApp / email status callback (sent, delivered, read: about 3 per outbound message) and every bounce
report finds its row by `outbound_messages.provider_message_id`, which had no index: a sequential scan of a
table that only grows. Additive; built CONCURRENTLY outside the migration transaction so writes never wait.
Forward-only.
"""

from __future__ import annotations

from alembic import op

revision = "automation_0010"
down_revision = "automation_0009"
branch_labels = None
depends_on = None

STATEMENTS = (
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS outbound_provider_msg_idx "
    "ON automation.outbound_messages (provider_message_id) WHERE provider_message_id IS NOT NULL",
)


def upgrade() -> None:
    with op.get_context().autocommit_block():  # CONCURRENTLY cannot run inside a transaction
        for statement in STATEMENTS:
            op.execute(statement)


def downgrade() -> None:
    raise NotImplementedError("forward-only; restore from snapshot")
