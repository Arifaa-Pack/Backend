"""D54 / SRS §3: the content editor role (edits and publishes website content in the CMS; no CRM access).

Revision ID: crm_0017
Revises: crm_0016
Create Date: 2026-11-19

Staff accounts stay in the CRM (one sign-in for everyone, D26); a content editor's token carries
`role = content_editor`, which no CRM route accepts. Only the users.role CHECK list changes. Forward-only.
"""

from __future__ import annotations

from arifaa_pack.migrations import execute_sql

revision = "crm_0017"
down_revision = "crm_0016"
branch_labels = None
depends_on = None

SQL = r"""
SET search_path = crm, public;

ALTER TABLE users DROP CONSTRAINT IF EXISTS users_role_check;
ALTER TABLE users ADD CONSTRAINT users_role_check
    CHECK (role IN ('md', 'manager', 'admin', 'sales', 'content_editor'));
"""


def upgrade() -> None:
    execute_sql(SQL)


def downgrade() -> None:
    raise NotImplementedError("forward-only; restore from snapshot")
