# Reuse catalogue — what to import, never rewrite

One place per capability. If you need something in this list, import it. If it's missing, add it to the
owning package (and this file), not to your app. Enforced by import-linter (packages never import each other).

## kx.core — shared by every package and every client
| Capability | Import | Notes |
|---|---|---|
| Deploy settings, DSNs | `kx.core.settings`: `get_settings`, `CoreSettings`, `LogicalDb` | env prefix `KX_` |
| DB engines / transactions | `kx.core.db`: `Databases.transaction(LogicalDb.X)`, `.dependency()`, `make_base(schema)` | one engine per logical DB |
| Money (never float) | `kx.core.money`: `d`, `q`, `pct`, `display`, `to_currency`, `MONEY_PLACES`, `UNIT_PRICE_PLACES` | ROUND_HALF_UP |
| IDs, hashing | `kx.core.identity`: `new_id` (UUIDv7), `sha256_hex`, `hmac_sha256_hex`, `constant_time_equals` | |
| Contacts | `normalise_phone` (E.164), `normalise_email`, `normalise_address`, `recipient_hash` | grant check depends on these |
| Priced-content hash | `kx.core.identity.priced_content_sha256(body, pdf_sha)` | CRM issues, automation verifies — one function |
| Business hours / paused timers | `kx.core.timewindow.Window`: `contains`, `next_open`, `add_open_minutes` | 2 h approval timer pauses overnight |
| Events | `kx.core.events`: `Event`, `Outbox.add`, `relay_once`, `inbox_once`, `InMemoryBus`, event names, `NO_SUBSCRIBER` (events nothing subscribes to, and why) | outbox in same transaction |
| Typed event payloads | `kx.core.event_payloads`: `make_event`, `parse_payload`, 33 payload models (incl. `ChangeRequested`, `ChangeRequestClosed`, `DealAssigned`, `DealWon`) | |
| HTTP errors | `kx.core.errors`: `NotFound`, `Conflict`, `Forbidden`, `Unauthorized`, `RuleViolation`, `install_problem_handlers` | RFC 9457 |
| Auth | `kx.core.auth`: `current_principal`, `require_role`, `require_human_mfa`, `Role` | approvals need MFA |
| Logging | `kx.core.logging`: `get_logger`, `configure_logging`, `mask_pii` | PII masked automatically |
| Price safety | `kx.core.price_guard`: `check_prices`, `extract_prices` | contest winner C, fails closed |
| Cross-package contracts | `kx.core.contracts`: `CrmGateway`, `GrantConsumer`, `MessageSender`, `ClickToCall`, `StaffMailer`, DTOs | no cost/markup fields, ever |
| Staff login (D26) | `kx.core.passwords` (Argon2id, TOTP, `SeedBox`), `kx.core.auth.TokenIssuer` (RS256, JWKS) | in-house, no Cognito |
| Background jobs (D25) | `kx.core.jobs`: `JobStore`, `JobRunner`, `JobSpec` (PostgreSQL schedule table; Celery only in the app) | |
| Paid / free order per field (D41) | `kx.core.platform`: `Field`, `Plan`, `order`, `resolved`, `summary` | env `KX_PLATFORM_PLAN`, `KX_PLATFORM__<FIELD>`; `docs/PLATFORM_MODES.md` |
| Vendor fallback (D30) | `kx.core.fallback`: `CircuitBreaker`, `FallbackChain`, `AllVendorsFailed` | switch metric `VendorSwitch`; `alert` hook = app `OpsNotifier` |
| Retention, partitions, archive (D48) | `kx.core.retention`: `prune_batches`, `ensure_month_partitions`, `drop_old_partitions`, `archive_query`; `kx.core.contracts.BlobStore` | `docs/DATABASE.md` |
| Spend caps (D34) | `kx.core.spend`: `SpendMeter`, `SpendCaps` | integers in Redis, 80 % / 100 % alerts |
| Kill-switch ladder (D35) | `kx.core.killswitch`: `KillSwitchLadder`, `KillLevel`, `engaged_at` | |
| STOP rules (§8.1) | `kx.core.optout`: `match`, `match_voice`, `confirmation_text` | |
| Signed public tokens | `kx.core.signed`: `sign`, `verify` | website sessions |
| Metrics (D46) | `kx.core.metrics`: `emit_metric` (OpenTelemetry API; no-op until the app installs a provider) | exported to SigNoz |
| Phone parsing without exceptions | `kx.core.identity.try_normalise_phone` | webhooks must never 500 on a bad number |

## Planned in docs/V1_CHANGE_PLAN.md (do not build twice; add them here when done)

| Capability | Will live in |
|---|---|

## kx.crm — sales CRM (any manufacturer)
| Capability | Import |
|---|---|
| Price list (pure, v1) | `kx.crm.pricing.price_list`: `PriceTier`, `TierPrice`, `resolve_tier`, `min_price_for`, `convert_tier`, `list_line`, `discount_from_list_pct` |
| Negotiation ladder (pure, v1) | `kx.crm.pricing.engine`: `ladder_between`, `evaluate_ladder_counter` (§12.2 rules 1-5) over `kx.crm.pricing.types.LadderState` |
| Price-list I/O | `kx.crm.pricing.service`: `list_pricing_inputs`, `current_tiers`, `tiers_of_version`, `lead_time_text`, `fx_with_date`, `PricingNeeded`, `QuantityOutsideTiers` |
| Pricing maths (pure; cost-based = **v2**) | `kx.crm.pricing.engine`: `unit_cost_per_1000`, `freight_per_1000`, `resolve_band`, `price_line`, `quote_totals`, `concession_ladder`, `evaluate_counter`, `estimate_delivery` |
| Business rules (pure) | `kx.crm.rules` (stage machine, §15 LOST reasons, `lost_proposal_due`, staleness 3/7 days = v2, qualification fields) |
| Advance → PAYMENT, WON with the re-order date + order, order statuses | `kx.crm.orders.service`: `record_payment`, `mark_won`, `update_order_status` |
| LOST proposal after silence (task, never automatic) | `kx.crm.deals.lost_proposals.propose_lost` (daily job `lost_proposals`) |
| Audit + timeline + outbox in one call | `kx.crm._shared.journal.record()` |
| Handover after the final quote (AI off, reason `handover`) | `kx.crm.deals.service.hand_over` |
| Who may act on a deal (approve, edit, view; MD takeover read-only) | `kx.crm._shared.access`: `ensure_can_approve`, `ensure_can_edit`, `ensure_can_view` |
| Approvals (salesperson, manager any deal, MD on takeover; special price; 120-min escalation) | `kx.crm.approvals.service`: `open_approval`, `decide`, `sweep_overdue`, `initial_role` |
| Edit then approve / revise a quote (special price below the minimum) | `kx.crm.quotes.service.revise_quote` (`QuoteEdits`, `LineEdit` in `kx.crm._shared.quote_edits`) |
| The amounts a buyer may be told (§11.1; AI context and staff replies) | `kx.crm.quotes.allowed`: `allowed_prices`, `issued_offer`, `allowed_prices_for` |
| What one CRM slice offers the others (rule 5) | `kx/crm/<slice>/api.py`; lazy lookup `kx.crm._shared.surface.resolve` |
| Change requests (raise, start, revise link, resolve, decline, 120-min sweep) | `kx.crm.change_requests.service`; HTTP `kx.crm.change_requests.router`; contract `CrmGateway.raise_change_request`, `ChangeField`, `ChangeType` |
| Who is alerted | `kx.crm._shared.staff`: `approver_for` (owner, MD on takeover, backup when away, else manager), `covering_user`, `backup_of`, `manager_user`, `owner_of`, `staff_contact` |
| Deal assignment (round-robin, rules, away/backup, returning customer), reassign, takeover, hand-back, availability | `kx.crm.assignment.service` (pure: `choose`, `rule_pool`, `available`, `next_in_turn`); HTTP `kx.crm.assignment.router`, `PUT /users/{id}/availability` |
| Staff login, users | `kx.crm.auth.service`; HTTP `kx.crm.auth.router`, `kx.crm.auth.users_router` |
| §14 hand-over: staff reply, click-to-call | `kx.crm.handover.service` (`reply`, `call_buyer`) |
| §8.2 phone status, Requirement Summary | `kx.crm.contacts.phone`, `kx.crm.deals.summary` |
| §7 CMS product mirror | `kx.crm.catalogue.service.mirror_product` |
| D32 WhatsApp quote -> email | `kx.crm.quotes.issuing.resend_by_email` |
| §20/§21 records, dashboards, exports | `kx.crm.records.router`, `kx.crm.reports.router`, kill switch `kx.crm.operations.router` |
| Feature slices (ADR-0001) | one folder per feature under `kx/crm/`: `deals/`, `quotes/` (service, issuing, grants, pdf), `approvals/`, `pricing/` (pure maths + service, price-list service, fx, proposals), `negotiation/`, `orders/`, `change_requests/`, `assignment/`, `handover/`, `contacts/`, `catalogue/`, `auth/`, `company/`, `texts/`, `reports/`, `retention/`, `inbox/`, routers in `admin/`, `operations/`, `records/`, `screens/`; shared code in `_shared/` (journal, access, staff, settings, API plumbing). Map: `docs/arch/CRM_SLICES.md`. Public surface for apps: `kx.crm` |
| Gateways for other packages | `kx.crm.gateway`: `CrmGatewayImpl`, `GrantConsumerImpl`, `ApprovalStatusReaderImpl` |
| HTTP API | `kx.crm.build_router(databases, deps)` (assembles each slice's `router.py`): deals, assignment (reassign/takeover/hand-back), quotes + approvals, change requests, screens (inbox, price list, Excel, stock), admin (settings, availability) |
| Price list maintenance (CRUD with audit, Excel validate-then-import as a new version, export) | `kx.crm.pricing.price_list_service` (pure `parse_rows`, `overlap_errors`; `add_tier`, `update_tier`, `delete_tier`, `import_xlsx`, `export_xlsx`) |
| My inbox (§20.2) | `kx.crm.inbox.service.inbox_for` |
| Stock table (§15.5) | `kx.crm.orders.service.set_stock`, `move_stock` |
| Pricing needed (D8) | `kx.crm.quotes.service.pricing_needed` (the gateway calls it when the AI may not price) |
| Jobs + handlers | `kx.crm.jobs`: `CrmRuntime`, `build_blueprint`, `HANDLERS`, `dispatch` |
| Quote PDF (EN/AR, RTL; delivery time, Incoterm + place, payment terms, FX line, salesperson) and the §11.4 message (signed by the salesperson, "open to discuss" line in the message only) | `kx.crm.quotes.pdf`: `QuoteRenderer`, `QuoteDocument`, `SalesContact`, `quote_message`, `open_to_discuss`, `Letterhead`, `S3PdfUploader` |
| AI cover line for the quote message (D9) | `CrmGateway.set_cover_note` → `kx.crm.quotes.service.set_cover_note`; written in `kx.agents.graphs.quotation` (`COVER_NOTE`) |

## kx.automation — channels and jobs
| Capability | Import |
|---|---|
| The only way out | `kx.automation.outbound.service.OutboundService` (grant, kill switch, suppression, rate limit, 2/week cap, buyer hours) |
| Which number sends (buyer's thread → regional `countries` → default; email = company address) | `kx.automation.repo.channel_account(..., address=, country=)` |
| WhatsApp Cloud API | `kx.automation.whatsapp.channel.WhatsAppClient` (+ signature verify, webhook parsing, media download) |
| Email (D44): Stalwart JMAP send + mailbox poll; SES v2 API backup; inbound MIME | `kx.automation.email.stalwart` (`JmapClient`, `StalwartSender`, `MailboxPoller`), `kx.automation.email.channel.EmailSender` (SES), parsing helpers |
| Bounce / complaint reports (DSN, ARF) | `kx.automation.email.feedback.parse_feedback` |
| Staff alert calls (Telnyx TeXML, D43) | `kx.automation.calls.channel` (`TelnyxCaller`, TeXML builders, signature check), `kx.automation.alerts.service.VoiceAlerts`, `PauseAlerts` |
| §16 reminder ladder (approvals and change requests) | `kx.automation.alerts.service.ApprovalLadder` over `LadderTicket.for_approval` / `.for_change` (`kx.automation._shared.ladder`) |
| Staff notices (§17 "notified", alert hours) | `kx.automation.alerts.service.notify_staff` |
| §17 notifications matrix (event → recipients) | `kx.automation.handlers.EventHandlers.registry()`; table test `kx-core-automation/tests/test_notifications_matrix_db.py` |
| Webhooks router | `kx.automation.webhooks.router.build_router(WebhookDeps)` |
| Jobs | `kx.automation.jobs`: `register_jobs`, `periodic_jobs`, `JobScheduler`, `AutomationRuntime` (scraper only with `features.scraping`, D14) |
| Kill switch / rate limits | `kx.automation.outbound.killswitch.KillSwitch` (= `kx.core.killswitch.KillSwitchLadder`), `kx.automation.outbound.ratelimit.TokenBucketLimiter` |
| Email chain Stalwart -> SES (D44) | `kx.automation.email.chain.EmailChain` (order from `kx.core.platform`) |
| Click-to-call (§14) | `kx.automation.alerts.service.CallBridge` |
| CMS publish webhook (§7) | `kx.automation.cms.service.build_router` |
| STOP flow, unsubscribe link | `kx.automation.optout`, `kx.automation.optout.router` |
| Admin: regional numbers, templates | `kx.automation.admin.router.build_router` |
| Price scraping → proposals | `kx.automation.scraping.service.PriceScraper` |
| §16 follow-ups (day 2 / 5 / 10, re-order) | `kx.automation.followups.service.FollowUps` (scheduled by `EventHandlers`) |
| Feature slices (ADR-0001) | one folder per feature under `kx/automation/`: `outbound/`, `inbound/`, `optout/`, `webhooks/`, `whatsapp/`, `email/`, `calls/`, `alerts/`, `followups/`, `scraping/`, `cms/`, `admin/`; shared code in `_shared/` (outbox, ports, settings, media, channel errors, the send helper); map `docs/arch/AUTOMATION_AGENTS_SLICES.md` |
| What an app imports | `from kx.automation import ...`: the 33 names of `kx/automation/__init__.py` (`__all__`); import-linter enforces it |

## kx.agents — AI agent framework
| Capability | Import |
|---|---|
| 12 roles, prompts (`AgentSpec.enabled`; repeat sales off in v1) | `kx.agents.roles.registry`: `AgentRegistry` (`disabled()`), `AgentSpec`, `PromptStore`, `DefaultPromptStore`, `ModelTier` |
| Models + fallback | `kx.agents.llm.service`: `ModelRouter`, `AnthropicApiProvider` (Claude API direct, D27; no sampling parameters, effort per tier, D51); safe reply + breaker in `AgentService` |
| Routing (no LLM; agreement wording and post-quote change topics → deal graph; payment topics → deal graph in every stage; handed-over deals silent or the after-hours acknowledgement) | `kx.agents.router.service` (`PAYMENT_TOPIC`) |
| Reply guard | `kx.agents.guard` (prices, cost wording, injection, length) |
| Graphs | `kx.agents.graphs.conversation` / `quotation` (interrupt/resume) / `deal` |
| Memory, checkpointer | `kx.agents.memory`: `open_checkpointer`, turn store |
| Hybrid KB search | `kx.agents.knowledge`: `KnowledgeBase`, `KnowledgeIndexer`, `Embedder`, `ContentSource` |
| Voice (D52) | `kx.agents.voice`: `VoiceRouter`, `voice_routes`, `make_livekit_entrypoint`; worker entry `arifaa_pack.voice_worker` |
| AI audit trail | `kx.agents.tracing.service.Tracer` (Phoenix or Langfuse over OTLP, PII-masked, D46) |
| Website conversation API (§6) | `kx.agents.website.router.build_router(WebsiteDeps)` |
| Run cost table | `kx.agents.runtime.runs.PRICES`, `call_cost` |
| Entry point | `kx.agents.runtime.service.AgentService`, `kx.agents.handlers.AgentHandlers` |
| Test kit | `kx.agents.testing` (fake model, `FakeCrmGateway`, fake embedder), `kx.agents.evals` (§23 scenarios s01-s10 + trick prompts t01-t07) |
| Change requests from the AI | tool `kx.agents.tools.service.raise_change_request`; deal graph `intent=change` + `PAYMENT_TOPIC`; phrase `Phrase.CHANGE_REQUEST`, `team_member` |
| Feature slices (ADR-0001) | one folder per feature under `kx/agents/`: `runtime/` (AgentService, runs, context, middleware), `router/`, `graphs/`, `tools/`, `guard/`, `llm/`, `roles/` (registry, languages), `knowledge/`, `memory/`, `voice/`, `website/`, `tracing/`, `evals/`; map `docs/arch/AUTOMATION_AGENTS_SLICES.md` |
| What an app imports | `from kx.agents import ...`: the 25 names of `kx/agents/__init__.py` (`__all__`); import-linter enforces it |

## Client app (copy per client) — `apps/arifaa-pack`
`company.yaml` (identity), `config.py` (`KX_APP_*`), `adapters.py` (address book, `HttpEmbedder` = BGE-M3 / Qwen3 on TEI (D47), Payload CMS
source, `PlatformVoicePlugins`, `R2BlobStore`, `OpsNotifier`), `container.py` (composition root + `EventBus`), `telemetry.py` (OpenTelemetry → SigNoz),
`voice_worker.py` (LiveKit entry + prewarm), `api.py`, `cli.py`
(`kx api | worker | beat | voice | migrate | reindex | platform show | create-user`), `migrations/` (Alembic, one script dir per DB).
Free speech server: `services/speech/` (own image).

**New client (e.g. Arifaa Auto):** copy `apps/arifaa-pack` → `apps/arifaa-auto`, edit `company.yaml`, prompts
and `crm.settings` seed; a new app stack in `infra/server/stacks/` with its own company code and database prefix
(`docs/MULTI_PROJECT.md`). No platform code changes.
