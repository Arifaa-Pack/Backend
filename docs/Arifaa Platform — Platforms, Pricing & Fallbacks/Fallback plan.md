# Fallback plan (16 Nov v2)

Every field has a main platform, an alternative and a last resort, so one vendor failing never stops a sale. The fields and platforms follow *Accounts & Server Plan v2* §2. Switches are automatic wherever code can detect the failure. The price guard, approval gate, consent and quiet hours run after every switch, whichever vendor answered.

## Paid version and free version

Each field runs in one of two versions, chosen by environment variables (`docs/PLATFORM_MODES.md`):

- `KX_PLATFORM_PLAN=paid|free` sets every field at once.
- `KX_PLATFORM__<FIELD>=paid|free` overrides one field.

| Version | Order inside a field |
| --- | --- |
| **paid** | paid platform → **free alternative on our server** (automatic) → last resort |
| **free** | free alternative only → last resort |

Fields with no free platform (AI, staff calls, WhatsApp) always run their paid platform; the app refuses to start if one is set to `free`. Changing a version means a redeploy (Coolify), not a code change.

## How a switch works

- **One adapter per field.** The code calls the field (`llm`, `stt`, `tts`, `calls`, `email`, `embeddings` …), never a vendor directly. The order comes from the platform version above; a vendor without its key or URL is left out.
- **Detect failure:** HTTP 5xx, connection lost, HTTP 429, or slower than the trigger time below.
- **Circuit breaker:** after 3 failures in 60 seconds the vendor is skipped for 5 minutes; then one test call decides whether it comes back.
- **Alert:** every switch writes the `VendorSwitch` metric (SigNoz alert rule) and emails the ops address, at most once per field every 15 minutes.
- **Manual override:** change the field's version and redeploy; the CRM admin kill switch still pauses a whole channel at once.

## Fallback chain per field

| Field | Paid version | Free version | Last resort | Switch trigger | What the buyer or staff sees |
| --- | --- | --- | --- | --- | --- |
| AI brain | Money turns: Claude Sonnet 5.5; other turns: Claude Haiku 5.5 (Claude API) → Gemini 3.8 Flash standby (only if suite-passed and Arifa agreed) | same (no free model) | Safe reply ("Let me check with our sales team") + hand-over | No first word in 8 s, 5xx or 429, a refusal, or the price guard rejects twice | A slightly slower answer; never a wrong price |
| Speech-to-text | Deepgram Nova-3 → faster-whisper Small (our server) | faster-whisper Small | Switch to text chat | No transcript 3 s after speech ends | One "Sorry, could you repeat that?" |
| Text-to-speech | ElevenLabs Flash → Piper (en, ar, hi, zh) | Piper; Tamil: ElevenLabs if its key is set | Reply shown as text (Sinhala is always text) | No audio 1.5 s after the text is ready | A different voice |
| Real-time voice | LiveKit self-hosted | same | Website text chat → WhatsApp click-to-chat | Browser cannot join in 5 s, or the free-mode call cap is reached | "Voice is busy, let's chat here" |
| Staff alert calls, buyer call-backs | Telnyx (TeXML) | same (later: Asterisk + UAE SIP line) | WhatsApp + push → next person on the ladder | Not connected in 30 s | The next person is alerted |
| WhatsApp to buyers | Meta Cloud API | same | Email with the same approved quote → salesperson told to call | Not delivered in 10 min | The quote arrives by email |
| Email out | Stalwart (relays through Amazon SES) → SES API direct | same | WhatsApp → salesperson told | Stalwart error or timeout | No difference |
| Buyer email replies | Stalwart mailbox, read every 30 s | same | — | Poller silent 10 min (alert) | Replies a few minutes late |
| Background jobs | Celery workers + one Celery Beat | same | Manager handles reminders by hand from the CRM inbox | Beat heartbeat older than 2 min (`/healthz/beat`, UptimeRobot) | Reminders a few minutes late; none lost or doubled (idempotent tasks) |
| Job broker | Redis on the server (AOF on) | same | Tasks re-created from the PostgreSQL outbox and schedule table | Container health check | No difference |
| Server | Namecheap dedicated server (Coolify restarts failed containers) | same | Static "contact us on WhatsApp" page (Vercel or Cloudflare) → restore from backup on a new server | UptimeRobot fails 2 min | A simpler page |
| Database | PostgreSQL on the server, WAL archived to R2 | same | Point-in-time restore from R2 (no standby: restore time, not 1 minute) | Health check | "Please wait" until restored |
| Knowledge search | BGE-M3 on TEI + full-text (Qwen3 is the alternative model) | same | Full-text only → hand-over | Embedding call fails or > 2 s | Slightly less precise answers |
| Website and CRM | Vercel Pro → our server | our server | Static page → WhatsApp click-to-chat | Uptime check fails 2 min | A simpler page |
| Staff login | In-house: password + TOTP code | same | Email code (not MFA for approvals); break-glass admin (every use alerted) | Manual, or TOTP device lost | Approvals wait; nothing is sent unapproved |
| Monitoring | SigNoz on the server + UptimeRobot outside | same | Grafana stack | SigNoz down (UptimeRobot still alerts) | Nothing visible to buyers |
| AI audit trail | Phoenix | same (Langfuse is the alternative) | App logs | Automatic | Nothing visible |
| Firewall | Cloudflare → SafeLine WAF + CrowdSec | same | App rate limits + daily AI spend cap | SafeLine down: Coolify proxy serves directly (rules re-checked) | Nothing visible |

## Rules that never change during a fallback

- **The price guard runs on every reply,** whichever model wrote it. A backup that fails the guard twice triggers the safe reply.
- **The approval gate stays closed.** If the database or login is down, quotes wait; nothing priced is sent without a salesperson's approval and a send grant.
- **No cold calls.** A telephony fallback only reaches staff or a buyer who asked for a call.
- **Consent covers every platform.** Each paid platform, the server location (USA, OPEN #69) and the free alternatives on our server are named in the processor list before go-live.
- **A price turn only falls back to a tested model** that passed the 50-scenario negotiation suite in all five languages.
- **No job runs twice with effect.** Every Celery task checks an idempotency key before sending a message or placing a call, because `acks_late` can redeliver.
- **Spend caps per company per day:** alert at 80%, degrade to text-only or hand-over at 100%.
- **Kill-switch ladder:** stop marketing sends → stop outbound calls → text only → hold and hand everything to staff.

## Test before go-live

On the staging server, switch off each vendor in turn (including Redis, Celery Beat, Stalwart and SigNoz), and run once with `KX_PLATFORM_PLAN=free` and once with `paid`; record how long each take-over took against the trigger times above.
