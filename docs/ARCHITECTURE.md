# Architecture (16 Nov v2)

This replaces `docs/Architecture-and-System-Design.docx` (12 Nov, AWS). Business rules: the SRS. Platforms: the server plan v2. Database: `docs/DATABASE.md`. Paid / free switch: `docs/PLATFORM_MODES.md`.

## 1. The system in one picture

```
                Buyers (browser, WhatsApp, email)            Staff (CRM screens, phone)
                         │                                          │
                 Cloudflare DNS + proxy (Turnstile on website chat)  │
                         │                                          │
   ┌─────────────────────▼──────────────────────────────────────────▼───────────────────┐
   │ Namecheap dedicated server (Ubuntu 24.04, Coolify)                                 │
   │                                                                                    │
   │  SafeLine WAF :80/:443 ──► Coolify proxy (Traefik) ──► services                    │
   │  CrowdSec (bans from logs)                                                         │
   │                                                                                    │
   │  ┌───────── app stack (one per project) ─────────┐   ┌──── shared services ───────┐ │
   │  │ api (FastAPI)       CRM API, website API,     │   │ PostgreSQL 18 + pgvector   │ │
   │  │                     webhooks, /healthz        │   │   via PgBouncer            │ │
   │  │ worker (Celery ×3)  outbox relay, sends,      │   │ Redis (broker, limits)     │ │
   │  │                     AI runs, PDFs, mail poll  │   │ TEI: BGE-M3 / Qwen3        │ │
   │  │ beat (exactly 1)    schedule sweep, heartbeat │   │ speech: faster-whisper +   │ │
   │  │ voice (LiveKit      website voice calls       │   │         Piper (free STT/TTS)│ │
   │  │        agent)                                 │   │ LiveKit server (media)     │ │
   │  │ cms (Payload)       website admin             │   │ Stalwart mail (SMTP, JMAP) │ │
   │  │ web / crm-web       only if self-hosted       │   │ Phoenix (+ own Postgres)   │ │
   │  └───────────────────────────────────────────────┘   │ SigNoz (OTel, ClickHouse)  │ │
   │                                                      └────────────────────────────┘ │
   └────────────────────────────────────────────────────────────────────────────────────┘
          │                │              │              │             │          │
     Claude API       Deepgram /     Telnyx (calls)  Meta WhatsApp  Amazon SES   Cloudflare R2
     (+ Gemini        ElevenLabs                     Cloud API      (relay)      (files, backups,
      standby)        (paid version)                                             archives)
```

The website and CRM screens (Next.js, not in this repo) run on Vercel Pro in the paid version, or as `web` / `crm-web` services on the server in the free version. Both call the same `api`.

## 2. Code layout and boundaries

| Layer | Package | Owns | Talks to others through |
| --- | --- | --- | --- |
| Core | `kx.core` (`packages/kx-core-db`) | settings, DB engines, money, ids, events / outbox / inbox, job store, auth, fallback + circuit breaker, **platform modes**, **retention helpers**, metrics (OTel API), price guard, contracts | — |
| CRM | `kx.crm` | `crm` DB: deals, quotes, approvals, grants, pricing, settings, staff login | `kx.core.contracts` protocols, events |
| Automation | `kx.automation` | `automation` DB: WhatsApp, email (Stalwart → SES), calls (Telnyx), opt-out, outbound gate, jobs | same |
| Agents | `kx.agents` | `agents` DB: LangGraph graphs, tools, guard, memory, knowledge search, voice routing, website sessions | same |
| App | `arifaa_pack` | wiring (`container.py`), vendor adapters, Celery app, API, CLI, voice worker, telemetry, migrations | imports everything; imported by nothing |
| Speech | `services/speech` | free STT/TTS server (own image) | HTTP only |

import-linter enforces: `kx.core` imports no other `kx.*`; the three packages are independent; nothing imports the app. Celery is imported only by the app.

## 3. Processes

| Process | Command | Replicas | Does |
| --- | --- | --- | --- |
| api | `kx api` | 1–2 | HTTP: CRM API, website API, webhooks (Meta, Telnyx, CMS), health |
| worker | `kx worker` | 1 (concurrency 3) | Celery tasks: outbox relays, sends, AI runs, PDFs, ingest, mail poll, retention |
| beat | `kx beat` | **exactly 1** | sweeps the PostgreSQL schedule table every 30 s, periodic jobs, heartbeat |
| voice | `kx voice` | 1 (idle processes 2) | LiveKit agent: website voice calls |
| migrate | `kx migrate` | one-off before each release | Alembic for every DB + LangGraph checkpointer tables |

Long waits (follow-ups day 2/5/10, the 30/60/120-minute ladder) are rows in `automation.scheduled_jobs`, never Celery ETAs. Every task is idempotent (`acks_late`).

## 4. A buyer turn, end to end

```
buyer message ─► webhook (Meta / website API) ─► automation.inbound_messages + outbox
   ─► relay ─► EventBus ─► scheduled_jobs ─► worker: AgentService.run
        ├─ knowledge search: TEI embedding (2 s) + full-text, RRF; same-model chunks only
        ├─ LLM: Claude (Sonnet for money turns, Haiku otherwise) [→ Gemini standby] [→ safe reply]
        ├─ price guard (fails closed) on the reply, whichever model answered
        └─ reply ─► OutboundService (consent, quiet hours, opt-out, rate limit, send grant for priced kinds)
                  ─► channel: WhatsApp (Meta) | email (Stalwart → SES) ─► status webhooks / mail poll
```

Quotes are built by the backend (§11.1); the AI never calculates or types a price. Approvals need a TOTP-authenticated human.

## 5. Website voice call

```
browser ─(Turnstile, consent)─► api /v1/public/voice/token ─► LiveKit room
LiveKit server ◄──► voice worker (AgentSession: Silero VAD + turn detector)
     STT: Deepgram ─► faster-whisper (speech server)      [free version: faster-whisper only]
     reply: the same agent graph as chat (llm_node), price guard
     TTS: ElevenLabs ─► Piper (speech server)             [free version: Piper; Tamil ElevenLabs; Sinhala text]
```

LiveKit's fallback adapters switch vendors inside a live call. In free STT mode the number of calls at once is capped (`KX_APP_VOICE_MAX_CONCURRENT_FREE`); above it the visitor gets chat.

## 6. Paid / free switch

`kx.core.platform.order(field)` turns `KX_PLATFORM_PLAN` + `KX_PLATFORM__<FIELD>` + the configured keys into an ordered vendor list. The app builds each field's adapter chain from it (`FallbackChain` for email, LiveKit fallback adapters for voice, the model router for the LLM). Every switch is logged, counted (`VendorSwitch`) and alerted (`OpsNotifier`). Details: `docs/PLATFORM_MODES.md`.

## 7. Safety backbone (unchanged by the platform move)

- **Outbox / inbox:** every state change writes audit + timeline + outbox in one transaction; relays publish exactly once; inboxes dedupe.
- **OutboundService:** the only way out; checks suppression (§8.1), consent, quiet hours, rate limits, kill switch and, for priced kinds, consumes a single-use send grant.
- **Price guard:** every AI reply and every staff reply on a handed-over deal.
- **Spend caps:** AI USD, voice minutes, WhatsApp templates per company per day (80 % alert, 100 % degrade).
- **Kill-switch ladder:** marketing → calls → text only → hold.

## 8. Observability

| Signal | Path | Where to look |
| --- | --- | --- |
| App logs (PII-masked JSON on stdout) | also OpenTelemetry → OTLP → SigNoz, after masking, with the trace id of the request / job and the exception type + stack trace | SigNoz → Logs (filter `service.name`) |
| Other containers' logs (Postgres, Stalwart, LiveKit, …) | Docker json-file logs → SigNoz collector (filelog, named by compose service); app containers skipped (label `kx.logs=otlp`) | SigNoz → Logs |
| Metrics (`VendorSwitch`, `QueueLagSeconds`, `SpendCap`, `OutboundStuck`; stored lowercase) from api, worker and voice | OpenTelemetry SDK → OTLP → SigNoz | SigNoz dashboards + alert rules (email via Stalwart) |
| Traces: every API request (health checks excluded), Celery publish + run (Beat → job in one trace), Claude calls (metadata only) | OpenTelemetry FastAPI / Celery instrumentation → OTLP → SigNoz | SigNoz → Traces |
| Server and container metrics (CPU, memory, disk, network, per container) | SigNoz collector: hostmetrics (`/hostfs`), docker_stats (Docker socket) | SigNoz → Infrastructure / dashboards |
| AI audit trail (prompts masked, model, tokens, guard result) | OpenTelemetry → Phoenix (or Langfuse) | Phoenix UI |
| Uptime | UptimeRobot (outside) → `/healthz`, `/healthz/beat` | UptimeRobot alerts |

## 9. Deployment

- **Image:** one `linux/amd64` image (`Dockerfile`) for api / worker / beat / voice / migrate; the speech server has its own image (`services/speech/Dockerfile`).
- **Coolify** builds or pulls images and runs the compose stacks in `infra/server/stacks/`; secrets are Coolify's encrypted environment variables.
- **Release:** CI (lint, tests, image build) → push image to GHCR → Coolify deploy webhook → `migrate` job → rolling restart of api / worker / voice; Beat restarts last.
- **Traffic:** Cloudflare (proxied) → SafeLine (80/443) → Coolify proxy. Direct ports: 25 (Stalwart MX), LiveKit media (7881/tcp, 3478/udp, 50000–50100/udp), 22 from admin IPs only. Everything else is on internal Docker networks.

## 10. Security

- Staff: Argon2 passwords, RS256 access tokens (15 min), rotating refresh (12 h), TOTP for approvals; break-glass admin alerted.
- Webhooks: Meta HMAC signature; Telnyx signed per-attempt URL + Ed25519 signature; CMS HMAC.
- Edge: SafeLine WAF rules + CrowdSec bans; webhook paths allow-listed but rate-limited; client IP from `CF-Connecting-IP` only when `KX_TRUSTED_CLIENT_IP_HEADER` is set.
- Data at rest: server disks (LUKS recommended at provisioning), R2 encrypts at rest; TOTP seeds encrypted with a Fernet key.
- Location: USA server (Phoenix, AZ); Arifa must approve (OPEN #69).

## 11. Known limits

- One server: no automatic failover. Recovery = restore from WAL-G on a new server (runbook). Website can show a static "contact us on WhatsApp" page meanwhile.
- No GPU: CPU speech-to-text is slower; Deepgram is the quality path.
- Monitoring on the same server goes down with it; UptimeRobot covers that from outside.
