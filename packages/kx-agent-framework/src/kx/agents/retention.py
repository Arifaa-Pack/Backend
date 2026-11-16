"""D48: retention of the agents database (Interactions tier).

* plumbing (published outbox, handled inbox) is pruned like the other databases;
* `tool_calls` and `guard_events` are partitioned by month: partitions are created two months ahead and the
ones
  older than the hot window are archived to R2 and dropped;
* `agent_runs` older than `agent_runs_months` are archived to R2 and then deleted (their partitioned children
  cascade). Conversations and turns follow the §22 rules (anonymised with the contact), not this job.
"""

from __future__ import annotations

from datetime import date

from kx.core.contracts import BlobStore
from kx.core.db import Databases
from kx.core.retention import (
    PruneRule,
    archive_query,
    drop_old_partitions,
    ensure_month_partitions,
    prune_batches,
)
from kx.core.settings import LogicalDb, RetentionSettings

SCHEMA = "agents"
PARTITIONED = ("tool_calls", "guard_events")


def plumbing_rules(r: RetentionSettings) -> tuple[PruneRule, ...]:
    return (
        PruneRule(SCHEMA, "outbox_events", "published_at", r.plumbing_days),
        PruneRule(SCHEMA, "inbox_events", "processed_at", r.plumbing_days),
    )


async def run_retention(
    databases: Databases, r: RetentionSettings, *, today: date, store: BlobStore | None, company: str
) -> dict[str, int]:
    out: dict[str, int] = {}
    async with databases.transaction(LogicalDb.AGENTS) as s:
        for table in PARTITIONED:
            await ensure_month_partitions(s, SCHEMA, table, today=today)
    archive = store if r.archive else None
    for table in PARTITIONED:
        dropped = await drop_old_partitions(
            databases,
            LogicalDb.AGENTS,
            SCHEMA,
            table,
            today=today,
            hot_months=r.partition_hot_months,
            store=archive,
            company=company,
        )
        out[f"{table}_partitions_dropped"] = len(dropped)
    runs_days = r.agent_runs_months * 31
    if archive is not None:
        out["agent_runs_archived"] = await archive_query(
            databases,
            LogicalDb.AGENTS,
            f"SELECT * FROM {SCHEMA}.agent_runs "  # noqa: S608 - constants and an int, no user input
            f"WHERE started_at < now() - interval '{runs_days} days'",
            archive,
            key=f"archive/{company}/agents/agent_runs/before-{today.isoformat()}",
        )
    out["agent_runs"] = await prune_batches(
        databases, LogicalDb.AGENTS, PruneRule(SCHEMA, "agent_runs", "started_at", runs_days), batch=r.batch
    )
    for rule in plumbing_rules(r):
        out[rule.table] = await prune_batches(databases, LogicalDb.AGENTS, rule, batch=r.batch)
    return out
