"""D55 / D57 / D58 website visitors: where they are on the site, and the limits on the assistant speaking first.

Revision ID: agents_0006
Revises: agents_0005
Create Date: 2026-11-20

* `site_context`: the visitor's latest `SiteContext` (page, family, product, section, enquiry list), sent with
  every message and every page or product change. Voice reads it too (one visitor, one thread: D58).
* `engaged_at`: the visitor's first message or voice turn. The assistant never speaks first before it
  (design doc AS-01: "Arifa does not speak first").
* `proactive_count`, `last_proactive_at`: the nudges sent (at most 3 per session, user decision 19 Nov).
* `recording_notice_at`: when the voice recording notice was given, so it is given once per session
  (a page load or a rejoin does not repeat it).

Idempotent; forward-only.
"""

from __future__ import annotations

from arifaa_pack.migrations import execute_sql

revision = "agents_0006"
down_revision = "agents_0005"
branch_labels = None
depends_on = None

SQL = r"""
SET search_path = agents, public;

ALTER TABLE visitors ADD COLUMN IF NOT EXISTS site_context jsonb;
ALTER TABLE visitors ADD COLUMN IF NOT EXISTS engaged_at timestamptz;
ALTER TABLE visitors ADD COLUMN IF NOT EXISTS proactive_count smallint NOT NULL DEFAULT 0;
ALTER TABLE visitors ADD COLUMN IF NOT EXISTS last_proactive_at timestamptz;
ALTER TABLE visitors ADD COLUMN IF NOT EXISTS recording_notice_at timestamptz;
"""


def upgrade() -> None:
    execute_sql(SQL)


def downgrade() -> None:
    raise NotImplementedError("forward-only; restore from snapshot")
