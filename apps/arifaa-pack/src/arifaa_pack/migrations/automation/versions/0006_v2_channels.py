"""v2 channels (16 Nov, D43 Telnyx, D44 Stalwart): allow the `stalwart` provider on channel accounts.

Revision ID: automation_0006
Revises: automation_0005
Create Date: 2026-11-16

Sources: *Accounts & Server Plan v2* §2 (email = Stalwart relaying through SES; calls = Telnyx). Old provider
names stay allowed so existing rows (and history) remain valid; the code no longer writes `postmark` or
`twilio`. `telnyx` was already allowed by the baseline. Idempotent; forward-only.
"""

from __future__ import annotations

from arifaa_pack.migrations import execute_sql

revision = "automation_0006"
down_revision = "automation_0005"
branch_labels = None
depends_on = None

SQL = r"""
SET search_path = automation, public;

ALTER TABLE channel_accounts DROP CONSTRAINT IF EXISTS channel_accounts_provider_check;
ALTER TABLE channel_accounts ADD CONSTRAINT channel_accounts_provider_check CHECK (provider IN
    ('meta_cloud','ses','stalwart','telnyx','livekit_sip','postmark','twilio'));

-- the email account is served by Stalwart (SES is its relay and the backup sender)
UPDATE channel_accounts SET provider = 'stalwart' WHERE channel = 'email' AND provider IN ('ses','postmark');
"""


def upgrade() -> None:
    execute_sql(SQL)


def downgrade() -> None:
    raise NotImplementedError("forward-only; restore from snapshot")
