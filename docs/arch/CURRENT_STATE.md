# Current state (recon, Phase 0.0)

Snapshot of `main` at `ed9e32e` (19 Nov), taken on branch `arch/phase0-tooling` before any code moves.
Purpose: the starting point for the feature-slice restructuring (`docs/adr/0001-modular-monolith-with-feature-slices.md`).

**How it was gathered (no guessing from file names):**

- Protocols, events, routes, jobs: introspection of the real wiring, the same code that became `kx map`
  (`arifaa_pack.featuremap`): the app's `create_app()` and `container.build()` run once offline (empty in-memory
  database, in-memory checkpointer, no Celery), then the container, its `EventBus`, `JobRunner` and the mounted
  FastAPI routes are walked. "Wired at" paths depend on the settings of that run (local defaults: no Chromium,
  Stalwart off), so a few optional implementations show no path.
- Imports inside packages: an AST scan of every module (names re-exported by `kx/crm/models/__init__.py` resolved
  to the module that defines them). The scan script is not committed (one-off); its tables are below.

## 1. Package inventory

| Package | Import | Python files | Lines | Shape |
| --- | --- | --- | --- | --- |
| `packages/kx-core-db` | `kx.core` | 24 | 4,502 | flat modules |
| `packages/kx-core-crm` | `kx.crm` | 71 | 16,028 | `api/` (18 + `__init__`), `services/` (31), `models/` (9), `pricing/` (3), top level `deps`, `gateway`, `jobs`, `pdf`, `rules` |
| `packages/kx-core-automation` | `kx.automation` | 29 | 6,013 | flat modules + `channels/` (6) |
| `packages/kx-agent-framework` | `kx.agents` | 27 | 4,852 | flat modules + `graphs/` (4), `evals/` (1) |
| `apps/arifaa-pack` | `arifaa_pack` | 12 (+33 migration files) | 2,342 | wiring, adapters, Celery, API, CLI, voice worker, telemetry, `migrations/<db>/versions` |

`kx.crm.services` (31): access, approvals, assignment, auth, catalogue, change_requests, company, consent,
dashboards, deals, fx, grants, handover, inbox, issuing, journal, lost_proposals, negotiation, orders, phone,
price_lists, price_proposals, pricing, quotes, retention, scheduled_reports, settings, staff, summary, texts,
workbook.

`kx.crm.api` (18): admin, assignment, auth, catalogue_workbook, change_requests, company, context, deal_actions,
deals, operations, pagination, quotes, records, reports, schemas, screens, texts, users.

`kx.crm.models` (8 + `__init__` re-exporting all): base, catalogue, changes, customers, deals, fulfilment, people,
plumbing, quotes.

`kx.automation`: admin_api, alerts, channels/{calls, email, email_chain, feedback, stalwart, whatsapp}, cms,
contact_hours, handlers, inbound, jobs, killswitch, media, models (ORM), optout, outbound, outbox, ports,
ratelimit, repo (queries), retention, scraping, settings, unsubscribe, webhooks.

`kx.agents`: context, evals/harness, graphs/{compose, conversation, deal, quotation}, guard, handlers, knowledge,
language, memory, middleware, models (**the LLM model router, not ORM**), orchestrator, orm (ORM), registry,
retention, runs, service, testing, tools, tracing, voice, website.

The package `__init__.py` files of `kx.crm` and `kx.agents` are empty, and `kx.automation`'s holds only a
docstring: no package declares a public surface yet (rule 3 of the brief has nothing to point at today).

## 2. Wiring, events, routes, jobs (from the container)

### Protocols in `kx.core.contracts`

| Protocol | Implemented by | File | Wired at (container attribute path) |
| --- | --- | --- | --- |
| `BlobStore` | `kx.automation.media:S3MediaStore` | `packages/kx-core-automation/src/kx/automation/media.py` | `container.automation_runtime.inbound_mail`<br>`container.automation_runtime.media`<br>`container.automation_runtime.outbound._media`<br>`container.periodic['jobs.dispatch_due'].<d>.archive` |
| `ClickToCall` | `kx.automation.alerts:CallBridge` | `packages/kx-core-automation/src/kx/automation/alerts.py` | `container.crm_deps.caller` |
| `CrmGateway` | `kx.crm.gateway:CrmGatewayImpl` | `packages/kx-core-crm/src/kx/crm/gateway.py` | `container.agent_service.gateway` |
| `GrantConsumer` | `kx.crm.gateway:GrantConsumerImpl` | `packages/kx-core-crm/src/kx/crm/gateway.py` | `container.automation_runtime.outbound._grants` |
| `MessageSender` | `kx.automation.outbound:OutboundService` | `packages/kx-core-automation/src/kx/automation/outbound.py` | `container.automation_runtime.outbound` |
| `StaffMailer` | `arifaa_pack.adapters:StaffCodeMailer` | `apps/arifaa-pack/src/arifaa_pack/adapters.py` | `container.crm_deps.mailer` |

### Other Protocols (package-internal ports)

| Protocol | Implemented by | File | Wired at |
| --- | --- | --- | --- |
| `kx.agents.handlers:ReplyAddressBook` | `arifaa_pack.adapters:ThreadAddressBook` | `apps/arifaa-pack/src/arifaa_pack/adapters.py` | `container.runner.specs['agents.handle_event'].handler.<agent_last_resort>.__self__.addresses` |
| `kx.agents.knowledge:ContentSource` | `arifaa_pack.adapters:PayloadContentSource` | `apps/arifaa-pack/src/arifaa_pack/adapters.py` | `container.knowledge_indexer._source` |
| `kx.agents.knowledge:Embedder` | `arifaa_pack.adapters:HttpEmbedder` | `apps/arifaa-pack/src/arifaa_pack/adapters.py` | `container.knowledge_indexer._embedder` |
| `kx.agents.knowledge:KnowledgeSearch` | `kx.agents.knowledge:KnowledgeBase` | `packages/kx-agent-framework/src/kx/agents/knowledge.py` | `container.agent_service.knowledge` |
| `kx.agents.models:ModelProvider` | `kx.agents.models:AnthropicApiProvider` | `packages/kx-agent-framework/src/kx/agents/models.py` | `container.agent_service.runner.models.providers[0]` |
| `kx.agents.models:ModelProvider` | `kx.agents.models:GeminiApiProvider` | `packages/kx-agent-framework/src/kx/agents/models.py` | - |
| `kx.agents.registry:PromptStore` | `kx.agents.registry:DefaultPromptStore` | `packages/kx-agent-framework/src/kx/agents/registry.py` | `container.agent_service.prompts` |
| `kx.agents.voice:VoicePlugins` | `arifaa_pack.adapters:PlatformVoicePlugins` | `apps/arifaa-pack/src/arifaa_pack/adapters.py` | - |
| `kx.agents.website:HumanCheck` | `arifaa_pack.adapters:TurnstileVerifier` | `apps/arifaa-pack/src/arifaa_pack/adapters.py` | - |
| `kx.agents.website:PublicLimits` | `arifaa_pack.adapters:PublicLimiter` | `apps/arifaa-pack/src/arifaa_pack/adapters.py` | `container.website_deps.limits` |
| `kx.automation.alerts:Caller` | `kx.automation.channels.calls:TelnyxCaller` | `packages/kx-core-automation/src/kx/automation/channels/calls.py` | `container.webhook_deps.voice._caller` |
| `kx.automation.alerts:KillSwitchReader` | `kx.core.killswitch:KillSwitchLadder` | `packages/kx-core-db/src/kx/core/killswitch.py` | `container.crm_deps.kill_switch` |
| `kx.automation.channels.email:SesV2Client` | `(none in the platform: an external client)` | `-` | - |
| `kx.automation.channels.email_chain:EmailVendor` | `kx.automation.channels.email:EmailSender` | `packages/kx-core-automation/src/kx/automation/channels/email.py` | `container.automation_runtime.outbound._email._vendors['ses']`<br>`container.voice_switch.<ops>._mailer._vendors['ses']` |
| `kx.automation.channels.email_chain:EmailVendor` | `kx.automation.channels.email_chain:EmailChain` | `packages/kx-core-automation/src/kx/automation/channels/email_chain.py` | `container.automation_runtime.outbound._email`<br>`container.voice_switch.<ops>._mailer` |
| `kx.automation.channels.email_chain:EmailVendor` | `kx.automation.channels.stalwart:StalwartSender` | `packages/kx-core-automation/src/kx/automation/channels/stalwart.py` | - |
| `kx.automation.jobs:MediaDownloader` | `kx.automation.channels.whatsapp:WhatsAppClient` | `packages/kx-core-automation/src/kx/automation/channels/whatsapp.py` | `container.automation_runtime.whatsapp_media` |
| `kx.automation.media:S3Client` | `(none in the platform: an external client)` | `-` | - |
| `kx.automation.outbound:EmailTransport` | `kx.automation.channels.email:EmailSender` | `packages/kx-core-automation/src/kx/automation/channels/email.py` | `container.automation_runtime.outbound._email._vendors['ses']`<br>`container.voice_switch.<ops>._mailer._vendors['ses']` |
| `kx.automation.outbound:EmailTransport` | `kx.automation.channels.email_chain:EmailChain` | `packages/kx-core-automation/src/kx/automation/channels/email_chain.py` | `container.automation_runtime.outbound._email`<br>`container.voice_switch.<ops>._mailer` |
| `kx.automation.outbound:EmailTransport` | `kx.automation.channels.stalwart:StalwartSender` | `packages/kx-core-automation/src/kx/automation/channels/stalwart.py` | - |
| `kx.automation.outbound:KillSwitchReader` | `kx.core.killswitch:KillSwitchLadder` | `packages/kx-core-db/src/kx/core/killswitch.py` | `container.crm_deps.kill_switch` |
| `kx.automation.outbound:RateLimiter` | `kx.automation.ratelimit:TokenBucketLimiter` | `packages/kx-core-automation/src/kx/automation/ratelimit.py` | `container.automation_runtime.outbound._limiter` |
| `kx.automation.outbound:TemplateSpend` | `kx.core.spend:SpendMeter` | `packages/kx-core-db/src/kx/core/spend.py` | `container.spend` |
| `kx.automation.outbound:WhatsAppTransport` | `kx.automation.channels.whatsapp:WhatsAppClient` | `packages/kx-core-automation/src/kx/automation/channels/whatsapp.py` | `container.automation_runtime.whatsapp_media` |
| `kx.automation.ports:ApprovalStatusReader` | `kx.crm.gateway:ApprovalStatusReaderImpl` | `packages/kx-core-crm/src/kx/crm/gateway.py` | `container.automation_runtime.ladder._approvals` |
| `kx.automation.ports:MediaStore` | `kx.automation.media:S3MediaStore` | `packages/kx-core-automation/src/kx/automation/media.py` | `container.automation_runtime.inbound_mail`<br>`container.automation_runtime.media`<br>`container.automation_runtime.outbound._media` … |
| `kx.automation.ports:TaskScheduler` | `kx.automation.jobs:JobScheduler` | `packages/kx-core-automation/src/kx/automation/jobs.py` | `container.automation_runtime.scheduler` |
| `kx.core.jobs:Kicker` | `arifaa_pack.jobqueue:CeleryKicker` | `apps/arifaa-pack/src/arifaa_pack/jobqueue.py` | - |
| `kx.crm.pdf:PdfEngine` | `kx.crm.pdf:ChromiumEngine` | `packages/kx-core-crm/src/kx/crm/pdf.py` | - |
| `kx.crm.pdf:PdfEngine` | `kx.crm.pdf:WeasyPrintEngine` | `packages/kx-core-crm/src/kx/crm/pdf.py` | `container.crm_deps.renderer.engines[0]` |
| `kx.crm.pdf:PdfUploader` | `kx.crm.pdf:S3PdfUploader` | `packages/kx-core-crm/src/kx/crm/pdf.py` | `container.crm_deps.uploader` |
| `kx.crm.services.fx:FxSource` | `arifaa_pack.adapters:OpenRatesFx` | `apps/arifaa-pack/src/arifaa_pack/adapters.py` | `container.crm_deps.fx_source` |

### Events → handlers

| Event | Payload | Handlers (package: function [inbox name]) |
| --- | --- | --- |
| `agent.run_completed.v1` | `AgentRunCompleted` | **no subscriber** |
| `approval.overdue.v1` | `ApprovalOverdue` | **no subscriber** |
| `catalogue.product_content_changed.v1` | `ProductContentChanged` | `app: arifaa_pack.container:cms_product_draft` |
| `catalogue.product_published.v1` | `ProductPublished` | `crm: kx.crm.jobs:on_product_published` |
| `change_request.closed.v1` | `ChangeRequestClosed` | **no subscriber** |
| `change_request.raised.v1` | `ChangeRequested` | `automation: kx.automation.handlers:EventHandlers._change_requested [automation.change_request_ladder]` |
| `consent.opted_out.v1` | `OptedOut` | `automation: kx.automation.handlers:EventHandlers._opted_out [automation.opt_out]`<br>`crm: kx.crm.jobs:on_opted_out` |
| `consent.stop_unclear.v1` | `StopUnclear` | `automation: kx.automation.handlers:EventHandlers._stop_unclear [automation.stop_unclear]` |
| `contact.anonymised.v1` | `ContactAnonymised` | `agents: kx.agents.handlers:AgentHandlers._anonymise [agents.anonymise]`<br>`automation: kx.automation.handlers:EventHandlers._contact_anonymised [automation.anonymise_contact]` |
| `conversation.summary_updated.v1` | `ConversationSummaryUpdated` | `crm: kx.crm.jobs:on_summary_updated` |
| `deal.assigned.v1` | `DealAssigned` | `automation: kx.automation.handlers:EventHandlers._deal_assigned [automation.deal_assigned]` |
| `deal.next_action_set.v1` | `DealNextActionSet` | `automation: kx.automation.handlers:EventHandlers._next_action_set [automation.schedule_followup]` |
| `deal.qualified.v1` | `DealQualified` | `crm: kx.crm.jobs:on_deal_qualified` |
| `deal.requirement_summary.v1` | `RequirementSummary` | `automation: kx.automation.handlers:EventHandlers._requirement_summary [automation.requirement_summary]` |
| `deal.stage_changed.v1` | `DealStageChanged` | `automation: kx.automation.handlers:EventHandlers._stage_changed [automation.lost_stops_followups]` |
| `deal.won.v1` | `DealWon` | `automation: kx.automation.handlers:EventHandlers._deal_won [automation.deal_won]` |
| `kb.content_published.v1` | `KbContentPublished` | `agents: kx.agents.handlers:AgentHandlers._kb [agents.kb_reindex]` |
| `lane.risk_assessed.v1` | `LaneRiskAssessed` | **no subscriber** |
| `message.received.v1` | `MessageReceived` | `agents: kx.agents.handlers:AgentHandlers._message [agents.reply]`<br>`crm: kx.crm.jobs:on_message_received` |
| `message.status.v1` | `MessageStatus` | `crm: kx.crm.jobs:on_message_status` |
| `negotiation.paused.v1` | `NegotiationPaused` | `automation: kx.automation.handlers:EventHandlers._negotiation_paused [automation.negotiation_paused]` |
| `order.status_changed.v1` | `OrderStatusChanged` | `automation: kx.automation.handlers:EventHandlers._order_status_changed [automation.order_update]` |
| `price.proposed.v1` | `PriceProposed` | `crm: kx.crm.jobs:on_price_proposed` |
| `price.stale.v1` | `PriceStale` | **no subscriber** |
| `quote.approved.v1` | `QuoteApproved` | `agents: kx.agents.handlers:AgentHandlers._quote [agents.quotation_resume]`<br>`automation: kx.automation.handlers:EventHandlers._quote_approved [automation.send_approved_quote]` |
| `quote.correction_requested.v1` | `QuoteCorrectionRequested` | `agents: kx.agents.handlers:AgentHandlers._quote [agents.quotation_resume]` |
| `quote.not_delivered.v1` | `QuoteNotDelivered` | `crm: kx.crm.jobs:on_quote_not_delivered` |
| `quote.rejected.v1` | `QuoteRejected` | `agents: kx.agents.handlers:AgentHandlers._quote [agents.quotation_resume]` |
| `quote.resend_requested.v1` | `QuoteResendRequested` | `automation: kx.automation.handlers:EventHandlers._quote_resend [automation.resend_quote_by_email]` |
| `quote.submitted.v1` | `QuoteSubmitted` | `automation: kx.automation.handlers:EventHandlers._quote_submitted [automation.approval_ladder]` |
| `settings.changed.v1` | `SettingsChanged` | **no subscriber** |
| `staff.notice_requested.v1` | `StaffNotice` | `automation: kx.automation.handlers:EventHandlers._staff_notice [automation.staff_notice]` |
| `texts.text_changed.v1` | `MessageTextChanged` | `app: arifaa_pack.texts_sync:translate_text` |

### HTTP routes → router module → service functions

| Method | Path | Router module | Endpoint | Service functions referenced |
| --- | --- | --- | --- | --- |
| GET | `/.well-known/jwks.json` | `arifaa_pack.api` | `create_app.jwks` | - |
| GET | `/healthz` | `arifaa_pack.api` | `create_app.healthz` | - |
| GET | `/healthz/beat` | `arifaa_pack.api` | `create_app.healthz_beat` | `arifaa_pack.telemetry:beat_health` |
| GET | `/v1/admin/channel-accounts` | `kx.automation.admin_api` | `build_router.accounts` | - |
| PATCH | `/v1/admin/channel-accounts/{account_id}` | `kx.automation.admin_api` | `build_router.edit_account` | - |
| GET | `/v1/admin/kill-switch` | `kx.crm.api.operations` | `router.get_level` | - |
| PUT | `/v1/admin/kill-switch` | `kx.crm.api.operations` | `router.set_level` | `kx.crm.services.journal:record` |
| GET | `/v1/admin/message-templates` | `kx.automation.admin_api` | `build_router.templates` | - |
| PUT | `/v1/admin/message-templates` | `kx.automation.admin_api` | `build_router.upsert_template` | - |
| POST | `/v1/admin/message-templates/{template_id}/status` | `kx.automation.admin_api` | `build_router.template_status` | - |
| GET | `/v1/approvals` | `kx.crm.api.quotes` | `router.list_approvals` | `kx.crm.api.pagination:keyset`<br>`kx.crm.api.pagination:page_of` |
| POST | `/v1/approvals/{approval_id}/decision` | `kx.crm.api.quotes` | `router.decide` | `kx.crm.services.approvals:decide` |
| POST | `/v1/auth/login` | `kx.crm.api.auth` | `router.login` | `kx.crm.services.auth:login` |
| POST | `/v1/auth/logout` | `kx.crm.api.auth` | `router.logout` | `kx.crm.services.auth:logout` |
| GET | `/v1/auth/me` | `kx.crm.api.auth` | `router.me` | - |
| POST | `/v1/auth/mfa/email/send` | `kx.crm.api.auth` | `router.mfa_email_send` | `kx.crm.services.auth:send_email_code` |
| POST | `/v1/auth/mfa/email/verify` | `kx.crm.api.auth` | `router.mfa_email_verify` | `kx.crm.services.auth:verify_second_step` |
| POST | `/v1/auth/mfa/totp` | `kx.crm.api.auth` | `router.mfa_totp` | `kx.crm.services.auth:verify_second_step` |
| POST | `/v1/auth/password` | `kx.crm.api.auth` | `router.password_change` | `kx.crm.services.auth:change_password` |
| POST | `/v1/auth/password/reset/finish` | `kx.crm.api.auth` | `router.reset_finish` | `kx.crm.services.auth:finish_reset` |
| POST | `/v1/auth/password/reset/start` | `kx.crm.api.auth` | `router.reset_start` | `kx.crm.services.auth:start_reset` |
| POST | `/v1/auth/refresh` | `kx.crm.api.auth` | `router.refresh` | `kx.crm.services.auth:refresh` |
| POST | `/v1/auth/totp/confirm` | `kx.crm.api.auth` | `router.totp_confirm` | `kx.crm.services.auth:confirm_totp` |
| POST | `/v1/auth/totp/enrol` | `kx.crm.api.auth` | `router.totp_enrol` | `kx.crm.services.auth:begin_totp` |
| GET | `/v1/catalogue/workbook` | `kx.crm.api.catalogue_workbook` | `router.download` | `kx.crm.services.workbook:download` |
| POST | `/v1/catalogue/workbook/apply` | `kx.crm.api.catalogue_workbook` | `router.apply` | `kx.crm.services.workbook:apply` |
| POST | `/v1/catalogue/workbook/preview` | `kx.crm.api.catalogue_workbook` | `router.preview` | `kx.crm.services.workbook:preview` |
| GET | `/v1/change-requests` | `kx.crm.api.change_requests` | `router.inbox` | `kx.crm.api.deals:visible_to` |
| GET | `/v1/change-requests/{cr_id}` | `kx.crm.api.change_requests` | `router.card` | `kx.crm.services.access:check_view`<br>`kx.crm.services.deals:get_deal` |
| POST | `/v1/change-requests/{cr_id}/decline` | `kx.crm.api.change_requests` | `router.decline` | `kx.crm.services.change_requests:decline` |
| POST | `/v1/change-requests/{cr_id}/resolve` | `kx.crm.api.change_requests` | `router.resolve` | `kx.crm.services.change_requests:resolve` |
| POST | `/v1/change-requests/{cr_id}/revise` | `kx.crm.api.change_requests` | `router.revise` | `kx.crm.services.access:check_edit`<br>`kx.crm.services.change_requests:link_revision`<br>`kx.crm.services.deals:get_deal`<br>`kx.crm.services.quotes:revise_quote` |
| POST | `/v1/change-requests/{cr_id}/start` | `kx.crm.api.change_requests` | `router.start` | `kx.crm.services.change_requests:start` |
| GET | `/v1/companies` | `kx.crm.api.records` | `router.companies` | `kx.crm.api.pagination:keyset`<br>`kx.crm.api.pagination:page_of` |
| POST | `/v1/companies` | `kx.crm.api.records` | `router.create_company` | `kx.crm.services.journal:record` |
| PATCH | `/v1/companies/{company_id}` | `kx.crm.api.records` | `router.edit_company` | `kx.crm.services.journal:record` |
| GET | `/v1/company` | `kx.crm.api.company` | `router.show` | `kx.crm.services.company:letterhead` |
| PATCH | `/v1/company` | `kx.crm.api.company` | `router.edit` | `kx.crm.services.company:letterhead`<br>`kx.crm.services.company:update` |
| DELETE | `/v1/company/logo` | `kx.crm.api.company` | `router.remove_logo` | `kx.crm.services.company:letterhead`<br>`kx.crm.services.company:update` |
| PUT | `/v1/company/logo` | `kx.crm.api.company` | `router.upload_logo` | `kx.crm.services.company:letterhead`<br>`kx.crm.services.company:logo_data_uri`<br>`kx.crm.services.company:update` |
| GET | `/v1/contacts` | `kx.crm.api.records` | `router.contacts` | `kx.crm.api.deals:visible_to`<br>`kx.crm.api.pagination:keyset`<br>`kx.crm.api.pagination:page_of` |
| GET | `/v1/contacts/{contact_id}` | `kx.crm.api.records` | `router.contact` | `kx.crm.api.deals:visible_to` |
| PATCH | `/v1/contacts/{contact_id}` | `kx.crm.api.records` | `router.edit_contact` | `kx.crm.api.deals:visible_to`<br>`kx.crm.services.journal:record` |
| GET | `/v1/dashboard/summary` | `kx.crm.api.admin` | `router.summary` | `kx.crm.services.price_proposals:material_freshness` |
| GET | `/v1/dashboards/manager` | `kx.crm.api.reports` | `router.manager` | `kx.crm.services.dashboards:manager_board` |
| GET | `/v1/dashboards/md` | `kx.crm.api.reports` | `router.md` | `kx.crm.services.dashboards:counts` |
| GET | `/v1/dashboards/salesperson` | `kx.crm.api.reports` | `router.salesperson` | `kx.crm.services.dashboards:salesperson_board` |
| GET | `/v1/deals` | `kx.crm.api.deals` | `router.list_deals` | `kx.crm.api.pagination:keyset`<br>`kx.crm.api.pagination:page_of` |
| PATCH | `/v1/deals/orders/{order_id}` | `kx.crm.api.deal_actions` | `router.order_status` | `kx.crm.services.orders:update_order_status` |
| GET | `/v1/deals/{deal_id}` | `kx.crm.api.deals` | `router.get_deal` | `kx.crm.services.access:check_view`<br>`kx.crm.services.deals:contact_of`<br>`kx.crm.services.deals:get_deal`<br>`kx.crm.services.deals:missing_for`<br>`kx.crm.services.deals:requirements_of` |
| POST | `/v1/deals/{deal_id}/ai` | `kx.crm.api.deals` | `router.toggle_ai` | `kx.crm.services.deals:contact_of`<br>`kx.crm.services.deals:get_deal`<br>`kx.crm.services.deals:missing_for`<br>`kx.crm.services.deals:requirements_of`<br>`kx.crm.services.negotiation:pause_ai`<br>`kx.crm.services.negotiation:resume_ai` |
| POST | `/v1/deals/{deal_id}/call` | `kx.crm.api.deal_actions` | `router.call` | `kx.crm.services.deals:get_deal`<br>`kx.crm.services.handover:call_buyer` |
| POST | `/v1/deals/{deal_id}/handback` | `kx.crm.api.assignment` | `router.hand_back` | `kx.crm.api.deals:deal_detail`<br>`kx.crm.services.assignment:hand_back` |
| POST | `/v1/deals/{deal_id}/messages` | `kx.crm.api.deal_actions` | `router.staff_message` | `kx.crm.services.deals:get_deal`<br>`kx.crm.services.handover:reply` |
| POST | `/v1/deals/{deal_id}/offer` | `kx.crm.api.deals` | `router.manager_offer` | `kx.crm.services.negotiation:manager_offer`<br>`kx.crm.services.negotiation:resume_ai` |
| POST | `/v1/deals/{deal_id}/opt-out` | `kx.crm.api.deal_actions` | `router.opt_out` | `kx.crm.api.deals:deal_detail`<br>`kx.crm.services.access:check_edit`<br>`kx.crm.services.consent:record_opt_out`<br>`kx.crm.services.deals:get_deal` |
| POST | `/v1/deals/{deal_id}/payments` | `kx.crm.api.deal_actions` | `router.payment` | `kx.crm.services.access:check_edit`<br>`kx.crm.services.deals:get_deal`<br>`kx.crm.services.orders:record_payment` |
| POST | `/v1/deals/{deal_id}/personal-contact-shared` | `kx.crm.api.deal_actions` | `router.personal_contact_shared` | `kx.crm.api.deals:deal_detail`<br>`kx.crm.services.access:check_edit`<br>`kx.crm.services.deals:get_deal`<br>`kx.crm.services.deals:personal_contact_shared` |
| GET | `/v1/deals/{deal_id}/quotes` | `kx.crm.api.deals` | `router.deal_quotes` | `kx.crm.services.access:check_view`<br>`kx.crm.services.deals:get_deal` |
| POST | `/v1/deals/{deal_id}/quotes` | `kx.crm.api.deals` | `router.request_quote` | `kx.crm.services.access:check_edit`<br>`kx.crm.services.deals:get_deal`<br>`kx.crm.services.quotes:request_quote` |
| POST | `/v1/deals/{deal_id}/quotes/manual` | `kx.crm.api.assignment` | `router.manual_quote` | `kx.crm.services.access:check_edit`<br>`kx.crm.services.deals:get_deal`<br>`kx.crm.services.quotes:manual_quote` |
| POST | `/v1/deals/{deal_id}/quotes/revise` | `kx.crm.api.deals` | `router.revise_quote` | `kx.crm.services.access:check_edit`<br>`kx.crm.services.deals:get_deal`<br>`kx.crm.services.quotes:revise_quote` |
| POST | `/v1/deals/{deal_id}/reassign` | `kx.crm.api.assignment` | `router.reassign` | `kx.crm.api.deals:deal_detail`<br>`kx.crm.services.assignment:reassign` |
| POST | `/v1/deals/{deal_id}/reopen` | `kx.crm.api.deal_actions` | `router.reopen` | `kx.crm.api.deals:deal_detail`<br>`kx.crm.services.deals:get_deal`<br>`kx.crm.services.deals:reopen` |
| POST | `/v1/deals/{deal_id}/stage` | `kx.crm.api.deals` | `router.move` | `kx.crm.services.access:check_edit`<br>`kx.crm.services.assignment:assign`<br>`kx.crm.services.deals:contact_of`<br>`kx.crm.services.deals:get_deal`<br>`kx.crm.services.deals:missing_for`<br>`kx.crm.services.deals:move_stage`<br>`kx.crm.services.deals:requirements_of` |
| POST | `/v1/deals/{deal_id}/takeover` | `kx.crm.api.assignment` | `router.take_over` | `kx.crm.api.deals:deal_detail`<br>`kx.crm.services.assignment:take_over` |
| GET | `/v1/deals/{deal_id}/timeline` | `kx.crm.api.deals` | `router.timeline` | `kx.crm.api.pagination:keyset`<br>`kx.crm.api.pagination:page_of`<br>`kx.crm.services.access:check_view`<br>`kx.crm.services.deals:get_deal` |
| POST | `/v1/deals/{deal_id}/won` | `kx.crm.api.deal_actions` | `router.won` | `kx.crm.services.access:check_edit`<br>`kx.crm.services.deals:get_deal`<br>`kx.crm.services.orders:mark_won` |
| GET | `/v1/exports/{entity}` | `kx.crm.api.reports` | `router.export` | `kx.crm.api.deals:visible_to` |
| PUT | `/v1/feature-flags/{key}` | `kx.crm.api.admin` | `router.put_flag` | - |
| GET | `/v1/fx-rates` | `kx.crm.api.records` | `router.fx_rates` | - |
| POST | `/v1/fx-rates` | `kx.crm.api.records` | `router.fx_override` | `kx.crm.services.journal:record` |
| GET | `/v1/inbox` | `kx.crm.api.screens` | `router.my_inbox` | `kx.crm.services.inbox:inbox_for` |
| GET | `/v1/materials/freshness` | `kx.crm.api.admin` | `router.freshness` | `kx.crm.services.price_proposals:material_freshness` |
| GET | `/v1/orders` | `kx.crm.api.records` | `router.orders` | `kx.crm.api.deals:visible_to`<br>`kx.crm.api.pagination:keyset`<br>`kx.crm.api.pagination:page_of` |
| GET | `/v1/price-list/export` | `kx.crm.api.screens` | `router.export_price_list` | `kx.crm.services.price_lists:export_xlsx` |
| GET | `/v1/price-list/history` | `kx.crm.api.screens` | `router.price_list_history` | - |
| POST | `/v1/price-list/import` | `kx.crm.api.screens` | `router.import_price_list` | `kx.crm.services.price_lists:import_xlsx` |
| GET | `/v1/price-proposals` | `kx.crm.api.admin` | `router.proposals` | `kx.crm.api.pagination:keyset`<br>`kx.crm.api.pagination:page_of` |
| POST | `/v1/price-proposals/{rate_id}/approve` | `kx.crm.api.admin` | `router.approve` | `kx.crm.services.price_proposals:approve_rate` |
| POST | `/v1/price-proposals/{rate_id}/reject` | `kx.crm.api.admin` | `router.reject` | `kx.crm.services.price_proposals:reject_rate` |
| DELETE | `/v1/price-tiers/{tier_id}` | `kx.crm.api.screens` | `router.remove_tier` | `kx.crm.services.price_lists:delete_tier` |
| PATCH | `/v1/price-tiers/{tier_id}` | `kx.crm.api.screens` | `router.edit_tier` | `kx.crm.services.price_lists:update_tier` |
| GET | `/v1/products` | `kx.crm.api.records` | `router.products` | - |
| GET | `/v1/products/{product_id}` | `kx.crm.api.records` | `router.product` | - |
| PATCH | `/v1/products/{product_id}` | `kx.crm.api.records` | `router.edit_product` | `kx.crm.services.journal:record` |
| GET | `/v1/products/{product_id}/history` | `kx.crm.api.screens` | `router.product_history` | - |
| GET | `/v1/products/{product_id}/price-tiers` | `kx.crm.api.screens` | `router.tiers` | `kx.crm.services.price_lists:tiers_for` |
| POST | `/v1/products/{product_id}/price-tiers` | `kx.crm.api.screens` | `router.add_tier` | `kx.crm.services.price_lists:add_tier` |
| POST | `/v1/public/sessions` | `kx.agents.website` | `build_router.start` | `kx.agents.language:normalise_language` |
| POST | `/v1/public/sessions/{session_id}/identify` | `kx.agents.website` | `build_router.identify` | - |
| POST | `/v1/public/sessions/{session_id}/messages` | `kx.agents.website` | `build_router.message` | - |
| POST | `/v1/public/sessions/{session_id}/voice` | `kx.agents.website` | `build_router.voice` | - |
| GET | `/v1/public/unsubscribe` | `kx.automation.unsubscribe` | `build_router.unsubscribe_page` | - |
| POST | `/v1/public/unsubscribe` | `kx.automation.unsubscribe` | `build_router.unsubscribe` | `kx.automation.optout:apply_stop` |
| GET | `/v1/quote-versions/{version_id}` | `kx.crm.api.quotes` | `router.get_version` | `kx.crm.services.access:check_view`<br>`kx.crm.services.deals:get_deal`<br>`kx.crm.services.issuing:lines_of` |
| GET | `/v1/quote-versions/{version_id}/costs` | `kx.crm.api.quotes` | `router.get_costs` | `kx.crm.services.quotes:costs_of` |
| GET | `/v1/settings` | `kx.crm.api.admin` | `router.list_settings` | - |
| PUT | `/v1/settings/{key}` | `kx.crm.api.admin` | `router.put_setting` | - |
| GET | `/v1/stock` | `kx.crm.api.screens` | `router.stock` | - |
| PUT | `/v1/stock` | `kx.crm.api.screens` | `router.set_stock` | `kx.crm.services.orders:set_stock` |
| GET | `/v1/texts` | `kx.crm.api.texts` | `router.list_texts` | `kx.crm.services.texts:as_dict`<br>`kx.crm.services.texts:list_texts` |
| POST | `/v1/texts/translate-missing` | `kx.crm.api.texts` | `router.translate_missing` | `kx.crm.services.texts:request_missing_translations` |
| PUT | `/v1/texts/{key}` | `kx.crm.api.texts` | `router.edit` | `kx.crm.services.texts:as_dict`<br>`kx.crm.services.texts:update_english` |
| GET | `/v1/texts/{key}/history` | `kx.crm.api.texts` | `router.history` | `kx.crm.services.texts:history` |
| POST | `/v1/texts/{key}/reset` | `kx.crm.api.texts` | `router.reset` | `kx.crm.services.texts:as_dict`<br>`kx.crm.services.texts:reset` |
| GET | `/v1/users` | `kx.crm.api.users` | `router.list_users` | - |
| POST | `/v1/users` | `kx.crm.api.users` | `router.create` | `kx.crm.services.auth:create_user` |
| PATCH | `/v1/users/{user_id}` | `kx.crm.api.users` | `router.patch` | `kx.crm.services.journal:record` |
| PUT | `/v1/users/{user_id}/availability` | `kx.crm.api.admin` | `router.availability` | `kx.crm.services.assignment:set_availability` |
| POST | `/v1/users/{user_id}/reset-mfa` | `kx.crm.api.users` | `router.reset_mfa` | `kx.crm.services.auth:reset_mfa` |
| POST | `/v1/users/{user_id}/unlock` | `kx.crm.api.users` | `router.unlock` | `kx.crm.services.auth:unlock` |
| POST | `/webhooks/cms` | `kx.automation.cms` | `build_router.cms_published` | `kx.automation.outbox:emit`<br>`kx.automation.repo:first_receipt` |
| POST | `/webhooks/telnyx/gather/{attempt_id}` | `kx.automation.webhooks` | `_telnyx_routes.telnyx_gather` | `kx.automation.channels.calls:gather_reply_texml`<br>`kx.automation.channels.calls:verify_signature` |
| POST | `/webhooks/telnyx/status/{attempt_id}` | `kx.automation.webhooks` | `_telnyx_routes.telnyx_status` | `kx.automation.channels.calls:verify_signature` |
| GET | `/webhooks/whatsapp` | `kx.automation.webhooks` | `_whatsapp_routes.whatsapp_verify` | `kx.automation.channels.whatsapp:verify_challenge` |
| POST | `/webhooks/whatsapp` | `kx.automation.webhooks` | `_whatsapp_routes.whatsapp_events` | `kx.automation.channels.whatsapp:parse_webhook`<br>`kx.automation.channels.whatsapp:verify_signature`<br>`kx.automation.inbound:record_whatsapp` |

### Background job tasks and Beat entries

| Task / Beat entry | Kind | Body | Calls |
| --- | --- | --- | --- |
| `agents.handle_event` | task (max 5 attempts) | `arifaa_pack.container:_wire_events.<locals>.agents_event` | `kx.agents.handlers:AgentHandlers.last_resort` |
| `app.cms_product_draft` | task (max 5 attempts) | `arifaa_pack.container:cms_product_draft` | - |
| `app.translate_text` | task (max 5 attempts) | `arifaa_pack.texts_sync:translate_text` | `kx.crm.services.texts:load_edits`<br>`kx.crm.services.texts:save_translation` |
| `automation.alert_negotiation_paused` | task (max 5 attempts) | `kx.automation.jobs:register_jobs.<locals>.pause_alert` | - |
| `automation.deliver_outbound` | task (max 6 attempts) | `kx.automation.jobs:register_jobs.<locals>.deliver` | - |
| `automation.handle_event` | task (max 5 attempts) | `kx.automation.jobs:register_jobs.<locals>.event` | - |
| `automation.ingest_inbound_email` | task (max 5 attempts) | `kx.automation.jobs:register_jobs.<locals>.email` | `kx.automation.channels.email:parse_inbound`<br>`kx.automation.channels.feedback:parse_feedback`<br>`kx.automation.inbound:record_email`<br>`kx.automation.inbound:record_mail_event` |
| `automation.ingest_whatsapp_media` | task (max 5 attempts) | `kx.automation.jobs:register_jobs.<locals>.media` | `kx.automation.inbound:attach_media`<br>`kx.automation.inbound:media_id_of` |
| `automation.optout_no_answer` | task (max 5 attempts) | `kx.automation.jobs:register_jobs.<locals>.optout_check` | `kx.automation.handlers:stop_after_no_answer` |
| `automation.quote_delivery_check` | task (max 5 attempts) | `kx.automation.jobs:register_jobs.<locals>.quote_check` | - |
| `automation.run_approval_ladder` | task (max 5 attempts) | `kx.automation.jobs:register_jobs.<locals>.ladder` | - |
| `automation.send_followup` | task (max 5 attempts) | `kx.automation.jobs:register_jobs.<locals>.followup` | - |
| `crm.handle_event` | task (max 5 attempts) | `arifaa_pack.container:_wire_events.<locals>.crm_event` | `kx.crm.jobs:dispatch` |
| `app.heartbeat` | Beat `0:00:30` | `arifaa_pack.container:_periodic.<locals>.beat_heartbeat` | `arifaa_pack.telemetry:heartbeat` |
| `app.retention` | Beat `<crontab: 11 23 * * * (m/h/dM/MY/d)>` | `arifaa_pack.container:_periodic.<locals>.retention_plumbing` | `kx.agents.retention:plumbing_rules`<br>`kx.agents.retention:run_retention`<br>`kx.automation.retention:plumbing_rules`<br>`kx.crm.services.retention:plumbing_rules`<br>`kx.crm.services.retention:run_retention` |
| `automation.mail_poll` | Beat `0:00:30` | `kx.automation.jobs:periodic_jobs.<locals>.mail_poll` | `kx.automation.channels.stalwart:raw_key` |
| `automation.price_scrapes` | Beat `<crontab: 0 2 * * * (m/h/dM/MY/d)>` | `kx.automation.jobs:periodic_jobs.<locals>.scrapes` | - |
| `automation.requeue_outbound` | Beat `<crontab: */5 * * * * (m/h/dM/MY/d)>` | `kx.automation.jobs:periodic_jobs.<locals>.requeue` | `kx.automation.outbox:emit` |
| `crm.approval_sweep` | Beat `<crontab: */5 * * * * (m/h/dM/MY/d)>` | `kx.crm.jobs:periodic_jobs.<locals>.sweep` | `kx.crm.services.approvals:sweep_overdue`<br>`kx.crm.services.change_requests:sweep_escalations` |
| `crm.fx_rates` | Beat `<crontab: 5 2 * * * (m/h/dM/MY/d)>` | `kx.crm.jobs:periodic_jobs.<locals>.fx_rates` | `kx.crm.services.fx:update_rates`<br>`kx.crm.services.fx:wanted` |
| `crm.lost_proposals` | Beat `<crontab: 7 4 * * * (m/h/dM/MY/d)>` | `kx.crm.jobs:periodic_jobs.<locals>.lost` | `kx.crm.services.lost_proposals:propose_lost` |
| `crm.price_staleness` | Beat `<crontab: 23 5 * * * (m/h/dM/MY/d)>` | `kx.crm.jobs:periodic_jobs.<locals>.staleness` | `kx.crm.services.price_proposals:emit_stale`<br>`kx.crm.services.quotes:expire_quotes` |
| `crm.retention` | Beat `<crontab: 41 23 * * * (m/h/dM/MY/d)>` | `kx.crm.jobs:periodic_jobs.<locals>.retention_` | `kx.crm.services.retention:run_retention` |
| `crm.scheduled_reports` | Beat `<crontab: */15 * * * * (m/h/dM/MY/d)>` | `kx.crm.jobs:periodic_jobs.<locals>.reports` | `kx.crm.services.scheduled_reports:send_due` |
| `jobs.dispatch_due` | Beat `0:00:30` | `arifaa_pack.container:_periodic.<locals>.dispatch_due` | - |
| `relay.agents` | Beat `0:00:05` | `arifaa_pack.container:_periodic.<locals>.relay_agents` | - |
| `relay.automation` | Beat `0:00:05` | `kx.automation.jobs:periodic_jobs.<locals>.relay` | - |
| `relay.crm` | Beat `0:00:05` | `kx.crm.jobs:periodic_jobs.<locals>.relay` | - |

Counts: 6 contract Protocols, 27 package-internal Protocols, 33 event names (all with a payload model), 117 routes
(98 CRM, 12 automation, 4 website, 3 app), 13 job tasks, 15 Beat entries.

Reading notes:

- "Service functions referenced" lists the platform functions an endpoint names in its code (helpers in its own
  router module followed). Methods called on injected objects (`deps.settings.update(...)`) do not show. 12 CRM
  routes show none: they read or write ORM rows in the router itself (`GET /v1/products`, `GET /v1/settings`,
  `GET /v1/stock`, `GET /v1/users`, ...) or call a method on a dependency (`PUT /v1/settings/{key}`). These are the
  routes where rule 4 (`router → service → repository → models`) is not followed today.
- Six events have no subscriber: `agent.run_completed.v1` (published by `kx.agents.runs`),
  `approval.overdue.v1` (`kx.crm.services.approvals`), `change_request.closed.v1`
  (`kx.crm.services.change_requests`), `price.stale.v1` (`kx.crm.services.price_proposals`),
  `settings.changed.v1` (`kx.crm.services.settings`) and `lane.risk_assessed.v1` (payload only; nothing publishes
  it). They are written to the outbox and relayed to nobody. Not a defect by itself (the §16 escalation runs from
  the `quote.submitted` ladder), but worth knowing before anyone adds a subscriber.

## 3. Imports inside each package that would cross slice walls (rule 5)

Rule 5: slices do not import other slices' `repository` or `models`. There are no repository modules today:
services (and some routers) query the ORM directly, so every ORM import from outside the slice that will own the
model becomes a rule-5 violation unless it moves to `_shared/` or behind the owning slice's service. Which slice
owns each model module is decided in Phase 1 (`docs/arch/CRM_SLICES.md`); the tables give the size of the problem
per module and per importer.

#### `kx.crm`: data-layer modules and who imports them

Count = imported names per importer group (a `from x import A, B` counts 2).

| Data module | Names | Importer groups (names) |
| --- | --- | --- |
| `models.base` | 2 from 2 groups | `jobs` 1, `services.journal` 1 |
| `models.catalogue` | 32 from 18 groups | `services.pricing` 9, `services.workbook` 3, `api.records` 2, `services.catalogue` 2, `services.price_lists` 2, `services.price_proposals` 2, `api.admin` 1, `api.deals` 1, `api.reports` 1, `gateway` 1, `jobs` 1, `services.assignment` 1, `services.deals` 1, `services.fx` 1, `services.negotiation` 1, `services.quotes` 1, `services.summary` 1, `services.texts` 1 |
| `models.changes` | 7 from 7 groups | `api.change_requests` 1, `api.deals` 1, `gateway` 1, `services.change_requests` 1, `services.dashboards` 1, `services.deals` 1, `services.inbox` 1 |
| `models.customers` | 23 from 15 groups | `api.records` 2, `api.reports` 2, `gateway` 2, `services.consent` 2, `services.deals` 2, `services.issuing` 2, `services.retention` 2, `services.summary` 2, `jobs` 1, `services.assignment` 1, `services.dashboards` 1, `services.grants` 1, `services.orders` 1, `services.phone` 1, `services.pricing` 1 |
| `models.deals` | 49 from 28 groups | `api.deals` 3, `services.deals` 3, `services.inbox` 3, `services.lost_proposals` 3, `services.quotes` 3, `api.admin` 2, `api.reports` 2, `services.assignment` 2, `services.consent` 2, `services.dashboards` 2, `services.issuing` 2, `services.negotiation` 2, `services.phone` 2, `services.pricing` 2, `services.retention` 2, `services.summary` 2, `api.change_requests` 1, `api.records` 1, `gateway` 1, `jobs` 1, `services.access` 1, `services.approvals` 1, `services.change_requests` 1, `services.grants` 1, `services.handover` 1, `services.journal` 1, `services.orders` 1, `services.staff` 1 |
| `models.fulfilment` | 11 from 6 groups | `services.orders` 5, `services.pricing` 2, `api.records` 1, `api.reports` 1, `api.screens` 1, `services.deals` 1 |
| `models.people` | 20 from 14 groups | `services.auth` 4, `services.settings` 3, `api.users` 2, `api.admin` 1, `api.auth` 1, `api.deals` 1, `api.reports` 1, `services.assignment` 1, `services.dashboards` 1, `services.handover` 1, `services.inbox` 1, `services.issuing` 1, `services.scheduled_reports` 1, `services.staff` 1 |
| `models.plumbing` | 5 from 5 groups | `api.screens` 1, `services.dashboards` 1, `services.journal` 1, `services.scheduled_reports` 1, `services.texts` 1 |
| `models.quotes` | 60 from 18 groups | `gateway` 6, `services.issuing` 6, `services.quotes` 6, `services.approvals` 4, `services.deals` 4, `services.negotiation` 4, `api.admin` 3, `api.deals` 3, `api.quotes` 3, `services.change_requests` 3, `services.dashboards` 3, `services.grants` 3, `services.inbox` 3, `services.lost_proposals` 3, `api.reports` 2, `services.pricing` 2, `services.access` 1, `services.price_lists` 1 |

#### `kx.crm`: service modules imported by other service / api modules (future cross-slice calls)

| Importer | Imports from (names) |
| --- | --- |
| `api.admin` | `services.approvals` 1, `services.assignment` 1, `services.journal` 1, `services.price_proposals` 1 |
| `api.assignment` | `services.access` 1, `services.deals` 1, `services.journal` 1, `services.quotes` 1 |
| `api.catalogue_workbook` | `services.journal` 1, `services.workbook` 1 |
| `api.change_requests` | `services.access` 1, `services.deals` 1, `services.journal` 1, `services.quotes` 1 |
| `api.company` | `services.journal` 1 |
| `api.deal_actions` | `services.access` 1, `services.consent` 1, `services.deals` 1, `services.handover` 1, `services.journal` 1, `services.orders` 1 |
| `api.deals` | `services.access` 1, `services.approvals` 1, `services.assignment` 1, `services.change_requests` 1, `services.journal` 1, `services.negotiation` 1, `services.quotes` 1 |
| `api.operations` | `services.journal` 2 |
| `api.quotes` | `services.access` 1, `services.approvals` 1, `services.deals` 1, `services.issuing` 1 |
| `api.records` | `services.journal` 2 |
| `api.reports` | `services.dashboards` 5 |
| `api.screens` | `services.dashboards` 1, `services.inbox` 1, `services.journal` 1, `services.orders` 1, `services.price_lists` 1 |
| `api.texts` | `services.journal` 1 |
| `api.users` | `services.auth` 1, `services.journal` 2 |
| `services.access` | `services.staff` 1 |
| `services.approvals` | `services.access` 1, `services.change_requests` 1, `services.deals` 1, `services.grants` 1, `services.issuing` 1, `services.journal` 4, `services.settings` 1, `services.staff` 1 |
| `services.assignment` | `services.approvals` 1, `services.change_requests` 1, `services.journal` 4, `services.settings` 1, `services.staff` 1 |
| `services.auth` | `services.journal` 3, `services.staff` 1 |
| `services.catalogue` | `services.journal` 2 |
| `services.change_requests` | `services.access` 1, `services.deals` 1, `services.issuing` 1, `services.journal` 4, `services.staff` 1 |
| `services.company` | `services.journal` 3, `services.staff` 1 |
| `services.consent` | `services.journal` 4, `services.staff` 1 |
| `services.deals` | `services.journal` 4 |
| `services.fx` | `services.journal` 3, `services.staff` 1 |
| `services.grants` | `services.deals` 1, `services.journal` 3, `services.settings` 1 |
| `services.handover` | `services.access` 1, `services.company` 1, `services.deals` 1, `services.grants` 3, `services.issuing` 1, `services.journal` 3 |
| `services.inbox` | `services.approvals` 1, `services.change_requests` 1 |
| `services.issuing` | `services.company` 1, `services.deals` 1, `services.grants` 4, `services.journal` 4, `services.phone` 1, `services.settings` 1, `services.staff` 1 |
| `services.lost_proposals` | `services.journal` 3, `services.staff` 1 |
| `services.negotiation` | `services.deals` 1, `services.issuing` 1, `services.journal` 4, `services.pricing` 1, `services.quotes` 6, `services.settings` 1, `services.staff` 1 |
| `services.orders` | `services.deals` 1, `services.grants` 1, `services.journal` 4, `services.quotes` 2, `services.staff` 1 |
| `services.phone` | `services.journal` 4, `services.staff` 1 |
| `services.price_lists` | `services.journal` 2, `services.pricing` 1 |
| `services.price_proposals` | `services.journal` 3, `services.pricing` 1, `services.settings` 1 |
| `services.pricing` | `services.settings` 1 |
| `services.quotes` | `services.approvals` 1, `services.deals` 1, `services.grants` 1, `services.issuing` 2, `services.journal` 3, `services.pricing` 11, `services.settings` 1, `services.staff` 1 |
| `services.retention` | `services.deals` 1, `services.journal` 3 |
| `services.scheduled_reports` | `services.dashboards` 2, `services.journal` 3, `services.settings` 1, `services.staff` 1 |
| `services.settings` | `services.company` 1, `services.journal` 3 |
| `services.summary` | `services.company` 1, `services.deals` 1, `services.issuing` 1, `services.journal` 4, `services.phone` 1 |
| `services.texts` | `services.journal` 3 |
| `services.workbook` | `services.journal` 3, `services.price_lists` 2, `services.pricing` 1 |

#### `kx.automation`: data-layer modules and who imports them

Count = imported names per importer group (a `from x import A, B` counts 2).

| Data module | Names | Importer groups (names) |
| --- | --- | --- |
| `models` | 27 from 10 groups | `inbound` 5, `outbound` 5, `handlers` 4, `jobs` 3, `scraping` 3, `admin_api` 2, `alerts` 2, `channels.calls` 1, `optout` 1, `outbox` 1 |
| `repo` | 18 from 6 groups | `inbound` 7, `outbound` 5, `alerts` 2, `optout` 2, `cms` 1, `handlers` 1 |

#### `kx.automation`: module-to-module imports outside the data layer (flat package)

| Importer | Imports from (names) |
| --- | --- |
| `alerts` | `channels` 1, `channels.calls` 12, `ports` 4, `settings` 1 |
| `channels.calls` | `channels` 2, `settings` 1 |
| `channels.email` | `channels` 1, `settings` 1 |
| `channels.email_chain` | `channels` 1, `channels.email` 1 |
| `channels.stalwart` | `channels` 1, `channels.email` 2, `settings` 2 |
| `channels.whatsapp` | `channels` 2, `settings` 1 |
| `cms` | `outbox` 1 |
| `handlers` | `alerts` 3, `optout` 1, `outbound` 1, `outbox` 1, `ports` 3, `settings` 1 |
| `inbound` | `channels.email` 1, `channels.feedback` 2, `channels.whatsapp` 1, `optout` 1, `outbox` 1 |
| `jobs` | `alerts` 3, `channels.email` 1, `channels.feedback` 1, `channels.stalwart` 2, `handlers` 3, `inbound` 1, `outbound` 3, `outbox` 1, `ports` 3, `scraping` 1, `settings` 1 |
| `optout` | `outbox` 1 |
| `outbound` | `channels` 1, `channels.email` 1, `channels.whatsapp` 1, `contact_hours` 2, `outbox` 1, `ports` 4, `ratelimit` 2, `settings` 1 |
| `ports` | `alerts` 1 |
| `scraping` | `channels` 2, `outbox` 1, `ports` 2, `settings` 1 |
| `unsubscribe` | `optout` 1, `settings` 1 |
| `webhooks` | `alerts` 2, `channels.calls` 1, `channels.whatsapp` 1, `inbound` 1, `ports` 2, `settings` 1 |

#### `kx.agents`: data-layer modules and who imports them

Count = imported names per importer group (a `from x import A, B` counts 2).

| Data module | Names | Importer groups (names) |
| --- | --- | --- |
| `orm` | 17 from 6 groups | `memory` 7, `runs` 4, `knowledge` 3, `evals` 1, `handlers` 1, `website` 1 |

#### `kx.agents`: module-to-module imports outside the data layer (flat package)

| Importer | Imports from (names) |
| --- | --- |
| `context` | `knowledge` 1, `registry` 2 |
| `evals` | `context` 1, `language` 1, `models` 1, `orchestrator` 1, `registry` 3, `service` 1, `testing` 4 |
| `graphs` | `context` 4, `guard` 3, `middleware` 3, `models` 4, `orchestrator` 1, `registry` 2, `tools` 1 |
| `guard` | `context` 3, `registry` 1 |
| `handlers` | `knowledge` 1, `language` 1, `memory` 1, `registry` 1, `service` 3 |
| `memory` | `models` 1 |
| `middleware` | `context` 2, `language` 1 |
| `models` | `registry` 1 |
| `orchestrator` | `registry` 1 |
| `registry` | `language` 2 |
| `runs` | `context` 1 |
| `service` | `context` 3, `graphs` 4, `guard` 2, `knowledge` 1, `language` 1, `memory` 1, `models` 1, `orchestrator` 2, `registry` 7, `runs` 3, `tracing` 1 |
| `tools` | `context` 1, `registry` 2 |
| `voice` | `guard` 1, `language` 2, `registry` 2, `service` 2 |
| `website` | `language` 1, `service` 2, `voice` 1 |

#### What the tables say for Phase 1

- `kx.crm.models.quotes` (70 names from 19 groups), `models.deals` (54 from 29 groups) and `models.catalogue`
  (45 from 19 groups) are used almost everywhere. Moving them into one slice would create most of the rule-5
  baseline; they are the candidates for `_shared/` (or for a narrow read API in their slice). The decision changes
  where the slice walls run, so it is a Phase 1 mapping question (`docs/arch/CRM_SLICES.md`), not made here.
- `services.journal` (audit + timeline + outbox) is imported by nearly every service and many routers: a
  `_shared/` module by nature, as the brief already suggests.
- `kx.automation` has a real data layer split: `models` (ORM) and `repo` (queries); `repo` is imported by 6
  groups. `kx.agents` keeps its ORM in `orm` (6 importers).

### App imports of package internals (rule 3)

The app imports concrete modules of every package (there is no public surface yet). Those that rule 3 would
forbid as written:

| App module | Import | Note |
| --- | --- | --- |
| `arifaa_pack/container.py` | `kx.crm.models.Product` | `cms_product_draft` writes `cms_product_id` with an ORM update in the app |
| `arifaa_pack/cli.py` (`first-run`) | `kx.crm.models.User` | checks whether the MD / admin exist |
| `container.py`, `cli.py`, `config.py`, `texts_sync.py` | `kx.agents.models` (`ModelRouter`, providers, `Effort`) | **not ORM**: the LLM model router. A rule "no `kx.*.models`" would forbid it by name |

## 4. Brief vs repository

| Brief says | Repository | Effect on the plan |
| --- | --- | --- |
| "the four existing import rules" | `pyproject.toml` `[tool.importlinter]` has **three** contracts (core independent, packages independent, platform never imports the app). "Only the app imports Celery" is enforced by dependencies (only `apps/arifaa-pack/pyproject.toml` lists `celery`), not by import-linter | none for Phase 0; Phase 2 could add a fourth contract (`forbidden: kx.* -> celery`) |
| Rule 3 / Phase 2 forbidden contract on `kx.*.*.models` | `kx.agents.models` is the LLM model router (used by the app); agents' ORM is `kx.agents.orm`; automation's queries are `kx.automation.repo` | the Phase 2 contract must name the ORM modules per package (or `kx.agents.models` be renamed in its slice move); question for the lead |
| `kx.crm/__init__.py` "public surface" | empty today | Phase 1.2 creates it |
| Suggested `kx.automation` slice `followups/` | `FollowUps` lives in `kx/automation/handlers.py` | Phase 2 mapping |
| Quality gate "full suite" | 864 tests collected on Linux (`live-test/scripts/pytest_in_linux.sh`), 0 failed, 4 skipped (need `redis-server`); the launch notes said 856 | the baseline is 864 |
| Pin third-party actions to a commit SHA | `ci.yml` / `release.yml` pin by tag (`actions/checkout@v6`, `astral-sh/setup-uv@v8.1.0` ...) | new workflows pin SHAs; the existing ones are left as they are (another branch edits `ci.yml`) |
| "Alembic migration history" per package | all revisions live in the app: `apps/arifaa-pack/src/arifaa_pack/migrations/<db>/versions` | none (never touched) |
| `docs/arch/`, `docs/adr/`, `.github/CODEOWNERS`, PR template | none existed | created in Phase 0 |

## 5. Documents that disagree with the code

- `docs/REUSE_CATALOGUE.md` line 17 says `kx.core.event_payloads` has "23 payload models"; `PAYLOADS` maps 33.
- `docs/ARCHITECTURE.md` (16 Nov v2) and the brief agree on the layers and boundaries; no conflict found.

## 6. Found, not fixed

- `kx/automation/ports.py:16` imports `kx.automation.alerts.LadderTicket` under `TYPE_CHECKING` while `alerts`
  imports `ports`: a type-only cycle between the port module and an implementation module.
- Six events are published with no subscriber (section 2); `lane.risk_assessed.v1` is not published at all.
- `docs/REUSE_CATALOGUE.md:17` stale payload count (section 5).
