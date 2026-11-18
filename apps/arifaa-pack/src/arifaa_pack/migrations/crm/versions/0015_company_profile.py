"""Editable company details (client decision 18 Nov): admin, MD and manager change the letterhead, logo and bank
details printed on quotes, without a developer.

Revision ID: crm_0015
Revises: crm_0014
Create Date: 2026-11-18

One settings row, `company.profile` (JSON): name, address, trn, phone, email, logo (a PNG / JPEG data URI) and
bank {account_name, bank_name, iban, swift}. An empty field falls back to `company.yaml`. History, audit and the
`settings.changed` event come with every settings change; a change of bank details also alerts the MD and the
manager (payment-fraud protection). Idempotent; forward-only.
"""

from __future__ import annotations

from arifaa_pack.migrations import execute_sql

revision = "crm_0015"
down_revision = "crm_0014"
branch_labels = None
depends_on = None

SQL = r"""
SET search_path = crm, public;
INSERT INTO settings (key, value, description) VALUES
 ('company.profile', '{}',
  'Letterhead, logo and bank details on quotes; empty fields come from company.yaml (18 Nov)')
ON CONFLICT (key) DO NOTHING;
"""


def upgrade() -> None:
    execute_sql(SQL)


def downgrade() -> None:
    raise NotImplementedError("forward-only; restore from snapshot")
