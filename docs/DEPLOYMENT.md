# Deployment guide (16 Nov v2: dedicated server + Coolify)

Server stacks and runbooks: `infra/server/README.md`. Platforms, paid / free version and costs: the server plan v2 and `docs/Arifaa Platform — Platforms, Pricing & Fallbacks/Recommendation.md`. The switch: `docs/PLATFORM_MODES.md`. Databases: `docs/DATABASE.md`. This page is the run order.

## Topology (one Namecheap dedicated server per environment)

| Runs | Compose stack | What |
|---|---|---|
| `api` | `compose.app.yml` | CRM API `/v1`, staff login `/v1/auth`, website API `/v1/public`, webhooks `/webhooks/{whatsapp,telnyx,cms}`, `/.well-known/jwks.json`, `/healthz`, `/healthz/beat` |
| `worker` | `compose.app.yml` | Celery worker (concurrency 3): deliveries, ladder steps, follow-ups, event handlers, ingest, mail poll, retention |
| `beat` | `compose.app.yml`, **exactly 1** | sweeps `automation.scheduled_jobs` every 30 s, relays outboxes every 5 s, periodic jobs, heartbeat to Redis |
| `voice` | `compose.app.yml` | LiveKit agent worker (paid: Deepgram / ElevenLabs → free fallback; free: speech server) |
| `redis` | `compose.app.yml` | Celery broker, rate limits, kill switches, spend counters (`noeviction`, AOF) |
| `cms` | `compose.app.yml` | Payload CMS (Node), database `pack_cms` |
| `web`, `crm-web` | `compose.app.yml` profile `selfhost-web` | only in the free hosting version (otherwise Vercel Pro) |
| `postgres`, `pgbouncer` | `compose.data.yml` | PostgreSQL 18 + pgvector, WAL-G to R2; PgBouncer (transaction mode) |
| `phoenix-postgres` | `compose.obs.yml` | Phoenix's own database (Telemetry tier) |
| `embeddings` | `compose.ai.yml` | TEI `cpu-1.8`: BGE-M3 (or Qwen3), private |
| `speech` | `compose.ai.yml` | faster-whisper Small + Piper (free STT / TTS), private |
| `livekit` | `compose.voice.yml` | LiveKit server (media ports open) |
| `stalwart` | `compose.mail.yml` | mail server: MX, JMAP for the app, relays out through SES |
| `phoenix`, `signoz` | `compose.obs.yml` | AI audit trail; logs / metrics / traces + alerts |
| `safeline`, `crowdsec` | `compose.edge.yml` | WAF on 80/443 in front of Coolify's proxy; IP bans |

Outside the server (processors Arifa must approve): Anthropic (Claude), Meta (WhatsApp), Telnyx, Amazon SES, Cloudflare (DNS, R2, Turnstile), and in the paid version Deepgram, ElevenLabs, Vercel; Google only if Gemini standby is approved. **Server location: USA (OPEN #69).**

## First deploy (once per server)

1. Order the server (v2 §5), Ubuntu 24.04, reverse DNS for the public IP; harden SSH (keys only, admin IPs).
2. Install Coolify (`infra/server/README.md` §2). Create a project *pack* with environments *staging* / *production*.
3. Cloudflare: DNS records (`api.`, `cms.`, `voice.` DNS-only for media, `mail.` + MX), origin certificate for SafeLine; R2 buckets + an access key.
4. Deploy `compose.data.yml`; run `infra/server/postgres/db_bootstrap.sh` (prefix `pack_`): roles, databases, extensions, per-role `search_path` / `statement_timeout`. Confirm WAL-G pushes to R2.
5. Deploy `compose.ai.yml`, `compose.voice.yml`, `compose.mail.yml`, `compose.obs.yml`, `compose.edge.yml`. Finish Stalwart's setup wizard: domain, DKIM, SES smarthost, the sending account (`sales@`, the JMAP login of the app), sending limits.
6. Fill the app environment in Coolify (below; secrets as *encrypted*), including `KX_PLATFORM_PLAN`.
7. Deploy `compose.app.yml`: `migrate` runs first (Alembic for every DB + checkpointer tables), then api / worker / beat / voice.
8. `kx platform show` in the api terminal: check the resolved matrix.
9. Create staff: `kx create-user --role md --email … --name …` (one-time password; the person enrols an authenticator app). Create the break-glass admin and store its TOTP seed offline.
10. UptimeRobot: HTTP monitors on `https://api.<domain>/healthz` and `/healthz/beat` (2-minute interval).

## Every release

`git tag vX.Y.Z && git push --tags` → CI (ruff, format, mypy strict, import-linter, pytest on pgvector Postgres, amd64 image build, speech image build, `docker compose config` of every stack) → image pushed to GHCR → Coolify deploy webhook → `migrate` → rolling restart (Beat last).

## Local development

`docs/LOCAL_DEV.md` (Docker Desktop on D:, one profile at a time).

## Environment variables

Every key with its default and a comment is in `.env.example`. On the server they are Coolify environment variables (secrets marked encrypted).

| Group | Prefix | Keys |
|---|---|---|
| Platform modes (D41) | `KX_PLATFORM` | `KX_PLATFORM_PLAN`, `KX_PLATFORM__STT`, `__TTS`, `__EMAIL`, `__LLM`, `__CALLS`, `__WHATSAPP`, `__LLM_STANDBY`, `__EMBEDDINGS`, `__AI_TRACE` |
| Core | `KX_` | `ENV`, `COMPANY_CODE`, `COMPANY_TIMEZONE`, `BASE_CURRENCY`, `CRM_DSN`, `AUTOMATION_DSN`, `AGENTS_DSN` (`postgresql+asyncpg://` to PgBouncer), `DB_PGBOUNCER`, `DB_POOL_SIZE`, `DB_MAX_OVERFLOW`, `REDIS_URL`, `LOG_LEVEL`, `LOG_JSON`, `SERVICE_TOKEN_SECRET`, `TRUSTED_CLIENT_IP_HEADER` |
| Storage (R2) | `KX_STORAGE__` | `ENDPOINT_URL`, `REGION`, `ACCESS_KEY_ID`, `SECRET_ACCESS_KEY`, `BUCKET` |
| Retention | `KX_RETENTION__` | `PLUMBING_DAYS`, `FAILED_JOB_DAYS`, `AGENT_RUNS_MONTHS`, `PARTITION_HOT_MONTHS`, `ARCHIVE` |
| Staff login | `KX_` | `JWT_ISSUER`, `JWT_AUDIENCE`, `JWT_PRIVATE_KEY`, `JWT_KEY_ID`, `AUTH__ENCRYPTION_KEY`, `AUTH__*` lifetimes and lockout |
| Channels | `KX_AUTO_` | `PUBLIC_BASE_URL`; `WHATSAPP__*`; `STALWART__*` (url, username, password, from_address, mailbox, bounce_mailbox); `SES__FROM_ADDRESS` (+ `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `KX_AUTO_SES__REGION` for the SES API backup); `TELNYX__*` (api_key, account_sid, from_number, public_key, voice, language, ring_timeout_seconds); `ALERTS__*`, `FOLLOWUP__*`, `RATE_LIMIT__*`, `OPTOUT__*` |
| App | `KX_APP_` | `CORS_ORIGINS`, `CMS_URL`, `CMS_API_KEY`, `CMS_WEBHOOK_SECRET`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `LLM_EFFORT_FAST` / `_SMART` / `_JUDGE`, `EMBEDDING_URL`, `EMBEDDING_QUERY_TIMEOUT_SECONDS`, `EMBEDDING_INDEX_TIMEOUT_SECONDS`, `AI_TRACE_ENDPOINT`, `AI_TRACE_HEADERS`, `PHOENIX_PROJECT`, `OTLP_ENDPOINT`, `OPS_ALERT_EMAILS`, `TURNSTILE_SECRET_KEY`, `LIVEKIT_URL` / `_API_KEY` / `_API_SECRET`, `SPEECH_URL`, `VOICE_MAX_CONCURRENT_FREE`, `CELERY_BROKER_URL`, `WORKER_CONCURRENCY`, `SPEND_*`, `PDF_RENDERER`, `CHROMIUM_PATH` |
| Voice plug-ins | (none) | `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`, `DEEPGRAM_API_KEY`, `ELEVEN_API_KEY` (read by the LiveKit plug-ins themselves) |
| Migrations | | `KX_MIGRATE_<DB>_DSN` (direct to Postgres :5432, not PgBouncer), `KX_DDL_DIR` (set in the image) |

## v1 business settings (crm.settings, seeded by migrations)

Unchanged: changed in the CRM by the manager, MD or admin, not through env vars.

| Group | Keys and defaults |
|---|---|
| Pricing | `pricing.mode=price_list` |
| Quote defaults | `quote.vat_pct_domestic=5`, `quote.vat_pct_export=0`, `quote.validity_days=7`, `quote.default_incoterm=EXW`, `quote.payment_terms_text`, `quote.currency_by_country={}` |
| Negotiation and change requests | `negotiation.max_steps=3`, `negotiation.free_samples_allowed=false`, `change_request.sla_working_hours=4` |
| Approvals | `approval.reminder_minutes=[30,60,120]`, `approval.md_value_limit_aed=null` (off) |
| Follow-ups and assignment | `followup.lost_after_days=14`, `followup.weekend_days_by_country`, `assignment.rules=[]` |
| Notifications | `notifications.order_status_to_buyer=false` |
| Opt-out (§8.1) | `optout.ask_reason=false`, `optout.live_minutes=10`, `optout.unclear_answer_hours=24` |
| v2 features | **off in v1:** `features.scraping=false`, `features.delivery_calc=false`, `features.news_digest=false` |

**Regional WhatsApp numbers:** each is an `automation.channel_accounts` row with `region` and `countries[]`.

## Manual / external steps before go-live

- Arifa approves the **USA server location** (OPEN #69), the processor list and consent text in en / ar / hi / ta / zh; yes or no to **Gemini** standby; Claude API account in Arifa's name with prepaid credit and a monthly spend cap.
- **Meta:** Tech Provider onboarding, a number per region, approved templates (every name in `AutomationSettings.alerts`, the follow-ups, `requirement_summary`, the 24-hour re-open template, EN/AR); webhook `https://api.<domain>/webhooks/whatsapp`.
- **Amazon SES:** domain identity + Easy DKIM, production access, SMTP credentials for Stalwart's relay, an IAM key limited to `ses:SendEmail` for the API backup; email feedback forwarding on (bounce and complaint reports go to the sending address, `sales@`, where the mail poller reads them).
- **Stalwart:** MX, SPF (`include:amazonses.com`), DKIM, DMARC, reverse DNS; sending limits raised (OPEN #71).
- **Telnyx:** account + caller-ID number, API key, the account SID for TeXML, the public key for webhook signatures; live test calls to du and e& (OPEN #70). **TDRA:** no AI call to UAE numbers that did not ask for one.
- **Paid version keys:** Deepgram, ElevenLabs; Vercel Pro project pointing at `https://api.<domain>`.
- **Retrieval test** for BGE-M3 (en / ar / hi / ta / zh / si); **Haiku 5.5** pass mark on the 50 negotiation scenarios.
- **faster-whisper Small** measured on the server; set `KX_APP_VOICE_MAX_CONCURRENT_FREE` (OPEN #72).
- **Backups:** WAL-G running, first restore test done (`infra/server/runbooks/restore.md`).
- Live switch-off test of every vendor, once in `paid` and once in `free` (Fallback plan, "Test before go-live").
