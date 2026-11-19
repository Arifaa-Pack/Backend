# Arifaa Auto: what changed in the shared packages (parked)

**Status: parked.** Arifaa Auto is not being developed or used now (client decision, 19 Nov); the work is on
Arifaa Pack. This one document keeps what an Auto backend built on the shared `kx-*` packages will need when
that work starts again. Nothing else in the repository is written for Auto.

## 1. Why imports changed

The packages were reorganised by feature (ADR-0001, `docs/adr/0001-modular-monolith-with-feature-slices.md`):
the CRM code moved from `kx/crm/services/*` and `kx/crm/api/*` into one folder per feature
(`kx/crm/quotes/`, `kx/crm/deals/` ...); then automation and agents got the same layout (`kx/automation/outbound/`,
`email/`, `followups/` ..., `kx/agents/runtime/`, `guard/`, `llm/` ...). Behaviour did not change. Full maps: `docs/arch/CRM_SLICES.md`,
`docs/arch/AUTOMATION_AGENTS_SLICES.md`.

## 2. What to do in the Auto backend

1. **Import each package's public surface**, not its modules: `from kx.crm import build_router, CrmDeps, ...`
   (the 21 names in `kx/crm/__init__.py`), `from kx.automation import OutboundService, EmailChain, ...` (33 names
   in `kx/automation/__init__.py`), `from kx.agents import AgentService, ModelRouter, ...` (25 names in
   `kx/agents/__init__.py`); each list is the module's `__all__`, and `apps/arifaa-pack` shows the wiring. An app
   never imports an automation or agents slice module, nor an ORM or query module (`kx.crm.models`,
   `kx.automation.models`, `kx.automation.repo`, `kx.agents.orm`); import-linter enforces both (`pyproject.toml`).
2. **Replace every old path** (table below): none of them works any more. The shims were removed on 19 Nov
   (ADR-0001 Phase 2c), after the tests moved to the new paths.
3. A feature folder that kept its old module's name (`kx.automation.outbound`, `.inbound`, `.optout`, `.alerts`,
   `.scraping`, `.cms`, `.webhooks`, `kx.agents.guard`, `.knowledge`, `.memory`, `.tools`, `.tracing`, `.voice`,
   `.website`) still exists, but its `__init__.py` re-exports nothing: import from the module the table names
   (`service.py` / `router.py`). `FollowUps` is in `kx.automation.followups.service` (no longer on
   `kx.automation.handlers`).

## 3. Old → new import paths

| Old import path | New import path | Old path still works? |
| --- | --- | --- |
| `kx.agents.context` | `kx.agents.runtime.context` | **no: removed** |
| `kx.agents.guard` | `kx.agents.guard.service` | **no: the folder no longer re-exports it** |
| `kx.agents.knowledge` | `kx.agents.knowledge.service` | **no: the folder no longer re-exports it** |
| `kx.agents.language` | `kx.agents.roles.language` | **no: removed** |
| `kx.agents.memory` | `kx.agents.memory.service` | **no: the folder no longer re-exports it** |
| `kx.agents.middleware` | `kx.agents.runtime.middleware` | **no: removed** |
| `kx.agents.models` | `kx.agents.llm.service` | **no: removed** |
| `kx.agents.orchestrator` | `kx.agents.router.service` | **no: removed** |
| `kx.agents.registry` | `kx.agents.roles.registry` | **no: removed** |
| `kx.agents.runs` | `kx.agents.runtime.runs` | **no: removed** |
| `kx.agents.service` | `kx.agents.runtime.service` | **no: removed** |
| `kx.agents.tools` | `kx.agents.tools.service` | **no: the folder no longer re-exports it** |
| `kx.agents.tracing` | `kx.agents.tracing.service` | **no: the folder no longer re-exports it** |
| `kx.agents.voice` | `kx.agents.voice.service` | **no: the folder no longer re-exports it** |
| `kx.agents.website` | `kx.agents.website.router` | **no: the folder no longer re-exports it** |
| `kx.automation.admin_api` | `kx.automation.admin.router` | **no: removed** |
| `kx.automation.alerts` | `kx.automation.alerts.service` | **no: the folder no longer re-exports it** |
| `kx.automation.channels` | `kx.automation._shared.channels` | **no: removed** |
| `kx.automation.channels.calls` | `kx.automation.calls.channel` | **no: removed** |
| `kx.automation.channels.email` | `kx.automation.email.channel` | **no: removed** |
| `kx.automation.channels.email_chain` | `kx.automation.email.chain` | **no: removed** |
| `kx.automation.channels.feedback` | `kx.automation.email.feedback` | **no: removed** |
| `kx.automation.channels.stalwart` | `kx.automation.email.stalwart` | **no: removed** |
| `kx.automation.channels.whatsapp` | `kx.automation.whatsapp.channel` | **no: removed** |
| `kx.automation.cms` | `kx.automation.cms.service` | **no: the folder no longer re-exports it** |
| `kx.automation.contact_hours` | `kx.automation.outbound.contact_hours` | **no: removed** |
| `kx.automation.inbound` | `kx.automation.inbound.service` | **no: the folder no longer re-exports it** |
| `kx.automation.inbound.webhooks` | `kx.automation.webhooks.router` | **no: removed** |
| `kx.automation.killswitch` | `kx.automation.outbound.killswitch` | **no: removed** |
| `kx.automation.media` | `kx.automation._shared.media` | **no: removed** |
| `kx.automation.optout` | `kx.automation.optout.service` | **no: the folder no longer re-exports it** |
| `kx.automation.optout.unsubscribe` | `kx.automation.optout.router` | **no: removed** |
| `kx.automation.outbound` | `kx.automation.outbound.service` | **no: the folder no longer re-exports it** |
| `kx.automation.outbox` | `kx.automation._shared.outbox` | **no: removed** |
| `kx.automation.ports` | `kx.automation._shared.ports` | **no: removed** |
| `kx.automation.ratelimit` | `kx.automation.outbound.ratelimit` | **no: removed** |
| `kx.automation.scraping` | `kx.automation.scraping.service` | **no: the folder no longer re-exports it** |
| `kx.automation.settings` | `kx.automation._shared.settings` | **no: removed** |
| `kx.automation.unsubscribe` | `kx.automation.optout.router` | **no: removed** |
| `kx.automation.webhooks` | `kx.automation.webhooks.router` | **no: the folder no longer re-exports it** |
| `kx.crm.api.admin` | `kx.crm.admin.router` | **no: removed** |
| `kx.crm.api.assignment` | `kx.crm.assignment.router` | **no: removed** |
| `kx.crm.api.auth` | `kx.crm.auth.router` | **no: removed** |
| `kx.crm.api.catalogue_workbook` | `kx.crm.catalogue.router` | **no: removed** |
| `kx.crm.api.change_requests` | `kx.crm.change_requests.router` | **no: removed** |
| `kx.crm.api.company` | `kx.crm.company.router` | **no: removed** |
| `kx.crm.api.context` | `kx.crm._shared.api_context` | **no: removed** |
| `kx.crm.api.deal_actions` | `kx.crm.deals.actions_router` | **no: removed** |
| `kx.crm.api.deals` | `kx.crm.deals.router` | **no: removed** |
| `kx.crm.api.operations` | `kx.crm.operations.router` | **no: removed** |
| `kx.crm.api.pagination` | `kx.crm._shared.pagination` | **no: removed** |
| `kx.crm.api.quotes` | `kx.crm.quotes.router` | **no: removed** |
| `kx.crm.api.records` | `kx.crm.records.router` | **no: removed** |
| `kx.crm.api.reports` | `kx.crm.reports.router` | **no: removed** |
| `kx.crm.api.schemas` | `kx.crm._shared.schemas` | **no: removed** |
| `kx.crm.api.screens` | `kx.crm.screens.router` | **no: removed** |
| `kx.crm.api.session_guard` | `kx.crm.auth.session_guard` | **no: removed** |
| `kx.crm.api.texts` | `kx.crm.texts.router` | **no: removed** |
| `kx.crm.api.users` | `kx.crm.auth.users_router` | **no: removed** |
| `kx.crm.pdf` | `kx.crm.quotes.pdf` | **no: removed** |
| `kx.crm.services.access` | `kx.crm._shared.access` | **no: removed** |
| `kx.crm.services.approvals` | `kx.crm.approvals.service` | **no: removed** |
| `kx.crm.services.assignment` | `kx.crm.assignment.service` | **no: removed** |
| `kx.crm.services.auth` | `kx.crm.auth.service` | **no: removed** |
| `kx.crm.services.catalogue` | `kx.crm.catalogue.service` | **no: removed** |
| `kx.crm.services.change_requests` | `kx.crm.change_requests.service` | **no: removed** |
| `kx.crm.services.company` | `kx.crm.company.service` | **no: removed** |
| `kx.crm.services.consent` | `kx.crm.contacts.consent` | **no: removed** |
| `kx.crm.services.dashboards` | `kx.crm.reports.dashboards` | **no: removed** |
| `kx.crm.services.deals` | `kx.crm.deals.service` | **no: removed** |
| `kx.crm.services.fx` | `kx.crm.pricing.fx` | **no: removed** |
| `kx.crm.services.grants` | `kx.crm.quotes.grants` | **no: removed** |
| `kx.crm.services.handover` | `kx.crm.handover.service` | **no: removed** |
| `kx.crm.services.inbox` | `kx.crm.inbox.service` | **no: removed** |
| `kx.crm.services.issuing` | `kx.crm.quotes.issuing` | **no: removed** |
| `kx.crm.services.journal` | `kx.crm._shared.journal` | **no: removed** |
| `kx.crm.services.lost_proposals` | `kx.crm.deals.lost_proposals` | **no: removed** |
| `kx.crm.services.negotiation` | `kx.crm.negotiation.service` | **no: removed** |
| `kx.crm.services.orders` | `kx.crm.orders.service` | **no: removed** |
| `kx.crm.services.phone` | `kx.crm.contacts.phone` | **no: removed** |
| `kx.crm.services.price_lists` | `kx.crm.pricing.price_list_service` | **no: removed** |
| `kx.crm.services.price_proposals` | `kx.crm.pricing.proposals` | **no: removed** |
| `kx.crm.services.pricing` | `kx.crm.pricing.service` | **no: removed** |
| `kx.crm.services.quotes` | `kx.crm.quotes.service` | **no: removed** |
| `kx.crm.services.retention` | `kx.crm.retention.service` | **no: removed** |
| `kx.crm.services.scheduled_reports` | `kx.crm.reports.scheduled` | **no: removed** |
| `kx.crm.services.settings` | `kx.crm._shared.settings` | **no: removed** |
| `kx.crm.services.staff` | `kx.crm._shared.staff` | **no: removed** |
| `kx.crm.services.summary` | `kx.crm.deals.summary` | **no: removed** |
| `kx.crm.services.texts` | `kx.crm.texts.service` | **no: removed** |
| `kx.crm.services.workbook` | `kx.crm.catalogue.workbook` | **no: removed** |
| `kx.crm.throttle` | `kx.crm.auth.throttle` | **no: removed** |

Names that moved to another module in Phase 2d (19 Nov; the old module path still exists, the name does not):

| Name | Was in | Now in |
| --- | --- | --- |
| `TRANSIENT_REASONS` | `kx.automation.outbound.service` | `kx.automation._shared.sending` |
| `LadderTicket` | `kx.automation.alerts.service` | `kx.automation._shared.ladder` |
| `Attachment` | `kx.automation.email.channel` | `kx.automation._shared.channels` |
| `QuoteEdits`, `LineEdit` | `kx.crm.quotes.service` | `kx.crm._shared.quote_edits` |
| `CompanyProfile`, `BankDetails`, `iban_ok` | `kx.crm.company.service` | `kx.crm._shared.company_profile` |
| `allowed_prices` | `kx.crm.gateway` | `kx.crm.quotes.allowed` |
| `deal_detail`, `visible_to` | `kx.crm.deals.router` | `kx.crm.deals.views` |
| `FollowUps` | `kx.automation.handlers` (Phase 2c) | `kx.automation.followups.service` |

Inside `kx.crm`, a slice reaches another slice only through that slice's `api.py` (rule 5); an Auto backend uses
the `kx.crm` public surface and is not affected.

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
