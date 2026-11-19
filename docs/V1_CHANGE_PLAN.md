# v1 change plan

Four plans live in this file:

0. **19 Nov website AI + CMS plan (current):** the AI drives the website, a CMS package, voice = chat, one hand-off
   pack. Work packages WP-C0 … WP-C9, decisions D54–D61. Below, first.
1. **16 Nov v2 platform plan:** move the platform to the *Accounts & Server Plan v2* (one dedicated server, a free/paid switch per field, database tiers). Work packages WP-V2-01 … WP-V2-14, decisions D41–D53.
2. **11 Nov plan (done 12 Nov except WP26):** bring the code in line with the updated SRS and the 11 Nov platform decisions. WP13–WP29.
3. **5 Nov plan (done 6 Nov):** WP1–WP12, kept below for history. Log: `docs/V1_PROGRESS.md`.

**Sources of truth:**

| Short name | File | What it decides |
|---|---|---|
| **SRS** | `docs/Arifaa Pack — Detailed Business Flow v1 (SRS basis) (2).md` | Business rules. "§" numbers below refer to it. Read-only, except the §4 / §22 hosting lines updated on 16 Nov (flagged for Arifa's approval) |
| **Server plan v2** | `docs/Arifaa Platform — Platforms, Pricing & Fallbacks/Arifaa Platform — Accounts & Server Plan (for Arifa IT) v2.docx` | **The platform per field (§2), paid version cost (§3), free version cost (§4), the server (§5).** Wins over every other platform file |
| **Platform docs** | same folder: `Recommendation.md`, `Fallback plan.md`, `Options by field.md` (+ `Options by field — alternatives and usage.docx`), `Platform contest.md`, `Official sources.md` | Vendors, fallbacks, costs and measurements. Updated to v2 on 16 Nov; edit them only to follow the server plan |

When this plan and those files disagree, those files win. Where they are silent, the build takes the option that blocks, alerts or hands over to a person (never the one that sends, discounts or promises) and records the choice in `docs/OPEN_QUESTIONS.md`.

Keep the rules in `CONTRACTS.md`: packages never import each other; shared helpers come from `kx.core`; money is Decimal; every state change writes audit + timeline + outbox in one transaction (`kx.crm.services.journal.record`).

---

## 19 Nov website AI + CMS plan

### Why

The Arifa Pack website (design doc v4 and the site / user / data-flow doc, Rizwan, 18 Nov) needs one assistant
that moves the site to what a visitor asks for, by text or voice, and qualifies the visitor into a deal. The client
team must edit the site's content, and the frontend developer needs real content endpoints. Before this plan the
site's assistant called Claude directly (no price guard, no CRM), the backend's Website API could not move a page,
and the planned Payload CMS did not exist.

### Decisions

| # | Decision |
|---|---|
| D54 | The CMS is a platform package, `packages/kx-cms` (own database `cms`), not Payload (ADR-0003; SRS §4 change awaits Arifa's approval, OPEN_QUESTIONS #79). Draft → preview → publish, every version kept, no prices. A publish re-renders the website (`/api/revalidate`), re-indexes the AI's knowledge and updates the CRM. |
| D55 | The AI drives the website only through validated intent actions (`show_family`, `show_product`, `navigate`, `scroll_to`, `factory_tour`, `add_to_enquiry`, `fill_enquiry`, `ask_contact`), resolved against the site manifest built from published CMS content. It never reads the site's code and never sends selectors. |
| D56 | One front agent per website turn; the product and manufacturing specialists are read-only tools it may call in parallel. The price graphs stay behind the deterministic router. |
| D57 | Speed in three layers: the website's own instant resolver (from the manifest), the server's instant resolver (< 50 ms, first streamed event), then the agent. Replies stream sentence by sentence, each sentence price-guarded. While voice is on, typed text uses the same data channel. |
| D58 | Chat and voice share one conversation thread (`web:{session_id}`). |
| D59 | WhatsApp and email opt-ins are recorded with their wording and time (SRS §8, §22); opt-out still wins. |
| D60 | One generated hand-off folder `docs/handoff/` (CRM / CMS / website API documents, OpenAPI, one Postman collection per package, the tester and frontend guides); `make handoff`; CI fails when stale. |
| D61 | A website product (`ac-8325`) is the marketing record; its sellable **variants** are the CRM products (the ERP items: packings, roll weights, retail packs), linked by the product's id. ERP items have no codes: the CRM code is generated from description + unit. The AI explains variants with availability words only ("in stock", "limited", "made to order") and quantity breaks without prices. Container + lid quote as two lines. |

### Work packages

| WP | What | State |
|---|---|---|
| WP-C0 | Merge `docs/frontend` + `docs/testing` into `docs/handoff`, one generator, per-package Postman collections | done (19 Nov) |
| WP-C1 | `kx.cms` package: content model, drafts / publish / versions / restore, media (R2 presigned), preview, manifest, public + admin APIs, outbox, content editor role | done (19 Nov) |
| WP-C2 | `kx content import-site`: the website's catalogue, clients, page layouts (section ids), chapters, stations, synonyms, Arabic | done (19 Nov) |
| WP-C3 | Website API v2: `SiteContext` in, actions out, SSE streaming, `/events`, `/contact`, `/v1/public/enquiries` | done (20 Nov) |
| WP-C4 | Agents drive the site: manifest resolver, `show_on_site`, `find_products`, product variants, specialists, router keywords from config, prompts | done (20 Nov) |
| WP-C5 | Voice = chat: actions and captions on the LiveKit data channel, rejoin after a page load | |
| WP-C6 | Opt-in consent (D59) | done: CRM part with WP-C9, the API paths (`/contact`, `/enquiries`) with WP-C3 |
| WP-C7 | Hand-off content: WEBSITE_API head, FRONTEND_GUIDE (.md + .docx), TESTER_HELPER scenarios | |
| WP-C8 | Docs and map | |
| WP-C9 | ERP import into the CRM (all categories kept, non-sellable flagged, unit / measure, closing stock) and product variants (D61) | done (19 Nov) |

---

## 16 Nov v2 platform plan

### Why

The *Accounts & Server Plan v2* (16 Nov) replaces the AWS stack with **one Namecheap dedicated server** (Intel Xeon E-2236, 6 cores / 12 threads, 64 GB RAM, 2 × 960 GB SSD RAID 1, Ubuntu 24.04, managed with Coolify) plus a few online services on Arifa's accounts. Every paid platform that has an open-source alternative gets that alternative on our server, and each field can run in the **paid version** (§3, ≈ US$232 / 819 / 2,459 a month at Light / Normal / Heavy) or the **free version** (§4, ≈ US$171 / 575 / 1,739). The resource-usage bench (14 Nov, `resource-usage-bench/`) also found go-live bugs that are fixed here.

### What changed (11 Nov → 16 Nov)

| Field | 11 Nov | 16 Nov v2 (main → free alternative) |
|---|---|---|
| Server | ECS Fargate, 2 zones, AWS me-central-1 | **Namecheap dedicated server + Coolify** (Phoenix, AZ, USA: Arifa must approve the location, OPEN #69) |
| Database | RDS Multi-AZ | **PostgreSQL 18 + pgvector** on the server, PgBouncer, WAL-G backups to R2, three tiers (D48) |
| Cache + broker | ElastiCache Redis, 2 nodes | **Redis** on the server (`noeviction`, AOF) |
| AI | Claude Sonnet 5.5 / Haiku 5.5; Gemini backups off | same; **Gemini 3.8 Flash standby** (paid, only when used). No free model |
| Speech-to-text | Deepgram → Soniox | **Deepgram Nova-3 → faster-whisper Small** (free, CPU) |
| Text-to-speech | ElevenLabs → Google Chirp | **ElevenLabs Flash → Piper** (free; en, ar, hi, zh) |
| Live voice | LiveKit Cloud | **LiveKit self-hosted** |
| Staff alert calls | Telnyx (not built) → Twilio | **Telnyx** (TeXML); later Asterisk + UAE SIP line |
| WhatsApp | Meta Cloud API | same |
| Email | SES → Postmark → Gmail API | **Stalwart mail server** (send + buyer replies) relaying through **Amazon SES**; SES API as app backup |
| Website + CRM hosting | Vercel Pro | **Vercel Pro → self-hosted** on the server |
| Knowledge search | Qwen3-Embedding-0.6B | **BGE-M3 → Qwen3-Embedding-0.6B**, both on TEI `cpu-1.8` |
| AI audit trail | Phoenix | **Phoenix → Langfuse** |
| Monitoring | Better Stack (Sentry SDK) + CloudWatch | **SigNoz (OpenTelemetry) + UptimeRobot** → Grafana stack |
| Firewall + bots | AWS WAF + Turnstile | **SafeLine WAF + CrowdSec + Turnstile** |
| DNS + files | client DNS, S3 + KMS | **Cloudflare DNS + R2** |
| Secrets / vendor order | Secrets Manager, Parameter Store | Coolify encrypted env vars; **vendor order from `KX_PLATFORM_*` env vars** |
| Staff login | in-house + TOTP | same |

### Decisions (16 Nov)

D1–D40 still hold unless a row here replaces them. D29, D30 (order source), D31, D33, D36, D39 and D40 are replaced.

| # | Decision | Source |
|---|---|---|
| D41 | **Free / paid switch per field = environment variables.** `KX_PLATFORM_PLAN=paid\|free` sets every field; `KX_PLATFORM__<FIELD>=paid\|free` overrides one field (a change = redeploy). **Paid** runs the paid platform first and the free alternative as the automatic fallback, then the last resort; **free** runs only the free platform. Fields with no free platform (`llm`, `calls`, `whatsapp`) refuse `free` at start-up. The resolved matrix is logged at start and shown by `kx platform show`; every switch is logged, counted (`VendorSwitch`) and alerted. Table: `docs/PLATFORM_MODES.md`. Replaces the Parameter Store part of D30 (breaker, triggers and safety gates of D30 stay) | Server plan v2 §2–§4; Hamthan 16 Nov |
| D42 | **Only the v2 §2 platforms stay in the code.** Removed: Twilio, Postmark, Gmail API, Soniox, Google Chirp, Gemini 3.5 Flash-Lite, Better Stack, Sentry SDK, Parameter Store, SNS, LocalStack, S3 KMS, CloudWatch metrics, all Terraform | Server plan v2 §2; Hamthan 16 Nov |
| D43 | **Staff alert calls and buyer call-backs: Telnyx TeXML** (TwiML-compatible). Each attempt stores its script; Telnyx fetches the TeXML from a signed per-attempt URL; status callbacks are form posts checked by the signed token and, when configured, the Ed25519 signature. Last resort: WhatsApp alert + next person on the ladder. An Asterisk + UAE operator SIP adapter can join later behind the same `Caller` protocol (`KX_PLATFORM__CALLS=free`) | Server plan v2 §2; replaces D31 |
| D44 | **Email: Stalwart (JMAP) → SES v2 API.** The app sends through Stalwart's JMAP API; Stalwart relays outbound mail through SES SMTP (inbox reputation). Buyer replies and SES bounce/complaint reports land in Stalwart mailboxes and are read by a Beat job (`Email/changes`) every 30 s. If Stalwart is down the app sends through the SES API directly. Last resort: WhatsApp → salesperson told | Server plan v2 §2; replaces D33 |
| D45 | **Files on Cloudflare R2** (S3 API, `endpoint_url`): quote PDFs, inbound media, raw inbound mail, archives, database backups. No KMS parameters (R2 encrypts at rest) | Server plan v2 §2 |
| D46 | **Monitoring: OpenTelemetry → SigNoz** (metrics, traces, logs) on the server + **UptimeRobot** from outside (`/healthz`, `/healthz/beat`). The Beat heartbeat is a Redis timestamp. AI traces go to Phoenix (or Langfuse) over OTLP. PII stays masked in logs | Server plan v2 §2; replaces D29 |
| D47 | **Knowledge search: BGE-M3** on TEI `cpu-1.8` (1,024 dimensions, no query instruction); Qwen3-Embedding-0.6B is the alternative (`KX_PLATFORM__EMBEDDINGS`). A model change means `kx reindex`; search only reads chunks of the configured model. Query timeout 2 s → full-text; indexing timeout 120 s | Server plan v2 §2; Options by field §13; updates D28 |
| D48 | **Database in three tiers by growth** (`docs/DATABASE.md`): *Business* (`crm`, `cms`) kept forever; *Interactions* (`agents`, `automation`) with monthly partitions on append-only logs, nightly batched pruning of plumbing tables and archives to R2; *Telemetry* (Phoenix, SigNoz) on their own stores with 30–90 day retention. Each logical database keeps its own DSN, so any one can move to its own server by changing one setting. PgBouncer (transaction mode) in front; pools 5 + 5, worker concurrency 3, `max_connections` 200 | Server plan v2 §2, Options by field §10; Hamthan 16 Nov |
| D49 | **Hosting: one dedicated server with Coolify.** Compose stacks in `infra/server/`; image `linux/amd64`; Cloudflare → SafeLine → Coolify proxy → services; WAL-G + R2 off-site backups and a monthly restore test. Terraform and the AWS workflows are deleted | Server plan v2 §2, §5; Hamthan 16 Nov |
| D50 | **Eight similar projects later: design only now** (`docs/MULTI_PROJECT.md`): shared heavy services (speech, embeddings, LiveKit, Postgres, monitoring) once per server, one app stack per project, versioned `kx-*` packages. Built now: one project, but database names already carry the company prefix (`pack_crm` …) | Hamthan 16 Nov |
| D51 | **Claude request parameters:** no `temperature` / `top_p` / `top_k` (Sonnet 5.5 and Haiku 5.5 reject non-default values with HTTP 400); effort and token limits set per tier. A refusal (`stop_reason=refusal`) gives the safe reply + hand-over; server-side refusal fallbacks stay off (a fallback model has not passed the negotiation suite, D27) | Claude API docs; bench R1 |
| D52 | **Voice by mode:** paid = Deepgram → faster-whisper and ElevenLabs → Piper inside one call (LiveKit fallback adapters); free = faster-whisper + Piper. Tamil uses ElevenLabs (also in free mode, when its key is set), else text chat; Sinhala is always text chat. Free-mode calls at once are capped (default 3), above the cap the visitor gets chat. The speech server is its own image (`services/speech/`) | Server plan v2 §2; Options by field §2–§3; Hamthan 16 Nov; replaces D36 |
| D53 | **Firewall and bots:** Cloudflare (DNS, proxy) → SafeLine WAF → app; CrowdSec bans from logs (Cloudflare bouncer for proxied traffic, firewall bouncer for direct ports); Turnstile on website chat; webhook paths (Meta, Telnyx) are allow-listed but rate limited and signature-checked; the client IP comes from `CF-Connecting-IP` only when configured | Server plan v2 §2; Options by field §17; replaces D39 |

### Work packages (16 Nov)

Docs first, then code. One commit series per WP, with the § / D reference.

| WP | What | Main files |
|---|---|---|
| WP-V2-01 | Decision documents (this section, OPEN_QUESTIONS, platform masters, SRS §4/§22 hosting lines, CLAUDE.md, CONTRACTS.md) | `docs/`, `CLAUDE.md`, `CONTRACTS.md` |
| WP-V2-02 | Reference documents: `ARCHITECTURE.md`, `DATABASE.md`, `PLATFORM_MODES.md`, `MULTI_PROJECT.md`, `LOCAL_DEV.md`, `infra/server/README.md`; README, DEPLOYMENT, REUSE_CATALOGUE, HANDOFF_CONTEXT, DELIVERY | `docs/` |
| WP-V2-03 | Go-live fixes from the bench: Claude parameters (D51), WhatsApp 500 on a bad number, BGE-M3 + model filter + timeouts (D47), pool sizes | `kx/agents/models.py`, `kx/automation/channels/whatsapp.py`, `arifaa_pack/adapters.py`, `kx/agents/knowledge.py` |
| WP-V2-04 | Platform modes core (D41), ops alerts on switches, `kx platform show`; Parameter Store removed | `kx/core/platform.py`, `kx/core/settings.py`, `arifaa_pack/container.py` |
| WP-V2-05 | R2 storage (D45) | `kx/automation/media.py`, `kx/crm/pdf.py` |
| WP-V2-06 | Email: Stalwart JMAP + SES backup + bounce reports (D44); Postmark, SNS removed | `kx/automation/channels/stalwart.py`, `feedback.py`, `jobs.py` |
| WP-V2-07 | Calls: Telnyx TeXML (D43); Twilio removed | `kx/automation/channels/calls.py`, `alerts.py`, `webhooks.py` |
| WP-V2-08 | Voice: platform-mode plugins, free speech server, voice-worker fixes (D52) | `services/speech/`, `arifaa_pack/voice_worker.py`, `kx/agents/voice.py` |
| WP-V2-09 | Monitoring: OpenTelemetry metrics, SigNoz, heartbeat (D46); Sentry / Better Stack / CloudWatch removed | `kx/core/metrics.py`, `arifaa_pack/telemetry.py`, `arifaa_pack/api.py` |
| WP-V2-10 | Database tiers (D48): PgBouncer mode, prefixes, partitions, pruning, archive | `kx/core/db.py`, `kx/core/retention.py`, migrations |
| WP-V2-11 | Server stacks and runbooks (D49, D53) | `infra/server/` |
| WP-V2-12 | Local development compose with profiles (7 GB laptop) | `docker-compose.yml`, `docs/LOCAL_DEV.md` |
| WP-V2-13 | Remove AWS Terraform and workflows; amd64 image | `infra/`, `.github/workflows/`, `Dockerfile` |
| WP-V2-14 | `.env.example`, frontend docs, removed-vendor guard test, full lint + test | `.env.example`, `tests/` |

---

## 11 Nov plan

### What changed since 5 Nov

**In the SRS:**

| § | Change |
|---|---|
| §7 | SEO, AEO and GEO fields are editable in the CMS. Product and production information is public in the CMS and **mirrored to the CRM by product code when published**. CMS is the master for descriptive content; the CRM for prices, MOQ and the default delivery time. Prices never reach the CMS, the website, the AI's knowledge or structured data |
| §7A | New section: the CRM flow per stage and per role (salesperson, manager, MD, admin). New items: an AI summary at the top of the deal page; the manager can **reopen** a LOST deal; a team board; a kill switch in the admin flow |
| §8.1 | New: the final STOP (opt-out) flow, after a 3-agent review |
| §8.2 | New: confirming the buyer's phone number silently (Verified / Likely / Reachable / Not reached), no OTP; quotes only to Reachable or better |
| §11.1 | New: the backend builds every quote with fixed rules; the AI never calculates or types a price |
| §14 | New: after handover the salesperson continues on the same company number from the CRM inbox; click-to-call through the company number; record when a personal contact is shared |
| §15 | Payment: confirming the advance is required, recording its details is optional. WON/LOST is mandatory; the expected re-order date is optional |
| §16 | New table: which channel is used when (call or text) |
| §19 | New: international channels summary (v1 WhatsApp, email, web; v1.5 SMS fallback; v2 WeChat, LINE, Telegram, Viber) |

**In the platform docs (11 Nov):**

| Field | Before | Now |
|---|---|---|
| Background jobs | Procrastinate (PostgreSQL queue) | **Celery 5.7 + Celery Beat**, Redis broker, long waits in a PostgreSQL schedule table |
| Staff login + MFA | Amazon Cognito | **In-house** (FastAPI): Argon2 passwords, RS256 tokens, TOTP authenticator code, email code fallback |
| Monitoring | Sentry + UptimeRobot + CloudWatch Container Insights | **Better Stack** (uptime, status page, errors via the Sentry SDK, logs, on-call) |
| AI tracing | Langfuse | **Arize Phoenix** self-hosted in the UAE |
| AI route | Bedrock primary, Anthropic API fallback | **Claude API direct**: Sonnet 5.5 on money turns, Haiku 5.5 on the rest |
| Knowledge search | Titan Text Embeddings v2 (not offered in me-central-1) | **Qwen3-Embedding-0.6B** in a UAE container, PostgreSQL full-text fallback |
| Cache + broker | ElastiCache Valkey | **ElastiCache Redis OSS**, 2 nodes, `noeviction` |
| Staff alert calls | Twilio | **Telnyx**, Twilio as backup |
| Email | SES send, SES Mail Manager inbound | **SES Essentials** send, **Postmark** for buyer replies and as send backup, Gmail API as last email route |
| Voice | ElevenLabs / Sarvam / Azure by language | **Deepgram Nova-3** STT (backup Soniox), **ElevenLabs Flash** TTS (backup Google Chirp 3 HD), LiveKit Cloud; Sinhala stays text-only |
| Quote PDF | WeasyPrint | **Headless Chromium** for Arabic, WeasyPrint backup |
| Every field | one vendor | **primary → backup → last resort**, automatic switch, circuit breaker, on-call alert |

### Decisions (11 Nov)

D1–D16 (5 Nov, below) still hold unless a row here replaces them.

| # | Decision | Source |
|---|---|---|
| D17 | The backend builds every quote (tier, list price, FX, VAT, specs, delivery time, terms, version, PDF). The AI collects inputs and writes only the covering message. A line the system can't price is left empty and marked Pricing needed for the salesperson | §11.1 |
| D18 | STOP: rules match first (whole-message keywords, clear phrases); stop at once on every channel through one suppression list; cancel queued follow-ups, reminders and templates; one plain confirmation within 5 minutes in the buyer's language (no apology, button, question, offer or promotion); reasons only when the buyer volunteers them; `optout.ask_reason` = off; channel-specific stops ("use email instead" stops WhatsApp only, "don't call me" stops calls only); the AI may add a stop, never remove one; unclear message pauses follow-ups and either asks one question (buyer replied in the last 10 minutes; no answer in 24 h = opt-out) or goes to the salesperson; CRM shows an Opted out badge, notifies the salesperson and manager, proposes LOST (reason *Opted out* or the buyer's own); a permanent log; one-click email unsubscribe with an optional reason; on voice "stop" means "wait" | §8.1 |
| D19 | Phone confirmation is silent: format check, then the Requirement Summary PDF is the "useful thing" that proves the number. Status from WhatsApp signals: Delivered = Reachable, Read = Likely, buyer reply = Verified, failed = Not reached. No OTP, no "verify your identity", no test call. Quotes go only to Reachable or better; for Not reached the salesperson decides | §8.2, §6.5 |
| D20 | The salesperson confirms the advance; amount, currency, method, reference, date and proof are optional. Marking WON or LOST is mandatory; the expected re-order date is optional | §15, §5 steps 12–13 |
| D21 | After handover the salesperson replies from the CRM inbox on the same regional company number and in the same chat, signed with their name; email from the company address with the salesperson's name; click-to-call from the CRM through the company number; the CRM records when a personal contact was shared | §14 |
| D22 | CMS is the master for descriptive content; the CRM for prices, MOQ and default delivery time. Publishing in the CMS mirrors the product record to the CRM by product code and re-indexes the AI's knowledge. Prices never appear in the CMS, the website, the AI's knowledge or structured data | §7 |
| D23 | The deal page shows a short AI summary of the conversation at the top | §7A.2 |
| D24 | The manager can reopen a LOST deal | §7A.1 |
| D25 | Background jobs: Celery 5.7 + Celery Beat with Redis as broker. Exactly one Beat. Long waits (day 2/5/10 follow-ups, the 30/60/120-minute ladder) live in a PostgreSQL schedule table that Beat sweeps every 30 s, never as long Celery ETAs. Tasks are idempotent with `acks_late`; one event loop per worker process; Redis `noeviction` | Recommendation, Platform contest R4 |
| D26 | Staff login is in-house: Argon2 passwords, short-lived RS256 tokens, TOTP authenticator code as the main second step, email code only as fallback and recovery, rate limits, lockout, audit log; a break-glass admin account with no MFA bypass whose every use is alerted | Recommendation, Fallback plan |
| D27 | AI: Claude Sonnet 5.5 on money turns, Haiku 5.5 on the rest, called directly through the Claude API (Bedrock dropped). Backups (Gemini 3.8 Flash / 3.5 Flash-Lite) only after they pass the 50-scenario suite in all five languages **and** Arifa agrees: off until then. Last resort: a safe reply ("Let me check with our sales team") and hand-over. Switch when there is no first word in 8 s, on 5xx/429, or when the price guard rejects twice | Recommendation, Fallback plan |
| D28 | Knowledge search: Qwen3-Embedding-0.6B (1,024 dimensions, so `vector(1024)` stays) in our UAE container, plus PostgreSQL full-text; full-text only when the embedding call fails or takes > 2 s. A model change means re-indexing. Retrieval test in en/ar/hi/ta/zh/si before go-live; bge-m3 is the runner-up | Recommendation, Platform contest R4 |
| D29 | Monitoring: Better Stack for uptime, status page, errors (Sentry SDK), logs and on-call; logs are PII-masked before they leave AWS; Phoenix in the UAE for AI traces; CloudWatch Database Insights for SQL; AWS Budgets for bills | Recommendation |
| D30 | Every field has primary → backup → last resort. One adapter per field; the vendor order comes from Parameter Store (changes without a deploy); a circuit breaker skips a vendor for 5 minutes after 3 failures in 60 seconds, then one test call decides; every switch writes a metric and pages on-call; the admin can force a vendor on or off. The price guard, approval gate, consent and quiet hours run after every switch | Fallback plan |
| D31 | Staff alert calls and buyer call-backs: Telnyx → Twilio → WhatsApp + push → next person on the ladder; switch if not connected in 30 s | Fallback plan |
| D32 | A WhatsApp quote not delivered in 10 minutes goes by email with the same approved quote (its own single-use grant for the email channel) → SMS (v1.5) → the salesperson is told to call | Fallback plan |
| D33 | Email: SES → Postmark → Gmail API from the salesperson's Workspace mailbox → WhatsApp → the salesperson is told. Buyer replies: Postmark inbound → Gmail API | Fallback plan, Options by field §8 |
| D34 | Spend caps per company per day (AI, voice minutes, WhatsApp templates): alert at 80 %, degrade to text-only or hand-over at 100 % | Recommendation, Fallback plan |
| D35 | Kill-switch ladder: stop marketing sends → stop outbound calls → text only → hold and hand everything to staff | Fallback plan |
| D36 | Voice: Deepgram Nova-3 → Soniox → website text chat; ElevenLabs Flash → Google Chirp 3 HD → text on screen; LiveKit Cloud → text chat → WhatsApp click-to-chat. Sinhala is text-only | Fallback plan, Platform contest R1 |
| D37 | Quote PDFs: headless Chromium on the worker, WeasyPrint as backup | Recommendation |
| D38 | FX: CBUAE daily rates; the manager can override | Recommendation, §10 |
| D39 | Public website endpoints: AWS WAF + Cloudflare Turnstile + app rate limits + the daily AI spend cap | Recommendation |
| D40 | Cache, rate limits, kill switches and the Celery broker: ElastiCache Redis OSS, 2 nodes in 2 zones, `noeviction` | Recommendation, Options by field §11 |

### Work packages

Work in this order. Each WP is one commit with its § / D references. Run the narrowest tests first, then `make lint && make test`.

#### WP13. Documents
All `.md` files point at the SRS and the platform docs; CLAUDE.md, CONTRACTS.md, REUSE_CATALOGUE.md, HANDOFF_CONTEXT.md, DEPLOYMENT.md, DELIVERY.md, README.md, `infra/README.md` and `db/README.md` describe the 11 Nov stack. New questions go to `docs/OPEN_QUESTIONS.md` (#40 on).

#### WP14. STOP flow (§8.1, D18)
- **Pure matcher** `kx.automation.optout`: whole-message keywords (EN, AR, HI) and clear phrases → `opt_out`; a keyword inside a sentence → `context` (the AI reads it); phrases that stop one channel ("use email instead" → WhatsApp, "don't call me" → calls). Returns the scope, the matched rule id and the language of the matched word.
- **Suppression by scope** (`all`, `whatsapp`, `email`, `phone`): `OutboundService` checks it before every message and the voice caller before every call.
- **Stop at once:** cancel queued outbound messages and scheduled follow-ups, reminders and templates for that recipient.
- **One plain confirmation** within 5 minutes, free-form inside the 24-hour window, in the buyer's language; the text says only that the stop is done, what stops and how to restart.
- **Event** `consent.opted_out.v1` (`OptedOut`: contact/deal/thread refs, address hash, scope, source rule | ai | unsubscribe_link | salesperson | no_answer, exact words, volunteered reason, decided_by). Raised by automation (rules, unsubscribe link) or the CRM (AI tool, salesperson).
- **Permanent log** `automation.opt_out_log`: exact words, time (UTC), channel, rule or AI version, what stopped, confirmation text and when it was sent.
- **CRM:** Opted out badge (`deals.opted_out_at`, `ai_paused_reason='opt_out'`), salesperson + manager notified, LOST proposed as a task with reason `opted_out` (new LOST reason) or the buyer's own words; consent row; dashboard counts.
- **AI:** tools `record_opt_out(scope, words)` (add only) and `flag_unclear_stop`; prompt rules for §8.1 step 6; unclear → follow-ups paused, one question if the buyer replied in the last 10 minutes (no answer in 24 h → opt-out job), otherwise a review task for the salesperson.
- **Email:** `List-Unsubscribe` + `List-Unsubscribe-Post: List-Unsubscribe=One-Click` on every buyer email; `GET/POST /v1/public/unsubscribe` with an optional reason.
- **Voice:** "stop" makes the AI stop talking and listen; the call ends only on a clear request; calls suppressed only after a clear "don't call me".
- **Settings:** `optout.ask_reason=false`, `optout.live_minutes=10`, `optout.unclear_answer_hours=24`.

**Done when:** the matcher spec covers every example sentence in §8.1; a DB test shows STOP suppresses all channels, cancels the queued follow-up, sends exactly one confirmation and proposes LOST; "use email instead" stops WhatsApp only.

#### WP15. Phone confirmation and Requirement Summary (§8.2, §6.5, §17 row 1, D19)
- `contacts.phone_status` (`unknown`, `not_reached`, `reachable`, `likely`, `verified`), `phone_status_at`, `whatsapp_profile_name`.
- Format check on entry (`normalise_phone` + mobile/landline from `phonenumbers`); a landline or invalid number makes the AI ask the §8.2 question.
- On QUALIFIED the CRM renders the Requirement Summary PDF (no prices; EN/AR) and raises `deal.requirement_summary.v1`; automation sends it as an approved WhatsApp template with a document header (the buyer has not written to WhatsApp yet), else by email.
- Automation's `MESSAGE_STATUS` carries `contact_ref`, `deal_ref`, `channel`; the CRM moves the phone status forward only (delivered → reachable, read → likely, inbound reply → verified, failed → not_reached).
- Not reached: the AI says the §8.2 line if the buyer is still on the website; otherwise email, else a task for the salesperson.
- Issuing a WhatsApp quote to a `not_reached` or `unknown` number opens a task for the salesperson instead of sending.

**Done when:** status transitions are unit-tested, and a DB test shows a failed summary blocks the WhatsApp quote and raises the task.

#### WP16. Payment, WON, LOST reopen, visibility (§15, §7A.1, §3, D20, D24)
- `PaymentIn`: only the confirmation is required; every detail optional. `WonIn.expected_reorder_date` optional.
- `POST /v1/deals/{id}/reopen` (manager, MFA, reason required): LOST → the stage it was lost from (stage history).
- §3 visibility on every list: a salesperson sees their own deals (and the ones they cover) in `/deals`, timeline, quotes, approvals, change requests and orders.
- `POST /v1/deals/{id}/personal-contact-shared` records that a salesperson shared a personal number (§14 practical notes).

#### WP17. Salesperson inbox reply, click-to-call, AI summary (§14, §7A.2, D21, D23)
- `POST /v1/deals/{id}/messages` (salesperson/manager/MD on takeover): a text reply to the buyer on the same channel and number, signed "— Name, Arifa Pack". Goes out through `OutboundService` (consent, opt-out and the 24-hour rule apply; after 24 h only the approved re-open template). Never a price outside an approved quote: the price guard runs on staff text too and blocks unapproved amounts.
- `POST /v1/deals/{id}/call`: click-to-call through the company number (Telnyx → Twilio bridge to the salesperson's phone), logged on the deal.
- Agents publish `conversation.summary_updated.v1`; the CRM stores `deals.ai_summary` and returns it on the deal page.

#### WP18. CMS publish → AI knowledge + CRM product mirror (§7, D22)
- `POST /webhooks/cms` (HMAC-signed by Payload): on publish of a product, page, FAQ, certificate or tour hotspot, raise `kb.content_published.v1` (re-index) and, for products, `catalogue.product_published.v1`.
- The CRM upserts the product by **code** (`products.sku`): name, category, specification, `cms_product_id`, published flag. Prices, tiers, MOQ for quoting and the default delivery time are never overwritten from the CMS.
- Unpublish removes the KB document and marks the CRM product not published (still quotable only if the manager says so).

#### WP19. Website API (§6)
- `POST /v1/public/sessions`: start a website conversation (Turnstile token, language, consent for voice). Returns a short-lived session token.
- `POST /v1/public/sessions/{id}/messages`: one chat turn through the same `AgentService` (same router, graphs and price guard).
- `POST /v1/public/sessions/{id}/voice`: a LiveKit room token whose participant metadata carries `deal_ref` and `language` (what the voice worker expects).
- `POST /v1/public/sessions/{id}/identify`: phone or email the buyer typed; returning buyers are recognised only after they identify themselves (§6.7).
- Rate-limited per IP and session; refused when the daily AI spend cap is reached (D34, D39).

#### WP20. Background jobs on Celery (D25)
- `kx.core.jobs`: the PostgreSQL schedule store (`automation.scheduled_jobs`): enqueue with a de-duplication key, replace, pull forward, cancel by key prefix, claim (one at a time per lock key, oldest first), finish, retry with back-off, recover stuck rows.
- `kx.automation.jobs.JobScheduler` implements `TaskScheduler` over that store; the procrastinate scheduler, blueprints and dependency are removed from the packages.
- `arifaa_pack.celery_app`: one Celery app; task `kx.run_job(job_id)`; Beat entries: sweep due rows (30 s), relay the three outboxes (5 s), approval sweep (5 min), requeue lost outbound (5 min), lost proposals, price staleness/quote expiry, retention, FX update (daily), daily summary (daily), Beat heartbeat to Better Stack (30 s). Scraper only with `features.scraping`.
- `kx worker` → `celery worker` (one event loop per process via `worker_process_init`); new `kx beat`.
- `acks_late=True`, `task_reject_on_worker_lost=True`, prefetch 1, visibility timeout above the longest task. Every task body checks its idempotency key before sending or calling.

**Done when:** a DB test shows two claims of one job run it once, lock keys run in order, a stuck row is recovered, and the ladder / follow-up / delivery paths run through the store.

#### WP21. In-house staff login + MFA (D26)
- `crm.staff_credentials` (Argon2id hash, encrypted TOTP secret, failed attempts, lock-out), `crm.auth_sessions` (hashed refresh tokens), `crm.auth_challenges` (hashed one-time codes).
- `POST /v1/auth/login` → challenge; `POST /v1/auth/mfa/totp` or `/mfa/email/send` + `/mfa/email/verify` → RS256 access token (15 min, `amr` records the factor) + refresh cookie; `/auth/refresh`, `/auth/logout`, `/auth/me`; TOTP enrolment; password change; recovery by email code.
- `GET /.well-known/jwks.json`; `kx.core.auth` verifies with the local public key (no Cognito).
- Admin: create user (temporary password), reset MFA, unlock, deactivate.
- 5 failed attempts → 15-minute lock; every login, failure, lock and MFA change is audited; a break-glass login raises `staff.notice_requested.v1` to the MD and the manager (the generic staff notice).
- Remove the Cognito Terraform module and `users.cognito_sub` use.

#### WP22. AI route, fallbacks and spend caps (D27, D30, D34)
- Drop `BedrockProvider`. `ModelRouter` tiers: FAST = Haiku 5.5, SMART = Sonnet 5.5 (money turns: negotiation, quotation, deal closer, commercial check).
- `kx.core.fallback`: `CircuitBreaker` (3 failures / 60 s → open 5 min → one trial call) and `FallbackChain` (ordered vendors, trigger time, alert hook).
- Gemini backups: wiring only, off until the suite passes and Arifa agrees (`KX_APP_GEMINI_FALLBACK_ENABLED=false`).
- Safe reply + hand-over when every model fails or the price guard rejects twice.
- Per-model price table (official prices in `Official sources.md`) fills `agent_runs.cost_usd`.
- Daily spend caps in Redis per company: AI USD, voice minutes, WhatsApp templates; 80 % alert, 100 % degrade (AI: hand-over; voice: text chat; templates: hold for staff).

#### WP23. Knowledge search on Qwen3 (D28)
- `HttpEmbedder` (OpenAI-compatible `/v1/embeddings`) for the Qwen3-Embedding-0.6B container; query instruction prefix per the model card; 1,024 dims; normalised.
- `KnowledgeBase.search` falls back to full-text + trigram when embedding fails or takes > 2 s.
- Remove the Titan embedder.

#### WP24. Observability (D29)
- Sentry SDK pointed at Better Stack (`KX_APP_SENTRY_DSN`), PII scrubbed in `before_send`.
- Beat heartbeat URL (`KX_APP_BETTERSTACK_HEARTBEAT_URL`), pinged every 30 s.
- Phoenix tracing over OpenTelemetry (OTLP HTTP), PII-masked; replaces Langfuse.
- CloudWatch metrics via Embedded Metric Format log lines: `Kairox/VendorSwitch`, `Kairox/QueueLagSeconds`, `Kairox/SpendCap`.

#### WP25. Channel fallbacks and kill-switch ladder (D30–D33, D35)
- Calls: `TelnyxCaller` (TeXML, same IVR) → `TwilioCaller`; webhooks for both; switch when not connected in 30 s.
- Email: `PostmarkSender` as send backup; `POST /webhooks/postmark/inbound` for buyer replies (Basic auth); Gmail API adapter slot (off until Workspace mailboxes are confirmed).
- WhatsApp quote not delivered in 10 minutes → the CRM issues an email grant for the same approved version → sent by email; else a task "call the buyer".
- Vendor order per field from Parameter Store (`/kx/<env>/<company>/vendors/<field>`), cached 60 s; admin override API.
- Kill-switch levels: `marketing` → `calls` → `text_only` → `hold`; admin API `PUT /v1/admin/kill-switch`.

#### WP26. Voice, PDF renderer, FX (D36–D38)
- Voice routing: Deepgram Nova-3 STT and ElevenLabs Flash TTS for en/ar/hi/ta/zh with LiveKit fallback adapters to Soniox and Google Chirp 3 HD; Sinhala text-only.
- PDF: headless Chromium (`--print-to-pdf`) with WeasyPrint fallback.
- FX: daily CBUAE rate job writing `crm.fx_rates`; a manager override always wins.

#### WP27. Infra, CI, compose, Dockerfile
- ECS services: `api` ×2, `worker` (Celery), `beat` (exactly 1), `voice`, `cms`, `embeddings` (Qwen3), `otel-collector`, `phoenix`; one-off `kx migrate`.
- ElastiCache Redis OSS with `maxmemory-policy noeviction`; remove the Cognito module; Secrets Manager shells for every vendor in the Recommendation; Parameter Store vendor order.
- Compose: Redis instead of Valkey; CI unchanged (pgvector Postgres).

#### WP28. CRM screens API completion (§20, §21)
Contacts and companies, product catalogue, orders, users and roles, team board, dashboards per role (§21, incl. opt-out counts and reasons), exports (XLSX/CSV/PDF from any list), WhatsApp numbers per region and message templates (admin).

#### WP29. Hand-offs
- `.env.example` with every key the code reads.
- Frontend hand-off docs (from the code's OpenAPI): `docs/frontend/CRM_API.md`, `docs/frontend/CMS.md`, `docs/frontend/WEBSITE_API.md`.

---

## 5 Nov plan (done 6 Nov)

**Status:** the code implemented the 4 Nov rules (309 tests green). On 5 Nov the business flow changed:
- the salesperson approves quotes, not the manager/MD;
- pricing comes from a manual price list, not cost + markup;
- change requests go to the salesperson;
- delivery calculation and scraping move to v2.

This was the work list to update the code. Source of truth then: the 5 Nov SRS (now the SRS above; § numbers are unchanged).

Work in the order below. Each work package (WP) ends with **Done when** checks. Keep the rules in `CONTRACTS.md`:
- packages never import each other;
- shared helpers come from `kx.core`;
- money is Decimal only;
- every state change writes audit + timeline + outbox in one transaction (`kx.crm.services.journal.record`).

### 0. Decisions already made (Hamthan, 5 Nov)

| # | Decision |
|---|---|
| D1 | The assigned **salesperson** approves the 1st quote, every revised quote and the final quote. There is no second check. The manager can approve any deal. The MD approves only deals it has taken over |
| D2 | There is no "sales manager" role, only a **manager**. CRM roles: `md`, `manager`, `admin`, `sales` (= salesperson) |
| D3 | When a buyer pushes below the minimum, the **salesperson is alerted and the manager notified**. A special price below the minimum needs the **manager's** approval (proposed in §26; confirm) |
| D4 | Reminders: WhatsApp at 30 min, alert call at 60 min, escalate to the manager at 120 min, counted in alert hours 08:00–22:00 Asia/Dubai. Each staff user's preferred alert channels are a per-user setting (to confirm with the client) |
| D5 | Assignment: round-robin by default. Optional rules match the buyer's language, country and product category. Away status and a backup person are respected. The manager or MD can reassign. The MD can take over and hand back; during a takeover the salesperson is read-only |
| D6 | Price list: per product and quantity tier, a **list price** (top) and a **minimum** (floor). Optional minimum % per category, which a per-product value overrides. Prices are in AED with daily FX conversion (manager override). VAT 5% for the UAE, 0% for export. Validity 7 days; Incoterm EXW by default |
| D7 | Concessions: at most 3 equal steps from list to minimum. Step 1 can be goodwill; steps 2–3 need a bigger quantity. A first ask below the minimum gets the next step; an insisted ask below the minimum pauses the AI (§12.2) |
| D8 | Custom, printed or unpriced products: the AI collects the requirements, and the salesperson prices them by hand. The AI never negotiates them |
| D9 | The "open to discuss" line goes in the **message only** (not the PDF). It must sound natural and vary |
| D10 | Delivery time on the quote = the product's default ("X–Y working days after advance payment"), editable by the salesperson. No calculation in v1 |
| D11 | Email is sent from one company address. **WhatsApp uses one number per region**. Messages are signed with the assigned salesperson's name |
| D12 | Change requests (product, spec, print, quantity outside the tiers or below MOQ, delivery, samples, other) go to the salesperson. The AI tells the buyer the update comes "within 4 working hours" (setting). A quantity change *inside* the tiers is repriced by the AI, but the revised quote still needs the salesperson's approval |
| D13 | Payment is **never** handled by the AI. After the final quote is approved: alert the salesperson, notify the manager, and the AI stops replying on that deal |
| D14 | v2, switched off in v1: price scraping, cost build-up pricing, the 3/7-day staleness block, delivery-time calculation, the news digest, the ERP link, re-order follow-up and the repeat-sales agent. **Keep that code**, behind flags |
| D15 | v1 stores `expected_reorder_date` at WON (for v2). LOST is proposed 14 days after the last follow-up (setting); the salesperson confirms. *(11 Nov: the re-order date is now optional, D20)* |
| D16 | Effort owner: Hamthan (approvals, change requests, pricing tiers) |

### WP1. Database migration 0002 (Alembic, one revision per DB)

Add `apps/arifaa-pack/src/arifaa_pack/migrations/<db>/versions/0002_v1_flow.py`. **Do not edit the 0001 DDL files.** The baseline is already applied.

**crm**

- **approvals:**
  - `required_role` CHECK becomes `IN ('sales','manager','md')`;
  - **drop** the CHECK `kind <> 'final' OR required_role = 'md'`;
  - add the `kind` value `'special_price'` (manager only);
  - add the column `approver_user_id uuid` (the deal owner at submit time).
- **users:** add
  - `languages text[]`, `countries text[]`, `category_codes text[]`;
  - `is_away bool default false`, `backup_user_id uuid`;
  - `alert_channels text[] default '{whatsapp}'`;
  - `round_robin_seq bigint`.
- **deals:** add
  - `owner_user_id` (if missing);
  - `taken_over_by uuid`, `taken_over_at`;
  - `ai_paused_reason` allows `'handover'` and `'concessions_exhausted'`;
  - `low_ask_count int default 0` per open negotiation.
- **New `product_price_tiers`:**
  - columns: `id`, `product_id`, `min_qty`, `max_qty` (null = no upper limit), `list_price numeric(14,6)`, `min_price numeric(14,6)`, `currency text default 'AED'`, `valid_from timestamptz`, `created_by`;
  - CHECK `min_price <= list_price`;
  - exclusion constraint so a product's tiers never overlap.
- **product_categories:** add `min_discount_pct numeric(5,2)` (the shortcut).
- **products:** add `moq`, `default_lead_days_min`, `default_lead_days_max`, `custom_printing bool`, `is_quotable bool`.
- **New `change_requests`:**
  - columns: `id`, `deal_id`, `quote_version_id`, `type` (CHECK in product/spec/printing/quantity/delivery/samples/payment/other), `buyer_text`, `buyer_text_en`, `fields jsonb` (`[{field, current, requested}]`), `status` (open/in_progress/resolved/declined), `assigned_to`, `sla_due_at`, `resolved_version_id`, `decline_reason`, timestamps.
- **quote_versions:** add `delivery_time_text`, `incoterm`, `delivery_place`, `payment_terms_text`, `fx_rate`, `fx_rate_date`, `signed_by_user_id`.
- **orders:** add `expected_reorder_date date`.
- **contacts:** add a generated `recipient_hash` column plus an index (from the DDL v1.1 list); `source` allows `'phone'`.
- **Settings seed:**
  - `pricing.mode = 'price_list'`;
  - `quote.vat_pct_domestic = 5`, `quote.validity_days = 7`, `quote.default_incoterm = 'EXW'`;
  - `negotiation.max_steps = 3`;
  - `change_request.sla_working_hours = 4`;
  - `approval.reminder_minutes = [30,60,120]`;
  - `approval.md_value_limit_aed = null` (off);
  - `followup.lost_after_days = 14`;
  - `assignment.rules = []`;
  - `features.scraping = false`, `features.delivery_calc = false`, `features.news_digest = false`.

**automation**

- **channel_accounts:** add `region text` and `countries text[]` (WhatsApp number per region).
- **outbound_messages:** add `template_params jsonb`, `contact_ref`, `recipient_hash`; statuses `bounced`, `complained`.
- **call_attempts:** `UNIQUE (approval_ref, attempt_no)`.

**agents**

- **turns:** add `run_id`.
- **conversations:** add `summary_upto_turn`.

**Done when:**
- `kx migrate` runs twice on fresh DBs and on the 0001 state;
- the ORM models in each package match (`kx.crm` / `kx.automation` / `kx.agents` have column-vs-`information_schema` tests; extend them).

### WP2. Pricing: price-list mode (`kx.crm.pricing`)

- **New pure module `pricing/price_list.py`:**
  - `resolve_tier(tiers, qty) -> TierPrice(list_price, min_price, tier_id)`. Raises `LookupError` if no tier fits, which the caller turns into a change request (below MOQ / outside the tiers).
  - `min_price_for(tier, category_min_pct)`: the per-product value wins.
- **Add to `pricing/engine.py`:**
  - `ladder_between(top, floor, steps) -> tuple[Decimal, ...]`: equal steps, 4 dp;
  - an `evaluate_counter` variant that takes that ladder and `low_ask_count`, applying §12.2 rules 1–5.
  - Keep the cost-based functions untouched (they are for v2).
- **`services/pricing.py`:** when `pricing.mode='price_list'`:
  - build the lines from tiers;
  - apply FX (`fx_rates`, manager override) and VAT;
  - skip the staleness block.
- **New spec file `tests/spec/test_spec_price_list.py`, using §11–12 and scenario numbers:**
  - 50,000 pcs at AED 0.25: line 12,500.00, VAT 625.00, total 13,125.00; USD at 3.6725 = 3,403.68;
  - ladder 0.2500 / 0.2433 / 0.2367 / 0.2300;
  - scenario 2: 70,000 × 0.2367 = 16,569.00;
  - first ask below the minimum gets the next step; an insisted ask below the minimum gives NEEDS_MANAGER;
  - moving to a higher tier gives the new list price plus `requires_approval=True`;
  - a property test that no offer ever goes below the minimum.

**Done when:** the new spec file passes, the old pricing specs still pass, and `NegotiationState` is no longer built from cost when in price-list mode.

### WP3. Approvals: salesperson approves (`services/approvals.py`, `quotes.py`, `issuing.py`)

- **Approver:**
  - the approver is the deal owner (role `sales`), for every quote: `initial`, `revision`, `counter` and `final`;
  - `manager` may approve any deal;
  - `md` only when `deals.taken_over_by` is set.
- **Takeover:** while a takeover is active, the owner is read-only (403).
- **MFA:** keep `require_human_mfa`.
- **Special price:** a `special_price` approval (manager) is created when the salesperson proposes below the minimum. Once approved, it unlocks that price for that quote version only.
- **Events:** the `QuoteSubmitted` payload's `approver` = the salesperson and `escalate_to` = the manager. Due time = `Window.add_open_minutes` with the 120-minute escalation.
- **Remove** the MD value-limit routing, unless `approval.md_value_limit_aed` is set.
- **Grants:** still issue a single-use grant for every buyer-facing version (unchanged).

**Done when:** the DB tests prove each of these:
- the salesperson can approve their own deal, but not another salesperson's;
- the manager can approve any deal;
- the MD can approve only after a takeover;
- the owner can't edit during a takeover;
- a final quote approved by the salesperson issues a grant.

### WP4. Change requests (CRM + contracts + agents)

- **kx.core:**
  - add event `CHANGE_REQUESTED = "change_request.raised.v1"` with an `event_payloads.ChangeRequested` payload (deal_ref, change_request_ref, type, assigned_to as StaffContact, sla_due_at);
  - add `CrmGateway.raise_change_request(deal_id, type, buyer_text, fields, actor) -> UUID`.
- **kx.crm:** `services/change_requests.py`:
  - raise (with SLA due = now + 4 working hours, using `Window`), start, resolve (link the new version), decline;
  - API routes for the inbox and the card;
  - `GatewayImpl` implements the new method.
- **kx.agents:**
  - new tool `raise_change_request`;
  - in `graphs/deal.py`, any non-price intent, or a quantity outside the tiers, raises a change request;
  - the reply uses the fixed phrase "within 4 working hours" (a PromptStore phrase).
- **kx.automation:** a handler for `CHANGE_REQUESTED` sends an immediate alert and starts the reminder ladder (reuse `ApprovalLadder` with a different script).

**Done when:**
- a deal-graph test shows "Can you deliver in 5 days?" creates a change request, with no promise in the reply;
- a CRM DB test covers the full lifecycle;
- an automation test shows the alert and the reminders.

### WP5. Assignment, away/backup, reassign, takeover (`services/assignment.py`, new)

- Round-robin over active `sales` users, with an atomic `round_robin_seq` update.
- Optional rules from `assignment.rules`, checked in order: language → country → category.
- An away user is replaced by their backup, or by the manager if there is no backup.
- A returning customer goes to their last owner, if that person is active.
- APIs: reassign (manager/MD), take over and hand back (MD). Both users are notified, and the timeline records it.

**Done when:** unit tests cover the rule order and away/backup, and DB tests cover concurrent assignment with no duplicates.

### WP6. Sending the quote (automation + CRM PDF)

- Pick the WhatsApp `channel_account` by the buyer's country (`countries[]`), with a fallback to the default region. Email always uses the company address.
- Messages are signed with the owner's name.
- The "open to discuss" line is written by the AI communication agent **in the message**, in natural, varied wording. It is not in the PDF.
- The PDF template shows `delivery_time_text`, Incoterm and place, payment terms, and the FX rate line, with Arabic laid out right-to-left.

**Done when:** a regional routing test passes, the PDF renders the new fields, and the guard still passes on the generated message.

### WP7. Final quote and handover

- On final approval:
  - emit `QUOTE_APPROVED` (kind final, as today);
  - the automation handler also alerts the salesperson and notifies the manager;
  - the CRM sets `ai_enabled=false` with `ai_paused_reason='handover'`.
- After handover the orchestrator routes buyer messages to the salesperson inbox. Outside working hours it sends the automatic acknowledgement only.

**Done when:** an orchestrator test shows a handed-over deal gets no AI reply, and an automation test covers the alert plus the manager notice.

### WP8. Payment, WON, LOST, order

- **WON:** requires a recorded advance and `expected_reorder_date`. *(11 Nov: the re-order date is optional, D20.)*
- **LOST:** reasons per §15.
- **Job:** proposes LOST `followup.lost_after_days` after the last follow-up (a task for the salesperson; never automatic).

### WP9. Switch v2 features off (flags; keep the code)

| Feature | What changes in v1 |
|---|---|
| `features.scraping=false` | Do not register the scraper periodic job |
| Staleness check | Skipped in price-list mode |
| `features.delivery_calc=false` | The agents' `delivery_estimate` tool returns the product's default lead-time text |
| `features.news_digest=false` | Off |
| Repeat-sales agent | Disabled in the registry |

**Done when:** the app boots with the flags off, and the smoke test asserts the scraper job is not registered.

### WP10. AI behaviour updates (`kx.agents`)

- **Negotiator prompt and policy:** §12.2 rules. Never payment, never longer delivery, never reveal the minimum. Custom items always go to the salesperson.
- **Fixed phrases:**
  - below the minimum: "within 60 minutes in working hours";
  - change requests: "within 4 working hours";
  - after handover: an outside-hours acknowledgement.
- **Evals:** replace the YAML scenarios with the 10 scenarios in §23, plus the trick-prompt cases.

### WP11. CRM API for the screens (§20)

Endpoints:
- inbox (approvals, change requests, below-minimum, handed-over messages, with SLA timers);
- change-request card actions;
- price list CRUD;
- **Excel import/export** (`openpyxl`; validate min ≤ list, no overlaps, codes exist; return row errors);
- stock table;
- reassign, takeover and hand-back;
- settings for the new keys.

Keep the routers thin; the services do the work.

### WP12. Notifications matrix (§17)

Map every row of §17 to an event plus a handler in `kx.automation.handlers`, and add a table-driven test: event → expected recipients and channels.

### Final check (same as CI)

`make lint && make test`:
- ruff, format, mypy strict and import-linter clean;
- all tests green on Postgres;
- then update `docs/DELIVERY.md` with the new test counts.
