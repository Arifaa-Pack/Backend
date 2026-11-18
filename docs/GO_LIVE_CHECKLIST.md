# Go-live checklist: Arifa Pack on arifapack.com

One ordered list from "nothing bought" to "live". Decisions behind it (18 Nov): free plan first (paid per
service later), USA dedicated server accepted, domain **arifapack.com**, Telnyx + UAE number ready, Gemini 3.8
Flash as the Claude standby, MD and admin created at launch (the admin adds everyone else). Technical detail:
`docs/DEPLOYMENT.md` and `infra/server/README.md`. Tick each line; nothing later works without the line above.

## A. Accounts to get (owner: you / Arifa)

| # | Account | What exactly | Goes into |
|---|---|---|---|
| A1 | **Server** | Namecheap dedicated server (v2 §5: Xeon E-2236, 64 GB, 2 x 960 GB RAID 1), Ubuntu 24.04, reverse DNS = `mail.arifapack.com` | SSH for Kairox |
| A2 | **Cloudflare** | arifapack.com on Cloudflare; an R2 bucket (location hint `weur` or `apac`) + R2 access key; a separate backup bucket with bucket lock, its push token(s) and a delete-only-for-retention token; origin certificate; Authenticated Origin Pulls on | `KX_STORAGE__*`, DNS; backup keys (`infra/server/README.md` §7) |
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
| `voice` | A | server IP | **proxied** (signalling WebSocket through Cloudflare; media goes to the server IP directly, `infra/server/README.md` §4) |
| `mail` | A | server IP | DNS only |
| `@` | MX 10 | `mail.arifapack.com` | — |
| `@` | TXT (SPF) | `v=spf1 include:amazonses.com mx -all` | — |
| `<selector>._domainkey` | TXT | Stalwart's DKIM records (RSA and Ed25519, shown after its wizard) | — |
| SES Easy DKIM | 3 x CNAME | from the SES console | — |
| `_dmarc` | TXT | `v=DMARC1; p=quarantine; rua=mailto:dmarc@arifapack.com` | — |

## C. Server deploy (Kairox; `docs/DEPLOYMENT.md` "First deploy")

1. Harden SSH, install Coolify with its dashboard (8000) and realtime (6001 / 6002) bound to 127.0.0.1, its proxy
   on 127.0.0.1:8080 / 8443 with the Traefik dashboard port removed and joined to `kx-proxy` at 10.200.0.2; create
   the `kx-data`, `kx-ai`, `kx-obs`, `kx-mail`, `kx-proxy` networks; ufw + the `DOCKER-USER` backstop; the CrowdSec
   firewall bouncer on `INPUT` and `DOCKER-USER` (`infra/server/README.md` §2, steps 2-7).
   Secrets in `/opt/kx/secrets/` (root, 0700; files 0600), never in the checkout: `git status` on the server shows
   no `env/*.env` or `userlist.txt`.
2. `compose.data.yml` (with `WALG_LIBSODIUM_KEY`, key also stored offline; the superuser password in the file
   `/opt/kx/secrets/postgres_password`, M8) -> `infra/server/postgres/db_bootstrap.sh`
   (prefix `pack_`); confirm WAL-G pushes to R2 and that the objects are encrypted (a downloaded WAL file does not
   decompress with `zstd -t`); from another container on `kx-data`, `psql -h postgres -U postgres` is refused.
   R2: bucket lock on `backups/` (7 days postgres, 14 days volumes); separate push and delete tokens
   (`infra/server/README.md` §7).
3. `compose.ai.yml`, `compose.voice.yml`, `compose.edge.yml`, `compose.obs.yml`.
4. **SigNoz**: v0.129.0 compose **with** `infra/server/signoz/compose.signoz-overrides.yml`; create the SigNoz
   admin straight away (else it accepts no data); add the alert rules (`infra/server/README.md` §5).
5. **Stalwart v1.1.1** (`compose.mail.yml`): web wizard (hostname `mail.arifapack.com`, DKIM on, logging Console,
   DNS manual), account `sales@arifapack.com`, relay route `ses` + outbound strategy (steps in the file), raise the
   25-per-hour throttle; Stalwart's own authentication-failure ban on (CrowdSec has no Stalwart parser yet). Then
   delete `KX_STALWART_RECOVERY_ADMIN` from the **mail** resource in Coolify (it and `KX_SES_SMTP_PASSWORD` are
   never in `pack.env`, M13), redeploy it, and check `docker inspect <stalwart> --format '{{.Config.Env}}'` shows
   `STALWART_RECOVERY_ADMIN=` empty and `docker inspect <api>` shows neither variable.
6. App env from `infra/server/stacks/env/pack.env.example` into `/opt/kx/secrets/pack.env` (and
   `pack-cms.env`), Coolify variables `REDIS_PASSWORD` (same value as in the redis URLs) and the rest marked
   encrypted; deploy `compose.app.yml` (migrate runs first). Every app container runs with `KX_ENV=production`.
7. **`kx first-run --md-email … --md-name … --admin-email … --admin-name …`** in the api terminal: checks the
   databases are migrated, creates the MD and the admin (prints each one-time password once), shows the platform
   matrix and what is still missing.
8. UptimeRobot monitors: `https://api.arifapack.com/healthz` and `/healthz/beat` (2 minutes), plus a heartbeat
   monitor for the nightly volume backup (`KX_BACKUP_HEARTBEAT_URL`); run `volumes-to-r2.sh` once by hand and
   check it names the Stalwart volumes Coolify created (`docker volume ls`) and the heartbeat arrives.
9. **Exposure check, recorded here** (`infra/server/README.md` §2 step 8): `ss -lntup` on the server (public
   listeners only 22, 25, 80, 443, 7881, 3478/udp, 50000-50100/udp; 8000, 6001, 6002, 8080, 8443, 8081, 3301, 4317,
   4318, 6006, 9443 on 127.0.0.1 only) and, from an outside machine, `nmap -Pn -p- <ip>` +
   `sudo nmap -Pn -sU -p 3478,50000-50100 <ip>` (open: 25, 7881, the UDP media ports; everything else filtered).
   `iptables -L DOCKER-USER -n -v` shows the backstop rules. Each `kx-*` network holds only its members
   (`docker network inspect`). Date + result: ______________________
10. Isolation spot checks: Redis refuses a connection without its password (`NOAUTH`); Phoenix UI asks for a login
    (default admin password changed) and traces still arrive with the API key in `KX_APP_AI_TRACE_HEADERS`;
    SafeLine console only on 127.0.0.1:9443; Cloudflare Authenticated Origin Pulls on.
11. **Live-test infra review, MEDIUM items (18 Nov)**, each checked on the server (`infra/server/README.md`):
    - [ ] M1/M2 release pipeline: Cloudflare Tunnel + Access in front of Coolify's API only (§2 step 9: without
      the Access headers 403, the dashboard path 404); Coolify token *write + deploy*; `KX_APP_IMAGE` and
      `KX_CMS_IMAGE` set as `…:<tag>@sha256:…` on the app resource; one tag released to staging end to end (the
      workflow waits for the deployment and `/healthz`).
    - [ ] M3 GitHub: `production` environment with required reviewers and a `v*`-tags-only deployment rule;
      `staging` tags only; Dependabot enabled; workflow default permissions *read* (`docs/DEPLOYMENT.md`).
    - [ ] M4 `docker stats --no-stream`: every container shows its own limit, not the host total.
    - [ ] M5 deploys are not zero-downtime: release outside sales hours; a voice call held across a deploy ends
      only after `KX_VOICE_STOP_GRACE` (300 s).
    - [ ] M6 `db_bootstrap.sh` run twice in a row: the app still logs in through PgBouncer.
    - [ ] M7 CrowdSec: `cscli metrics show acquisition` lists the Stalwart container (docker), SafeLine and
      auth.log; `cscli postoverflows list` has `crowdsecurity/cdn-whitelist`; the firewall bouncer config has
      `scenarios_containing`; the Cloudflare bouncer runs from its config file; a SafeLine log line shows the real
      client IP, not a Cloudflare one.
    - [ ] M8/M9 `docker inspect` of postgres: no `POSTGRES_PASSWORD`, no `PGDATA` override; the volume is
      mounted at `/var/lib/postgresql` and `SHOW data_directory` = `/var/lib/postgresql/18/docker`.
    - [ ] M10 `pg-health-check.sh` in root's cron; its Healthchecks.io check is green; a test `/fail` reached the
      alert email; SigNoz disk alert (> 80 %) added. **RPO ≈ 5 min, RTO ≈ new server + ≈ 1 h restore, agreed by
      Arifa: name + date ______________________**
    - [ ] M11 the postgres image was built from the pinned digest with the WAL-G checksum (build log shows
      `/tmp/walg.tar.gz: OK`).
    - [ ] M14 `volumes-to-r2.sh` run by hand stops and restarts Stalwart (mail back within a minute);
      `volumes-restore-test.sh` passes once by hand and is in cron with its heartbeat.
    - [ ] M15 `docker inspect -f '{{.HostConfig.CapDrop}} {{.HostConfig.SecurityOpt}}'` on each container shows
      `[ALL]` (except cms / web) and `no-new-privileges`; the SigNoz collector has no `/var/run/docker.sock` mount
      and container metrics still arrive.
    - [ ] M16 the Monday `image-scan` workflow ran green (or its findings are being fixed).

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
| Voice (website) | a browser call in English and Arabic, from a network outside the office (signalling through Cloudflare, media direct); one call held 5+ minutes | agent answers within 2 s; no drop |
| Workbook | download -> change one price -> upload -> apply | new price on the next quote |
| Monitoring | stop the worker 3 minutes | UptimeRobot `/healthz/beat` alert, SigNoz shows it |
| Backup | restore test (`infra/server/runbooks/restore.md`) | data back |
| Switch-offs | each vendor off once (Fallback plan) | safe reply / hand-over, never silence |

## G. Go-live day

Switch the website's chat and voice on, announce the WhatsApp number, watch SigNoz and the inbox for the first
day, and keep Kairox on call. Revoke any test keys shared during setup.
