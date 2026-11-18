"""Message template versions: an edit never stops a live WhatsApp template (client decision 18 Nov).

Revision ID: automation_0009
Revises: automation_0008
Create Date: 2026-11-18

Before: changing an approved template overwrote it and set it back to `submitted`, so that message could not be
sent until Meta approved the new text (minutes to two days). Now each edit of a live template is a new row
(`version` + 1) submitted to Meta under its own name (`provider_name`, e.g. `quote_ready_v2`); the approved
version keeps sending. When Meta approves the new one it takes over and the older approved row becomes
`superseded` (kept: it is the history). Additive and idempotent; forward-only.
"""

from __future__ import annotations

from arifaa_pack.migrations import execute_sql

revision = "automation_0009"
down_revision = "automation_0008"
branch_labels = None
depends_on = None

SQL = r"""
SET search_path = automation, public;

ALTER TABLE message_templates ADD COLUMN IF NOT EXISTS version integer NOT NULL DEFAULT 1;
ALTER TABLE message_templates ADD COLUMN IF NOT EXISTS provider_name text;
ALTER TABLE message_templates ADD COLUMN IF NOT EXISTS created_by text;
ALTER TABLE message_templates ADD COLUMN IF NOT EXISTS created_at timestamptz NOT NULL DEFAULT now();

ALTER TABLE message_templates DROP CONSTRAINT IF EXISTS message_templates_channel_name_language_key;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'message_templates_version_key') THEN
    ALTER TABLE message_templates
      ADD CONSTRAINT message_templates_version_key UNIQUE (channel, name, language, version);
  END IF;
END
$$;

ALTER TABLE message_templates DROP CONSTRAINT IF EXISTS message_templates_provider_status_check;
ALTER TABLE message_templates ADD CONSTRAINT message_templates_provider_status_check
  CHECK (provider_status IN ('draft','submitted','approved','rejected','paused','superseded'));

-- one live (approved) version per template and language
CREATE UNIQUE INDEX IF NOT EXISTS message_templates_one_live_idx
  ON message_templates (channel, name, language) WHERE provider_status = 'approved';
"""


def upgrade() -> None:
    execute_sql(SQL)


def downgrade() -> None:
    raise NotImplementedError("forward-only; restore from snapshot")
