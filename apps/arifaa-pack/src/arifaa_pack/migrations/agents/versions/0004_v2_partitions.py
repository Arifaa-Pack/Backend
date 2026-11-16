"""D48 database tiers: monthly partitions for the append-only AI logs + retention indexes.

Revision ID: agents_0004
Revises: agents_0003
Create Date: 2026-11-16

Sources: docs/DATABASE.md (16 Nov), bench R1 row growth (tool_calls / guard_events grow with every turn and are
read only by recent dashboards). Both become `PARTITION BY RANGE (at)` with primary key `(id, at)` (a partitioned
table's keys must contain the partition column), their own id sequence, partitions for this month and the next
two plus a DEFAULT partition, and the rows copied over (the system is not live yet). The nightly retention job
keeps creating partitions ahead and drops (after archiving to R2) the ones older than the hot window.
Also: the missing index on tool_calls(run_id) (FK with ON DELETE CASCADE), and indexes the pruning job needs.
Idempotent; forward-only.
"""

from __future__ import annotations

from arifaa_pack.migrations import execute_sql

revision = "agents_0004"
down_revision = "agents_0003"
branch_labels = None
depends_on = None

SQL = r"""
SET search_path = agents, public;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_partitioned_table pt JOIN pg_class c ON c.oid = pt.partrelid
                 JOIN pg_namespace n ON n.oid = c.relnamespace
                 WHERE n.nspname = 'agents' AND c.relname = 'tool_calls') THEN
    ALTER TABLE tool_calls RENAME TO tool_calls_old;
    CREATE SEQUENCE tool_calls_p_id_seq;
    PERFORM setval('tool_calls_p_id_seq', coalesce((SELECT max(id) FROM tool_calls_old), 0) + 1, false);
    CREATE TABLE tool_calls (
        id             bigint NOT NULL DEFAULT nextval('tool_calls_p_id_seq'),
        run_id         uuid NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
        tool           text NOT NULL,
        args           jsonb NOT NULL,
        result_summary text,
        status         text NOT NULL CHECK (status IN ('ok','error','denied')),
        duration_ms    integer,
        at             timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (id, at)
    ) PARTITION BY RANGE (at);
    ALTER SEQUENCE tool_calls_p_id_seq OWNED BY tool_calls.id;
    CREATE TABLE tool_calls_default PARTITION OF tool_calls DEFAULT;
  END IF;

  IF NOT EXISTS (SELECT 1 FROM pg_partitioned_table pt JOIN pg_class c ON c.oid = pt.partrelid
                 JOIN pg_namespace n ON n.oid = c.relnamespace
                 WHERE n.nspname = 'agents' AND c.relname = 'guard_events') THEN
    ALTER TABLE guard_events RENAME TO guard_events_old;
    CREATE SEQUENCE guard_events_p_id_seq;
    PERFORM setval('guard_events_p_id_seq', coalesce((SELECT max(id) FROM guard_events_old), 0) + 1, false);
    CREATE TABLE guard_events (
        id      bigint NOT NULL DEFAULT nextval('guard_events_p_id_seq'),
        run_id  uuid REFERENCES agent_runs(id) ON DELETE CASCADE,
        guard   text NOT NULL CHECK (guard IN
                  ('price_number','citation','injection','disclosure','pii','commitment','language','cost_leak')),
        action  text NOT NULL CHECK (action IN ('blocked','rewritten','flagged')),
        detail  jsonb NOT NULL DEFAULT '{}'::jsonb,
        at      timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (id, at)
    ) PARTITION BY RANGE (at);
    ALTER SEQUENCE guard_events_p_id_seq OWNED BY guard_events.id;
    CREATE TABLE guard_events_default PARTITION OF guard_events DEFAULT;
  END IF;
END $$;

-- month partitions: this month and the next two (the retention job keeps two ahead from now on)
DO $$
DECLARE
  t text;
  m date;
BEGIN
  FOREACH t IN ARRAY ARRAY['tool_calls', 'guard_events'] LOOP
    FOR i IN 0..2 LOOP
      m := (date_trunc('month', now()) + make_interval(months => i))::date;
      EXECUTE format(
        'CREATE TABLE IF NOT EXISTS agents.%I PARTITION OF agents.%I FOR VALUES FROM (%L) TO (%L)',
        t || '_' || to_char(m, 'YYYYMM'), t, m, (m + interval '1 month')::date);
    END LOOP;
  END LOOP;
END $$;

-- copy the rows of the old tables (rows outside the new months land in the DEFAULT partition)
DO $$
BEGIN
  IF to_regclass('agents.tool_calls_old') IS NOT NULL THEN
    INSERT INTO tool_calls (id, run_id, tool, args, result_summary, status, duration_ms, at)
      SELECT id, run_id, tool, args, result_summary, status, duration_ms, at FROM tool_calls_old;
    DROP TABLE tool_calls_old;
  END IF;
  IF to_regclass('agents.guard_events_old') IS NOT NULL THEN
    INSERT INTO guard_events (id, run_id, guard, action, detail, at)
      SELECT id, run_id, guard, action, detail, at FROM guard_events_old;
    DROP TABLE guard_events_old;
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS tool_calls_run_idx ON tool_calls (run_id);
CREATE INDEX IF NOT EXISTS guard_events_day_idx ON guard_events (at, guard);
CREATE INDEX IF NOT EXISTS guard_events_run_idx ON guard_events (run_id);

-- pruning (D48): published / processed plumbing rows by age
CREATE INDEX IF NOT EXISTS outbox_published_idx ON outbox_events (published_at) WHERE published_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS inbox_processed_idx ON inbox_events (processed_at);
CREATE INDEX IF NOT EXISTS agent_runs_started_idx ON agent_runs (started_at);
"""


def upgrade() -> None:
    execute_sql(SQL)


def downgrade() -> None:
    raise NotImplementedError("forward-only; restore from snapshot")
