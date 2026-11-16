# Platform modes: paid version and free version per field

Source: *Accounts & Server Plan v2* §2 (platforms), §3 (paid version cost), §4 (free version cost). Decision D41.

Every field has a **main platform** (usually paid) and, where one exists, a **free open-source alternative** that runs on our server. Which one runs is chosen with environment variables, so a change is a redeploy (Coolify → *Redeploy*), never a code change.

## The switch

| Variable | Values | Default | Effect |
| --- | --- | --- | --- |
| `KX_PLATFORM_PLAN` | `paid`, `free` | `paid` | Preset for every field below |
| `KX_PLATFORM__STT` | `paid`, `free` | the preset | Speech-to-text |
| `KX_PLATFORM__TTS` | `paid`, `free` | the preset | Text-to-speech |
| `KX_PLATFORM__EMAIL` | `paid`, `free` | the preset | Email (same chain in both; kept for symmetry) |
| `KX_PLATFORM__LLM` | `paid` | `paid` | AI. `free` stops the app at start-up: there is no free model |
| `KX_PLATFORM__CALLS` | `paid` | `paid` | Staff alert calls. `free` is refused until an Asterisk adapter exists |
| `KX_PLATFORM__WHATSAPP` | `paid` | `paid` | WhatsApp. `free` is refused: Meta is the only route |
| `KX_PLATFORM__LLM_STANDBY` | `none`, `gemini` | `none` | Gemini 3.8 Flash after Claude (paid, used only when Claude fails; needs D27 + OPEN #74) |
| `KX_PLATFORM__EMBEDDINGS` | `bge-m3`, `qwen3` | `bge-m3` | Knowledge-search model (both free). A change needs `kx reindex` |
| `KX_PLATFORM__AI_TRACE` | `phoenix`, `langfuse` | `phoenix` | AI audit trail backend (both free, both OTLP) |

**paid** = the paid platform first, then the **free alternative automatically** if it fails, then the last resort.
**free** = the free alternative only, then the last resort.

A vendor whose key or URL is missing is left out of the order (for example, paid STT without `DEEPGRAM_API_KEY` runs faster-whisper only). The app logs a warning and raises one ops alert when a paid field has lost its paid vendor this way.

## Order per field (what `kx.core.platform.order()` returns)

| Field | paid | free | Last resort (in code) | Needs |
| --- | --- | --- | --- | --- |
| `llm` | `claude` (+ `gemini` if standby) | refused | safe reply + hand-over | `KX_APP_ANTHROPIC_API_KEY` |
| `stt` | `deepgram` → `whisper` | `whisper` | website text chat | `DEEPGRAM_API_KEY`; `KX_APP_SPEECH_URL` |
| `tts` (en, ar, hi, zh) | `elevenlabs` → `piper` | `piper` | reply shown as text | `ELEVEN_API_KEY`; `KX_APP_SPEECH_URL` |
| `tts` (ta) | `elevenlabs` | `elevenlabs` if its key is set | text chat | `ELEVEN_API_KEY` |
| `tts` (si) | — | — | text chat (always) | — |
| `calls` | `telnyx` | refused | WhatsApp alert + next person | `KX_AUTO_TELNYX__*` |
| `whatsapp` | `meta` | refused | email (own grant, D32) | `KX_AUTO_WHATSAPP__*` |
| `email` | `stalwart` → `ses` | `stalwart` → `ses` | WhatsApp → salesperson told | `KX_AUTO_STALWART__*`; `KX_AUTO_SES__*` + AWS keys |
| `embeddings` | `bge-m3` or `qwen3` | same | PostgreSQL full-text | `KX_APP_EMBEDDING_URL` |
| `ai_trace` | `phoenix` or `langfuse` | same | none (spans dropped) | `KX_APP_AI_TRACE_ENDPOINT` (+ `_HEADERS` for Langfuse) |

Fields that are not in the code, only in the server stacks (`infra/server/`):

| Field | Paid version | Free version | Where it is chosen |
| --- | --- | --- | --- |
| Live voice | LiveKit self-hosted | same | `compose.voice.yml` |
| Website and CRM hosting | Vercel Pro | `web` / `crm-web` services on our server | `compose.app.yml` profile `selfhost-web`; DNS |
| Monitoring | SigNoz + UptimeRobot | same (Grafana stack is the documented alternative) | `compose.obs.yml` |
| Firewall | SafeLine + CrowdSec + Turnstile | same | `compose.edge.yml` |

## Presets and their monthly cost (v2 §3, §4)

| Preset | Fields | Light | Normal | Heavy |
| --- | --- | --- | --- | --- |
| `KX_PLATFORM_PLAN=paid` + Vercel | Deepgram, ElevenLabs, Vercel; free alternatives as fallback | ≈ 232 | ≈ 819 | ≈ 2,459 |
| `KX_PLATFORM_PLAN=free` + self-hosted web | faster-whisper, Piper (ElevenLabs for Tamil only), web on our server | ≈ 171 | ≈ 575 | ≈ 1,739 |

Mixed example (paid speech-to-text for quality, free voice output):

```
KX_PLATFORM_PLAN=free
KX_PLATFORM__STT=paid
```

## What is logged and alerted

- At start-up every process logs `platform.resolved` with the matrix, and warns on each field whose paid vendor is unavailable.
- `kx platform show` prints the same matrix (field, mode, order, missing keys).
- Every switch at run time (a vendor failed and the next one answered) writes the `VendorSwitch` metric (field, from, to) and calls `OpsNotifier`: one email to `KX_APP_OPS_ALERT_EMAILS` per field per 15 minutes. SigNoz has an alert rule on the metric.
- Voice: LiveKit's fallback adapters report a vendor going unavailable mid-call the same way.

## Free-mode limits

- **Voice calls at once in free STT mode** are capped by `KX_APP_VOICE_MAX_CONCURRENT_FREE` (default 3; CPU speech-to-text). Above the cap the website offers text chat. Measure on the real server (OPEN #72).
- **Piper voices:** English, Arabic, Hindi, Chinese. Tamil needs ElevenLabs; Sinhala is always text chat.
- faster-whisper Small is noticeably less accurate than Deepgram on Arabic, Hindi, Tamil and product names (Options by field §2).

## Runbook: switch a field

1. In Coolify, edit the app's environment (`KX_PLATFORM_PLAN` or `KX_PLATFORM__<FIELD>`).
2. Redeploy the `api`, `worker`, `beat` and `voice` services.
3. Check `kx platform show` (Coolify → service → terminal) and the `platform.resolved` log line.
4. For `KX_PLATFORM__EMBEDDINGS`: start the new TEI model first, then run `kx reindex`; until it finishes, search falls back to full-text for chunks of the new model.
