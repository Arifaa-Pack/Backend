"""Editable message texts (client decisions 18 Nov): the manager / MD edit the English text, the AI translates.

Revision ID: crm_0014
Revises: crm_0013
Create Date: 2026-11-18

One row per text and language that differs from the built-in wording (`kx.core.texts.CATALOG`). English rows are
written by staff (`source = 'staff'`, `en_version` counts the edits); the other languages by the translation job
(`source = 'ai'`, `en_version` = the English version they were translated from, 0 = the built-in English, so an outdated translation is
visible). Every change is also in the audit trail (old and new text). Readable by the reporting role (no
personal or price data: classified ALLOW in crm_0013). Idempotent; forward-only.
"""

from __future__ import annotations

from arifaa_pack.migrations import execute_sql

revision = "crm_0014"
down_revision = "crm_0013"
branch_labels = None
depends_on = None

SQL = r"""
SET search_path = crm, public;

CREATE TABLE IF NOT EXISTS message_texts (
    key         text NOT NULL,
    language    text NOT NULL,
    body        text NOT NULL CHECK (length(body) BETWEEN 1 AND 2000),
    source      text NOT NULL CHECK (source IN ('staff', 'ai')),
    en_version  integer NOT NULL CHECK (en_version >= 0),
    updated_by  uuid REFERENCES users(id),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (key, language)
);

DO $$
DECLARE
  prefix text := regexp_replace(current_database(), 'crm$', '');
  r text;
BEGIN
  FOR r IN SELECT rolname FROM pg_roles WHERE rolname IN (prefix || 'reporting_ro', 'reporting_ro') LOOP
    EXECUTE format('GRANT SELECT ON crm.message_texts TO %I', r);
  END LOOP;
END
$$;
"""


def upgrade() -> None:
    execute_sql(SQL)


def downgrade() -> None:
    raise NotImplementedError("forward-only; restore from snapshot")
