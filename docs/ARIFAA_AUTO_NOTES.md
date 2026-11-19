# Arifaa Auto: what changed in the shared packages (parked)

**Status: parked.** Arifaa Auto is not being developed or used now (client decision, 19 Nov); the work is on
Arifaa Pack. This one document keeps what an Auto backend built on the shared `kx-*` packages will need when
that work starts again. Nothing else in the repository is written for Auto.

## 1. Why imports changed

The packages were reorganised by feature (ADR-0001, `docs/adr/0001-modular-monolith-with-feature-slices.md`):
the CRM code moved from `kx/crm/services/*` and `kx/crm/api/*` into one folder per feature
(`kx/crm/quotes/`, `kx/crm/deals/` ...), and three automation features got folders (`outbound/`, `inbound/`,
`optout/`). Behaviour did not change. Full maps: `docs/arch/CRM_SLICES.md`,
`docs/arch/AUTOMATION_AGENTS_SLICES.md`.

## 2. What to do in the Auto backend

1. **Import the CRM's public surface**, not its modules: `from kx.crm import build_router, CrmDeps,
   CrmGatewayImpl, SettingsService, jobs, ...` (the 21 names in `kx/crm/__init__.py`, `__all__`). An app never
   imports an ORM or query module (`kx.crm.models`, `kx.automation.models`, `kx.automation.repo`,
   `kx.agents.orm`); import-linter enforces it (`pyproject.toml`).
2. **Replace the removed paths** (table below, "no: removed"): they no longer exist.
3. **Move off the temporary aliases** ("yes"): they still work (the old module name is the same module object)
   but are deleted once nothing in either project uses them.
4. `kx.automation.outbound`, `kx.automation.inbound` and `kx.automation.optout` keep their names (each is now a
   folder whose `__init__.py` is the old module).

## 3. Old → new import paths

| Old import path | New import path | Old path still works? |
| --- | --- | --- |
| `kx.automation.contact_hours` | `kx.automation.outbound.contact_hours` | yes (alias, temporary) |
| `kx.automation.killswitch` | `kx.automation.outbound.killswitch` | yes (alias, temporary) |
| `kx.automation.ratelimit` | `kx.automation.outbound.ratelimit` | yes (alias, temporary) |
| `kx.automation.unsubscribe` | `kx.automation.optout.unsubscribe` | yes (alias, temporary) |
| `kx.automation.webhooks` | `kx.automation.inbound.webhooks` | yes (alias, temporary) |
| `kx.crm.api.admin` | `kx.crm.admin.router` | **no: removed** |
| `kx.crm.api.assignment` | `kx.crm.assignment.router` | **no: removed** |
| `kx.crm.api.auth` | `kx.crm.auth.router` | **no: removed** |
| `kx.crm.api.catalogue_workbook` | `kx.crm.catalogue.router` | **no: removed** |
| `kx.crm.api.change_requests` | `kx.crm.change_requests.router` | **no: removed** |
| `kx.crm.api.company` | `kx.crm.company.router` | yes (alias, temporary) |
| `kx.crm.api.context` | `kx.crm._shared.api_context` | **no: removed** |
| `kx.crm.api.deal_actions` | `kx.crm.deals.actions_router` | **no: removed** |
| `kx.crm.api.deals` | `kx.crm.deals.router` | **no: removed** |
| `kx.crm.api.operations` | `kx.crm.operations.router` | **no: removed** |
| `kx.crm.api.pagination` | `kx.crm._shared.pagination` | **no: removed** |
| `kx.crm.api.quotes` | `kx.crm.quotes.router` | **no: removed** |
| `kx.crm.api.records` | `kx.crm.records.router` | yes (alias, temporary) |
| `kx.crm.api.reports` | `kx.crm.reports.router` | yes (alias, temporary) |
| `kx.crm.api.schemas` | `kx.crm._shared.schemas` | **no: removed** |
| `kx.crm.api.screens` | `kx.crm.screens.router` | yes (alias, temporary) |
| `kx.crm.api.session_guard` | `kx.crm.auth.session_guard` | yes (alias, temporary) |
| `kx.crm.api.texts` | `kx.crm.texts.router` | yes (alias, temporary) |
| `kx.crm.api.users` | `kx.crm.auth.users_router` | **no: removed** |
| `kx.crm.pdf` | `kx.crm.quotes.pdf` | yes (alias, temporary) |
| `kx.crm.services.access` | `kx.crm._shared.access` | yes (alias, temporary) |
| `kx.crm.services.approvals` | `kx.crm.approvals.service` | yes (alias, temporary) |
| `kx.crm.services.assignment` | `kx.crm.assignment.service` | yes (alias, temporary) |
| `kx.crm.services.auth` | `kx.crm.auth.service` | yes (alias, temporary) |
| `kx.crm.services.catalogue` | `kx.crm.catalogue.service` | yes (alias, temporary) |
| `kx.crm.services.change_requests` | `kx.crm.change_requests.service` | yes (alias, temporary) |
| `kx.crm.services.company` | `kx.crm.company.service` | yes (alias, temporary) |
| `kx.crm.services.consent` | `kx.crm.contacts.consent` | **no: removed** |
| `kx.crm.services.dashboards` | `kx.crm.reports.dashboards` | **no: removed** |
| `kx.crm.services.deals` | `kx.crm.deals.service` | yes (alias, temporary) |
| `kx.crm.services.fx` | `kx.crm.pricing.fx` | yes (alias, temporary) |
| `kx.crm.services.grants` | `kx.crm.quotes.grants` | **no: removed** |
| `kx.crm.services.handover` | `kx.crm.handover.service` | yes (alias, temporary) |
| `kx.crm.services.inbox` | `kx.crm.inbox.service` | yes (alias, temporary) |
| `kx.crm.services.issuing` | `kx.crm.quotes.issuing` | yes (alias, temporary) |
| `kx.crm.services.journal` | `kx.crm._shared.journal` | yes (alias, temporary) |
| `kx.crm.services.lost_proposals` | `kx.crm.deals.lost_proposals` | yes (alias, temporary) |
| `kx.crm.services.negotiation` | `kx.crm.negotiation.service` | yes (alias, temporary) |
| `kx.crm.services.orders` | `kx.crm.orders.service` | yes (alias, temporary) |
| `kx.crm.services.phone` | `kx.crm.contacts.phone` | yes (alias, temporary) |
| `kx.crm.services.price_lists` | `kx.crm.pricing.price_list_service` | yes (alias, temporary) |
| `kx.crm.services.price_proposals` | `kx.crm.pricing.proposals` | yes (alias, temporary) |
| `kx.crm.services.pricing` | `kx.crm.pricing.service` | yes (alias, temporary) |
| `kx.crm.services.quotes` | `kx.crm.quotes.service` | yes (alias, temporary) |
| `kx.crm.services.retention` | `kx.crm.retention.service` | **no: removed** |
| `kx.crm.services.scheduled_reports` | `kx.crm.reports.scheduled` | yes (alias, temporary) |
| `kx.crm.services.settings` | `kx.crm._shared.settings` | yes (alias, temporary) |
| `kx.crm.services.staff` | `kx.crm._shared.staff` | **no: removed** |
| `kx.crm.services.summary` | `kx.crm.deals.summary` | **no: removed** |
| `kx.crm.services.texts` | `kx.crm.texts.service` | yes (alias, temporary) |
| `kx.crm.services.workbook` | `kx.crm.catalogue.workbook` | yes (alias, temporary) |
| `kx.crm.throttle` | `kx.crm.auth.throttle` | yes (alias, temporary) |

## 4. Other changes of 19 Nov an Auto backend inherits

- **Settings:** the website's origins moved out of the CRM CORS list to `KX_APP_CORS_PUBLIC_ORIGINS` (M2); new
  sign-in limits (`KX_AUTH__RATE_*`, `KX_AUTH__ACCOUNT_LOCK_FAILURES`), the website AI sub-cap (M6), the quick
  message merge window `KX_APP_REPLY_MERGE_SECONDS=8`. All in `.env.example`.
- **Staff sign-in:** a token ends with its sign-in session (logout, password change, MFA reset, deactivation,
  role change); the one-time password must be changed before any non-auth route; an IP where the account signed
  in during the last 30 days is not held back by attacks on the account from elsewhere.
- **Requests:** bodies over 1 MB (10 MB for the two spreadsheet imports) → 413; NUL characters → 400; security
  headers on every answer; `/docs` and `/openapi.json` off in production.
- **Email:** mail whose sender fails SPF / DKIM / DMARC is kept, flagged and not acted on (M8); quotes and AI
  replies answer in the buyer's email thread.
- **Where things are wired:** `uv run kx map` (or `docs/FEATURE_MAP.md`) lists every Protocol and its
  implementation, every event and its handlers, every route and job.
