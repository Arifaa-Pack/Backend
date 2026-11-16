# Runbook: add a second project on the same server (design: docs/MULTI_PROJECT.md)

Not built yet (D50). When project 2 starts:

1. Copy `apps/arifaa-pack` to the new app (or, once the `kx-*` wheels are published, a new repo that pins them).
2. `db_bootstrap.sh` with `KX_BOOTSTRAP_PREFIXES=<code>_` and the new role passwords → databases `<code>_crm`, `<code>_automation`, `<code>_agents`, `<code>_cms`.
3. Copy `compose.app.yml` to a new Coolify resource with `name: kx-<code>`, its own env file (`KX_COMPANY_CODE=<code>`, DSNs with the new prefix, its own Redis).
4. Shared services stay as they are: one PgBouncer (add the new roles to `userlist.txt`), one TEI, one speech server, one LiveKit (new API key pair), one Stalwart (new domain + account), one Phoenix (`KX_APP_PHOENIX_PROJECT=<code>`), one SigNoz.
5. Capacity: one E-2236 (64 GB) holds the shared services (≈ 20 GB) plus about 8–10 project stacks of ≈ 2.5–4 GB each.
