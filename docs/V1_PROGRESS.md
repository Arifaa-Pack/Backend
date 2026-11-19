# v1 flow progress (WP1–WP12 of docs/V1_CHANGE_PLAN.md)

Baseline before the work (5 Nov, commit 1d61062, Linux container with pgvector Postgres 18 + redis-server):
**309 passed**, `make lint` clean.

Environment note: on Windows the DB tests cannot run natively (psycopg's async pool needs a selector event
loop, and WeasyPrint needs GTK/Pango). The gate was run in a `python:3.14-slim-trixie` container with the
same system libraries as the production image, against a `pgvector/pgvector:pg18` container, like CI.

## WP1. Database migration 0002

**Plan.** Files: `migrations/{crm,automation,agents}/versions/0002_v1_flow.py`, a raw-SQL helper in
`arifaa_pack/migrations/__init__.py`, the ORM models of each package, the root `tests/conftest.py` (test DBs
are now built by Alembic: 0001 DDL then 0002), `infra/scripts/db_bootstrap.sh` (btree_gist).
Tests: the three ORM-vs-`information_schema` tests (extended to the new tables/columns), a new
`tests/db/test_migrations_v1.py` (upgrade from the 0001 state with data, run twice, constraints and seeds).

**Done.** Revisions `crm_0002`, `automation_0002`, `agents_0002` (all items of the WP1 list). Additions the
later WPs need, each tied to a rule: `quote_line_tiers` (price-list snapshot per quote line, §24 "an open quote
keeps its prices"), approval kinds `revision`/`counter`, `approvals.escalated_at` (§16 120-min escalation),
grant policy `sales` (D1), task kinds `pricing_needed` (§10, D8) and `lost_proposal` (§16, D15), LOST reasons
`budget`/`duplicate`/`other` (§15.4), call purpose `change_request_alert` (§13.5), `channel_accounts.is_default`
(WP6 fallback region), `change_requests.ai_notes` (§13.2 card), settings `quote.vat_pct_export` (§10). The
freeze trigger now also locks the new buyer-visible quote fields (§11.3.3).
Test DBs are now built by Alembic (0001 then 0002), so every DB test runs on the migrated schema.
Tests added: `tests/db/test_migrations_v1.py` (8 tests: upgrade from a 0001 DB with data, twice; SQL re-run;
seeds kept/added; recipient hashes = Python; approval CHECKs; tier overlap/min ≤ list; freeze; automation
UNIQUE/default; agents columns); the 3 ORM tests now cover the new tables and columns.

## WP2. Pricing: price-list mode

**Plan.** New `kx/crm/pricing/price_list.py` (pure); `ladder_between` + `evaluate_ladder_counter` and
`LadderState` in `pricing/engine.py` / `types.py` (cost functions untouched); price-list branch in
`services/pricing.py`, `services/quotes.py` (`request_quote`, `create_final_version`) and
`services/negotiation.py`; typed settings for the new keys; contract verdict kinds `REQUOTE`/`CHANGE_REQUEST`.
Tests first: `tests/spec/test_spec_price_list.py` (§11-12, §23 numbers, 2 hypothesis properties, 3 benches),
`packages/kx-core-crm/tests/test_price_list_db.py` (DB).

**Done.** Quotes in `pricing.mode='price_list'` are built from the product's current tier version: list
price, FX at the day's rate (the converted line total is the AED line total at the printed rate, so
12,500.00 AED = USD 3,403.68), VAT by country (5 % / export 0 %), Incoterm from `quote.default_incoterm`,
delivery time text from the product default (D10), payment terms from a setting, signed by the owner (D11).
No staleness block, no delivery estimate. Each line keeps a price-list snapshot (`quote_line_tiers`) so an open
quote keeps its prices (§24). Negotiation in price-list mode builds `LadderState` from that snapshot (never
from cost) and applies §12.2 rules 1-5; a tier change drafts a revised quote for approval; quantity outside the
tiers or a hand-priced line returns `CHANGE_REQUEST`. The 4 Nov cost-based DB tests now run with
`pricing.mode='cost'` (fixture `cost_mode`): the v2 path keeps its coverage.
New revision `crm_0003` seeds `quote.payment_terms_text`, `quote.currency_by_country`,
`negotiation.free_samples_allowed`.
Tests added: 36 spec tests (`test_spec_price_list.py`) and 11 DB tests (`test_price_list_db.py`).
Totals: 362 passed.

## WP3. Approvals: the salesperson approves

**Plan.** New `services/access.py` (who may approve / edit / view); `services/approvals.py` (approver,
escalation, special price, sweep to the manager), `services/staff.py` (`approver_for`, `manager_user`),
`services/issuing.py` + `grants.py` (grant policy = who approved), `services/quotes.py` (`revise_quote`, final
quote to the salesperson), API routes (`/approvals/{id}/decision` open to sales, `/deals/{id}/quotes/revise`,
takeover read-only on write routes), `QuoteSubmitted` payload. Tests: `test_approvals_db.py` (DB + HTTP).

**Done.** Every quote kind (initial, revision, final) is approved by the deal's salesperson; the manager may
approve any deal; the MD only a deal it has taken over (approvals then go to the MD); the owner is read-only
during a takeover (403 on quote, revise, stage and decision routes; view stays). `QuoteSubmitted`: approver =
salesperson, escalate_to = manager, due = 120 alert-hour minutes. A revised price below the tier minimum opens
a manager-only `special_price` approval first; once approved, the salesperson's approval opens on the same
version (scenario 3). The MD value limit only applies when `approval.md_value_limit_aed` is set. The sweep
escalates overdue approvals to the manager. Each buyer-facing version still gets a single-use grant
(policy = the approver's role). The 4 Nov tests that asserted MD final approval were updated to the 5 Nov
rules (D1).
Tests added: 11 (`test_approvals_db.py`: own deal vs another salesperson, manager any deal, MD only after
takeover, owner read-only over HTTP, final quote grant single-use, edit within range / above list refused,
scenario 3 special price, special price declined, scenario 6 timer 14:00 → 16:00 and 21:30 → 09:30, MD value
limit). Totals: 373 passed.

## WP4. Change requests

**Plan.** kx.core: events `CHANGE_REQUESTED` / `CHANGE_REQUEST_CLOSED`, payloads, `ChangeField`/`ChangeType`,
`CrmGateway.raise_change_request`. kx.crm: `services/change_requests.py`, `api/change_requests.py`, gateway,
status reader, sweep. kx.agents: tool `raise_change_request`, deal graph `intent=change`, phrases. kx.automation:
the ladder generalised (`LadderTicket`) and moved to the §16 schedule; handler for `CHANGE_REQUESTED`. Tests
first: `kx-agent-framework/tests/test_change_requests.py`, `kx-core-crm/tests/test_change_requests_db.py`,
automation ladder tests.

**Done.** The AI raises a card for any non-price request (product, spec, printing, quantity outside the tiers,
delivery, samples, payment, other) and answers with the fixed §13.1 sentence naming the salesperson and the
`change_request.sla_working_hours` setting; payment wording always becomes a payment card (deterministic check
before the model). The CRM fills the "current value" column from the quote, sets the SLA in alert hours and
alerts the salesperson. Lifecycle open → in progress → resolved (when the revised version is approved and sent)
or declined (reason required). The reminder ladder now follows §16 for approvals and change requests alike:
WhatsApp at once, WhatsApp at 30 min, alert call at 60 min, manager (WhatsApp + call) at 120 min, in alert hours,
stopping as soon as the item is decided. The 4 Nov ladder tests (3 calls / 20 min, press 2 → MD) were rewritten
to the §16 schedule (D4).
Tests added: 6 agents (scenario 4 delivery with no promise, payment ×3, quantity outside tiers, tier change,
no salesperson, tool), 6 CRM DB (card + alerts, SLA over night, scenario 4 lifecycle over HTTP, decline,
resolve rules, 120-minute sweep), 3 automation (§16 schedule, press 2, change-request ladder).
Totals: 389 passed.

## WP5. Assignment, away/backup, reassign, takeover

**Plan.** `services/assignment.py` (pure selection + DB), `services/staff.py` (backup covers), `services/access.py`
(the covering backup may act), approvals/change-request rerouting, `api/assignment.py` (reassign, takeover,
hand-back), `PUT /users/{id}/availability`, event `DealAssigned` + automation notice handler, revision `crm_0004`
(`change_requests.alert_ref`). Tests: `test_assignment_rules.py` (unit + hypothesis), `test_assignment_db.py`.

**Done.** A deal is assigned when it becomes QUALIFIED (AI or staff): returning customer → last active owner;
otherwise round-robin over active salespersons, narrowed by `assignment.rules` in order (language, country,
category; an unmatched rule is skipped); away salespersons are replaced by their backup, or the manager.
Round-robin is serialised by an advisory lock and `crm.assignment_seq`. Reassign (manager/MD), takeover and
hand-back (MD) move pending approvals (cancel + re-open) and open change-request cards (new alert ref) to the
person now responsible; the takeover pauses the AI (`md_takeover`); `DEAL_ASSIGNED` notifies the new person,
the previous one and the manager (automation, alert hours). While a salesperson is away, the backup covers
their deals (approvals move, the backup may approve and edit).
Tests added: 8 unit (rule order, several matches, unmatched rule, scenario 7, away/backup, all away → manager,
property: never picks someone away) and 8 DB (assign on qualify, 10 concurrent assignments = 5/5 with unique
turns, returning customer, language rule + away backup, approvals follow the backup, scenario 6 reassign over
HTTP, scenario 8 takeover/hand-back over HTTP, availability route). Totals: 405 passed.

## WP6. Sending the quote

**Plan.** automation `repo.channel_account` (regional number) + `outbound`/handler pass-through; CRM `pdf.py`
(document fields, labels, message), `quote.html.j2`, `issuing.py` (signer, country, language),
`quotes.set_cover_note`, revision `crm_0005` (`cover_note`, `change_summary`); contracts
(`OutboundRequest.buyer_country`, `CrmGateway.set_cover_note`); agents quotation graph writes the cover line.
Tests: `test_auto_regional_db.py`, `test_quote_document.py`, one DB test in `test_approvals_db.py`.

**Done.** WhatsApp goes from the number the buyer already writes to, else the regional number whose
`countries` list the delivery country, else the default number; email always from the company address. The
message is signed "— Sara, Arifa Pack" (the deal's salesperson, D11), carries the AI-written "open to discuss"
line (or an approved variant picked per quote when the AI wrote none), says what changed on a revised quote,
and has no discuss line on the final quote. The PDF prints delivery time, Incoterm and place, payment terms,
the FX rate and its date, and the salesperson's contact; Arabic is right-to-left; the cover line is never in
the PDF. The guard passes on the generated message against the deal's allowed prices.
Tests added: 3 automation (country routing + default, existing thread wins, email/alerts identity), 6 CRM unit
(PDF fields, no cover line in the PDF, Arabic RTL, PDF bytes, signed message + guard, varied fallback, final /
revision wording), 1 CRM DB (sent message: AI cover line, signature, region, guard). Totals: 415 passed.

## WP7. Final quote and handover

**Plan.** CRM `deals.hand_over` called on final approval, `QuoteApproved.salesperson/manager` (final only),
`DealContext.ai_paused_reason/team_available`; automation final-quote alert + manager notice; agents orchestrator
`acknowledge` route, `Phrase.HANDOVER_ACK`, service delivers it. Tests in agents (`test_db.py`, `test_routing.py`),
automation (`test_auto_db.py`), CRM (`test_approvals_db.py`).

**Done.** Approving the final quote sends it (QUOTE_APPROVED kind final, as before), alerts the salesperson
(`final_quote_alert`) and notifies the manager (`staff_notice`) in alert hours, and sets `ai_enabled=false`,
`ai_paused_reason='handover'`. Buyer messages on a handed-over deal never reach a model: they go to the
salesperson's timeline/inbox; outside staff hours the buyer gets only "Thanks — Sara will reply in the morning."
Tests added: 2 agents DB (no AI reply in hours; acknowledgement only after hours), 1 routing unit, 1 automation
(quote sent, salesperson alert, manager notice), 1 CRM DB (final approval hands over; only the final quote alerts).
Totals: 420 passed.

## WP8. Payment, WON, LOST, order

**Plan.** `services/orders.py` (`record_payment` → PAYMENT, new `mark_won`), `rules.py` (§15 LOST reasons,
`lost_proposal_due`), new `services/lost_proposals.py` + daily job, API (`/payments` for the salesperson with
MFA, `/won`), revision `crm_0006` (`payments.proof_s3_key`). Tests: `test_won_lost_db.py`.

**Done.** Recording the advance needs amount, currency and method (reference, date and proof file are stored)
and moves the deal to PAYMENT; WON is a separate salesperson step that needs the advance and the expected
re-order date and creates the order with that date. LOST reasons are the §15 list. A daily job raises one
`lost_proposal` task for the salesperson when the buyer has not replied 14 days (setting) after the last
follow-up; the deal never moves by itself. The 4 Nov test that went payment → WON in one step now does the two
steps.
Tests added: 7 (scenario 1 advance → PAYMENT → WON with the re-order date → order statuses; WON needs the advance;
§15 reasons; LOST with a new reason; scenario 9 date; silent buyer → one task, never LOST; a reply stops it).
Totals: 427 passed.

## WP9. Switch v2 features off (flags; code kept)

**Done.** `features.scraping=false`: `build_jobs(scraping=...)` keeps the scraper task but schedules it only when
the flag is on; the container reads the flag from `crm.settings` at boot. Staleness: no warning/block in
price-list mode (quote expiry still runs daily). `features.delivery_calc=false`: `delivery_estimate` returns the
quote's (or product's) default delivery text, no calculated date and no estimate row. `features.news_digest=false`:
there is no digest job in v1 (tables only). Repeat sales: `AgentSpec.enabled=False`, the orchestrator sends
won-customer messages to the salesperson. The 4 Nov blueprint test now checks both flag positions.
Tests added: smoke test asserts the scraper is not scheduled and repeat sales is off after boot; routing unit;
2 CRM DB (delivery answer without calculation, staleness job off). Totals: 430 passed.

## WP10. AI behaviour updates

**Done.** Base rules and role prompts follow §12.2 rule 6, §13, §18 and §24: price only; never payment, credit or
bank details; never a longer delivery as a concession, a delivery date, free samples or a discount on custom
items; never say or hint at a minimum, steps or a range; trick prompts get a normal sales answer asking for target
and volume; "are you a bot" → the company's AI assistant, a salesperson reviews every quotation; unsure or upset
buyer → the salesperson. Fixed phrases: "Let me check this with our sales team — you'll hear back within 60
minutes during working hours." (§12.2 rule 4, EN/AR), "within 4 working hours" (§13.1), the after-hours
handover acknowledgement (§14.6). On agreement the AI confirms product, quantity, unit price, total, delivery time
and Incoterm (§14.2); the agreed total is computed in Decimal and is the only new amount allowed. The router now
treats "OK, confirmed / go ahead / PO" as agreement (§14.1), sends post-quote change requests and every payment
topic to the deal graph (deterministic change request). The eval YAML scenarios were replaced by §23 scenarios
1–10 (s01–s10) and 7 trick/edge prompts (t01–t07); the harness now checks change requests and the after-hours
acknowledgement. Totals: 437 passed.

## WP11. CRM API for the screens (§20)

**Done.** New `api/screens.py` (thin) over `services/inbox.py`, `services/price_lists.py` and `orders.set_stock`:
`GET /inbox` (approvals, special prices, change requests, below-minimum / pricing-needed / LOST-proposal tasks,
buyer messages on handed-over deals; due time + overdue; a salesperson sees their own and what they cover, the
manager/MD everything); price-list tier CRUD (manager/admin with MFA, every change audited old/new, a tier used
by quotes cannot be deleted); Excel import (raw .xlsx body, all rows validated first: min ≤ list, no overlaps,
codes exist, AED; any error saves nothing and returns row errors; a valid file is the next price-list version)
and export; stock table (`GET/PUT /stock`, manager sets, salespersons read). Settings are now readable/writable
by the manager too (§20.11); the new keys validate. Change-request card actions, reassign/takeover/hand-back were
built in WP4/WP5. The gateway now turns "the AI may not price this" into a Pricing-needed task (D8, scenario 5)
and a quantity outside the tiers into a change request (D12); the two WP2 tests that expected an error now check
both layers.
Tests added: 2 unit (Excel row validation), 6 HTTP/DB (inbox per person, handed-over buyer messages, tier CRUD +
MFA + audit, Excel export/import, stock, settings). Totals: 445 passed.

## WP12. Notifications matrix (§17)

| §17 row | Event | Handler / where | Recipients and channel |
|---|---|---|---|
| New qualified deal assigned | `deal.assigned.v1` | automation `_deal_assigned` | salesperson WhatsApp notice (+ CRM inbox); buyer Requirement Summary **not built** |
| Quote waiting for approval | `quote.submitted.v1` | automation ladder | salesperson 0/30 min WhatsApp, 60 min call; manager at 120 min |
| Quote sent | `quote.approved.v1` | automation `_quote_approved` | buyer quote PDF + message (salesperson: CRM timeline) |
| Counter-offer handled by the AI | (CRM `negotiation.round` timeline) | CRM | salesperson CRM timeline; buyer AI reply |
| Buyer pushes below the minimum | `negotiation.paused.v1` | automation `PauseAlerts` | salesperson WhatsApp + call; manager WhatsApp; buyer 60-minute sentence (AI) |
| Change request | `change_request.raised.v1` | automation ladder | salesperson at once + reminders; manager at 120 min; buyer 4-hour sentence (AI) |
| Final quote approved and sent | `quote.approved.v1` (final) | automation | buyer final quote; salesperson alert; manager notice |
| Payment recorded / WON | `deal.won.v1` | automation `_deal_won` | manager + MD WhatsApp notice; buyer thank-you template |
| Order status changed | `order.status_changed.v1` | automation `_order_status_changed` | buyer WhatsApp only when `notifications.order_status_to_buyer` (default off) |
| MD takes over / reassigned | `deal.assigned.v1` | automation `_deal_assigned` | salesperson(s) + manager (not the MD on its own takeover) |
| Buyer opts out | `message.received.v1` (`opted_out`) | CRM (AI off, `opt_out`, timeline) + automation | buyer one confirmation template (passes the suppression); salesperson CRM |

The MD "Dashboard" column is the CRM data (no message). Tests added: 11 parametrized matrix rows, the pause
alert split, the opt-out confirmation (automation), the opt-out bypass on the real OutboundService, and 2 CRM DB
tests (WON event recipients + order-update flag; opt-out pauses the deal). The 4 Nov assertion "manager + MD
alerted on a pause" now reads "salesperson alerted, manager notified". New revision `crm_0007`
(`notifications.order_status_to_buyer`). Totals: 461 passed.


## Extra testing (after WP12)

* **§23 scenarios 1–10, end to end** (`tests/scenarios/test_scenarios_v1.py`): real CRM and automation on Postgres,
  the real router and graphs with the scripted fake model, the CRM outbox relayed to the real automation handlers and
  §16 ladder, and a sender that consumes each quote's single-use grant like `OutboundService`. Each test checks the
  stage history, who was alerted or notified, which versions were approved and sent, and runs `check_prices` on
  every AI reply against the deal's `allowed_prices`.
* **§17 matrix**, table-driven: `packages/kx-core-automation/tests/test_notifications_matrix_db.py` (11 rows + pause
  split + opt-out confirmation).
* **Safety** (`tests/scenarios/test_safety_v1.py`): the minimum, cost and un-offered steps never in the AI context
  and blocked if said (plus a hypothesis property); payment topics routed to a person in every open stage (24
  cases) and turned into a payment change request; no approving another salesperson's deal; owner read-only during
  an MD takeover; each grant single-use (and automation's send refused once used).
* **Whole-app smoke test** (`tests/integration`): migrate twice, boot the API, fan out an event; now also asserts
  the scraper is not scheduled and repeat sales is off.

Bugs these tests found and fixed: `QuoteSubmitted.remind_at` was never filled (approvals skipped the 30/60-minute
steps); follow-ups ignored the buyer country's weekend (`crm_0008`); no way to price a custom item by hand
(`quotes.manual_quote`); "OK, confirmed" and post-quote change requests did not reach the deal graph (WP10).

## Benchmarks (`make bench`, Linux container, 6 Nov)

| Benchmark | Mean | Budget |
|---|---|---|
| `ladder_between` (lru-cached) | 0.10 µs | µs |
| `resolve_tier` | 0.72 µs | µs |
| `evaluate_ladder_counter` (with tier check) | 2.28 µs | µs |
| `evaluate_counter` (v2) | 1.42 µs | µs |
| `price_line` (v2) | 2.55 µs | µs |
| price guard, 1000 messages | 5.95 ms | < 10 ms |

## Totals

502 passed, 0 skipped (309 before the work): CRM 110, automation 128, agents 64, root spec 131, scenarios + safety
41, unit 14, DB 12, integration 2. `make lint`: ruff, ruff format, mypy --strict (133 files), import-linter 3/3.

---

# 11 Nov plan (WP13–WP29 of docs/V1_CHANGE_PLAN.md)

Sources: the SRS (`docs/Arifaa Pack — Detailed Business Flow v1 (SRS basis) (2).md`) and the platform docs
(`docs/Arifaa Platform — Platforms, Pricing & Fallbacks/`). Gate: the same Linux container + pgvector Postgres 18 as
before (Windows cannot run psycopg's async pool or WeasyPrint natively).

## Baseline (12 Nov, before the work)

502 tests, but **13 failed by date**: the grant was issued on the app clock (fixed test dates of 9 Nov) and consumed
against the database's real `now()`, so every quote send in the DB tests expired on 11 Nov 06:00 UTC. Fixed in the
code, not the tests: `grants.issue_grant` now stamps `issued_at` / `expires_at` with the database clock, the same
clock `crm.consume_send_grant` checks (a security boundary must not depend on an app server's clock). Scenario 9 now
advances from the real send time. Two pure Excel tests that inherited a DB fixture moved to
`test_price_list_rows.py` (they now run on Windows). Result: **502 passed, 0 failed**.

## WP13. Documents

CLAUDE.md, CONTRACTS.md, README.md, `docs/V1_CHANGE_PLAN.md` (11 Nov plan, D17–D40), `docs/OPEN_QUESTIONS.md`
(#40–#60), HANDOFF_CONTEXT.md, DEPLOYMENT.md, CONTESTS.md (§5 platform contest), `db/README.md`, the root
`V1_CHANGE_PLAN.md` (now a pointer). Code comments say "the SRS" instead of the deleted `docs/BUSINESS_FLOW_V1.md`.

## WP16. Payment, WON, LOST reopen, visibility (§15, §7A.1, §3, §14; D20, D24)

* §15.1 / D20: confirming the advance is required, every detail optional (an amount needs its currency);
  timeline "Advance confirmed …". §15.2: WON mandatory, re-order date optional.
* §7A.1 / D24: `POST /v1/deals/{id}/reopen` (manager or MD, MFA, reason): LOST -> the stage it was lost from; the AI
  stays paused. §14: `POST /v1/deals/{id}/personal-contact-shared`.
* §3: a salesperson's `GET /v1/deals` shows only their own deals and those they cover; timeline and quote list check
  view access (before this, any salesperson could read any deal's timeline). §20.3 board filters: salesperson,
  country, product id / code, value in AED, opted out.
* Migration `crm_0009` (also used by WP14/15/17/18): `deals.opted_out_at`, `ai_summary(_at)`,
  `personal_contact_shared_at/_by`, LOST reason `opted_out`, task kinds `optout_review` / `phone_unreached` /
  `call_buyer`, `contacts.phone_status(_at)` + `whatsapp_profile_name` (back-filled `verified` for WhatsApp contacts),
  `products.spec` / `cms_published` / `cms_synced_at`, settings `optout.*`.
* After-the-deal routes (payment, WON, order status) moved to `kx.crm.api.deal_actions` (same paths).
* Tests: 2 DB (details-free payment + WON without a date; reopen) and 3 HTTP (§3 lists, reopen needs manager + MFA,
  personal contact).

## WP20. Background jobs on Celery (D25)

* `kx.core.jobs`: `JobStore` over `automation.scheduled_jobs` (enqueue with de-duplication key, replace, pull forward,
  cancel by key or prefix, claim with per-lock ordering under an advisory lock, finish kicks the next job on the same
  lock, retry with exponential back-off, recover rows left `running` by a dead worker, queue lag) and `JobRunner`
  (the task type's `JobSpec` owns its retry policy). Migration `automation_0003`.
* `kx.automation.jobs`: same task bodies; `JobScheduler` implements `TaskScheduler` (+ `cancel_followups`,
  `schedule_optout_check`, `cancel_optout_check`); `periodic_jobs()`. `kx.crm.jobs.periodic_jobs()`. Procrastinate is
  gone from both packages and the lock.
* App: `arifaa_pack.jobqueue` (the one Celery app: acks_late, reject on worker lost, prefetch 1, visibility timeout
  2 h; `CeleryKicker` publishes in a thread), `arifaa_pack.schedule` (Beat timetable: sweep 30 s, relays 5 s,
  heartbeat 30 s, the 4 Nov crons), `arifaa_pack.celery_app` (`kx.run_job`, `kx.periodic`; one event loop + container
  per worker process), `kx worker` / `kx beat`. The container's `EventBus` writes one job per subscribed package.
* The scraper's daily job returns at once while `features.scraping` is off (D14).
* Tests: 9 DB (store and scheduler); the smoke test boots the app, fans an event out to `scheduled_jobs`, runs it once
  and shows a redelivery is a no-op; Beat's timetable equals the worker's periodic map.

## WP14. STOP flow (§8.1, D18)

* `kx.core.optout`: the rules (whole-message words, clear phrases, channel phrases, stop word in a sentence -> the AI;
  voice: "stop" = wait), the confirmation texts (EN from the SRS; AR/HI/TA/ZH/SI drafts for review, OPEN #41).
* Automation: suppression by scope (`all`/`whatsapp`/`email`/`phone`) and by person (`contact_suppressions`); the
  permanent `opt_out_log`; `apply_stop` (suppress, cancel the queue, log, announce `consent.opted_out.v1`) inside the
  webhook's transaction; the one confirmation as free text in the buyer's language inside the 24-hour window;
  follow-ups cancelled; the 24-hour "no answer" timer; one-click email unsubscribe (RFC 8058 headers, page with an
  optional reason; a GET changes nothing). `OutboundService` now stores `contact_ref` and `recipient_hash` on every
  row (the columns existed since 0002 but were never filled). Migration `automation_0004`.
* CRM: `kx.crm.services.consent` (consent rows, Opted out badge, salesperson + manager notice via the new generic
  `staff.notice_requested.v1`, LOST proposed with reason `opted_out` or the buyer's own, channel switch for
  "use email instead"); a stop automation knew by one address is handed back with every address of the person.
* Agents: no AI reply to a message that already stopped; a stop word in a sentence is read by the fast model
  (opt-out / not / unclear; a failure counts as unclear); unclear -> the SRS question if the buyer was active in the
  last 10 minutes, else a review task; voice "stop" = listen, "stop calling me" = calls stopped.
* Tests: 29 spec (every example sentence in §8.1, confirmations, a hypothesis property), 5 automation DB, 5 agents DB,
  2 CRM DB, and the 5 earlier opt-out tests rewritten to the new rules.

Totals after WP14: **554 passed** (root 229, CRM 117, automation 139, agents 69); ruff, format, mypy --strict
(147 files) and import-linter clean.

## WP15. Phone confirmation and Requirement Summary (§8.2, §6.5, D19)

* `contacts.phone_status` moves forward only on WhatsApp's own signals (delivered = reachable, read = likely, buyer
  reply = verified, failed = not reached); `phone_kind` (mobile / landline / invalid) makes the AI ask the §8.2 line.
* On QUALIFIED the CRM renders the Requirement Summary PDF (no prices; headless Chromium, WeasyPrint fallback) and
  automation sends it as an approved template with a document header. A failed summary blocks the WhatsApp quote:
  email when the buyer gave one, else WhatsApp as the approving salesperson decides, with a task (OPEN #46).
* Tests: 12 CRM DB, 1 automation DB.

## WP21. In-house staff login + MFA (D26)

* `kx.core.passwords` (Argon2id, TOTP with replay protection, Fernet-sealed seeds), `kx.core.auth.TokenIssuer` (RS256,
  15-minute access tokens, `amr`), JWKS at `/.well-known/jwks.json`. Migration `crm_0010` (credentials, sessions,
  challenges).
* `/v1/auth/*`: login -> challenge; TOTP (`mfa=true`) or email code (`mfa=false`: no approvals); refresh in an HttpOnly
  cookie, rotated on every use, a replayed old token ends every session; logout, me, TOTP enrolment, password change,
  recovery by email code (always 202). 5 wrong passwords or codes lock for 15 minutes; every step audited. A
  break-glass sign-in notifies the MD and the manager (`staff.notice_requested.v1`).
* `/v1/users` (admin with MFA): list, create with a one-time password, edit (role, active, alert channels, assignment
  fields), reset MFA, unlock. `kx create-user` for the first admin. Staging and production refuse to start without
  `KX_JWT_PRIVATE_KEY` and `KX_AUTH__ENCRYPTION_KEY`.
* Tests: 6 HTTP+DB (email code then TOTP, lock-out, refresh rotation and reuse, single-use codes, break-glass alert,
  reset by code).

## WP17. Salesperson inbox reply, click-to-call, AI summary (§14, §7A.2, D21, D23)

* `POST /v1/deals/{id}/messages`: a staff reply on the buyer's channel and company number through `OutboundService`
  (kind `staff_reply`), signed "— Name, Company" on WhatsApp; email from the company address with the salesperson's
  name as sender (`outbound_messages.sender_name`, migration `automation_0005`). The price guard runs on staff text:
  an amount outside the latest issued quote or offer is refused (`unapproved_price`); percentages (payment terms)
  are for people. WhatsApp after 24 h answers `window_closed`; the screen resends with the approved re-open template
  (`handover.reopen_template`, default `staff_reopen`, params: name, company). The AI pauses (human takeover).
* `POST /v1/deals/{id}/call`: `CallBridge` rings the salesperson, then dials the buyer from the company number
  (`answerOnBridge`), after the §8.1 call stop and the calls kill switch; one ring per click; logged on the deal.
* Agents write the deal-page summary after the first exchange, every third exchange and at hand-over
  (`conversation.summary_updated.v1`); the CRM stores it without bumping the deal version, newest wins.
* Fixed on the way: the migration test's glob skipped revision `0010`.
* Tests: 10 CRM DB, 3 automation DB (bridge, call stop, kill switch), 1 outbound DB, 1 email, 8 agents spec.

Totals after WP17: **596 passed** (root 229, CRM 145, automation 145, agents 77); ruff, format and mypy --strict clean.

## WP18. CMS publish -> AI knowledge + CRM product mirror (§7, D22)

* `POST /webhooks/cms` (`kx.automation.cms`): HMAC-SHA256 over `"<timestamp>." + body` (`X-Kx-Timestamp`,
  `X-Kx-Signature: sha256=...`), 5-minute window, one receipt per `event_id`. Raises `kb.content_published.v1` per
  locale for pages, products, categories, FAQs, certificates, tour hotspots and testimonials, and
  `catalogue.product_published.v1` for products. Price-like spec keys are dropped before anything is stored.
* CRM `services/catalogue.py`: upsert by product code (case-insensitive); only descriptive fields; a new code is
  not quotable until the manager prices it; a new category is created; unpublish only clears the flag.
* Tests: 6 automation DB (signature, replay, stale, locales, no prices), 2 CRM DB.

## WP19. Website API (§6, D34, D39)

* `kx.agents.website`: `POST /v1/public/sessions` (Turnstile, signed session token, voice consent stored with its
  time: migration `agents_0003`), `/messages` (one turn through `AgentService`; the CRM deal is created on the first
  message), `/voice` (LiveKit token with `deal_ref` + `language`; refused for Sinhala and without voice consent),
  `/identify` (handed to the CRM merge; same answer whether the buyer was known). Per-IP and per-session token
  buckets (OPEN #61); the daily AI cap answers 503. `kx.core.signed` for the session token.
* Tests: 8 DB, 1 spec. Staging and production refuse to start without the Turnstile key.

## WP22. AI route, fallbacks and spend caps (D27, D30, D34)

* Bedrock removed (provider, config, client); `ModelRouter` = Claude API only (Haiku 5.5 FAST, Sonnet 5.5 SMART).
  Gemini stays a switch only: no Gemini client library is in the lock (not added without approval).
* `kx.core.fallback`: `CircuitBreaker` (3 failures / 60 s -> 5 minutes -> one trial) and `FallbackChain` (order,
  breakers, `Kairox/VendorSwitch` metric, alert hook, `AllVendorsFailed`).
* Agents: when every model fails, or Claude's breaker is open, the buyer gets "Let me check with our sales team."
  and the CRM pauses the AI with a task for the salesperson. Run cost from the official price table
  (`kx.agents.runs.PRICES`, Haiku's >100K tier included) is stored on `agent_runs.cost_usd` and added to the daily
  AI cap (`kx.core.spend.SpendMeter`: integers in Redis per local day, one alert at 80 % and at 100 %).
* Tests: 3 spec (breaker, chain, prices), 1 spec (spend), 1 DB (safe reply + breaker).

## WP23. Knowledge search on Qwen3 (D28)

* `HttpEmbedder` (OpenAI-compatible `/v1/embeddings`, Qwen3 query instruction, 1,024 dims checked, L2-normalised)
  replaces Titan. `KnowledgeBase.search` falls back to full-text + trigram when the embedding fails or takes > 2 s.
* Tests: the KB DB test now also searches with a failing and a slow embedder.

Totals after WP23: **618 passed** (root 234, CRM 147, automation 151, agents 86); ruff, format, mypy --strict and
import-linter clean.

## WP24. Observability (D29)

* Phoenix tracing over OpenTelemetry (`kx.agents.tracing`): one span per model call with OpenInference attribute
  names, PII masked; Langfuse removed from the dependencies (`uv lock`: langfuse and backoff gone, OTel 1.45.2 kept and
  now declared). Sentry SDK (Better Stack) now also starts in the API process.
* Tests: 2 (span content and masking; off without a collector).

## WP25. Channel fallbacks and kill-switch ladder (D30-D35)

* Email: `PostmarkSender` (fields per Postmark's API docs, read 12 Nov) and `EmailChain` (SES -> Postmark in the
  Parameter Store order, breaker, switch metric) used by `OutboundService` and the staff-code mailer.
  `POST /webhooks/postmark/inbound` (Basic auth, Postmark does not sign) records buyer replies like SES mail.
* `VendorOrder`: `<KX_APP_VENDOR_ORDER_SSM_PREFIX>/<field>`, cached 60 s, defaults on error.
* D32: 10 minutes after a priced WhatsApp message is sent, undelivered -> `quote.not_delivered.v1` with the exact
  content; the CRM grants the same hash on email and raises `quote.resend_requested.v1` (automation sends it), or
  tells the salesperson to call when there is no email.
* D35: `kx.core.killswitch` ladder (off / marketing / calls / text_only / hold) with `GET`/`PUT
  /v1/admin/kill-switch` (admin or MD with MFA, audited); follow-ups stop at `marketing`, calls at `calls`.
* Telnyx: not built (OPEN #55 lists the three points the docs did not confirm); calls stay on Twilio.
* Tests: 4 Postmark, 1 chain, 1 ladder, 1 outbound D32, 2 CRM D32.

Totals after WP25: **631 passed** (root 234, CRM 149, automation 160, agents 88); ruff, format, mypy --strict and
import-linter clean.

## WP28. CRM screens API completion (§20, §21)

* `kx.crm.api.records`: contacts (search, §3 visibility, edit non-identity fields), companies (list, create, edit),
  product catalogue (list, detail, manager+MFA edit of the CRM-owned fields only, D22), orders list, FX rates
  (latest per currency, manager+MFA override, D38).
* `kx.crm.api.reports`: `/dashboards/salesperson`, `/dashboards/manager` (incl. opt-out count and the buyers' own
  reasons), `/dashboards/md`; `/exports/{deals|contacts|companies|orders|products}?format=csv|xlsx`.
* `kx.automation.admin_api`: WhatsApp numbers per region (countries, default, status) and message templates
  (WhatsApp ones `submitted` until Meta's approval is recorded), admin with MFA.
* Kill switch: `GET`/`PUT /v1/admin/kill-switch` (WP25). Definitions chosen: OPEN #63, #64, #66.
* Tests: 5 CRM HTTP, 2 automation HTTP.

## WP29. Hand-offs

* `.env.example`: generated from `CoreSettings` (KX_), `AutomationSettings` (KX_AUTO_) and `AppSettings` (KX_APP_)
  = all 123 settings with the code's defaults and empty secrets, plus the voice-plugin, migration and CMS-container
  variables. Checked: all three settings classes load it.
* `docs/frontend/CRM_API.md`, `WEBSITE_API.md`: a written overview (design, roles, screens -> endpoints, sign-in,
  conventions, errors, mock server) + an endpoint reference generated from the code's OpenAPI, with the roles read
  from each route's auth dependency. `openapi-crm.json` / `openapi-website.json` for mock servers.
  `docs/frontend/CMS.md`: the content model (§7), the publish-webhook contract and the REST fields the platform
  reads back, from `kx.automation.cms` and `PayloadContentSource`.
* 46 routes had no docstring: each now states its purpose (shows in OpenAPI and the docs). One docstring was
  corrected after checking (`GET /v1/approvals` is manager / MD only).
* Makefile: `up` starts `postgres redis` (Valkey replaced in WP27); `worker` / `beat` targets for Celery.

Final totals: **638 passed** (root 234, CRM 154, automation 162, agents 88); ruff, ruff format, mypy --strict
(170 files) and import-linter (3 contracts) clean.

## WP26. FX daily rate (§11.2, D38) — blocked

* The manager override path is built and tested: `POST /v1/fx-rates` (manager + MFA) writes `crm.fx_rates`
  with source `manual`, and quoting reads the latest rate per currency; a manual rate always wins.
* The **automatic daily CBUAE feed is not built**: the documents do not give a machine-readable source,
  endpoint or licence for the UAE Central Bank rates (OPEN_QUESTIONS #56). Until that is confirmed, rates are
  entered by the manager. No `cbuae`/FX Beat entry exists, by design, so nothing silently serves a stale rate.

## WP27. Local stack: Redis

* `make up` and `docker-compose` run PostgreSQL (pgvector) + Redis. An earlier Valkey image was replaced with
  Redis so local matches the ElastiCache (Redis) target; the `SpendMeter` and rate limiter use it.

## 12 Nov. Audit pass (post-WP29 review and fixes)

A full audit of the 11 Nov code against the SRS and the platform docs (63 items done, 29 partial, 3 missing)
found and fixed the following, with tests. No SRS or platform doc was edited (read-only, as instructed).

**Access and safety**
* Quote-version read (`GET /v1/quote-versions/{id}`) now checks the caller may see the deal (§3); it leaked
  across salespeople before. Change-request and export lists apply the same `visible_to` filter.
* `handover.reply` / `call_buyer`: the "who may contact the buyer" check (§14 — admin never does, MD only on
  takeover) now runs **before** the "sender/caller configured" check, so authorisation no longer depends on
  infrastructure being present (admin got 503 instead of 403).
* Website `_ip` reads the **last** X-Forwarded-For entry (the ALB's own append); earlier entries are
  client-supplied and were spoofable for the per-IP rate limit.
* Opt-out reason counts on the manager dashboard de-duplicate per stop (one `(contact, at)`), not per consent
  row — a scope-`all` stop writes four rows and was counted 4×.

**Degrade and spend (D34/D35)**
* Model tiers corrected to D27: Haiku (FAST) on non-money roles, Sonnet (SMART) on every price-quoting role.
* AI spend cap: at 100 % of the daily AI budget a turn hands over **before** any model call (`budget_left`).
* WhatsApp-template cap: buyer templates are held at 100 % (`spend_cap`, a transient reason the caller
  retries); staff alerts are never held; each template actually sent is counted.
* Kill-switch `text_only` and an exhausted voice-minute budget now block the website voice token.
* Price-guard block now hands the deal to the salesperson (not just a safe reply).
* Metric names are bare (`VendorSwitch`, `SpendCap`) so the CloudWatch alarms in `infra/modules/observability`
  match what the app emits; a `SpendCap` alarm was added.

**Config and ops**
* PDF renderer: the container wires headless Chromium first, WeasyPrint as the backup (D37); without the
  Chromium binary (a developer laptop) it logs once and uses WeasyPrint only.
* Postmark inbound env key corrected (`inbound_username`); stale Telnyx/audio-bucket env vars removed.
* `.env.example`: added `KX_PROCESS`, `KX_API_PORT`, `KX_HEALTH_PATH`; marked `KX_AUTH__COOKIE_SECURE=false`
  as a local override. `docker/healthcheck.py` falls back to `KX_APP_API_PORT`.

**New features filling §-gaps the audit flagged partial/missing**
* §8.1 step 4: `POST /v1/deals/{id}/opt-out` — a salesperson records a buyer's stop (and their own reason),
  add-only, same effect as a STOP.
* §20.7 change history: `GET /v1/products/{id}/history` and `GET /v1/price-list/history` read the audit rows.
* §7A.3 team board: `unassigned=true` and `escalated=true` filters on the deals board.
* §21 scheduled reports: a daily summary and a weekly report to the manager and MD (`kx.crm.services.
  scheduled_reports`, Beat `crm.scheduled_reports` every 15 min; times/recipients are settings, `crm_0011`
  seed). The manager-board figures moved to `kx.crm.services.dashboards` so the report and the screen agree.

**Tests added:** 27 — root unit 17 (tiers, X-Forwarded-For, VendorOrder, metric/alarm names), CRM 7 (opt-out,
change history, team board, scheduled reports, export visibility, quote-version access, admin-never-contacts),
automation 2 (template cap, staff bypass + count), agents 1 (AI budget hand-over).

Totals after the audit pass: **665 passed** (root 251, CRM 161, automation 164, agents 89); ruff, ruff format,
mypy --strict (per package) and import-linter (3 contracts) clean. New migration `crm_0011` (report settings).

---

# 16 Nov v2 platform plan (WP-V2-01 … 14, D41–D53)

Source: *Accounts & Server Plan v2* §2–§5 and *Options by field — alternatives and usage* (16 Nov). Decisions by
Hamthan (16 Nov): env-var switch per field with the free alternative as the paid version's fallback; remove every
vendor not in v2 §2; Tamil voice via ElevenLabs also in the free version; database in three tiers; delete the AWS
Terraform; next projects = design only; docs first.

## WP-V2-01. Decision documents

**Done.** `docs/V1_CHANGE_PLAN.md` (16 Nov section: what changed, D41–D53, WP list); `docs/OPEN_QUESTIONS.md` (#69–#77;
#53, #54, #55, #65, #68 closed); platform docs `Recommendation.md` (v2 §2–§5, paid / free totals; 11 Nov kept as
history), `Fallback plan.md` (paid / free chains), `Options by field.md` (v2 pick table on top); SRS §4 and §22
hosting / data-location lines plus the two §19 vendor names (Telnyx, Stalwart via SES), flagged for Arifa's approval;
`CLAUDE.md` (source order, layout, vendor and database rules); `CONTRACTS.md` (ownership, `opentelemetry-api` in
`kx.core`, new helpers, [16 Nov] rules).

## WP-V2-02. Reference documents

**Done.** New: `docs/ARCHITECTURE.md` (supersedes `Architecture-and-System-Design.docx`), `docs/DATABASE.md`,
`docs/PLATFORM_MODES.md`, `docs/MULTI_PROJECT.md`, `docs/LOCAL_DEV.md`, `infra/server/README.md`. Updated: `README.md`,
`docs/DEPLOYMENT.md` (rewritten for the server), `docs/REUSE_CATALOGUE.md`, `docs/HANDOFF_CONTEXT.md` (16 Nov block),
`docs/DELIVERY.md`, `db/README.md`.

## WP-V2-03. Go-live fixes from the bench

**Done.** `kx/agents/models.py`: no `temperature` / `top_p` / `top_k` (Sonnet 5.5 and Haiku 5.5 reject them, bench R1);
adaptive thinking and `output_config.effort` per tier (FAST low, SMART medium, JUDGE high; `KX_APP_LLM_EFFORT_*`),
`max_tokens` 2,048 / 4,096 / 8,192. `final_reply` treats `stop_reason=refusal` as no answer (hand-over, D51).
WhatsApp: `kx.core.identity.try_normalise_phone`; `parse_webhook` skips an unparseable sender / recipient and the
route answers 200 to a payload it cannot parse (no more 500 + Meta retries). Embeddings: BGE-M3 without a query
instruction (Qwen3 keeps its instruction), query timeout 2 s, indexing timeout 120 s, batch 8; the vector leg of
search only reads chunks of the configured model; `KnowledgeIndexer.reembed_stale` + `kx reindex`.
Tests: payload test on the real `ChatAnthropic` request (no sampling keys, effort set), refusal test, bad-sender
webhook test (DB), phone parsing cases.

## WP-V2-04. Platform modes core (D41)

**Done.** `kx/core/platform.py` (Field, Plan, VENDORS, `resolve_modes`, `order`, `resolved`, `summary`);
`CoreSettings.platform_plan` + `PlatformModes` (`KX_PLATFORM_PLAN`, `KX_PLATFORM__<FIELD>`), `CoreSettings.modes()`;
`kx platform show`; container builds every chain from `order()` and logs `platform.resolved` /
`platform.paid_vendor_missing` at start. `OpsNotifier` (app) is the `FallbackChain.alert` hook: log + one email per
field per 15 min (Redis NX). Parameter Store `VendorOrder` and the SSM client are gone. Settings now ignore empty env
values (`env_ignore_empty`), so blank Coolify variables mean "default". Tests: `tests/spec/test_spec_platform_modes.py`
(hypothesis over preset × overrides × keys × language).

## WP-V2-05. R2 storage (D45)

**Done.** One aioboto3 S3 client with `endpoint_url` (`KX_STORAGE__*`), one bucket with prefixes (`media/`,
`inbound-mail/`, quotes, `archive/`); no SSE / KMS parameters; LocalStack removed.

## WP-V2-06. Email: Stalwart + SES (D44)

**Done.** `channels/stalwart.py` (JMAP client, `StalwartSender`: upload → `Email/import` → `EmailSubmission/set`,
returns the Message-ID; `MailboxPoller`), `channels/feedback.py` (DSN bounces, ARF complaints, `candidate_ids` to match
both Stalwart and SES message ids), `inbound.record_mail_event`, Beat job `automation.mail_poll` → one
`ingest_inbound_email` job per stored message. **Different from the plan:** no `mailbox_cursors` table: a JMAP
keyword (`$kxdone`) set after the job is queued is the cursor, and `first_receipt` dedupes a repeat. Bounce reports
arrive in the sending mailbox itself (no separate `bounces@`). Removed: Postmark, SNS, the `/webhooks/ses` and
`/webhooks/postmark/inbound` routes. Migration `automation_0006` (provider `stalwart`).

## WP-V2-07. Calls: Telnyx TeXML (D43)

**Done.** `channels/calls.py` (stdlib TeXML builders, `TelnyxCaller` over httpx, `CallbackUrls` with a signed
per-attempt token, Ed25519 `verify_signature`); routes `/webhooks/telnyx/{gather,status}/{attempt}`. **Different from
the plan:** the TeXML goes inline in the call request (the TeXML Calls API accepts `Texml`), so no `call_attempts.script`
column and no TeXML fetch route were needed. Twilio removed (dependency too).

## WP-V2-08. Voice (D52)

**Done.** `services/speech/` (faster-whisper Small INT8 + Piper, OpenAI-compatible, own Python 3.12 image, models
downloaded into a volume on first start); `arifaa_pack/voice_worker.py` (module-level `entry` + `prewarm`, process
executor, container built once per process); `kx.agents.voice.voice_routes` from the platform modes, `graph_llm()`
on the session; `PlatformVoicePlugins` (Deepgram, ElevenLabs, OpenAI-compatible → speech server, LiveKit fallback
adapters report switches to `OpsNotifier`); `VoiceSlots` free-version call cap at the token endpoint.
Dependencies added: `livekit-plugins-deepgram`, `-elevenlabs`, `-openai`, `-silero` 1.8.10. **Different from the
plan:** the turn-detector plug-in is not used (deprecated; its replacement calls LiveKit Cloud): end of turn = Silero
VAD. **Not verified live:** a real call through LiveKit + the speech server (needs Docker; see WP-V2-12).

## WP-V2-09. Monitoring (D46)

**Done.** `kx/core/metrics.py` on the OpenTelemetry API (counters / gauges; nothing on stdout, so the Celery
`sys.stdout.buffer` crash is gone); `arifaa_pack/telemetry.py` (OTLP/HTTP to SigNoz, `service.namespace` = company;
Redis heartbeat; `/healthz/beat`); the AI audit trail takes an endpoint + headers (Phoenix or Langfuse). Removed:
Sentry SDK, Better Stack heartbeat, CloudWatch EMF. Note: OpenTelemetry stores metric names lowercase
(`vendorswitch`).

## WP-V2-10. Database tiers (D48)

**Done.** `KX_DB_PGBOUNCER` connect arguments (no statement caches, unique statement names, settings per role);
checkpointer pool 3 and `prepare_threshold=None` behind PgBouncer, its tables created by `kx migrate`;
`kx/core/retention.py` (batched prune, month partitions, archive as gzipped CSV + manifest to R2); per-package rules
(`kx.crm.services.retention.plumbing_rules`, `kx.automation.retention`, `kx.agents.retention`); Beat job
`app.retention`; migrations `agents_0004` (partitioned `tool_calls` / `guard_events`, `tool_calls(run_id)` index),
`automation_0007`, `crm_0012` (pruning indexes, autovacuum); `db_bootstrap.sh` moved to `infra/server/postgres/`
(company prefix, per-role `search_path` / `statement_timeout`, Phoenix out of the app cluster); local init uses the
`pack_` prefix. **Not verified yet:** the migrations and the retention jobs against Postgres (DB tests need Docker).

## WP-V2-11 / 12 / 13. Server stacks, local compose, AWS removed

**Done.** `infra/server/` (postgres image with WAL-G, `postgresql.conf`, `pgbouncer.ini`, seven compose stacks,
`env/pack.env.example`, LiveKit and CrowdSec config, volume backup script, five runbooks); root `docker-compose.yml`
with profiles (`voice`, `mail`, `obs`, `embed`, `pgbouncer`) and models on `D:/kx-models`; Makefile targets
(`up-voice`, `up-mail`, `up-obs`, `up-embed`, `platform`, `stacks-check`, `image-speech`). Deleted: all Terraform
(`infra/bootstrap|org|envs|modules|scripts`), `deploy.yml`, `terraform.yml`, `_terraform-apply.yml`. New
`release.yml` (GHCR images → Coolify deploy webhook); `ci.yml` builds amd64 + the speech image and checks every
compose stack, shell scripts and Dockerfiles. Third-party image tags are pinned defaults to confirm at first deploy.

## WP-V2-14. Sweep

**Done.** `.env.example` rewritten by field (every `KX_*` key checked against the settings classes); frontend docs
regenerated (no API change); `tests/unit/test_no_removed_vendors.py` guards D42. `resource-usage-bench/` is excluded
from ruff (measurement scripts). Gate on Windows, no database: `ruff` clean, `mypy --strict` clean (182 files),
import-linter 3/3 kept, **434 passed, 264 skipped** (DB / Redis / WeasyPrint tests need the containers).

## 16 Nov. Verification on the local stack (DB tests, server stacks, voice, mail)

**Environment.** Python 3.14.10 via uv 0.13.7 (venv synced to the committed `uv.lock`: Twilio, Sentry, Langfuse
out, the four LiveKit plug-ins in; no dependency added). Docker Desktop 29.3 (WSL 2). Postgres 18.6 + pgvector and
Redis 8.2 from `make up`. Two laptop-only workarounds, nothing changed in the repo for them: the Windows
PostgreSQL 17 service owns :5432, so the compose Postgres was published on :5434 through a local override file;
WeasyPrint cannot load its GTK libraries on Windows (a wrong `libgobject` DLL from Tesseract on PATH: 44 PDF test
failures), so the suite ran in a Linux container like CI (Python 3.14.10, Pango, `redis-server`) on the compose
network, `KX_TEST_PG_DSN=postgresql://postgres:postgres@postgres:5432/postgres`. There is no D: drive on this
laptop: model caches went to `KX_MODELS_DIR=F:/kx-models`.

**Result: 699 passed, 0 skipped, 0 failed** (the 264 skipped DB / Redis / PDF tests now run); `ruff`, `ruff format`,
`mypy --strict` (182 files), import-linter 3/3 kept; `make stacks-check` 7/7 stacks; root compose with every
profile; shellcheck clean.

**Bugs found and fixed (with tests):**
* **The app could not start** (WP-V2-10): `open_checkpointer` passed `max_size=3` (1 for `kx migrate`) but no
  `min_size`, which psycopg_pool defaults to 4, so the pool refused to open in every api / worker / voice process
  and in `kx migrate`. Now `min_size=1` (`kx/agents/memory.py`). Caught by `test_app_smoke` and
  `test_quotation_waits_durably_and_resumes`.
* **Beat never ran two jobs** (WP-V2-06, -10): `automation.mail_poll` (buyer replies + bounce reports, D44,
  every 30 s) and `app.retention` (D48 nightly prune / partitions / archive, now 03:11 Dubai) were registered in
  the worker but missing from `arifaa_pack/schedule.py`. Caught by the smoke test's timetable check.
* **Email never worked against a real Stalwart** (WP-V2-06), two bugs in `channels/stalwart.py`, found by driving
  the app's own `JmapClient` / `StalwartSender` / `MailboxPoller` against `make up-mail`: (1) Stalwart answers
  `/.well-known/jmap` with 307 → `/jmap/session` and the shared httpx client does not follow redirects, so every
  send and poll failed on a non-JSON body; (2) the session's upload / download URLs are absolute templates on the
  container's hostname, and rewriting the host with `httpx.URL` percent-encoded `{accountId}` / `{blobId}`, so
  every upload went to a 404. New unit test `test_jmap_session_follows_the_well_known_redirect` (fails without
  the fix).
* **Stalwart data was outside every volume and backup** (WP-V2-11, -12): v0.14 keeps config, the mail store
  (accounts, mail, DKIM keys) and logs in `/opt/stalwart`, but the compose files mounted `/etc/stalwart` and
  `/var/lib/stalwart` (left empty), so the data sat in an unnamed volume and `volumes-to-r2.sh` backed up empty
  volumes. Now one named volume at `/opt/stalwart` in `compose.mail.yml` and `docker-compose.yml`; the backup
  copies `kx-mail_stalwart-data`. **Before the first server deploy only**; on a running server, copy the
  unnamed volume into the named one first.
* **`compose.app.yml` did not parse** (WP-V2-11): `env_file: [${KX_ENV_FILE:-…}]` is a YAML flow mapping inside a
  list; values quoted. Its CMS env file had no example: new `infra/server/stacks/env/pack-cms.env.example` (keys
  from the root `.env.example`); CI copies it; `make stacks-check` now sets the same dummy variables as CI (it had
  none, so it could not pass anywhere).
* **Telnyx gather on an unknown attempt** (WP-V2-07): a correctly signed URL for an attempt that does not exist
  answered "The manager is being alerted now" while nothing was escalated. `VoiceAlerts.record_digits` now raises
  `NotFound` (404). The test meant to check this had a no-op `.replace(...)`; it now asserts 404.
* Stale test: inbound email provider is `stalwart` (D44), not `ses`.
* Local compose: LiveKit `--dev` serves media on UDP 7882, which was not published (only TCP 7881 worked); added.

**Live checks on the laptop:**
* `kx migrate` (compose `migrate`) against fresh `pack_*` databases: exit 0, checkpointer tables created. The old
  local volume (11 Nov, un-prefixed DBs) was kept; the `pack_*` roles and DBs were added with
  `db_bootstrap.sh` (idempotent).
* **Voice profile** (`make up-voice`, free version): both images build; the speech server loads Whisper Small
  INT8 + Piper en/ar/hi/zh; round trip Piper → faster-whisper: English 0.6 s + 1.8 s, Arabic 0.5 s + 1.8 s, both
  transcripts right. The voice worker prewarms, registers with LiveKit, takes a dispatched room, resolves
  `stt=free:whisper tts=free:piper`, links to a test visitor and speaks the recording notice into the room
  (audio received). Not tested: a real browser call and a buyer turn through `AgentService` (no Claude key).
* **Mail profile** (`make up-mail`): local `sales@example.com` account; JMAP send through `StalwartSender`
  (Message-ID returned), `MailboxPoller` picks the message up once and the second poll returns 0 (`$kxdone`),
  `parse_inbound` reads it. Not testable here: the SES relay, DKIM, and SES feedback forwarding (OPEN #71).

**Notes, not changed:**
* `services/speech/download.py` has no timeout: one Piper voice download from Hugging Face hung at 0 bytes and the
  speech server stayed unhealthy until the file was copied from the bench cache. On the server, pre-seed the
  models volume or add a timeout + retry.
* First call per voice process: LiveKit reports the event loop blocked ≈ 1.2 s (container imports, botocore) and
  ≈ 0.7 s (OpenAI-compatible plug-in import); moving them into `prewarm` would remove that first-call delay.
* `docs/LOCAL_DEV.md` assumes drive D: (models, Docker disk); on this laptop Docker's disk is on C: (≈ 15 GB
  free) and models are on F:.

## 16 Nov (cont.). Embeddings, a real browser voice call, SigNoz, Phoenix

**Result: 700 passed, 0 skipped**; ruff, ruff format, mypy --strict, import-linter clean; 7/7 stacks.

**Knowledge search (D47), `make up-embed`, real code** (`HttpEmbedder` → TEI `cpu-1.8`, `KnowledgeIndexer`,
`KnowledgeBase.search`; scratch DB built by the app's Alembic revisions; 10 catalogue chunks in en / ar / hi / zh with
look-alike facts, 9 buyer questions):

| | BGE-M3 (server default) | Qwen3-Embedding-0.6B |
|---|---|---|
| TEI start / RAM | 49 s, 4.65 GiB (ONNX) | 24 s, 2.79 GiB |
| Right fact first | **9/9** (incl. "box *with* lid" vs "without") | **9/9** |
| Query (embed + SQL) | p50 159 ms, max 176 ms; 18 at once all answered | ≈ 230 ms |

Model switch: with `KX_PLATFORM__EMBEDDINGS=qwen3` and no re-index, the vector leg ignored every BGE-M3 chunk (no
mixed vector spaces); the real `kx reindex` (app image) re-embedded the 10 chunks in 9.5 s, then 9/9 again.
Fallback (D28): server down or over the 2 s cut-off → `kb.embedding_fallback` logged, no error, every answer "weak"
(the safe "team will confirm" + gap). **Finding:** the full-text fallback found **0/9** of these sentence questions:
`websearch_to_tsquery` needs every word and the trigram leg needs 0.6 similarity, so the lexical legs only help
keyword-style queries (decision needed, see the suggestions).
`kx` commands that build the container do not run natively on Windows (psycopg async + ProactorEventLoop in the
LangGraph checkpointer); they run in the Linux image, as on the server.

**Real browser voice call** (Playwright 1.55 + its Chromium + livekit-client 2.15.7, on the compose network, fake
microphone = a Piper WAV): the page called `POST /v1/public/sessions` (voice consent) and `/voice` like the website
(CORS allowed via `KX_APP_CORS_ORIGINS`), joined over WebRTC (ICE connected 0.2 s), published the mic; the agent joined
at 1.8 s. The visitor heard (transcript of the browser's own recording): *"… This conversation is recorded for
quality and to prepare your quotation. Let me check with our sales team."* The worker transcribed the visitor ("…
paper cups for coffee shops, I need about 20,000"), ran the graph, Claude failed (no key here) → safe reply + hand-over,
spoken by Piper. Warning seen under the laptop's load: Silero VAD 1.9 s behind real time.

**SigNoz** (official compose of tag v0.129.0, the last with compose files): app → OTLP/HTTP → collector → ClickHouse
verified: `queuelagseconds` from the real `kx-worker` (Beat heartbeat), `vendorswitch` (field email) and `spendcap`
(85 %) through `kx.core.metrics`, labels `service.namespace=pack`. Found: the collector opens 4317/4318 only after the
first admin account exists (OpAMP); SigNoz has moved to the Foundry installer; the app creates no HTTP / job spans.
Written into `infra/server/README.md` §5 and `docs/ARCHITECTURE.md` §8. Running the downloaded `foundryctl` binary was
blocked by the permission check, so Foundry itself is not tested.

**Phoenix** (server `compose.obs.yml`, pinned `version-12.9.0`): one model call through `kx.agents.tracing.Tracer`
stored as a span in project `kairox`, buyer phone and email masked (`+971*****67`, `s***@example.com`).

**Fixed (with tests):**
* **Phoenix's database could never start on the server** (WP-V2-11): `compose.obs.yml` mounted the `pgvector:pg18`
  volume at `/var/lib/postgresql/data`; Postgres 18 images refuse that (crash loop). Now one mount at
  `/var/lib/postgresql`, verified healthy.
* **Voice processes never exported metrics** (D46): only api and worker called `init_telemetry`, so a mid-call STT /
  TTS fallback's `VendorSwitch` was dropped. `voice_worker.prewarm` installs it (seen live: `telemetry.installed
  component=voice`); test `test_voice_processes_install_the_otlp_exporter` (fails without the fix).

**Bench (resource-usage-bench) re-read against today's code:** fixed since 14 Nov: Claude temperature, voice pickling,
voice LLM, WhatsApp 500, heartbeat stdout, embedding timeouts, pool sizes / worker concurrency, TEI `cpu-1.8` flags,
pruning, SafeLine instead of BunkerWeb. Still open: the voice container is built on each process's first call (not in
`prewarm`); free CPU speech-to-text on the server is unmeasured (OPEN #72; Whisper Small INT8 took 1.8 s per 4–5 s
clip on this laptop, the bench's large-v3-turbo ≈ 13 s); the bench tested Stalwart **v1.1** (data in
`/etc/stalwart` + `/var/lib/stalwart`) while the repo pins **v0.14** (data in `/opt/stalwart`): the mount must follow
the pinned version.

**`docs/Architecture-and-System-Design.docx` updated** (58 pages, schema-validated, rendered and checked): new
"16 November 2026 update" section at the front (platform changes, what was run, what was fixed, what is still open);
Figure 1 redrawn for the dedicated server; stale AWS / Terraform / Twilio / Postmark / Better Stack passages corrected
in §1.3–§10.5; a "Status 16 Nov" row on every defect. Re-checked defects of that guide: D1 resolved by the server
design, D2 fixed; **still open: D3** (price guard passes "I can do 450" and "QAR 500"), **D4** (an email-code session
can re-enrol TOTP), **D5** (reporting role reads every crm table), **D6** (no sweep for messages stuck in `sending`),
**D10** (LOST leaves approvals and grants live), **D12c** (`release.yml` does not wait for CI); D7–D9, D11, D12a/b not
re-checked.

## 17 Nov. Architecture-guide defects D3, D4, D5, D6, D10, D12c fixed; voice first-call delay

**Result: 736 passed, 0 failed**; ruff, ruff format, mypy --strict (184 files), import-linter 3/3. Every fix has a test
that fails without it.

* **D3 price guard** (`kx/core/price_guard.py`): (1) Gulf currencies QAR, OMR, KWD, BHD (codes, short forms, Arabic
  abbreviations) and "riyal(s)"; an amount in a currency not on the quote is a violation. (2) A whole number with no
  currency, keyword or decimals is a price when an offer phrase ("can do", "offer", "go down to", "final", "only" …)
  precedes it in the same clause and no noun follows it. "I can do 450 for you", "Our final offer is 450.", "QAR 500
  total" are now blocked; "Only 3 colours", "in 12 working days", "20000 pieces", "The MOQ is 10000" still pass.
  Checked on the 241 real AI texts (fixed phrases + every eval-scenario line): **no new false alarm**. 14 spec cases.
* **D4 authenticator** (`kx/crm/services/auth.py`, `api/auth.py`): replacing an ACTIVE authenticator needs a TOTP
  sign-in (an email-code session gets 403); the replacement is journalled and the user is emailed. First enrolment
  and lost-phone recovery (admin `reset_mfa`, then enrol) are unchanged. DB test.
* **D5 reporting role** (migration `crm_0013`, `db_bootstrap.sh`): no more blanket SELECT or default grant on future
  tables. Denied: credentials and tokens, cost / markup / freight cost, plumbing; column-limited: minimum price and
  ladder size on price tiers and quote tiers, minimum discount on categories, before/after values of the audit trail;
  the rest allowed by explicit list. The bootstrap no longer re-grants all tables (it re-runs on every deploy).
  Verified on the local `pack_crm` with the migration run as the owner role, like the server. A test fails while a
  crm table is unclassified (rule in `docs/DATABASE.md` §6).
* **D6 stuck sends** (migration `automation_0008` adds `outbound_messages.sending_at`; `jobs.expire_stuck_sending`
  in the 5-minute requeue job): a row sending for over 15 minutes becomes `failed` / `unknown:stuck_sending`, the
  `OutboundStuck` metric counts it (SigNoz rule `outboundstuck > 0`), and the CRM tells the salesperson to check the
  conversation (timeline + staff notice) without touching the buyer's phone status. **Never resent automatically**
  (the provider may have delivered it).
* **D10 LOST** (`kx/crm/services/deals.py`, `quotes.py`, automation `handlers.py`): closing a deal as LOST, in the same
  transaction, cancels its open approvals (they can no longer be decided), declines its open change requests and
  revokes unused send grants (counts in the audit row); `revise_quote` refuses a LOST deal; automation cancels the
  queued follow-ups on `deal.stage_changed` → LOST. **Not changed on purpose:** the AI on a LOST deal (the router lets
  only the concierge re-engage a returning buyer; it cannot state a price).
* **D12c release** (`.github/workflows`): `ci.yml` is also a reusable workflow; `release.yml` runs it first and builds
  / deploys only after it passes. Structure checked by parsing; **actionlint could not be run here** (blocked by the
  permission check) and the chain is first proven by a real tag on GitHub.
* **Voice first call** (`arifaa_pack/voice_worker.py`): `prewarm` now also imports the container modules and the
  speech plug-ins. Live browser call: agent joined after 1.2 s (1.8 s before); the first call's import stall fell from
  ≈ 1.9 s to 0.1 s.

**Bench suggestions not done (need a decision or a dependency):** looser full-text fallback (more answers but may
return a wrong fact), HTTP / job traces (two OpenTelemetry instrumentation packages), building the whole container in
`prewarm` (needs the job's event loop), free STT capacity on the real server (OPEN #72), Stalwart version.

## 17 Nov (continued). Monitoring live in SigNoz, Stalwart v1.1.1, D7–D12 re-check, Groq / Gemini fallback bench

**Result: 755 passed, 0 failed** (Linux container, PostgreSQL 18 + pgvector, Redis); ruff, ruff format, mypy --strict
(184 files), import-linter 3/3. Dependencies added with approval: `opentelemetry-instrumentation-fastapi` and
`-celery` 0.66b2 (lock also pulls `opentelemetry-instrumentation`, `-asgi`, `-util-http` 0.66b2, `asgiref`).

### Defects from the architecture guide (answers from the client)

* **D7 (manager approves):** a manual quote with a catalogue product, or a revision of a line outside every tier, is
  now a `special_price` approval: the manager decides first. Custom items with no catalogue product stay with the
  salesperson. 3 DB tests (`test_hand_priced_db.py`).
* **D8 (AI side only, "nothing more"):** the injection flag and knowledge gaps store masked text; anonymising a
  contact deletes its knowledge gaps, empties its guard details and clears the deal AI summary. CRM audit values,
  timeline rows and quote PDFs are kept (business record).
* **D9:** on the job's final attempt a failed buyer turn still sends the safe reply (same idempotency key as a normal
  reply, never twice) and pauses the AI with reason `exception`. 2 DB tests.
* **D11 re-checked, no code change:** the events without a consumer are informational; PRICE_STALE exists only in
  cost mode (v2, off). **Opt-out timing settings are read when a process starts: restart api / worker / voice after
  changing them.**
* **D12(a) (only the MD manages MD):** creating, editing, deactivating or resetting an MD account needs the MD; nobody
  changes their own role. **D12(b):** one app per project (v2 design); `company.yaml` letterhead filled from
  arifapack.com for now (ARIFA PACKING & PACKAGING LLC, Al Jurf Industrial Area 2, Ajman, +971 6 748 5384,
  info@arifapack.com). **The VAT TRN is still `REPLACE`: production refuses to start until it is filled in.**

### Monitoring: traces, logs and server monitoring, tested live against SigNoz v0.129.0

* **Traces:** every API request (`/healthz*` excluded) and Celery publish / run spans; Beat → `apply_async` →
  `kx.run_job` is one trace; Claude calls appear as `anthropic.messages.create` spans with metadata only (no prompt).
* **Logs:** structlog lines go to SigNoz after the PII mask, with the trace id of their request or job, plus
  `exception.type / message / stacktrace` (masked) for `log.exception`. Library warnings / errors too.
* **Server:** `infra/server/signoz/otel-collector-config.yaml` (SigNoz's file + hostmetrics, docker_stats, filelog) and
  `compose.signoz-overrides.yml` (mounts, `kx` network, OTLP on 127.0.0.1 only). Every stack's logs carry the compose
  service name; app containers are labelled `kx.logs=otlp` and skipped by the file reader (checked: 0 duplicate lines).
* **Live test:** three website chats with no Claude key. In ClickHouse: HTTP spans with status codes (201, 200, 401,
  404 and the 500 below), job traces, 35 app log lines with trace ids, `system.*` and `container.*` metrics for all 13
  containers, Postgres / ClickHouse / collector logs by name.
* **Found and fixed on the way (each with a unit test):**
  1. Every log timestamp was masked as a phone number (`20*****17T13:19…`): the mask ran after the timestamp.
  2. Deal ids and dates inside log values were half-masked (`01a1e9b0-…-b8*****65c9cc247`), so a deal could not be
     found from its logs. UUIDs and real ISO dates are now left alone; a phone typed like `0501-23-4567` is still
     masked.
  3. With console logs (`KX_LOG_JSON=false`, local dev) any `log.exception` raised TypeError and killed the request:
     the visitor's connection dropped instead of getting the safe reply.
  4. Celery's copy of stdout (`celery.redirected`) would have been forwarded a second time.
* Seen in SigNoz, not changed: with no active MD user, a hand-over cannot pause the AI (`no active MD user is
  configured`). Production always has the MD from the first-run setup; keep one active.
* Docker Desktop cancels some `docker_stats` calls (a few container points missing); recheck on the server.

### Stalwart v1.1.1 (newest stable, released 16 Nov), pinned and tested

* v1 is a rewrite: new database layout (no in-place upgrade from v0.x), `/etc/stalwart/config.json` +
  `/var/lib/stalwart`, settings managed over JMAP (`x:` objects) or the web wizard. Our app uses only standard JMAP,
  which worked unchanged.
* Tested locally with the app's own `JmapClient`, `StalwartSender`, `MailboxPoller`: send, inbox poll (second poll 0),
  and relay of an external message through a smart host that **requires STARTTLS and SMTP AUTH (as SES on 587)**:
  delivered, signed with **both RSA and Ed25519 DKIM**. SES setup = a `Relay` route `ses` (host
  `email-smtp.<region>.amazonaws.com`, 587, STARTTLS, user + password from env `SES_SMTP_PASSWORD`) and the outbound
  strategy `if is_local_domain(rcpt_domain) then 'local' else 'ses'` (steps in `compose.mail.yml`).
* **Bug found and fixed:** v1 keeps a record of every JMAP submission and refuses new ones at `maxSubmissions` (500):
  after 500 emails every send failed with `overQuota` and was retried forever. Reproduced with the cap at 10. The
  sender now deletes finished submission records on `overQuota` and resubmits the email already in Sent (never a
  second copy); deleting does not cancel a queued delivery (3 of 3 delivered after an immediate prune). Unit test.
* Files: `compose.mail.yml` (v1.1.1, two volumes, `init`, `KX_SES_SMTP_PASSWORD`, `KX_STALWART_RECOVERY_ADMIN`),
  `docker-compose.yml` (new local volumes `stalwart-v1*`; the old v0.14 volume is left untouched), backup script (both
  volumes), `env/pack.env.example`.

### Fallback bench: looser full-text search, Groq / Gemini free tier (`resource-usage-bench/docs/LLM_FALLBACK_RESULTS.md`)

* **Knowledge search with the embedding server down:** today's fallback (every word must match) answered **2 of 16**
  questions (only "we don't sell that"); an any-word version found the right product for **13 of 16**, the wrong one
  first twice. The answers built from it were right 14–15 of 16 with **no wrong product fact in 48 answers** (three
  models): the model answers only from the facts and declined or used the right chunk lower down.
  **Recommendation: strict first, any-word only when strict finds nothing. Implemented 18 Nov** (`knowledge.py`
  `_ANY_WORD_SQL`, DB test): the real search now answers 13 of 16 with the embedding server down (was 2 of 16).
  Chinese is not split into words by the `simple` text search; only the trigram leg can find it.
* **Free-tier backup model: not recommended.** Gemini 3.8 Flash (the standby named in the code) allows 20 requests a
  day per model (the bench hit it); Flash-Lite answered well but took 4–16 s with "high demand" errors. Groq
  (`openai/gpt-oss-120b`, `qwen/qwen3.8-27b`) was fast (0.15–0.5 s) and clean on all 7 price tricks, but its free
  limit is 8,000 tokens a minute (one or two real agent turns) and it returned "over capacity" once. Free Gemini
  terms let Google use the content (buyer messages, PDPL). For a backup, use a paid tier and run the 17-scenario eval
  suite live first (needs `langchain-openai` or `langchain-google-genai`).
* The Groq and Gemini keys were used from the environment only and are in no file. **Revoke both keys** (they were
  pasted into the chat).

**Still open:** VAT TRN for the letterhead; Stalwart v1 setup on the server (wizard,
`ses` route, outbound strategy, raise the 25-per-hour throttle); container metrics recheck on Linux. (The updated architecture guide replaced
`docs/Architecture-and-System-Design.docx` on 18 Nov.)

## 18 Nov. Product workbook: one Excel file for products, prices and website details

Client decisions (18 Nov): staff keep the catalogue in **one workbook**; one upload updates the CRM (prices,
tiers, MOQ, delivery days) and the CMS (product details only, **as drafts**: a manager previews and publishes);
English only; upload by manager / MD / admin (MFA); price changes need no approval, logged, the MD sees them.
Workbook is the master; small text / photo edits stay possible in the CMS.

* **Service** `kx/crm/services/workbook.py`: sheets *How to fill*, *Products*, *Price tiers*, *Categories*.
  Download = today's catalogue filled in. Upload = **preview** (every change old -> new, every error by sheet /
  row / column, warnings) -> **apply** (all or nothing; refused with 409 if the catalogue changed since the
  preview). A blank cell keeps the value, `-` clears it, a missing column or product changes nothing; a product
  in *Price tiers* gets those rows as its next price-list version only when they differ; prices with a currency
  in any text column are refused (they go to the website). Rules that are new (code format, delivery days,
  prices in text) are checked on new or changed values only, so a freshly downloaded file always uploads back.
  Quotable = has prices and is active.
* **API** `kx/crm/api/catalogue_workbook.py`: `GET /v1/catalogue/workbook`, `POST .../preview`,
  `POST .../apply?token=`. Uploads appear in the price-list history (`catalogue.workbook_applied`).
* **CMS drafts**: event `catalogue.product_content_changed.v1` (payload has no price field) -> job
  `app.cms_product_draft` -> `PayloadProductWriter` (Payload REST, `?draft=true`, `_status: draft`; category
  created as a draft if missing; specs merged; photos, rich text and SEO never touched); the CMS id is written
  back to the CRM product. Without `KX_APP_CMS_API_KEY` the job is skipped with a warning.
* **Docs**: `CRM_API.md` §5.1 (screen flow for the CRM developer), `CMS.md` §5 (fields and calls the CMS
  developer must support; the API user now needs create / update on drafts) and §2 (webhook `spec` keys);
  OpenAPI regenerated.
* Tests: 11 planning rules (unit), 3 database round trips (download -> upload unchanged = no change; apply
  creates product, category, tiers and one price-free CMS event; dash clears / blank keeps; stale preview and
  file errors refused), 2 CMS writer tests. **Full suite 771 passed, 0 failed**; ruff, mypy --strict (186
  files), import-linter clean.
* **Mistake made and repaired:** a `ruff --fix --select I001,RUF100` run treated every other `# noqa` as
  unused and removed 120 of them across the repository (with their explanations). All were restored from git
  HEAD, from the source copy in the 17 Nov app image and from this session's history, and checked word for word
  against those sources (0 mismatches). Lesson: never combine `--fix` with a narrow `--select`.

## 18 Nov (continued). Editable message texts and versioned WhatsApp templates

Client decisions: the manager edits (the MD may too and sees every change) the AI's fixed phrases, the buyer
messages that carry quotes and summaries, and the staff alerts; **English only**, the AI translates the buyer /
AI texts into Arabic, Hindi, Tamil, Chinese and Sinhala; WhatsApp template edits must never stop a live
template; manager, MD and admin edit templates. Opt-out confirmations, the opt-out questions and the
call-recording notice stay **not editable** (legal wording).

* **Catalogue** `kx/core/texts.py`: 37 texts (12 AI phrases, 13 buyer texts, 12 staff alerts) with their
  built-in wording (word for word as before; existing Arabic kept), placeholders and a note. Lookup: edit in
  that language -> built-in in that language -> edited English -> built-in English; a broken edit falls back to
  the built-in. Checks on every edit and translation: same placeholders, no amount with a currency, not empty,
  max 2,000 characters.
* **Wired**: `DefaultPromptStore.phrase` (every AI phrase), `pdf.quote_message` / `open_to_discuss` (only lines
  that exist in the buyer's language) / summary caption, labels and email subject, staff alerts in
  `automation/handlers.py` and `alerts.py`.
* **CRM**: table `crm.message_texts` (migration `crm_0014`, readable by the reporting role), service
  `services/texts.py`, API `GET /v1/texts`, `PUT /v1/texts/{key}`, `POST .../reset`, `GET .../history`,
  `POST /v1/texts/translate-missing`. Every change is in the audit trail.
* **Run time**: each process loads the edits at start and every 30 s (`arifaa_pack/texts_sync.py`); event
  `texts.text_changed.v1` -> job `app.translate_text` (Claude FAST tier) stores a translation only for the
  current English version and only if it passes the checks; until then a language keeps its previous text.
* **WhatsApp templates** (migration `automation_0009`): **bug fixed** — an edit used to overwrite the approved
  template and set it back to `submitted`, so that message stopped until Meta approved. Now an edit of the live
  version is a new version submitted under its own name (`quote_ready_v2`); the approved one keeps sending;
  approving the new one supersedes the old (history kept). Email templates go live at once with history.
* Docs: `CRM_API.md` §5.2 (texts screen, template versions). Tests: 11 unit (lookup, checks, AI / staff wiring),
  3 database (edit -> live -> translate with refused bad translations -> history -> reset; stale translation not
  stored; staff texts English only), template versions (WhatsApp and email). **786 passed, 0 failed**; lint,
  mypy --strict (192 files), import-linter clean.

## 18 Nov (continued). Editable company details (letterhead, logo, bank details)

Client decision: admin, MD and manager change the company details printed on quotes without a developer.

* Settings row `company.profile` (migration `crm_0015`): name, address, TRN, phone, email, logo (PNG / JPEG,
  max 200 KB, type checked by its first bytes) and bank {account name, bank, IBAN, SWIFT}. Checks: TRN 15 digits
  (spaces allowed when typed), IBAN checksum (stored in groups of four), SWIFT 8 / 11, email, phone. An empty
  field prints the `company.yaml` value. Settings history, audit and event come with every change.
* `kx/crm/services/company.py`: quotes, Requirement Summaries and the hand-over signature use the current
  letterhead (30 s cache); quotes already sent keep their PDF. The quote PDF now prints the logo and a "Bank
  details" block (only when set); the summary prints the logo.
* **Payment-fraud protection:** any change of bank details alerts the MD and the manager at once (staff notice)
  and is audited with the IBAN masked.
* The production start-up guard (D12b) now checks company.yaml **combined with** the CRM details: entering the
  TRN in the CRM is enough.
* API `GET/PATCH /v1/company`, `PUT/DELETE /v1/company/logo`; `CRM_API.md` §5.3.
* Tests: 10 unit (checks, fallback, the quote prints logo and bank only when set), 1 database (printed details,
  history, alert only on a bank change, IBAN masked, a wrong IBAN refused). Found by the tests and fixed: a TRN
  typed with spaces was refused; the alert showed " 456" instead of the last four IBAN digits.
  **797 passed, 0 failed**; ruff, mypy --strict (195 files), import-linter clean.

## 18 Nov (continued). Automatic daily FX rates

Client decision: automatic rates (the UAE Central Bank has no machine-readable feed). `kx/crm/services/fx.py` +
job `crm.fx_rates` (06:05 Dubai) + `OpenRatesFx` (open.er-api.com, free, no key; pluggable `FxSource`).
Currencies: GCC, USD / EUR / GBP, INR, PKR, LKR, CNY, plus any mapped in `quote.currency_by_country` or already
stored. **Rules:** a manager's rate for that day is never overwritten; a move above 10 % is not stored and the
manager is alerted (with missing currencies); a failing source leaves quotes on the latest rate, dated on the PDF.
`KX_APP_FX_RATES_AUTO=false` switches it off. `CRM_API.md` §5.4. Tests: adapter (AED per unit, failures raise),
database (stored and used by quotes, manual kept, EUR +21 % refused with an alert, missing reported).


## 18 Nov (continued). Gemini 3.8 Flash standby

Client decision: when Claude is down, Gemini 3.8 Flash answers. Dependency added with approval:
`langchain-google-genai==3.2.0` (kx-agent-framework; lock also adds google-ai-generativelanguage, google-api-core,
google-auth, grpcio-status, proto-plus, pyasn1, pyasn1-modules, filetype). `GeminiApiProvider` in
`kx/agents/models.py` (thinking level from the tier's effort); `_model_router` appends it after Claude with
`KX_PLATFORM__LLM_STANDBY=gemini` and refuses to start without `KX_APP_GEMINI_API_KEY` (a **paid** key: the free
tier allows 20 requests a day, measured 17 Nov). It answers inside the same guards as Claude. New command
`kx evals --vendor gemini` runs the 17 eval scenarios live against one vendor (exit 1 on any failure): run it with
the paid key before switching the standby on. Test: the chain is Claude -> Gemini only when switched on.
**803 passed, 0 failed**; lint, mypy --strict (196 files), import-linter clean.

## 18 Nov (continued). AI translation check (client decision: AI first, then a translator / Arifa person)

All 133 non-English strings in the code were read (`docs/translations/Translation review 18 Nov.xlsx`, 63 review
rows + texts not yet translated + matching words).

* **Price guard gap found and fixed:** grammatical Arabic puts the "-an" ending on the currency after numbers such
  as 11-99 ("٥٠ درهماً للقطعة", "50 ريالاً"): these unapproved amounts were **not detected**. Hindi, Chinese,
  Tamil and Sinhala native currency names (दिरहम, रुपये, 迪拉姆, திர்ஹம், ඩිර්හම් ...) were not either. Added
  (only unambiguous currency words); compared old vs new guard on the 114 real AI texts: **0 differences** (no new
  false alarm). 12 spec tests (9 currencies detected, 3 quantity / day sentences in Arabic, Hindi, Chinese stay
  clean).
* **Arabic grammar fixed** in built-in texts: "ستصلك عرض أسعار" -> "سيصلك" (change request), "ستصلك على
  واتساب" -> "سيصلك" (summary), "دفع مقدم" -> "دفعة مقدّمة" (quote message and default clause).
* **Suggestions for the reviewer** (not applied): smoother manager-60 and open-to-discuss Arabic; the Arabic
  recording notice omits the company name (legal: lawyer to approve); Sinhala opt-out confirmation needs the
  object form "ඔබව"; Chinese and Hindi wording closer to the English; the Arabic validity clause is right for 3-10
  days only (default 7). Missing Tamil / Chinese / Sinhala stop words listed with proposals (OPEN #42).
* Hindi, Tamil, Chinese and Sinhala versions of the editable texts are produced by the AI in the CRM
  ("Translate all missing") once the Claude key is set, then reviewed on that screen.
* **815 passed, 0 failed**; ruff, mypy --strict, import-linter clean.

## 18 Nov (continued). Deployment preparation

* `docs/GO_LIVE_CHECKLIST.md`: one ordered list for launch on arifapack.com: accounts to get (server,
  Cloudflare, Claude, paid Gemini, Meta, SES, Telnyx, Turnstile, UptimeRobot, Arifa's TRN / bank / logo), DNS
  records, server deploy order (SigNoz with our override, Stalwart v1.1.1 steps), the 15 WhatsApp templates to
  submit to Meta, data loading in the CRM, live tests, go-live day.
* `kx first-run --md-email … --md-name … --admin-email … --admin-name …`: checks each database is at the code's
  head (new `migrations.status`), creates the MD and the admin if missing (one-time passwords printed once),
  flags letterhead fields still missing (TRN) and prints the platform matrix; idempotent. **Run for real** on the
  local stack: it found `crm_0013` / `automation_0008` behind, `kx migrate` applied `crm_0014`, `crm_0015` and
  `automation_0009` to databases that already hold data, then first-run created both users and a second run left
  them alone. (On Windows `kx migrate`'s checkpointer step times out: psycopg async needs the selector event
  loop; the servers run Linux.)
* `pack.env.example`: `KX_PLATFORM_PLAN=free` (client decision), FX and Gemini standby notes, ops alert address.
* **815 passed, 0 failed**; ruff, mypy --strict (196 files), import-linter clean.

## 19 Nov. Round 2: security and infra fixes, quote polish, message merge, cleanup, restructuring, re-test

Client decisions of 19 Nov: M6 website ≤ 30 % of the daily AI budget; M7 layered price guard; M8 unverified mail
kept, flagged and silent (no auto opt-out, no auto-link, manager and salesperson alerted); TRN stays a placeholder.

* **Security review M2–M8, L1–L8** (`fix/live-test-defects`):
  - M2: CORS split, credentials only for the CRM; the website reaches only `/v1/public/*`.
  - M3: sign-in limits per IP and per account, the per account + IP lock and an account-wide backstop.
  - M4: no spreadsheet formulas in exports.
  - M6: website AI sub-cap and global / IP limits.
  - M7: numbers written in words are caught by the price guard.
  - M8: SPF / DKIM / DMARC from Stalwart's `Authentication-Results`.
  - L1: access tokens end with their sign-in session.
  - L2: body-size cap.
  - L3: salesperson company scope.
  - L4: Admin / IT off deals; exports limited to products.
  - L5: Telnyx signature required.
  - L6: quiet reset timing.
  - L7: one-time password enforced.
  - L8: valid time zones and language tags.
* **Defects and infra:**
  - D-8: circuit breaker on the query embedding.
  - The deal's salesperson updates the order status (SRS §5 step 14).
  - Infra review M1–M16: CI pinning and digests, image scan, stack limits, CrowdSec socket proxy, backups, WAL-G, graceful shutdown, worker recycling, the PDF renderer's CSP.
  - Monitoring gaps: worker health, trace ids on errors, Beat telemetry.
* **Buyer-facing:**
  - Quote email / PDF: no "Hi Customer", a filled "To" block, AI replies titled "Re: <subject>".
  - Quick WhatsApp / email messages within 8 s get one AI answer.
* **Cleanup:**
  - 7 indexes added and 1 duplicate dropped (`crm_0016`, `automation_0010`, `agents_0005`; `docs/DATABASE.md` §7).
  - Exports and workbook parsing moved off the event loop.
  - Dead code and duplicated blocks removed; `get_or_404`; shared role aliases.
* **Architecture (ADR-0001, branches `arch/phase0-tooling` and `arch/slices`):**
  - `kx map` and `docs/FEATURE_MAP.md` (CI), CODEOWNERS, PR template, ADRs.
  - CRM in 22 feature slices plus a public surface (`kx/crm/__init__.py`); 3 automation slices.
  - 10 import contracts, with baselines that may only shrink.
  - OpenAPI byte-identical and `kx map` path-only on every move.
  - Details: `docs/arch/REPORT.md`, `docs/arch/PROGRESS.md`.
* **Re-test** (`live-test/TEST_REPORT.md` §15):
  - 991 tests pass.
  - API matrix: 0 reachable without a token, 0 server errors.
  - Security probes: 43 / 43. Business flows with the real AI: 31 / 31.
  - Live checks pass: M8, and one real quote email to Gmail.
  - Load at 10 / 30 / 50 users: ≥ 98 % of WhatsApp replies within 60 s, 0 unanswered.
  - Six problems found and fixed:
    - an admin unlock now also lifts the per-IP lock;
    - a known IP skips the per-account sign-in limit (lock-out DoS);
    - logout ends the token's session at once;
    - a NUL character now gets 400 instead of 500;
    - security headers on every answer; no API docs in production;
    - a company's customer type now gets 422 instead of 500.
* **Tester pack** (branch `docs/tester-pack`): Postman collection and environments generated from the code (117 operations, 20 negative tests); the Word guide `docs/testing/TESTER_HELPER.docx`.
