# Feature map

Where each business feature lives, who implements each contract, and who reacts to each event.

- The tables below the line are **generated** by `kx map --format md` from the real container (ADR-0001); CI
  fails when they are stale. Refresh: `uv run kx map --write docs/FEATURE_MAP.md`. Narrow a question with
  `uv run kx map --feature quotes` (or `--format json` for a diff-friendly dump).
- The SRS table is **hand-maintained**: update it when a feature moves (slices: `docs/arch/CRM_SLICES.md`,
  `docs/arch/AUTOMATION_AGENTS_SLICES.md`). Business rules: the SRS (`docs/Arifaa Pack — Detailed Business Flow v1 (SRS basis) (2).md`).

## SRS sections → code (hand-maintained)

Paths are relative to each package's `src/` folder (`kx/crm/...` is `packages/kx-core-crm/src/kx/crm/...`).

| SRS | Feature | Main code | Events | Slice (ADR-0001) |
| --- | --- | --- | --- | --- |
| §8 | Lead capture and qualification; §8.1 opt-out; §8.2 phone status; Requirement Summary | `kx/crm/deals/service.py`, `kx/crm/rules.py`, `kx/crm/contacts/consent.py`, `kx/crm/contacts/phone.py`, `kx/crm/deals/summary.py`, `kx/crm/gateway.py` (`try_qualify`, `record_opt_out`); `kx/core/optout.py`, `kx/automation/optout/__init__.py`, `kx/automation/optout/unsubscribe.py`; `kx/agents/graphs/conversation.py` | `deal.qualified.v1`, `consent.opted_out.v1`, `consent.stop_unclear.v1`, `deal.requirement_summary.v1` | crm `deals/`, `contacts/`; automation `optout/` |
| §9 | Deal assignment (round robin, rules, backup, takeover, hand-back) | `kx/crm/assignment/service.py`, `kx/crm/_shared/staff.py`, `kx/crm/assignment/router.py`, `kx/crm/admin/router.py` (availability) | `deal.assigned.v1` | crm `assignment/` |
| §10–11 | Price list and quotation (build, approve, issue, send) | `kx/crm/pricing/price_list.py`, `kx/crm/pricing/engine.py`, `kx/crm/pricing/service.py`, `kx/crm/pricing/price_list_service.py`, `kx/crm/quotes/service.py`, `kx/crm/approvals/service.py`, `kx/crm/quotes/issuing.py`, `kx/crm/quotes/grants.py`, `kx/crm/quotes/pdf.py`, `kx/crm/quotes/router.py`, `kx/crm/deals/router.py`, `kx/crm/screens/router.py`; `kx/automation/outbound/__init__.py`; `kx/agents/graphs/quotation.py` | `quote.submitted.v1`, `quote.approved.v1`, `quote.rejected.v1`, `quote.correction_requested.v1`, `quote.not_delivered.v1`, `quote.resend_requested.v1` | crm `pricing/`, `quotes/`, `approvals/` |
| §12 | Negotiation inside the range (AI) | `kx/crm/pricing/engine.py` (`ladder_between`, `evaluate_ladder_counter`), `kx/crm/negotiation/service.py`, `kx/crm/gateway.py` (`evaluate_counter`); `kx/agents/graphs/deal.py`, `kx/agents/guard.py`; `kx/core/price_guard.py` | `negotiation.paused.v1` | crm `negotiation/`; agents `graphs/`, `guard.py` |
| §13 | Change requests (to the salesperson) | `kx/crm/change_requests/service.py`, `kx/crm/change_requests/router.py`, `kx/crm/gateway.py` (`raise_change_request`); `kx/agents/tools.py`; `kx/automation/handlers.py` + `kx/automation/alerts.py` (`ApprovalLadder`) | `change_request.raised.v1`, `change_request.closed.v1` | crm `change_requests/` |
| §14 | Final quotation and handover (AI off, staff reply, click-to-call) | `kx/crm/deals/service.py` (`hand_over`), `kx/crm/handover/service.py`, `kx/crm/deals/actions_router.py`; `kx/automation/alerts.py` (`CallBridge`); `kx/agents/orchestrator.py` | `quote.approved.v1` (final) | crm `handover/`, `deals/` |
| §15 | Payment, WON, orders and stock; LOST proposals | `kx/crm/orders/service.py`, `kx/crm/deals/lost_proposals.py`, `kx/crm/deals/actions_router.py`, `kx/crm/screens/router.py` (stock) | `deal.won.v1`, `order.status_changed.v1` | crm `orders/`, `deals/` |
| §16 | Follow-ups and reminders (day 2/5/10; 30/60/120-minute ladder) | `kx/automation/handlers.py` (`FollowUps`), `kx/automation/jobs.py`, `kx/automation/alerts.py`, `kx/automation/outbound/contact_hours.py`; `kx/crm/rules.py`, `kx/crm/approvals/service.py` (`sweep_overdue`) | `deal.next_action_set.v1`, `deal.stage_changed.v1` | automation `handlers.py` (`FollowUps`), `alerts.py` |
| §17 | Notifications matrix (event → recipients) | `kx/automation/handlers.py` (`EventHandlers.registry()`), `kx/automation/alerts.py` (`notify_staff`); table test `packages/kx-core-automation/tests/test_notifications_matrix_db.py` | every event in "Events: who reacts" below | automation `alerts.py` |

"Planned slice" names follow the brief's suggestions; Phase 1 / 2 fix them in `docs/arch/CRM_SLICES.md` and the
other mapping files, and this table is updated with each move.

---

<!-- BEGIN GENERATED: kx map --format md (do not edit by hand) -->

## Contracts (`kx.core.contracts`)

| Protocol | Implemented by | File |
| --- | --- | --- |
| `kx.core.contracts:BlobStore` | `kx.automation.media:S3MediaStore` | `packages/kx-core-automation/src/kx/automation/media.py` |
| `kx.core.contracts:ClickToCall` | `kx.automation.alerts:CallBridge` | `packages/kx-core-automation/src/kx/automation/alerts.py` |
| `kx.core.contracts:CrmGateway` | `kx.crm.gateway:CrmGatewayImpl` | `packages/kx-core-crm/src/kx/crm/gateway.py` |
| `kx.core.contracts:GrantConsumer` | `kx.crm.gateway:GrantConsumerImpl` | `packages/kx-core-crm/src/kx/crm/gateway.py` |
| `kx.core.contracts:MessageSender` | `kx.automation.outbound:OutboundService` | `packages/kx-core-automation/src/kx/automation/outbound/__init__.py` |
| `kx.core.contracts:StaffMailer` | `arifaa_pack.adapters:StaffCodeMailer` | `apps/arifaa-pack/src/arifaa_pack/adapters.py` |

## Other ports (Protocols inside a package)

| Protocol | Implemented by | File |
| --- | --- | --- |
| `kx.agents.handlers:MessageBurst` | `arifaa_pack.adapters:ThreadBurst` | `apps/arifaa-pack/src/arifaa_pack/adapters.py` |
| `kx.agents.handlers:ReplyAddressBook` | `arifaa_pack.adapters:ThreadAddressBook` | `apps/arifaa-pack/src/arifaa_pack/adapters.py` |
| `kx.agents.knowledge:ContentSource` | `arifaa_pack.adapters:PayloadContentSource` | `apps/arifaa-pack/src/arifaa_pack/adapters.py` |
| `kx.agents.knowledge:Embedder` | `arifaa_pack.adapters:HttpEmbedder` | `apps/arifaa-pack/src/arifaa_pack/adapters.py` |
| `kx.agents.knowledge:KnowledgeSearch` | `kx.agents.knowledge:KnowledgeBase` | `packages/kx-agent-framework/src/kx/agents/knowledge.py` |
| `kx.agents.models:ModelProvider` | `kx.agents.models:AnthropicApiProvider`<br>`kx.agents.models:GeminiApiProvider` | `packages/kx-agent-framework/src/kx/agents/models.py`<br>`packages/kx-agent-framework/src/kx/agents/models.py` |
| `kx.agents.registry:PromptStore` | `kx.agents.registry:DefaultPromptStore` | `packages/kx-agent-framework/src/kx/agents/registry.py` |
| `kx.agents.voice:VoicePlugins` | `arifaa_pack.adapters:PlatformVoicePlugins` | `apps/arifaa-pack/src/arifaa_pack/adapters.py` |
| `kx.agents.website:HumanCheck` | `arifaa_pack.adapters:TurnstileVerifier` | `apps/arifaa-pack/src/arifaa_pack/adapters.py` |
| `kx.agents.website:PublicLimits` | `arifaa_pack.adapters:PublicLimiter` | `apps/arifaa-pack/src/arifaa_pack/adapters.py` |
| `kx.automation.alerts:Caller` | `kx.automation.channels.calls:TelnyxCaller` | `packages/kx-core-automation/src/kx/automation/channels/calls.py` |
| `kx.automation.alerts:KillSwitchReader` | `kx.core.killswitch:KillSwitchLadder` | `packages/kx-core-db/src/kx/core/killswitch.py` |
| `kx.automation.channels.email:SesV2Client` | - | - |
| `kx.automation.channels:EmailTransport` | `kx.automation.channels.email:EmailSender`<br>`kx.automation.channels.email_chain:EmailChain`<br>`kx.automation.channels.stalwart:StalwartSender` | `packages/kx-core-automation/src/kx/automation/channels/email.py`<br>`packages/kx-core-automation/src/kx/automation/channels/email_chain.py`<br>`packages/kx-core-automation/src/kx/automation/channels/stalwart.py` |
| `kx.automation.jobs:MediaDownloader` | `kx.automation.channels.whatsapp:WhatsAppClient` | `packages/kx-core-automation/src/kx/automation/channels/whatsapp.py` |
| `kx.automation.media:S3Client` | - | - |
| `kx.automation.outbound:KillSwitchReader` | `kx.core.killswitch:KillSwitchLadder` | `packages/kx-core-db/src/kx/core/killswitch.py` |
| `kx.automation.outbound:RateLimiter` | `kx.automation.outbound.ratelimit:TokenBucketLimiter` | `packages/kx-core-automation/src/kx/automation/outbound/ratelimit.py` |
| `kx.automation.outbound:TemplateSpend` | `kx.core.spend:SpendMeter` | `packages/kx-core-db/src/kx/core/spend.py` |
| `kx.automation.outbound:WhatsAppTransport` | `kx.automation.channels.whatsapp:WhatsAppClient` | `packages/kx-core-automation/src/kx/automation/channels/whatsapp.py` |
| `kx.automation.ports:ApprovalStatusReader` | `kx.crm.gateway:ApprovalStatusReaderImpl` | `packages/kx-core-crm/src/kx/crm/gateway.py` |
| `kx.automation.ports:MediaStore` | `kx.automation.media:S3MediaStore` | `packages/kx-core-automation/src/kx/automation/media.py` |
| `kx.automation.ports:TaskScheduler` | `kx.automation.jobs:JobScheduler` | `packages/kx-core-automation/src/kx/automation/jobs.py` |
| `kx.core.jobs:Kicker` | `arifaa_pack.jobqueue:CeleryKicker` | `apps/arifaa-pack/src/arifaa_pack/jobqueue.py` |
| `kx.crm.auth.throttle:AuthThrottle` | `arifaa_pack.adapters:RedisAuthThrottle`<br>`kx.crm.auth.throttle:MemoryThrottle` | `apps/arifaa-pack/src/arifaa_pack/adapters.py`<br>`packages/kx-core-crm/src/kx/crm/auth/throttle.py` |
| `kx.crm.pricing.fx:FxSource` | `arifaa_pack.adapters:OpenRatesFx` | `apps/arifaa-pack/src/arifaa_pack/adapters.py` |
| `kx.crm.quotes.pdf:PdfEngine` | `kx.crm.quotes.pdf:ChromiumEngine`<br>`kx.crm.quotes.pdf:WeasyPrintEngine` | `packages/kx-core-crm/src/kx/crm/quotes/pdf.py`<br>`packages/kx-core-crm/src/kx/crm/quotes/pdf.py` |
| `kx.crm.quotes.pdf:PdfUploader` | `kx.crm.quotes.pdf:S3PdfUploader` | `packages/kx-core-crm/src/kx/crm/quotes/pdf.py` |

## Events: who reacts

| Event | Payload | Handlers |
| --- | --- | --- |
| `agent.run_completed.v1` | `kx.core.event_payloads:AgentRunCompleted` | - |
| `approval.overdue.v1` | `kx.core.event_payloads:ApprovalOverdue` | - |
| `catalogue.product_content_changed.v1` | `kx.core.event_payloads:ProductContentChanged` | `app: arifaa_pack.container:cms_product_draft` |
| `catalogue.product_published.v1` | `kx.core.event_payloads:ProductPublished` | `crm: kx.crm.jobs:on_product_published` |
| `change_request.closed.v1` | `kx.core.event_payloads:ChangeRequestClosed` | - |
| `change_request.raised.v1` | `kx.core.event_payloads:ChangeRequested` | `automation: kx.automation.handlers:EventHandlers._change_requested` |
| `consent.opted_out.v1` | `kx.core.event_payloads:OptedOut` | `automation: kx.automation.handlers:EventHandlers._opted_out`<br>`crm: kx.crm.jobs:on_opted_out` |
| `consent.stop_unclear.v1` | `kx.core.event_payloads:StopUnclear` | `automation: kx.automation.handlers:EventHandlers._stop_unclear` |
| `contact.anonymised.v1` | `kx.core.event_payloads:ContactAnonymised` | `agents: kx.agents.handlers:AgentHandlers._anonymise`<br>`automation: kx.automation.handlers:EventHandlers._contact_anonymised` |
| `conversation.summary_updated.v1` | `kx.core.event_payloads:ConversationSummaryUpdated` | `crm: kx.crm.jobs:on_summary_updated` |
| `deal.assigned.v1` | `kx.core.event_payloads:DealAssigned` | `automation: kx.automation.handlers:EventHandlers._deal_assigned` |
| `deal.next_action_set.v1` | `kx.core.event_payloads:DealNextActionSet` | `automation: kx.automation.handlers:EventHandlers._next_action_set` |
| `deal.qualified.v1` | `kx.core.event_payloads:DealQualified` | `crm: kx.crm.jobs:on_deal_qualified` |
| `deal.requirement_summary.v1` | `kx.core.event_payloads:RequirementSummary` | `automation: kx.automation.handlers:EventHandlers._requirement_summary` |
| `deal.stage_changed.v1` | `kx.core.event_payloads:DealStageChanged` | `automation: kx.automation.handlers:EventHandlers._stage_changed` |
| `deal.won.v1` | `kx.core.event_payloads:DealWon` | `automation: kx.automation.handlers:EventHandlers._deal_won` |
| `kb.content_published.v1` | `kx.core.event_payloads:KbContentPublished` | `agents: kx.agents.handlers:AgentHandlers._kb` |
| `lane.risk_assessed.v1` | `kx.core.event_payloads:LaneRiskAssessed` | - |
| `message.received.v1` | `kx.core.event_payloads:MessageReceived` | `agents: kx.agents.handlers:AgentHandlers._message`<br>`crm: kx.crm.jobs:on_message_received` |
| `message.status.v1` | `kx.core.event_payloads:MessageStatus` | `crm: kx.crm.jobs:on_message_status` |
| `negotiation.paused.v1` | `kx.core.event_payloads:NegotiationPaused` | `automation: kx.automation.handlers:EventHandlers._negotiation_paused` |
| `order.status_changed.v1` | `kx.core.event_payloads:OrderStatusChanged` | `automation: kx.automation.handlers:EventHandlers._order_status_changed` |
| `price.proposed.v1` | `kx.core.event_payloads:PriceProposed` | `crm: kx.crm.jobs:on_price_proposed` |
| `price.stale.v1` | `kx.core.event_payloads:PriceStale` | - |
| `quote.approved.v1` | `kx.core.event_payloads:QuoteApproved` | `agents: kx.agents.handlers:AgentHandlers._quote`<br>`automation: kx.automation.handlers:EventHandlers._quote_approved` |
| `quote.correction_requested.v1` | `kx.core.event_payloads:QuoteCorrectionRequested` | `agents: kx.agents.handlers:AgentHandlers._quote` |
| `quote.not_delivered.v1` | `kx.core.event_payloads:QuoteNotDelivered` | `crm: kx.crm.jobs:on_quote_not_delivered` |
| `quote.rejected.v1` | `kx.core.event_payloads:QuoteRejected` | `agents: kx.agents.handlers:AgentHandlers._quote` |
| `quote.resend_requested.v1` | `kx.core.event_payloads:QuoteResendRequested` | `automation: kx.automation.handlers:EventHandlers._quote_resend` |
| `quote.submitted.v1` | `kx.core.event_payloads:QuoteSubmitted` | `automation: kx.automation.handlers:EventHandlers._quote_submitted` |
| `settings.changed.v1` | `kx.core.event_payloads:SettingsChanged` | - |
| `staff.notice_requested.v1` | `kx.core.event_payloads:StaffNotice` | `automation: kx.automation.handlers:EventHandlers._staff_notice` |
| `texts.text_changed.v1` | `kx.core.event_payloads:MessageTextChanged` | `app: arifaa_pack.texts_sync:translate_text` |

## HTTP routes

| Method | Path | Endpoint | Calls |
| --- | --- | --- | --- |
| GET | `/.well-known/jwks.json` | `arifaa_pack.api:create_app.<locals>.jwks` | - |
| GET | `/healthz` | `arifaa_pack.api:create_app.<locals>.healthz` | - |
| GET | `/healthz/beat` | `arifaa_pack.api:create_app.<locals>.healthz_beat` | `arifaa_pack.telemetry:beat_health` |
| GET | `/v1/admin/channel-accounts` | `kx.automation.admin.router:build_router.<locals>.accounts` | - |
| PATCH | `/v1/admin/channel-accounts/{account_id}` | `kx.automation.admin.router:build_router.<locals>.edit_account` | - |
| GET | `/v1/admin/kill-switch` | `kx.crm.operations.router:router.<locals>.get_level` | - |
| PUT | `/v1/admin/kill-switch` | `kx.crm.operations.router:router.<locals>.set_level` | `kx.crm._shared.journal:record` |
| GET | `/v1/admin/message-templates` | `kx.automation.admin.router:build_router.<locals>.templates` | - |
| PUT | `/v1/admin/message-templates` | `kx.automation.admin.router:build_router.<locals>.upsert_template` | - |
| POST | `/v1/admin/message-templates/{template_id}/status` | `kx.automation.admin.router:build_router.<locals>.template_status` | - |
| GET | `/v1/approvals` | `kx.crm.quotes.router:router.<locals>.list_approvals` | `kx.crm._shared.pagination:keyset`<br>`kx.crm._shared.pagination:page_of` |
| POST | `/v1/approvals/{approval_id}/decision` | `kx.crm.quotes.router:router.<locals>.decide` | `kx.crm.approvals.service:decide` |
| POST | `/v1/auth/login` | `kx.crm.auth.router:router.<locals>.login` | `kx.crm.auth.service:login` |
| POST | `/v1/auth/logout` | `kx.crm.auth.router:router.<locals>.logout` | `kx.crm.auth.service:logout` |
| GET | `/v1/auth/me` | `kx.crm.auth.router:router.<locals>.me` | - |
| POST | `/v1/auth/mfa/email/send` | `kx.crm.auth.router:router.<locals>.mfa_email_send` | `kx.crm.auth.service:send_email_code` |
| POST | `/v1/auth/mfa/email/verify` | `kx.crm.auth.router:router.<locals>.mfa_email_verify` | `kx.crm.auth.service:verify_second_step` |
| POST | `/v1/auth/mfa/totp` | `kx.crm.auth.router:router.<locals>.mfa_totp` | `kx.crm.auth.service:verify_second_step` |
| POST | `/v1/auth/password` | `kx.crm.auth.router:router.<locals>.password_change` | `kx.crm.auth.service:change_password` |
| POST | `/v1/auth/password/reset/finish` | `kx.crm.auth.router:router.<locals>.reset_finish` | `kx.crm.auth.service:finish_reset` |
| POST | `/v1/auth/password/reset/start` | `kx.crm.auth.router:router.<locals>.reset_start` | `kx.crm.auth.service:start_reset` |
| POST | `/v1/auth/refresh` | `kx.crm.auth.router:router.<locals>.refresh` | `kx.crm.auth.service:refresh` |
| POST | `/v1/auth/totp/confirm` | `kx.crm.auth.router:router.<locals>.totp_confirm` | `kx.crm.auth.service:confirm_totp` |
| POST | `/v1/auth/totp/enrol` | `kx.crm.auth.router:router.<locals>.totp_enrol` | `kx.crm.auth.service:begin_totp` |
| GET | `/v1/catalogue/workbook` | `kx.crm.catalogue.router:router.<locals>.download` | `kx.crm.catalogue.workbook:download` |
| POST | `/v1/catalogue/workbook/apply` | `kx.crm.catalogue.router:router.<locals>.apply` | `kx.crm.catalogue.workbook:apply` |
| POST | `/v1/catalogue/workbook/preview` | `kx.crm.catalogue.router:router.<locals>.preview` | `kx.crm.catalogue.workbook:preview` |
| GET | `/v1/change-requests` | `kx.crm.change_requests.router:router.<locals>.inbox` | `kx.crm.deals.router:visible_to` |
| GET | `/v1/change-requests/{cr_id}` | `kx.crm.change_requests.router:router.<locals>.card` | `kx.crm._shared.access:check_view`<br>`kx.crm.deals.service:get_deal` |
| POST | `/v1/change-requests/{cr_id}/decline` | `kx.crm.change_requests.router:router.<locals>.decline` | `kx.crm.change_requests.service:decline` |
| POST | `/v1/change-requests/{cr_id}/resolve` | `kx.crm.change_requests.router:router.<locals>.resolve` | `kx.crm.change_requests.service:resolve` |
| POST | `/v1/change-requests/{cr_id}/revise` | `kx.crm.change_requests.router:router.<locals>.revise` | `kx.crm._shared.access:check_edit`<br>`kx.crm.change_requests.service:link_revision`<br>`kx.crm.deals.service:get_deal`<br>`kx.crm.quotes.service:revise_quote` |
| POST | `/v1/change-requests/{cr_id}/start` | `kx.crm.change_requests.router:router.<locals>.start` | `kx.crm.change_requests.service:start` |
| GET | `/v1/companies` | `kx.crm.records.router:router.<locals>.companies` | `kx.crm._shared.pagination:keyset`<br>`kx.crm._shared.pagination:page_of`<br>`kx.crm.deals.router:visible_to` |
| POST | `/v1/companies` | `kx.crm.records.router:router.<locals>.create_company` | `kx.crm._shared.journal:record` |
| PATCH | `/v1/companies/{company_id}` | `kx.crm.records.router:router.<locals>.edit_company` | `kx.crm._shared.journal:record`<br>`kx.crm.deals.router:visible_to` |
| GET | `/v1/company` | `kx.crm.company.router:router.<locals>.show` | `kx.crm.company.service:letterhead` |
| PATCH | `/v1/company` | `kx.crm.company.router:router.<locals>.edit` | `kx.crm.company.service:letterhead`<br>`kx.crm.company.service:update` |
| DELETE | `/v1/company/logo` | `kx.crm.company.router:router.<locals>.remove_logo` | `kx.crm.company.service:letterhead`<br>`kx.crm.company.service:update` |
| PUT | `/v1/company/logo` | `kx.crm.company.router:router.<locals>.upload_logo` | `kx.crm.company.service:letterhead`<br>`kx.crm.company.service:logo_data_uri`<br>`kx.crm.company.service:update` |
| GET | `/v1/contacts` | `kx.crm.records.router:router.<locals>.contacts` | `kx.crm._shared.pagination:keyset`<br>`kx.crm._shared.pagination:page_of`<br>`kx.crm.deals.router:visible_to` |
| GET | `/v1/contacts/{contact_id}` | `kx.crm.records.router:router.<locals>.contact` | `kx.crm.deals.router:visible_to` |
| PATCH | `/v1/contacts/{contact_id}` | `kx.crm.records.router:router.<locals>.edit_contact` | `kx.crm._shared.journal:record`<br>`kx.crm.deals.router:visible_to` |
| GET | `/v1/dashboard/summary` | `kx.crm.admin.router:router.<locals>.summary` | `kx.crm.pricing.proposals:material_freshness` |
| GET | `/v1/dashboards/manager` | `kx.crm.reports.router:router.<locals>.manager` | `kx.crm.reports.dashboards:manager_board` |
| GET | `/v1/dashboards/md` | `kx.crm.reports.router:router.<locals>.md` | `kx.crm.reports.dashboards:counts` |
| GET | `/v1/dashboards/salesperson` | `kx.crm.reports.router:router.<locals>.salesperson` | `kx.crm.reports.dashboards:salesperson_board` |
| GET | `/v1/deals` | `kx.crm.deals.router:router.<locals>.list_deals` | `kx.crm._shared.pagination:keyset`<br>`kx.crm._shared.pagination:page_of` |
| PATCH | `/v1/deals/orders/{order_id}` | `kx.crm.deals.actions_router:router.<locals>.order_status` | `kx.crm._shared.access:check_edit`<br>`kx.crm.orders.service:deal_of_order`<br>`kx.crm.orders.service:update_order_status` |
| GET | `/v1/deals/{deal_id}` | `kx.crm.deals.router:router.<locals>.get_deal` | `kx.crm._shared.access:check_view`<br>`kx.crm.deals.service:contact_of`<br>`kx.crm.deals.service:get_deal`<br>`kx.crm.deals.service:missing_for`<br>`kx.crm.deals.service:requirements_of` |
| POST | `/v1/deals/{deal_id}/ai` | `kx.crm.deals.router:router.<locals>.toggle_ai` | `kx.crm.deals.service:contact_of`<br>`kx.crm.deals.service:get_deal`<br>`kx.crm.deals.service:missing_for`<br>`kx.crm.deals.service:requirements_of`<br>`kx.crm.negotiation.service:pause_ai`<br>`kx.crm.negotiation.service:resume_ai` |
| POST | `/v1/deals/{deal_id}/call` | `kx.crm.deals.actions_router:router.<locals>.call` | `kx.crm.deals.service:get_deal`<br>`kx.crm.handover.service:call_buyer` |
| POST | `/v1/deals/{deal_id}/handback` | `kx.crm.assignment.router:router.<locals>.hand_back` | `kx.crm.assignment.service:hand_back`<br>`kx.crm.deals.router:deal_detail` |
| POST | `/v1/deals/{deal_id}/messages` | `kx.crm.deals.actions_router:router.<locals>.staff_message` | `kx.crm.deals.service:get_deal`<br>`kx.crm.handover.service:reply` |
| POST | `/v1/deals/{deal_id}/offer` | `kx.crm.deals.router:router.<locals>.manager_offer` | `kx.crm.negotiation.service:manager_offer`<br>`kx.crm.negotiation.service:resume_ai` |
| POST | `/v1/deals/{deal_id}/opt-out` | `kx.crm.deals.actions_router:router.<locals>.opt_out` | `kx.crm._shared.access:check_edit`<br>`kx.crm.contacts.consent:record_opt_out`<br>`kx.crm.deals.router:deal_detail`<br>`kx.crm.deals.service:get_deal` |
| POST | `/v1/deals/{deal_id}/payments` | `kx.crm.deals.actions_router:router.<locals>.payment` | `kx.crm._shared.access:check_edit`<br>`kx.crm.deals.service:get_deal`<br>`kx.crm.orders.service:record_payment` |
| POST | `/v1/deals/{deal_id}/personal-contact-shared` | `kx.crm.deals.actions_router:router.<locals>.personal_contact_shared` | `kx.crm._shared.access:check_edit`<br>`kx.crm.deals.router:deal_detail`<br>`kx.crm.deals.service:get_deal`<br>`kx.crm.deals.service:personal_contact_shared` |
| GET | `/v1/deals/{deal_id}/quotes` | `kx.crm.deals.router:router.<locals>.deal_quotes` | `kx.crm._shared.access:check_view`<br>`kx.crm.deals.service:get_deal` |
| POST | `/v1/deals/{deal_id}/quotes` | `kx.crm.deals.router:router.<locals>.request_quote` | `kx.crm._shared.access:check_edit`<br>`kx.crm.deals.service:get_deal`<br>`kx.crm.quotes.service:request_quote` |
| POST | `/v1/deals/{deal_id}/quotes/manual` | `kx.crm.assignment.router:router.<locals>.manual_quote` | `kx.crm._shared.access:check_edit`<br>`kx.crm.deals.service:get_deal`<br>`kx.crm.quotes.service:manual_quote` |
| POST | `/v1/deals/{deal_id}/quotes/revise` | `kx.crm.deals.router:router.<locals>.revise_quote` | `kx.crm._shared.access:check_edit`<br>`kx.crm.deals.service:get_deal`<br>`kx.crm.quotes.service:revise_quote` |
| POST | `/v1/deals/{deal_id}/reassign` | `kx.crm.assignment.router:router.<locals>.reassign` | `kx.crm.assignment.service:reassign`<br>`kx.crm.deals.router:deal_detail` |
| POST | `/v1/deals/{deal_id}/reopen` | `kx.crm.deals.actions_router:router.<locals>.reopen` | `kx.crm.deals.router:deal_detail`<br>`kx.crm.deals.service:get_deal`<br>`kx.crm.deals.service:reopen` |
| POST | `/v1/deals/{deal_id}/stage` | `kx.crm.deals.router:router.<locals>.move` | `kx.crm._shared.access:check_edit`<br>`kx.crm.assignment.service:assign`<br>`kx.crm.deals.service:contact_of`<br>`kx.crm.deals.service:get_deal`<br>`kx.crm.deals.service:missing_for`<br>`kx.crm.deals.service:move_stage`<br>`kx.crm.deals.service:requirements_of` |
| POST | `/v1/deals/{deal_id}/takeover` | `kx.crm.assignment.router:router.<locals>.take_over` | `kx.crm.assignment.service:take_over`<br>`kx.crm.deals.router:deal_detail` |
| GET | `/v1/deals/{deal_id}/timeline` | `kx.crm.deals.router:router.<locals>.timeline` | `kx.crm._shared.access:check_view`<br>`kx.crm._shared.pagination:keyset`<br>`kx.crm._shared.pagination:page_of`<br>`kx.crm.deals.service:get_deal` |
| POST | `/v1/deals/{deal_id}/won` | `kx.crm.deals.actions_router:router.<locals>.won` | `kx.crm._shared.access:check_edit`<br>`kx.crm.deals.service:get_deal`<br>`kx.crm.orders.service:mark_won` |
| GET | `/v1/exports/{entity}` | `kx.crm.reports.router:router.<locals>.export` | `kx.crm.deals.router:visible_to` |
| PUT | `/v1/feature-flags/{key}` | `kx.crm.admin.router:router.<locals>.put_flag` | - |
| GET | `/v1/fx-rates` | `kx.crm.records.router:router.<locals>.fx_rates` | - |
| POST | `/v1/fx-rates` | `kx.crm.records.router:router.<locals>.fx_override` | `kx.crm._shared.journal:record` |
| GET | `/v1/inbox` | `kx.crm.screens.router:router.<locals>.my_inbox` | `kx.crm.inbox.service:inbox_for` |
| GET | `/v1/materials/freshness` | `kx.crm.admin.router:router.<locals>.freshness` | `kx.crm.pricing.proposals:material_freshness` |
| GET | `/v1/orders` | `kx.crm.records.router:router.<locals>.orders` | `kx.crm._shared.pagination:keyset`<br>`kx.crm._shared.pagination:page_of`<br>`kx.crm.deals.router:visible_to` |
| GET | `/v1/price-list/export` | `kx.crm.screens.router:router.<locals>.export_price_list` | `kx.crm.pricing.price_list_service:export_xlsx` |
| GET | `/v1/price-list/history` | `kx.crm.screens.router:router.<locals>.price_list_history` | - |
| POST | `/v1/price-list/import` | `kx.crm.screens.router:router.<locals>.import_price_list` | `kx.crm.pricing.price_list_service:import_xlsx` |
| GET | `/v1/price-proposals` | `kx.crm.admin.router:router.<locals>.proposals` | `kx.crm._shared.pagination:keyset`<br>`kx.crm._shared.pagination:page_of` |
| POST | `/v1/price-proposals/{rate_id}/approve` | `kx.crm.admin.router:router.<locals>.approve` | `kx.crm.pricing.proposals:approve_rate` |
| POST | `/v1/price-proposals/{rate_id}/reject` | `kx.crm.admin.router:router.<locals>.reject` | `kx.crm.pricing.proposals:reject_rate` |
| DELETE | `/v1/price-tiers/{tier_id}` | `kx.crm.screens.router:router.<locals>.remove_tier` | `kx.crm.pricing.price_list_service:delete_tier` |
| PATCH | `/v1/price-tiers/{tier_id}` | `kx.crm.screens.router:router.<locals>.edit_tier` | `kx.crm.pricing.price_list_service:update_tier` |
| GET | `/v1/products` | `kx.crm.records.router:router.<locals>.products` | - |
| GET | `/v1/products/{product_id}` | `kx.crm.records.router:router.<locals>.product` | - |
| PATCH | `/v1/products/{product_id}` | `kx.crm.records.router:router.<locals>.edit_product` | `kx.crm._shared.journal:record` |
| GET | `/v1/products/{product_id}/history` | `kx.crm.screens.router:router.<locals>.product_history` | - |
| GET | `/v1/products/{product_id}/price-tiers` | `kx.crm.screens.router:router.<locals>.tiers` | `kx.crm.pricing.price_list_service:tiers_for` |
| POST | `/v1/products/{product_id}/price-tiers` | `kx.crm.screens.router:router.<locals>.add_tier` | `kx.crm.pricing.price_list_service:add_tier` |
| POST | `/v1/public/sessions` | `kx.agents.website:build_router.<locals>.start` | `kx.agents.language:normalise_language` |
| POST | `/v1/public/sessions/{session_id}/identify` | `kx.agents.website:build_router.<locals>.identify` | - |
| POST | `/v1/public/sessions/{session_id}/messages` | `kx.agents.website:build_router.<locals>.message` | - |
| POST | `/v1/public/sessions/{session_id}/voice` | `kx.agents.website:build_router.<locals>.voice` | - |
| GET | `/v1/public/unsubscribe` | `kx.automation.optout.unsubscribe:build_router.<locals>.unsubscribe_page` | - |
| POST | `/v1/public/unsubscribe` | `kx.automation.optout.unsubscribe:build_router.<locals>.unsubscribe` | `kx.automation.optout:apply_stop` |
| GET | `/v1/quote-versions/{version_id}` | `kx.crm.quotes.router:router.<locals>.get_version` | `kx.crm._shared.access:check_view`<br>`kx.crm.deals.service:get_deal`<br>`kx.crm.quotes.issuing:lines_of` |
| GET | `/v1/quote-versions/{version_id}/costs` | `kx.crm.quotes.router:router.<locals>.get_costs` | `kx.crm.quotes.service:costs_of` |
| GET | `/v1/settings` | `kx.crm.admin.router:router.<locals>.list_settings` | - |
| PUT | `/v1/settings/{key}` | `kx.crm.admin.router:router.<locals>.put_setting` | - |
| GET | `/v1/stock` | `kx.crm.screens.router:router.<locals>.stock` | - |
| PUT | `/v1/stock` | `kx.crm.screens.router:router.<locals>.set_stock` | `kx.crm.orders.service:set_stock` |
| GET | `/v1/texts` | `kx.crm.texts.router:router.<locals>.list_texts` | `kx.crm.texts.service:as_dict`<br>`kx.crm.texts.service:list_texts` |
| POST | `/v1/texts/translate-missing` | `kx.crm.texts.router:router.<locals>.translate_missing` | `kx.crm.texts.service:request_missing_translations` |
| PUT | `/v1/texts/{key}` | `kx.crm.texts.router:router.<locals>.edit` | `kx.crm.texts.service:as_dict`<br>`kx.crm.texts.service:update_english` |
| GET | `/v1/texts/{key}/history` | `kx.crm.texts.router:router.<locals>.history` | `kx.crm.texts.service:history` |
| POST | `/v1/texts/{key}/reset` | `kx.crm.texts.router:router.<locals>.reset` | `kx.crm.texts.service:as_dict`<br>`kx.crm.texts.service:reset` |
| GET | `/v1/users` | `kx.crm.auth.users_router:router.<locals>.list_users` | - |
| POST | `/v1/users` | `kx.crm.auth.users_router:router.<locals>.create` | `kx.crm.auth.service:create_user` |
| PATCH | `/v1/users/{user_id}` | `kx.crm.auth.users_router:router.<locals>.patch` | `kx.crm._shared.journal:record` |
| PUT | `/v1/users/{user_id}/availability` | `kx.crm.admin.router:router.<locals>.availability` | `kx.crm.assignment.service:set_availability` |
| POST | `/v1/users/{user_id}/reset-mfa` | `kx.crm.auth.users_router:router.<locals>.reset_mfa` | `kx.crm.auth.service:reset_mfa` |
| POST | `/v1/users/{user_id}/unlock` | `kx.crm.auth.users_router:router.<locals>.unlock` | `kx.crm.auth.service:unlock` |
| POST | `/webhooks/cms` | `kx.automation.cms.service:build_router.<locals>.cms_published` | `kx.automation.outbox:emit`<br>`kx.automation.repo:first_receipt` |
| POST | `/webhooks/telnyx/gather/{attempt_id}` | `kx.automation.inbound.webhooks:_telnyx_routes.<locals>.telnyx_gather` | `kx.automation.channels.calls:gather_reply_texml`<br>`kx.automation.channels.calls:verify_signature` |
| POST | `/webhooks/telnyx/status/{attempt_id}` | `kx.automation.inbound.webhooks:_telnyx_routes.<locals>.telnyx_status` | `kx.automation.channels.calls:verify_signature` |
| GET | `/webhooks/whatsapp` | `kx.automation.inbound.webhooks:_whatsapp_routes.<locals>.whatsapp_verify` | `kx.automation.channels.whatsapp:verify_challenge` |
| POST | `/webhooks/whatsapp` | `kx.automation.inbound.webhooks:_whatsapp_routes.<locals>.whatsapp_events` | `kx.automation.channels.whatsapp:parse_webhook`<br>`kx.automation.channels.whatsapp:verify_signature`<br>`kx.automation.inbound:record_whatsapp` |

## Background jobs (task table)

| Task | Handler | Calls |
| --- | --- | --- |
| `agents.handle_event` | `arifaa_pack.container:_wire_events.<locals>.agents_event` | `kx.agents.handlers:AgentHandlers.last_resort` |
| `app.cms_product_draft` | `arifaa_pack.container:cms_product_draft` | - |
| `app.translate_text` | `arifaa_pack.texts_sync:translate_text` | `kx.crm.texts.service:load_edits`<br>`kx.crm.texts.service:save_translation` |
| `automation.alert_negotiation_paused` | `kx.automation.jobs:register_jobs.<locals>.pause_alert` | - |
| `automation.deliver_outbound` | `kx.automation.jobs:register_jobs.<locals>.deliver` | - |
| `automation.handle_event` | `kx.automation.jobs:register_jobs.<locals>.event` | - |
| `automation.ingest_inbound_email` | `kx.automation.jobs:register_jobs.<locals>.email` | `kx.automation.channels.email:parse_inbound`<br>`kx.automation.channels.feedback:parse_feedback`<br>`kx.automation.inbound:record_email`<br>`kx.automation.inbound:record_mail_event` |
| `automation.ingest_whatsapp_media` | `kx.automation.jobs:register_jobs.<locals>.media` | `kx.automation.inbound:attach_media`<br>`kx.automation.inbound:media_id_of` |
| `automation.optout_no_answer` | `kx.automation.jobs:register_jobs.<locals>.optout_check` | `kx.automation.handlers:stop_after_no_answer` |
| `automation.quote_delivery_check` | `kx.automation.jobs:register_jobs.<locals>.quote_check` | - |
| `automation.run_approval_ladder` | `kx.automation.jobs:register_jobs.<locals>.ladder` | - |
| `automation.send_followup` | `kx.automation.jobs:register_jobs.<locals>.followup` | - |
| `crm.handle_event` | `arifaa_pack.container:_wire_events.<locals>.crm_event` | `kx.crm.jobs:dispatch` |

## Beat entries

| Entry | Schedule | Body | Calls |
| --- | --- | --- | --- |
| `app.heartbeat` | 0:00:30 | `arifaa_pack.container:_periodic.<locals>.beat_heartbeat` | `arifaa_pack.telemetry:heartbeat` |
| `app.retention` | <crontab: 11 23 * * * (m/h/dM/MY/d)> | `arifaa_pack.container:_periodic.<locals>.retention_plumbing` | `kx.agents.retention:plumbing_rules`<br>`kx.agents.retention:run_retention`<br>`kx.automation.retention:plumbing_rules`<br>`kx.crm.retention.service:plumbing_rules` |
| `automation.mail_poll` | 0:00:30 | `kx.automation.jobs:periodic_jobs.<locals>.mail_poll` | `kx.automation.channels.stalwart:raw_key` |
| `automation.price_scrapes` | <crontab: 0 2 * * * (m/h/dM/MY/d)> | `kx.automation.jobs:periodic_jobs.<locals>.scrapes` | - |
| `automation.requeue_outbound` | <crontab: */5 * * * * (m/h/dM/MY/d)> | `kx.automation.jobs:periodic_jobs.<locals>.requeue` | `kx.automation.outbox:emit` |
| `crm.approval_sweep` | <crontab: */5 * * * * (m/h/dM/MY/d)> | `kx.crm.jobs:periodic_jobs.<locals>.sweep` | `kx.crm.approvals.service:sweep_overdue`<br>`kx.crm.change_requests.service:sweep_escalations` |
| `crm.fx_rates` | <crontab: 5 2 * * * (m/h/dM/MY/d)> | `kx.crm.jobs:periodic_jobs.<locals>.fx_rates` | `kx.crm.pricing.fx:update_rates`<br>`kx.crm.pricing.fx:wanted` |
| `crm.lost_proposals` | <crontab: 7 4 * * * (m/h/dM/MY/d)> | `kx.crm.jobs:periodic_jobs.<locals>.lost` | `kx.crm.deals.lost_proposals:propose_lost` |
| `crm.price_staleness` | <crontab: 23 5 * * * (m/h/dM/MY/d)> | `kx.crm.jobs:periodic_jobs.<locals>.staleness` | `kx.crm.pricing.proposals:emit_stale`<br>`kx.crm.quotes.service:expire_quotes` |
| `crm.retention` | <crontab: 41 23 * * * (m/h/dM/MY/d)> | `kx.crm.jobs:periodic_jobs.<locals>.retention_` | `kx.crm.retention.service:run_retention` |
| `crm.scheduled_reports` | <crontab: */15 * * * * (m/h/dM/MY/d)> | `kx.crm.jobs:periodic_jobs.<locals>.reports` | `kx.crm.reports.scheduled:send_due` |
| `jobs.dispatch_due` | 0:00:30 | `arifaa_pack.container:_periodic.<locals>.dispatch_due` | - |
| `relay.agents` | 0:00:05 | `arifaa_pack.container:_periodic.<locals>.relay_agents` | - |
| `relay.automation` | 0:00:05 | `kx.automation.jobs:periodic_jobs.<locals>.relay` | - |
| `relay.crm` | 0:00:05 | `kx.crm.jobs:periodic_jobs.<locals>.relay` | - |

<!-- END GENERATED -->
