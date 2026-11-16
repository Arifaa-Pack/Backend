"""D48: what the nightly retention job may delete from the automation database (Interactions tier).

Only plumbing: finished jobs, published outbox rows, handled inbox rows, webhook receipts. Messages
(`inbound_messages` / `outbound_messages`) are never deleted here: their unique keys enforce exactly-once
sending, and they follow the §22 anonymise rules. Pending jobs and unpublished events are never touched.
"""

from __future__ import annotations

from kx.core.retention import PruneRule
from kx.core.settings import RetentionSettings

SCHEMA = "automation"


def plumbing_rules(r: RetentionSettings) -> tuple[PruneRule, ...]:
    return (
        PruneRule(
            SCHEMA,
            "scheduled_jobs",
            "finished_at",
            r.plumbing_days,
            keep="status NOT IN ('done','cancelled')",
        ),
        PruneRule(SCHEMA, "scheduled_jobs", "finished_at", r.failed_job_days, keep="status <> 'failed'"),
        PruneRule(SCHEMA, "outbox_events", "published_at", r.plumbing_days),
        PruneRule(SCHEMA, "inbox_events", "processed_at", r.plumbing_days),
        PruneRule(SCHEMA, "webhook_receipts", "received_at", r.plumbing_days),
    )
