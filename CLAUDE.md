# CLAUDE.md: guide for Claude Code in this repo

Kairox platform: a reusable B2B sales system (CRM + automation + AI agents). The first client is Arifa Packing & Packaging LLC (Arifaa Pack, Ajman, UAE). Python 3.14, uv workspace.

## Read first (in this order, only what the task needs)

1. **The SRS** (business rules, read-only): `docs/Arifaa Pack — Detailed Business Flow v1 (SRS basis) (2).md`. Quote its § numbers in commits. **Never edit it** (the only exception so far: the §4 / §22 hosting lines of 16 Nov, flagged for Arifa's approval).
2. **The server plan v2** (platform per field, paid and free version, the server): `docs/Arifaa Platform — Platforms, Pricing & Fallbacks/Arifaa Platform — Accounts & Server Plan (for Arifa IT) v2.docx` §2–§5. It wins over every other platform file. The `.md` files in that folder (`Recommendation.md`, `Fallback plan.md` first) follow it; edit them only to follow it.
3. `docs/V1_CHANGE_PLAN.md`: the 16 Nov v2 work list (WP-V2-01 … 14, decisions D41–D53) above the 11 Nov (WP13–WP29) and 5 Nov (WP1–WP12) plans. Log in `docs/V1_PROGRESS.md`; open decisions in `docs/OPEN_QUESTIONS.md`.
4. `CONTRACTS.md`: package boundaries, shared helpers, hard safety rules.
5. `docs/ARCHITECTURE.md`, `docs/DATABASE.md`, `docs/PLATFORM_MODES.md`: how the system, the databases and the paid / free switch work.
6. `docs/REUSE_CATALOGUE.md`: where each capability lives. Import, never rewrite.
7. Background only if needed: `docs/HANDOFF_CONTEXT.md`, `docs/DELIVERY.md`, `docs/DEPLOYMENT.md`, `docs/LOCAL_DEV.md`, `docs/MULTI_PROJECT.md`, `docs/CONTESTS.md`, `infra/server/README.md`.
8. Frontend hand-offs (generated from the code): `docs/frontend/`.

## Layout

- `packages/kx-core-db` → `kx.core`: shared core, contracts, price guard, auth tokens, job store, fallback/circuit breaker.
- `packages/kx-core-crm` → `kx.crm`: the CRM, one folder per feature (ADR-0001: `quotes/`, `deals/`, `approvals/`,
  `pricing/` …, shared code in `_shared/`, ORM in `models/`; map in `docs/arch/CRM_SLICES.md`); the app imports only
  `kx.crm` (its public surface). Staff login in `kx/crm/auth/service.py`. The pre-ADR-0001 paths
  (`kx/crm/services/*` …) are gone: import a feature from its own module.
- `packages/kx-core-db` → `kx.core` also holds the platform modes (`kx.core.platform`: paid / free order per field) and retention helpers (`kx.core.retention`).
- `packages/kx-core-automation` → `kx.automation`: one folder per feature (`outbound/` the gate, `inbound/`,
  `webhooks/`, `optout/`, `whatsapp/`, `email/` Stalwart → SES, `calls/` Telnyx, `alerts/`, `followups/`, `scraping/`,
  `cms/`, `admin/`; shared code in `_shared/`), `handlers.py`, `jobs.py`; the app imports only `kx.automation`.
- `packages/kx-agent-framework` → `kx.agents`: one folder per feature (`runtime/` the turn engine, `router/`,
  `graphs/`, `tools/`, `guard/`, `llm/`, `roles/`, `knowledge/`, `memory/`, `voice/`, `website/`, `tracing/`,
  `evals/`), `handlers.py`; the app imports only `kx.agents`. Map: `docs/arch/AUTOMATION_AGENTS_SLICES.md`.
- `apps/arifaa-pack` → `arifaa_pack`: wiring (`container.py`), vendor adapters (`adapters.py`), Celery app (`celery_app.py`), API, CLI `kx`, voice worker, telemetry, Alembic `migrations/`.
- `services/speech/`: the free speech server (faster-whisper + Piper), its own image (Python 3.12), outside the uv workspace.
- `db/ddl/0*.sql`: the v1 baseline. **Never edit it.** Schema changes are new Alembic revisions (`migrations/<db>/versions/000N_*.py`).
- `infra/server/`: the dedicated-server stacks (Coolify compose files, Postgres / PgBouncer, backups, runbooks). No Terraform, no AWS except SES.
- `tests/`: root unit, spec, DB and integration tests. Each package also has its own `tests/`.

## Commands

```bash
uv sync --all-packages --all-extras
make up                                   # local postgres (pgvector) + redis
export KX_TEST_PG_DSN=postgresql://postgres:postgres@localhost:5432/postgres
make test                                 # or: uv run pytest packages/kx-core-crm/tests -q
make lint                                 # ruff, ruff format --check, mypy --strict, lint-imports
make migrate                              # kx migrate (alembic, every DB)
kx api | kx worker | kx beat | kx voice   # API, Celery worker, the ONE Celery Beat, LiveKit voice worker
kx platform show                          # D41: resolved paid / free version per field
kx reindex                                # D47: re-embed knowledge chunks after an embedding-model switch
make up-voice | up-mail | up-obs | up-embed   # one local profile at a time (docs/LOCAL_DEV.md, Docker on D:)
uv run pytest tests/spec -m bench --benchmark-enable   # speed checks
```

## Rules

- **Packages never import each other** (import-linter). They cross only through `kx.core.contracts` Protocols and events (`kx.core.events`, `kx.core.event_payloads`).
- **Inside a package** (ADR-0001, import-linter): new code goes in its feature slice; routers above services; slices don't import each other's internals (the baseline in `pyproject.toml` may only shrink); `kx map` / `docs/FEATURE_MAP.md` say where things are wired (`uv run kx map --write docs/FEATURE_MAP.md` after wiring changes).
- **Celery is imported only by the app** (`arifaa_pack`). Packages schedule work through `kx.core.jobs` (PostgreSQL schedule table) and the `TaskScheduler` protocol. Long waits are rows in the table, never Celery ETAs. Every task is idempotent (`acks_late` can redeliver).
- **Money:** Decimal only (`kx.core.money`), never float.
- **IDs:** `kx.core.identity.new_id`; hashes and phone/email normalisation come from `kx.core.identity`.
- **Every state change:** `kx.crm._shared.journal.record()` writes audit + timeline + outbox in the same transaction.
- **The backend builds every quote** (§11.1); the AI never calculates or types a price.
- **AI-visible data** (`DealContext` and DTOs) never contains cost, markup, the minimum price or ladder steps.
- **Every AI reply** passes `kx.core.price_guard.check_prices` (it fails closed), whichever model or vendor answered. Priced messages go out only through `OutboundService` with a single-use send grant.
- **Opt-out (§8.1):** one suppression list checked before every message and call; the AI may add a stop, never remove one.
- **Approvals** use `require_human_mfa` (TOTP factor; an email-code login is not enough).
- **Vendors:** call a field's adapter (`llm`, `stt`, `tts`, `calls`, `email`, `embeddings` …), never a vendor directly. The order comes from `kx.core.platform.order()` (env `KX_PLATFORM_PLAN` + `KX_PLATFORM__<FIELD>`: paid = paid → free alternative; free = free only); a switch must be logged and alerted. Only the platforms of the server plan v2 §2 belong in the code.
- **Databases:** each package uses only its own logical database (own DSN); no cross-database SQL. Big append-only tables need a retention rule (`docs/DATABASE.md`).
- **Style:** `from __future__ import annotations` (but **not** in FastAPI router modules: it breaks dependencies defined inside router factories); `mypy --strict`; ruff line length 110; small functions; docstrings that explain the business reason.
- **Tests:** pure logic gets spec or unit tests (hypothesis where useful); DB behaviour gets `@pytest.mark.db` tests using the root `databases` fixture.
- **Dependencies:** don't add any without asking; everything needed is in `uv.lock`. After any `pyproject.toml` change, run `uv lock` and commit the lock.
- **Commits:** small, one work package (WP) at a time, with the § / D reference.

## Saving tokens

- Read only the files a WP names. Use `grep -n` before opening big files (the SRS is ~1,050 lines: grep its `## ` headings).
- Run the narrowest test target first (one package or one file), then `make test` once at the end.
