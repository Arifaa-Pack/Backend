# Database design (D48)

PostgreSQL 18 + pgvector on the dedicated server, PgBouncer in front, WAL-G backups to Cloudflare R2. The design has one aim: **the data can grow for years on one server, and any part can move to its own server later by changing one setting.**

## 1. Logical databases

One PostgreSQL cluster holds one **logical database per area**. Each has its own owner role, its own DSN in the app's settings and its own Alembic history. Packages never join across databases; they reference each other's rows with `*_ref uuid` columns and talk through events.

| Logical DB (name on the server) | Owner role | Used by | Tier | Grows by |
| --- | --- | --- | --- | --- |
| `pack_crm` | `pack_crm_app` (+ `pack_reporting_ro`) | `kx.crm` | Business | slowly: deals, quotes, audit (≈ 1 row per state change) |
| `pack_cms` | `pack_cms_app` | Payload CMS (Node) | Business | very slowly |
| `pack_automation` | `pack_automation_app` | `kx.automation` | Interactions | every message in or out, every job |
| `pack_agents` | `pack_agents_app` | `kx.agents` | Interactions | every buyer turn, every AI run and tool call, website sessions |
| `pack_phoenix` (separate container) | `pack_phoenix_app` | Arize Phoenix | Telemetry | every model call (≈ 4 KB) |
| SigNoz ClickHouse (own store) | — | SigNoz | Telemetry | logs, metrics, traces |

The `pack_` prefix is the company code (`KX_COMPANY_CODE`). A second project on the same server gets `auto_crm`, `auto_agents` … in the same cluster without any clash (`docs/MULTI_PROJECT.md`).

**Website visitor data** (sessions, chats, voice turns) lives in `pack_agents`; channel messages (WhatsApp, email) in `pack_automation`. Together they are the **Interactions tier**, the part that grows with buyer traffic and can move first to its own server. The CRM (the business record) stays small and safe in the **Business tier**.

## 2. Tiers and what happens to old rows

| Tier | Databases | Kept | How it stays small |
| --- | --- | --- | --- |
| **Business** | `crm`, `cms` | forever, except the §22 retention rules (lost leads anonymised after 12 months, won customers 5 years, consent log life + 5 years) | §22 jobs; `crm.audit_events` is append-only by trigger and never partitioned |
| **Interactions** | `agents`, `automation` | conversation data per §22 (open leads 24 months, call audio 90 days); plumbing 30 days | monthly partitions on append-only logs, nightly batched prune, archive to R2 before delete |
| **Telemetry** | Phoenix, SigNoz | 30–90 days (Phoenix), 15–30 days (SigNoz) | set in each tool; never mixed with business data |

### Partitioned by month

Only append-only logs with no uniqueness across rows (a primary key or unique index on a partitioned table must contain the partition key):

| Table | Partition key | Primary key | Kept hot | Then |
| --- | --- | --- | --- | --- |
| `agents.tool_calls` | `at` | `(id, at)` | 6 months (`KX_RETENTION__PARTITION_HOT_MONTHS`) | archived to R2, partition dropped |
| `agents.guard_events` | `at` | `(id, at)` | 6 months | archived, dropped |

Partitions are created two months ahead plus a `default` partition, by the nightly `retention` job (Python; no pg_partman needed).

### Pruned nightly (batched, 5,000 rows a batch)

| Table | Deleted when | Why not partitioned |
| --- | --- | --- |
| `automation.scheduled_jobs` | done / cancelled > 30 days; failed > 90 days | pending `dedup_key` must stay unique |
| `crm/automation/agents.outbox_events` | published > 30 days | identity sequence + relay index |
| `crm/automation/agents.inbox_events` | handled > 30 days | PK `(event_id, handler)` dedupes redeliveries |
| `automation.webhook_receipts` | > 30 days (Meta retries ≈ 7 days) | PK `(provider, external_id)` dedupes |
| `crm.idempotency_keys` | > 30 days | PK on the key |
| `agents.agent_runs` | > 13 months (`KX_RETENTION__AGENT_RUNS_MONTHS`), archived first | unique `(trigger_event_id, graph)` is the run's idempotency key |

Not pruned by this job: `automation.inbound_messages` / `outbound_messages` (their unique keys enforce exactly-once sending; they follow the §22 anonymise rules), and everything in the Business tier.

Values (OPEN #73): `KX_RETENTION__PLUMBING_DAYS=30`, `KX_RETENTION__FAILED_JOB_DAYS=90`, `KX_RETENTION__AGENT_RUNS_MONTHS=13`, `KX_RETENTION__PARTITION_HOT_MONTHS=6`, `KX_RETENTION__ARCHIVE=true`.

### Archive

Before a partition is dropped or old runs are deleted, the rows are written to R2 as gzipped CSV (`COPY … TO STDOUT`) with a JSON manifest (row count, columns, sha256):

```
archive/<company>/<db>/<table>/<yyyy-mm>.csv.gz
archive/<company>/<db>/<table>/<yyyy-mm>.json
```

Restore: create the table shape, `COPY … FROM STDIN` the file. This keeps the hot database small without losing history; the files can also be loaded into an analytics store later.

## 3. Connections: PgBouncer

| Setting | Value | Why |
| --- | --- | --- |
| PgBouncer mode | `transaction` | many short transactions from api / workers / voice |
| `default_pool_size` / `max_db_connections` | 20 / 40 per database | 3 app DBs × 40 + CMS + admin < 200 |
| Postgres `max_connections` | 200 | the bench measured 106 at the old defaults |
| App pools (`KX_DB_POOL_SIZE` / `KX_DB_MAX_OVERFLOW`) | 5 / 5 | per process, per database |
| Celery worker concurrency (`KX_APP_WORKER_CONCURRENCY`) | 3 | same reply times as 10, 1.7 GB less RAM (bench) |
| LangGraph checkpointer pool | max 3 | it opens its own psycopg pool |

With `KX_DB_PGBOUNCER=true` the app turns off server-side prepared statement caches (asyncpg `statement_cache_size=0`, psycopg `prepare_threshold=None`) and does not send `search_path` / `statement_timeout` at connect time; those are set per role (`ALTER ROLE … SET`) by `db_bootstrap.sh`. Migrations (`kx migrate`) connect **directly** to Postgres (port 5432) through `KX_MIGRATE_<DB>_DSN`. Advisory locks in the code are transaction-level, so they work through PgBouncer.

## 4. Backups and restore

- **WAL-G** ships every WAL segment to R2 continuously and takes a full base backup nightly; 7 full backups kept. Point-in-time restore to any second in that window.
- **Monthly restore test** on a scratch container (runbook `infra/server/runbooks/restore.md`).
- RAID 1 protects against one disk failing; it is **not** a backup.
- Phoenix and SigNoz are not backed up (telemetry; re-creatable).

## 5. Growth and when to split

Measured growth (bench R1 × planned usage, + 30 % indexes / WAL): ≈ 0.3 / 1.4 / 4.7 GB a month at Light / Normal / Heavy for app databases + traces; Heavy first year ≈ 56 GB. With pruning and partitions the hot app data stays well under that.

Move a database to its own server when **any** of these holds for a month: the Interactions tier passes 200 GB, p95 query time on `/v1/public/*` passes 300 ms, or CPU on the server passes 60 % at peak. Steps (no code change):

1. Start Postgres on the new server, restore that database from WAL-G (or `pg_dump`/`pg_restore` for one DB), let it catch up with logical replication.
2. Stop the writers (Coolify), switch `KX_<DB>_DSN` and `KX_MIGRATE_<DB>_DSN` to the new host, redeploy.
3. Drop the old copy after a week.

Order to move: `agents` + `automation` (Interactions) first, then Phoenix, then `crm` only if ever needed. Reporting load can later go to a read replica of `crm` (`pack_reporting_ro`).

## 6. Rules for new tables

- A new append-only log table: add it to the partition list or the prune list in the owning package's retention job, in the same change.
- Unique keys that enforce exactly-once behaviour (send grants, idempotency keys, dedup keys) must not be partitioned by time.
- A new `crm` table is **not** readable by the reporting role until a migration grants it (D5, `crm_0013`). In the
  same change: add the table to one of the ALLOW / LIMITED / DENY lists in `crm_0013` (the test reads them) and, if
  allowed, `GRANT SELECT` (or a column list) to `<prefix>reporting_ro` in your new migration. Never grant
  credentials, cost, markup, minimum prices or ladder steps. `tests/db/test_reporting_grants.py` fails while a
  table is unclassified.
- Every foreign key column gets an index (the missing `tool_calls(run_id)` index is added by `agents_0004`).
- Vectors: `agents.kb_chunks.embedding vector(1024)` with HNSW cosine; every chunk stores its `embedding_model`, and search only reads chunks of the configured model.
