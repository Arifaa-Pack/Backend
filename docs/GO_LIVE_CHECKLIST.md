# Go-live checklist: Arifa Pack on arifapack.com

One ordered list from "nothing bought" to "live". Decisions behind it (18 Nov): free plan first (paid per
service later), USA dedicated server accepted, domain **arifapack.com**, Telnyx + UAE number ready, Gemini 3.8
Flash as the Claude standby, MD and admin created at launch (the admin adds everyone else). Technical detail:
`docs/DEPLOYMENT.md` and `infra/server/README.md`. Tick each line; nothing later works without the line above.

## A. Accounts to get (owner: you / Arifa)

| # | Account | What exactly | Goes into |
|---|---|---|---|
| A1 | **Server** | Namecheap dedicated server (v2 §5: Xeon E-2236, 64 GB, 2 x 960 GB RAID 1), Ubuntu 24.04, reverse DNS = `mail.arifapack.com` | SSH for Kairox |
| A2 | **Cloudflare** | arifapack.com on Cloudflare; an R2 bucket (location hint `weur` or `apac`) + R2 access key; origin certificate | `KX_STORAGE__*`, DNS |
| A3 | **Claude API** (Anthropic) | Account in Arifa's name, prepaid credit, monthly spend cap | `KX_APP_ANTHROPIC_API_KEY` |
| A4 | **Gemini** (Google AI, **paid**) | Billing on (the free tier allows 20 requests a day) | `KX_APP_GEMINI_API_KEY` |
| A5 | **WhatsApp** (Meta) | Business verification, a number per region, a System User token; submit the templates in section D | `KX_AUTO_WHATSAPP__*` |
| A6 | **Amazon SES** | Domain identity arifapack.com + Easy DKIM, **production access**, SMTP credentials, email feedback forwarding on | `KX_SES_SMTP_PASSWORD`, `AWS_*` |
| A7 | **Telnyx** (ready) | TeXML application, the UAE caller-ID number, API key, account SID, webhook public key | `KX_AUTO_TELNYX__*` |
| A8 | **Turnstile** (Cloudflare) | Site key for the website, secret for the API | `KX_APP_TURNSTILE_SECRET_KEY` |
| A9 | **UptimeRobot** (free) | Account for outside checks | — |
| A10 | **Arifa** | VAT **TRN** (15 digits), bank details for quotes (account name, bank, IBAN, SWIFT), logo (PNG / JPEG, < 200 KB), MD and admin names / emails / mobiles | CRM screens (section E) |

## B. DNS records (Cloudflare)

| Name | Type | Value | Proxy |
|---|---|---|---|
| `api` | A | server IP | proxied |
| `crm`, `cms`, `www` / apex | as the frontend hosting says (Vercel or the self-hosted `web` / `crm-web`) | | proxied |
| `voice` | A | server IP | **DNS only** (media) |
| `mail` | A | server IP | DNS only |
| `@` | MX 10 | `mail.arifapack.com` | — |
| `@` | TXT (SPF) | `v=spf1 include:amazonses.com mx -all` | — |
| `<selector>._domainkey` | TXT | Stalwart's DKIM records (RSA and Ed25519, shown after its wizard) | — |
| SES Easy DKIM | 3 x CNAME | from the SES console | — |
| `_dmarc` | TXT | `v=DMARC1; p=quarantine; rua=mailto:dmarc@arifapack.com` | — |

## C. Server deploy (Kairox; `docs/DEPLOYMENT.md` "First deploy")

1. Harden SSH, install Coolify, create the `kx` Docker network.
2. `compose.data.yml` -> `infra/server/postgres/db_bootstrap.sh` (prefix `pack_`); confirm WAL-G pushes to R2.
3. `compose.ai.yml`, `compose.voice.yml`, `compose.edge.yml`, `compose.obs.yml`.
4. **SigNoz**: v0.129.0 compose **with** `infra/server/signoz/compose.signoz-overrides.yml`; create the SigNoz
   admin straight away (else it accepts no data); add the alert rules (`infra/server/README.md` §5).
5. **Stalwart v1.1.1** (`compose.mail.yml`): web wizard (hostname `mail.arifapack.com`, DKIM on, logging Console,
   DNS manual), account `sales@arifapack.com`, relay route `ses` + outbound strategy (steps in the file), raise the
   25-per-hour throttle; then remove `KX_STALWART_RECOVERY_ADMIN` from the env.
6. App env in Coolify from `infra/server/stacks/env/pack.env.example` (secrets encrypted); deploy
   `compose.app.yml` (migrate runs first).
7. **`kx first-run --md-email … --md-name … --admin-email … --admin-name …`** in the api terminal: checks the
   databases are migrated, creates the MD and the admin (prints each one-time password once), shows the platform
   matrix and what is still missing.
8. UptimeRobot monitors: `https://api.arifapack.com/healthz` and `/healthz/beat` (2 minutes).

## D. WhatsApp templates to submit to Meta (EN; AR for the buyer ones)

Buyer: `requirement_summary` (document header), `followup_1`, `followup_2`, `followup_3`, `reorder_reminder`,
`staff_reopen` (the 24-hour re-open), `thank_you`, `order_update`.
Staff: `approval_request`, `approval_reminder`, `approval_escalation`, `change_request`, `negotiation_paused`,
`staff_notice`, `final_quote_alert`.
Record each in the CRM (Settings > WhatsApp templates) and mark it approved when Meta approves. A later edit
creates a new version (`name_v2`) for Meta; the approved one keeps sending.

## E. Data and settings in the CRM (Arifa staff, with Kairox on a call)

1. The MD signs in, enrols the authenticator app, creates the manager and salespeople (or the admin does).
2. **Company details**: TRN, phone, email, address, logo, bank details (the MD and manager get an alert for the
   bank change: expected). Production will not start until the TRN is set here or in company.yaml.
3. **Product workbook**: Catalogue > Product workbook > Download, fill products + price tiers, Upload, check the
   preview, Apply. Then a manager publishes the product drafts in the CMS.
4. **Message texts**: Settings > Message texts > "Translate all missing"; the reviewer checks Arabic and the
   other languages there (start from `docs/translations/Translation review 18 Nov.xlsx`).
5. Business settings: alert hours, quote validity, spend caps (defaults kept), FX currencies if any country maps
   to a non-AED currency.

## F. Tests before go-live (each one live, on the server)

| Test | How | Pass |
|---|---|---|
| AI answers | `kx evals --vendor claude` | 17/17 |
| Gemini standby | `kx evals --vendor gemini` (paid key) | 17/17, then set `KX_PLATFORM__LLM_STANDBY=gemini` |
| WhatsApp | message the number; the AI replies; a quote goes out after approval | delivered, read status back |
| Email | a quote by email; a reply comes back into the deal | received, threaded, DKIM pass |
| Calls | `click-to-call` to a du and an e& mobile; an approval-alert call | both connect |
| Voice (website) | a browser call in English and Arabic | agent answers within 2 s |
| Workbook | download -> change one price -> upload -> apply | new price on the next quote |
| Monitoring | stop the worker 3 minutes | UptimeRobot `/healthz/beat` alert, SigNoz shows it |
| Backup | restore test (`infra/server/runbooks/restore.md`) | data back |
| Switch-offs | each vendor off once (Fallback plan) | safe reply / hand-over, never silence |

## G. Go-live day

Switch the website's chat and voice on, announce the WhatsApp number, watch SigNoz and the inbox for the first
day, and keep Kairox on call. Revoke any test keys shared during setup.
