"""D48 retention for the Interactions tier: batched pruning, monthly partitions, archives to R2.

Each package calls these helpers on its OWN database from its nightly retention job (`docs/DATABASE.md`):

* `prune_batches` deletes rows older than a cut-off in small batches (short transactions, no long locks,
  autovacuum keeps up), never touching rows the `keep` condition protects (pending jobs, unpublished outbox);
* `ensure_month_partitions` creates the next months' partitions (+ a DEFAULT one) of a range-partitioned
table;
* `drop_old_partitions` detaches, archives and drops partitions older than the hot window;
* `archive_query` streams `COPY (query) TO STDOUT` as gzipped CSV + a JSON manifest to the `BlobStore` (R2).

Identifiers come from code, never from users; they are still checked against a strict pattern.
"""

from __future__ import annotations

import gzip
import io
import json
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kx.core.contracts import BlobStore
from kx.core.db import Databases
from kx.core.identity import sha256_hex
from kx.core.logging import get_logger
from kx.core.settings import LogicalDb

log = get_logger(__name__)

_IDENT: Final = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
_PART: Final = re.compile(r"^(?P<parent>[a-z_][a-z0-9_]*)_(?P<y>\d{4})(?P<m>\d{2})$")


def _ident(name: str) -> str:
    if not _IDENT.match(name):
        raise ValueError(f"bad SQL identifier: {name!r}")
    return name


def _qualified(schema: str, table: str) -> str:
    return f"{_ident(schema)}.{_ident(table)}"


# ---------------------------------------------------------------- pruning
@dataclass(frozen=True, slots=True)
class PruneRule:
    """Delete rows of `schema.table` where `age_column` < now() - `days`, and `keep` (SQL) is false."""

    schema: str
    table: str
    age_column: str
    days: int
    keep: str | None = None  # e.g. "status IN ('todo','running')" — rows that must never be deleted

    def where(self) -> str:
        cond = f"{_ident(self.age_column)} < now() - make_interval(days => :days)"
        return f"{cond} AND NOT ({self.keep})" if self.keep else cond


async def prune_batches(databases: Databases, db: LogicalDb, rule: PruneRule, *, batch: int = 5000) -> int:
    """Delete in batches of `batch` rows, each batch its own transaction. Returns rows deleted."""
    table = _qualified(rule.schema, rule.table)
    sql = text(
        f"DELETE FROM {table} WHERE ctid IN "  # noqa: S608 - identifiers checked by _ident
        f"(SELECT ctid FROM {table} WHERE {rule.where()} LIMIT :batch)"
    )
    total = 0
    while True:
        async with databases.transaction(db) as s:
            result: Any = await s.execute(sql, {"days": rule.days, "batch": batch})
            deleted = int(result.rowcount or 0)
        total += deleted
        if deleted < batch:
            break
    if total:
        log.info("retention.pruned", table=table, rows=total)
    return total


# ---------------------------------------------------------------- partitions
def month_start(d: date) -> date:
    return date(d.year, d.month, 1)


def add_months(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    return date(d.year + y, m + 1, 1)


def partition_name(parent: str, month: date) -> str:
    return f"{_ident(parent)}_{month:%Y%m}"


async def ensure_month_partitions(
    s: AsyncSession, schema: str, parent: str, *, today: date, ahead: int = 2
) -> list[str]:
    """Create this month's and the next `ahead` months' partitions, and a DEFAULT partition (a row with an
    unexpected date never fails an insert). Idempotent."""
    made: list[str] = []
    parent_q = _qualified(schema, parent)
    for i in range(ahead + 1):
        start = add_months(month_start(today), i)
        name = partition_name(parent, start)
        await s.execute(
            text(
                f"CREATE TABLE IF NOT EXISTS {_qualified(schema, name)} PARTITION OF {parent_q} "
                f"FOR VALUES FROM ('{start.isoformat()}') TO ('{add_months(start, 1).isoformat()}')"
            )
        )
        made.append(name)
    await s.execute(
        text(
            f"CREATE TABLE IF NOT EXISTS {_qualified(schema, parent + '_default')} "
            f"PARTITION OF {parent_q} DEFAULT"
        )
    )
    return made


async def month_partitions(s: AsyncSession, schema: str, parent: str) -> list[tuple[str, date]]:
    rows = (
        await s.execute(
            text(
                "SELECT c.relname FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid "
                "JOIN pg_class p ON p.oid = i.inhparent JOIN pg_namespace n ON n.oid = p.relnamespace "
                "WHERE n.nspname = :schema AND p.relname = :parent"
            ),
            {"schema": schema, "parent": parent},
        )
    ).scalars()
    out = [
        (name, date(int(m["y"]), int(m["m"]), 1))
        for name in rows
        if (m := _PART.match(name)) and m["parent"] == parent
    ]
    return sorted(out, key=lambda x: x[1])


async def drop_old_partitions(
    databases: Databases,
    db: LogicalDb,
    schema: str,
    parent: str,
    *,
    today: date,
    hot_months: int,
    store: BlobStore | None,
    company: str,
) -> list[str]:
    """Archive (when `store` is given) and drop partitions whose month ended before the hot window."""
    cutoff = add_months(month_start(today), -hot_months)
    async with databases.transaction(db) as s:
        old = [(n, m) for n, m in await month_partitions(s, schema, parent) if m < cutoff]
    dropped: list[str] = []
    for name, month in old:
        if store is not None:
            await archive_query(
                databases,
                db,
                f"SELECT * FROM {_qualified(schema, name)}",  # noqa: S608 - identifiers checked
                store,
                key=archive_key(company, db, parent, month),
            )
        async with databases.transaction(db) as s:
            await s.execute(
                text(f"ALTER TABLE {_qualified(schema, parent)} DETACH PARTITION {_qualified(schema, name)}")
            )
            await s.execute(text(f"DROP TABLE {_qualified(schema, name)}"))
        dropped.append(name)
        log.info("retention.partition_dropped", partition=f"{schema}.{name}", archived=store is not None)
    return dropped


# ---------------------------------------------------------------- archive
def archive_key(company: str, db: LogicalDb, table: str, month: date) -> str:
    return f"archive/{company}/{db.value}/{table}/{month:%Y-%m}"


async def archive_query(
    databases: Databases,
    db: LogicalDb,
    query: str,
    store: BlobStore,
    *,
    key: str,
    params: dict[str, Any] | None = None,
) -> int:
    """`COPY (query) TO STDOUT` as gzipped CSV with a header row -> `<key>.csv.gz`, plus `<key>.json` (rows,
    sha256, time). Restore: `COPY <table> FROM STDIN WITH (FORMAT csv, HEADER true)`. Returns the row count.
    The query is built by code (table names checked), and bound values are inlined by Postgres' own
    quoting."""
    if params:
        async with databases.transaction(db) as s:
            for name, value in params.items():
                literal = (
                    await s.execute(text("SELECT quote_literal(CAST(:v AS text))"), {"v": str(value)})
                ).scalar_one()
                query = query.replace(f":{_ident(name)}", str(literal))
    buffer = io.BytesIO()
    async with databases.engine(db).connect() as conn:
        raw = await conn.get_raw_connection()
        driver: Any = raw.driver_connection  # asyncpg.Connection
        await driver.copy_from_query(query, output=buffer, format="csv", header=True)
    data = buffer.getvalue()
    rows = max(data.count(b"\n") - 1, 0)
    packed = gzip.compress(data, compresslevel=6)
    await store.put(f"{key}.csv.gz", packed, "application/gzip")
    manifest = {
        "rows": rows,
        "sha256_csv": sha256_hex(data),
        "bytes_gz": len(packed),
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "query": query[:500],
    }
    await store.put(f"{key}.json", json.dumps(manifest).encode(), "application/json")
    log.info("retention.archived", key=key, rows=rows)
    return rows
