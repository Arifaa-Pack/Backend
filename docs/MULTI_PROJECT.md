# Many projects on one platform (design, D50)

**Status: design only.** Today one project (`pack`) is built. This page fixes how the next ~8 similar projects (same platforms, different business flow) will reuse the platform, so nothing built now has to be undone.

## The problem

Each project needs the same heavy pieces: speech models (≈ 1–2 GB RAM), the embedding model (≈ 3–5 GB), LiveKit, PostgreSQL, Redis, mail, monitoring. Copying all of them per project would cost ≈ 25 GB RAM per project and repeat every model download, upgrade and fix eight times.

## The design: three layers

```
┌─────────────────────────────── one dedicated server (or more later) ───────────────────────────────┐
│ 1. Shared services stack (once per server)                                                         │
│    PostgreSQL cluster + PgBouncer · TEI (BGE-M3) · speech (whisper + Piper) · LiveKit · Stalwart   │
│    Phoenix · SigNoz · SafeLine · CrowdSec                                                          │
│                                                                                                    │
│ 2. Project app stacks (one per project, small)                                                     │
│    pack:  api · worker · beat · voice · cms · redis      → DBs pack_crm, pack_agents …             │
│    auto:  api · worker · beat · voice · cms · redis      → DBs auto_crm, auto_agents …             │
│    …                                                                                               │
│                                                                                                    │
│ 3. Platform code (versioned packages, built once)                                                  │
│    kx-core-db · kx-core-crm · kx-core-automation · kx-agent-framework   (wheels, e.g. 2.3.0)       │
│    each project = a thin app like apps/arifaa-pack: company.yaml, prompts, graphs, its own rules   │
└────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

### 1. Shared services (once per server)

| Service | Shared how | Per-project isolation |
| --- | --- | --- |
| PostgreSQL + PgBouncer | one cluster | own databases and roles per project (`<code>_crm`, `<code>_agents` …; `db_bootstrap.sh` already takes prefixes) |
| TEI embeddings | one model server, stateless | vectors live in each project's own `agents` DB |
| Speech (faster-whisper + Piper) | one server, stateless | none needed (no data kept) |
| LiveKit | one server | own API key / secret per project; room names prefixed `<code>-` |
| Stalwart | one mail server | own domain(s), accounts and DKIM keys per project |
| Phoenix | one instance | one Phoenix *project* per company (`KX_APP_PHOENIX_PROJECT`) |
| SigNoz | one instance | `service.namespace=<code>` resource attribute; dashboards filtered by it |
| SafeLine / CrowdSec | one edge | one site per project domain |

Redis is **per project** (it is the Celery broker; one project's backlog must not delay another's), and small (≈ 12 MB).

### 2. Project app stacks

A new project = copy `infra/server/stacks/compose.app.yml` with a new company code and env file, point it at the shared services, run `db_bootstrap.sh` with the new prefix and `kx migrate`. RAM per project ≈ 2.5–4 GB (api, worker ×3, beat, voice idle, CMS), not 25 GB.

### 3. Platform code as versioned packages

- The four `kx-*` packages get semantic versions and are published as wheels to a private index (GitHub Packages or a Coolify-hosted index); this repo becomes the platform repo.
- Each project repo holds only its app package (`apps/<project>`): `company.yaml`, prompts, its LangGraph graphs and business rules, migrations for its own settings rows, and the pinned `kx-*` versions in its `uv.lock`.
- A platform fix is released once and each project upgrades on its own schedule.
- Business-flow differences live in the app layer or in `crm.settings` rows, never as `if company == …` in the platform.

## What is already prepared now

- Database names carry the company prefix (`pack_crm` …), and `db_bootstrap.sh` creates prefixed roles and databases.
- `KX_COMPANY_CODE` already prefixes Redis keys, R2 object keys, `application_name` and the Phoenix project.
- Vendor choice per field is configuration (`KX_PLATFORM_*`), not code.
- The server stacks are split by concern (`compose.data.yml`, `compose.ai.yml`, `compose.voice.yml`, `compose.mail.yml`, `compose.obs.yml`, `compose.edge.yml`, `compose.app.yml`), so the shared files can later become the shared stack unchanged.

## What will be built when project 2 starts

1. Publish the `kx-*` wheels (CI job on tag), move `apps/arifaa-pack` to its own repo.
2. Split `infra/server/stacks/` into `shared/` and `project/` and give LiveKit / Stalwart per-project credentials.
3. Add `service.namespace` to telemetry resources.
4. Capacity check: one E-2236 (64 GB) holds the shared stack (≈ 20 GB) plus about 8–10 small project stacks; heavy voice use needs a second server for LiveKit + speech, or a GPU server for faster-whisper.
