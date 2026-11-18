# Infrastructure / deployment security and resilience review

Date: 18 Nov 2026. Mode: read-only static review (no containers started or inspected, no docker commands).
Scope: `Dockerfile`, `docker-compose.yml`, `docker/`, `services/speech/`, `infra/server/**` (stacks, env examples,
postgres, pgbouncer, backup, crowdsec, livekit, signoz override, runbooks), `.github/workflows/*`, `Makefile`,
`.env.example`, `apps/arifaa-pack/src/arifaa_pack/config.py`, `packages/*/src/kx/*/settings.py`; intent from
`docs/DEPLOYMENT.md`, `docs/GO_LIVE_CHECKLIST.md`, `infra/server/README.md`.

"Verify" marks findings that depend on how Coolify / upstream installers behave on the real server; they are
reasoned from the config, not observed.

## Summary

| Severity | Count |
|---|---|
| Critical | 0 |
| High | 7 |
| Medium | 16 |
| Low / Info | 12 |

Top risks, in order:

1. **H1** One flat `kx` Docker network with unauthenticated Redis (Celery broker, kill switches, spend counters), speech, TEI, Phoenix and Stalwart JMAP/admin, plus a network-reachable Postgres superuser. A foothold in any public-facing container (CMS, Stalwart :25, LiveKit) reaches all of them.
2. **H2** Docker-published ports skip `ufw`, so the README's "dashboard via SSH tunnel only" and "443 from Cloudflare only" rules do not cover ports that Coolify, SafeLine and SigNoz publish themselves (verify). Three components also want host port 8080.
3. **H3** The volume backup script names volumes `kx-mail_*`. Under Coolify the names get a UUID prefix (verify), so `docker run -v kx-mail_stalwart-data:/src` silently creates an empty volume and uploads empty tarballs every night.
4. **H4** Backups are unencrypted. The volume tarball includes Coolify's `/data/coolify`, which holds the SSH private keys and `APP_KEY`. The WAL-G credentials can delete backups and sit in the Postgres container's env.
5. **H6** `KX_SERVICE_TOKEN_SECRET` and `KX_AUTO_TELNYX__CALLBACK_SECRET` default to the public string `dev-only-change-me`, and nothing refuses that in production. `KX_ENV` defaults to `local`, which turns the existing JWT / Fernet guards off.
6. **H7** The `voice.` hostname is DNS-only, but 443 is firewalled to Cloudflare IPs and SafeLine serves a Cloudflare *origin* certificate, which browsers do not trust. Voice signalling will fail. Opening 443 to fix it removes the origin lock that the `CF-Connecting-IP` trust depends on.

---

## HIGH

### H1. Flat shared network; unauthenticated internal services; the Postgres superuser reachable from every stack
- **Where:** `infra/server/README.md:38` (all stacks on the one `kx` network); `infra/server/stacks/compose.app.yml:25-31` (Redis with no `requirepass` / ACL); `infra/server/stacks/env/pack.env.example:29-30` (`redis://redis:6379` with no password); `compose.obs.yml:27-39` (Phoenix with no `PHOENIX_ENABLE_AUTH`); `compose.ai.yml` (TEI and speech with no auth; `services/speech/app.py:138` binds 0.0.0.0); `compose.mail.yml` (Stalwart JMAP / web admin :8080 on `kx`); `compose.data.yml:14-15` and `postgresql.conf:2` (`listen_addresses='*'`, image-default `pg_hba` `host all all all scram-sha-256`, so `postgres` logs in over TCP); `pack.env.example:33` (`KX_APP_FORWARDED_ALLOW_IPS=*`).
- **Risk:** The internet-facing containers are Payload CMS (Node), Stalwart (SMTP :25), LiveKit and the SigNoz collector, which runs as root with the Docker socket. All of them share the `kx` network with:
  - Redis, with no auth. An attacker can queue `kx.run_job` / `kx.periodic` tasks, flip kill switches, reset spend caps (D34) and rate limits, or flush the broker. `redis:8.2` is a floating tag, and 8.2.0–8.2.1 carry the Lua use-after-free RCE (CVE-2025-49844), reachable by anyone who can connect.
  - Phoenix, with no auth: AI traces, possibly with buyer PII.
  - Stalwart admin / JMAP.
  - `postgres:5432` as superuser, password-guessable. A superuser can run `COPY … TO PROGRAM` and read the R2 credentials from the container env.
  - `api:8000` directly, with `FORWARDED_ALLOW_IPS=*`. `X-Forwarded-*` and `CF-Connecting-IP` can be spoofed, which bypasses per-IP limits.
- **Fix:**
  - Split networks: `kx-data` (postgres, pgbouncer: app + cms only), `kx-app-<company>` (redis: app only; internal), `kx-ai` (embeddings, speech: app only), `kx-obs`. Add `internal: true` where nothing needs egress.
  - Redis: `--requirepass` or an ACL user, and pin `redis:8.2.x` (≥ 8.2.2).
  - Phoenix: `PHOENIX_ENABLE_AUTH=true` and `PHOENIX_SECRET`.
  - Postgres: custom `pg_hba.conf` with `local all postgres peer`, `host all postgres 0.0.0.0/0 reject`, and `hostssl`/`host` for the app roles from the data network only.
  - Uvicorn: set `FORWARDED_ALLOW_IPS` to the Coolify proxy's address on a dedicated proxy network.

### H2. Docker-published ports bypass `ufw`; Coolify dashboard, SafeLine console and SigNoz UI likely public; port-8080 collision (verify)
- **Where:** `infra/server/README.md:43-44` relies on `ufw` and on "open the Coolify dashboard through an SSH tunnel only". `README.md:74-76` uses SigNoz v0.129.0's upstream compose, whose `signoz` service publishes `8080:8080`; `infra/server/signoz/compose.signoz-overrides.yml:32-34` only re-binds the collector ports. `README.md:91` covers SafeLine.
- **Risk:**
  - Docker inserts DNAT/FORWARD rules ahead of `ufw`'s INPUT chain. Any `ports:` mapping without a host IP is reachable from the internet whatever `ufw` says.
  - The Coolify installer publishes `8000` (dashboard), `6001` and `6002` (realtime) on 0.0.0.0, and Coolify is root-equivalent on the host. SafeLine's management console (`9443`) is published by its compose. SigNoz UI 8080 is published by upstream compose.
  - README §2 asks Coolify's Traefik to bind `127.0.0.1:8080`. SigNoz also wants host `:8080`, and Traefik's own API/dashboard default is 8080. One of them will fail to start, or the wrong one ends up on the port.
  - The CrowdSec host firewall bouncer adds rules to `INPUT` by default. Traffic to Docker-published ports (25, 7881, 3478, 50000-50100) passes through `FORWARD`/`DOCKER-USER`, so those bans have no effect on Stalwart or LiveKit.
- **Fix:**
  - Bind every admin UI to `127.0.0.1`: a Coolify `docker-compose.custom.yml` for 8000/6001/6002, SafeLine `MGT_PORT` as `127.0.0.1:9443`, and an override `ports: !override ["127.0.0.1:3301:8080"]` for SigNoz.
  - Add `DOCKER-USER` rules (or `ufw-docker`) as a backstop.
  - Set `iptables_chains: [INPUT, DOCKER-USER]` in `crowdsec-firewall-bouncer.yaml`.
  - After first deploy, run `ss -lntup` plus an external `nmap` and record the result in the go-live checklist.

### H3. Nightly volume backup probably copies empty volumes, silently
- **Where:** `infra/server/backup/volumes-to-r2.sh:14` (`VOLUMES=(kx-mail_stalwart-etc kx-mail_stalwart-data)`), `:17-20`.
- **Risk:** The names assume the compose project is `kx-mail`. Coolify deploys a Docker Compose resource under its own project name and prefixes named volumes with the resource UUID (verify with `docker volume ls`). `docker run -v <missing-name>:/src` does not fail. Docker creates a new empty volume, `tar` succeeds, and R2 receives a valid tarball of nothing every night while the real mail store, DKIM keys and config go unprotected. The same root cause breaks `crowdsec-acquis.yaml:13-14` (`container_name: kx-mail-stalwart-1`). Nothing checks or alerts on the outcome: cron output goes nowhere and the restore runbook does not cover volume restores.
- **Fix:** Resolve volumes by label (`docker volume ls -q --filter label=com.docker.compose.volume=stalwart-data`), fail if the count is not exactly 1 (`docker volume inspect` before use), and fail if the tarball is under a minimum size. Push a heartbeat (UptimeRobot or Healthchecks.io) on success. Add a volume restore drill to `runbooks/restore.md`.

### H4. Backups unencrypted, include the host's master secrets, and the backup credentials can delete them
- **Where:** `volumes-to-r2.sh:15` (tars all of `/data/coolify`), `:22`; `compose.data.yml:16-23` (WAL-G with no `WALG_LIBSODIUM_KEY` / `WALG_PGP_KEY`; R2 keys in the postgres container env); `runbooks/restore.md:8` (`wal-g delete retain FULL 7` uses the same keys).
- **Risk:**
  - `/data/coolify` contains `ssh/keys/*`: the private keys Coolify uses to SSH into the server, usually as root. It also contains `source/.env`, whose `APP_KEY` decrypts every "encrypted" Coolify env var.
  - All of it, plus every customer row in the WAL-G base backups, sits in plain text in R2. Anyone holding one R2 token gets root on the server and all of the data.
  - The R2 key that WAL-G uses has delete rights, needed for `delete retain`. It is readable by any Postgres superuser session through `COPY … PROGRAM 'env'`, so a single database compromise can wipe every backup.
- **Fix:**
  - Encryption: `WALG_LIBSODIUM_KEY` (keep it offline as well) and an `rclone crypt` remote or `age` for the volume tarballs. Exclude `/data/coolify/ssh` or encrypt it separately.
  - Split R2 tokens: an Object-Write-only token for push, a separate delete token used only by the retention job.
  - Enable R2 bucket lock / object-lock retention on `backups/`.

### H5. Production secret files are expected inside the Git checkout and are not git-ignored
- **Where:** `compose.app.yml:9` (`env_file: ${KX_ENV_FILE:-env/pack.env}`), `compose.app.yml:86` (`env/pack-cms.env`), `compose.data.yml:42` (`./secrets/userlist.txt`), `.gitignore:10-12` (only `.env` / `.env.*`).
- **Risk:** Coolify deploys these stacks from the Git repo and re-clones on each deploy. The compose files require `env/pack.env`, `env/pack-cms.env` and `secrets/userlist.txt` to exist relative to that checkout. The path of least resistance is to commit them, which puts every vendor key, DB password, JWT private key and SCRAM verifier into Git history. None of those three paths is ignored, and `git check-ignore` confirms it. The alternative, hand-placing them in Coolify's app directory, is lost on redeploy.
- **Fix:** Default `KX_ENV_FILE`, `KX_CMS_ENV_FILE` and `PGBOUNCER_USERLIST` to absolute host paths outside the checkout (e.g. `/opt/kx/secrets/…`, mode 0600, root-owned), or use Coolify env vars with `environment:` passthrough. Add `infra/server/stacks/env/*.env`, `infra/server/stacks/secrets/` and `**/userlist.txt` to `.gitignore`. Add a gitleaks / trufflehog step to CI.

### H6. Known default secrets survive into production; `KX_ENV` defaults to `local`
- **Where:** `packages/kx-core-db/src/kx/core/settings.py:122` (`service_token_secret = "dev-only-change-me"`), `packages/kx-core-automation/src/kx/automation/settings.py:71` (`callback_secret = "dev-only-change-me"`), `settings.py:98` (`env = Environment.LOCAL`). The guards in `apps/arifaa-pack/src/arifaa_pack/container.py:579-592` cover only the JWT key and the Fernet key, and only when `env` is staging or production.
- **Risk:**
  - If `KX_SERVICE_TOKEN_SECRET` is missing or mistyped in Coolify, website session tokens and email-unsubscribe tokens are signed with a string that is public in this repo, so anyone can forge them.
  - The Telnyx per-call callback tokens have the same problem.
  - If `KX_ENV` is missing, the process runs as `local`: it generates a throw-away JWT key per process, so tokens fail across api restarts, and it accepts a process-local TOTP key, so enrolments are lost. This happens silently instead of refusing to start.
  - `pack.env.example:31` ships `KX_SERVICE_TOKEN_SECRET=CHANGE`, which is not empty, so it is never rejected either.
- **Fix:** Add a single `model_validator` on `CoreSettings` / `AutomationSettings` that, outside local/test, refuses defaults or placeholders (`dev-only-change-me`, `CHANGE`, `replace-with…`) and any value shorter than 32 bytes. Make `env` a required field, or default it to `production`, so a missing variable fails closed.

### H7. Voice signalling path cannot work as documented; the obvious fix breaks the client-IP trust model
- **Where:**
  - `infra/server/README.md:43`: 80/443 allowed from Cloudflare IP ranges only.
  - `README.md:64`, `docs/GO_LIVE_CHECKLIST.md:29`: `voice.` is "DNS only".
  - `compose.voice.yml:2`: signalling goes through the Coolify proxy at `wss://voice.<domain>`.
  - `README.md:91`, `GO_LIVE_CHECKLIST.md:13`: SafeLine on 443 serves the Cloudflare origin certificate.
  - `pack.env.example:32-33`: `KX_TRUSTED_CLIENT_IP_HEADER=CF-Connecting-IP`, `FORWARDED_ALLOW_IPS=*`.
- **Risk:**
  - Browsers hitting `wss://voice.arifapack.com:443` directly are dropped by `ufw`, because they are not Cloudflare IPs.
  - If allowed through, they get a Cloudflare Origin CA certificate, which no browser trusts. Website voice fails at go-live.
  - Opening 443 to the world to fix it lets anyone reach `api.` on the origin IP with `Host: api.arifapack.com` and a forged `CF-Connecting-IP`. The app trusts that header unconditionally, so per-IP rate limits (`PUBLIC_BUCKETS`), Turnstile-adjacent abuse controls and CrowdSec attribution can all be bypassed.
- **Fix:** Proxy the signalling hostname through Cloudflare, which carries WebSockets, and keep only the media ports (7881/tcp, 3478/udp, 50000-50100/udp) direct. LiveKit already advertises `--node-ip` for media. Alternatively, give `voice.` a Let's Encrypt certificate on a separate listener or port that is not 443. Enable Cloudflare Authenticated Origin Pulls (mTLS) on SafeLine. In the app, accept `CF-Connecting-IP` only when the peer, after the proxy, is in Cloudflare's ranges.

---

## MEDIUM

### M1. A release does not change what Coolify runs
`compose.app.yml:7` defaults to `ghcr.io/kairox/app:latest`. `release.yml:63` pushes only `ghcr.io/<owner>/app:<tag>` (never `latest`, and the owner is `${GITHUB_REPOSITORY_OWNER,,}`, which may not be `kairox`). `release.yml:89` then triggers a Coolify redeploy with `force=false`. Unless someone edits `KX_APP_IMAGE` in Coolify for every release, the redeploy either re-pulls the old image or fails to pull `latest`. The pipeline reports success either way: the `curl` only queues a deploy and nobody checks `/healthz` or the version. **Fix:** have the deploy job set `KX_APP_IMAGE` and `KX_SPEECH_TAG` through the Coolify API (`PATCH /applications/{uuid}/envs`) before deploying, or push an immutable digest and reference it. Then poll the deployment status and `/healthz` and fail the job on error. The same `:latest` defaults apply to `KX_CMS_IMAGE`, `KX_WEB_IMAGE` and `KX_CRM_WEB_IMAGE` (`compose.app.yml:84,95,105`).

### M2. The GitHub deploy step needs a publicly reachable Coolify API
`release.yml:85-90` calls `${COOLIFY_URL}/api/v1/deploy` from GitHub-hosted runners. `README.md:44` keeps the dashboard and API behind an SSH tunnel. Either Coolify ends up internet-exposed (it is root-equivalent and has a history of critical CVEs) or deploys fail. **Fix:** expose only the API path through Cloudflare Tunnel plus Cloudflare Access with a service token, or use a self-hosted runner on the box, or pull-based deploys. Scope `COOLIFY_TOKEN` to `deploy` only.

### M3. GitHub Actions supply chain and least privilege
- `ci.yml:39,42,73,76,125` and `release.yml:41,49,51,58,68` pin actions by mutable tag (`@v6`, `@v4`, `@v7`, `@v8.1.0`), not by commit SHA.
- `release.yml:20-22` grants `packages: write` workflow-wide, including to `deploy`, which only needs the Coolify secret.
- `actions/checkout` keeps `persist-credentials` at its default (true).
- `workflow_dispatch` with `target=production` (`release.yml:12-18,81`) builds and deploys whatever branch the dispatcher picks. Safety rests entirely on the GitHub `production` environment having required reviewers and a deployment-branch rule (tags / `main` only), and that rule is not in the repo.
- There is no `pull_request_target` (good).

**Fix:** SHA-pin with a Renovate/Dependabot `github-actions` updater. Move `packages: write` to the `images` job. Set `persist-credentials: false`. Configure environment protection and record it in `DEPLOYMENT.md`. Add `zizmor` to the `stacks` job.

### M4. No memory or CPU limits on most services; the host OOM killer may pick Postgres
Only `embeddings` (6g) and `speech` (4g) are limited (`compose.ai.yml:15-17,35-37`). The services with no limit are api, worker, beat, voice, cms, redis (`maxmemory` caps data, not fork/AOF rewrite), postgres (8 GB `shared_buffers`), pgbouncer, stalwart, livekit, phoenix(+pg) and SigNoz/ClickHouse. The worker spawns headless Chromium per PDF (`pdf.py:279-289`) and has no `--max-memory-per-child` / `--max-tasks-per-child`. The voice workers load VAD and plug-ins per call. On the shared 64 GB host, a leak or burst invokes the kernel OOM killer, whose favourite target is usually the biggest RSS: Postgres. **Fix:** set `deploy.resources.limits.memory` (and `cpus`) for every service, sized from the bench (README §9). Add `worker_max_memory_per_child` (e.g. 1.5 GB) and `worker_max_tasks_per_child`. Set `oom_score_adj: -900` on postgres.

### M5. Shutdown and deploy behaviour: tasks parked for 2 h, calls dropped, API down during each deploy
- No `stop_grace_period` anywhere, so Docker's 10 s SIGTERM→SIGKILL applies.
- Celery has `acks_late` and `visibility_timeout=7200` (`jobqueue.py:38-48`). A task killed mid-run during a deploy (PDF render or LLM turn > 10 s) is not redelivered by Redis for 2 hours. Whether the Postgres job sweep reclaims a `running` row sooner is not visible from config; verify.
- Voice calls in progress are cut at the 10 s mark.
- `DEPLOYMENT.md:42` and `release.yml:5` say "rolling restart". Coolify's Docker Compose deploy is `docker compose up -d`, which recreates in place. With one api replica and no proxy-level health gating, every release has API downtime, and migrations run while the old worker may still be processing.

**Fix:** `stop_grace_period: 120s` for worker (Celery warm shutdown), `30s` for api (uvicorn `timeout_graceful_shutdown`), and voice equal to the maximum call length or a drain. Lower `visibility_timeout` toward the real longest task, or add a lease/heartbeat on `scheduled_jobs`. Document that deploys are not zero-downtime, or run two api replicas behind Traefik with healthchecks. Keep migrations expand/contract.

### M6. Re-running `db_bootstrap.sh` breaks PgBouncer logins
`db_bootstrap.sh:64` runs `ALTER ROLE … PASSWORD` on every run ("re-synced every run"; the restore runbook `restore.md:23` runs it too). Under `password_encryption=scram-sha-256` (`postgresql.conf:39`), each run stores a new random salt even when the password is unchanged. `pgbouncer.ini:12-14` uses `auth_type=scram-sha-256` with a static `userlist.txt` of SCRAM verifiers. PgBouncer can only pass SCRAM through when its stored verifier is byte-identical to the server's (same salt and iterations). After any bootstrap re-run the app's logins through PgBouncer fail until `userlist.txt` is regenerated, and only the rotation runbook says to do that. **Fix:** in `ensure_role`, skip the `ALTER … PASSWORD` when the role already exists and the password is unchanged (compare it against the stored verifier, or require an explicit `KX_BOOTSTRAP_ROTATE=1`). Better: switch PgBouncer to `auth_user` + `auth_query` (a `pgbouncer_auth` role with a `SECURITY DEFINER` lookup function) so there is no static userlist to drift. Add "regenerate userlist" to `restore.md`.

### M7. CrowdSec mostly blind outside SafeLine
- `crowdsec-acquis.yaml:12-16` uses `source: docker`, but `compose.edge.yml:16-21` does not mount `/var/run/docker.sock`, so the datasource cannot connect. This may also stop CrowdSec from starting.
- `container_name: kx-mail-stalwart-1` will not match Coolify's container names (verify).
- Stalwart's console log is labelled `type: syslog` and the collection loaded is `crowdsecurity/postfix` (`compose.edge.yml:12-13`), so no parser matches Stalwart lines and SMTP brute force goes undetected.
- Nothing whitelists Cloudflare ranges. If SafeLine logs the Cloudflare edge IP instead of the real client (real-IP not configured), HTTP scenarios will ban Cloudflare edges, and the host firewall bouncer, which by default receives all decisions, cuts the site off. README §4 warns about this, but nothing enforces it.
- See H2 for the `INPUT` vs `DOCKER-USER` chain.

**Fix:** mount the Docker socket read-only through a socket proxy (e.g. `tecnativa/docker-socket-proxy`, containers:read only), or have Stalwart log to a file, and use `container_name_regexp` or labels. Write or install a Stalwart parser. Install a Cloudflare IP whitelist (the `crowdsecurity/cdnwhitelist`-style postoverflow) and configure SafeLine real-IP from `CF-Connecting-IP`. Scope the firewall bouncer with `scopes`/`origins` or `scenarios_containing: [ssh, smtp]`. Also confirm the `crowdsecurity/cloudflare-bouncer:v0.3.0` image accepts env-only config. It normally needs a mounted config file listing account and zone IDs (verify).

### M8. Postgres: no TLS and default `pg_hba`; superuser password held in compose env
`postgresql.conf:2` listens on `*`, has no `ssl = on` and no custom `hba_file`. `compose.data.yml:15` puts the superuser password in container env, visible through `docker inspect` to anyone with Docker access. Combined with H1, any container on `kx` can try the superuser password over TCP. **Fix:** custom `pg_hba.conf` (superuser over local socket only; app roles from the data network; `reject` otherwise). Turn on `ssl` with a self-signed certificate if the network is ever extended beyond one host. Prefer `POSTGRES_PASSWORD_FILE` with a 0600 host file. Set `log_connections=on` and `log_disconnections=on`.

### M9. PG18 data mount pattern that the repo itself documents as broken (verify)
`compose.data.yml:24-26` mounts the volume at `/var/lib/postgresql/data` with `PGDATA=/var/lib/postgresql/data/pgdata`. `compose.obs.yml:15-16` records that on PG18 images "a mount at …/data makes the container refuse to start". The local stack (`docker-compose.yml:63`) and Phoenix use `/var/lib/postgresql`. The restore runbook (`restore.md:13`) uses the same `…/data` pattern. If the official PG18 entrypoint's "unused mount" check fires, the data tier, or a restore drill, will not start on day one. **Fix:** mount at `/var/lib/postgresql` and drop the `PGDATA` override (default `/var/lib/postgresql/18/docker`), and update `restore.md` paths. Test a cold start and a restore on staging.

### M10. WAL archiving failure is unmonitored; single server is the SPOF
- `archive_command = 'wal-g wal-push %p'` (`postgresql.conf:19-21`). If R2 or the key fails, WAL piles up in `pg_wal` until the disk fills and Postgres stops, taking everything down, because all tiers live on one box.
- README §5 alert rules cover app metrics only: no `pg_stat_archiver.failed_count` or `last_archived_time` age, no disk-usage or `pg_wal` size alert, no backup-age alert.
- RPO is about `archive_timeout`, 5 min. RTO after a server loss is a full re-provision plus `backup-fetch`. That is acceptable for v1 if the client has agreed to it.

**Fix:** add SigNoz alerts (Postgres receiver or a periodic `kx` metric) for archiver failures over 0, last archive older than 15 min, disk over 80 %, and newest WAL-G base backup older than 26 h. Set `max_slot_wal_keep_size`. Write the RPO/RTO into the go-live sign-off.

### M11. The WAL-G binary is downloaded unverified
`infra/server/postgres/Dockerfile:5-11`: `WALG_SHA256=""` by default, and the check is skipped when it is empty. `compose.data.yml:7-9` builds with no `args:`, so the README §8 instruction to "set the WAL-G checksum at the first deploy" has no mechanism. The binary is also the Ubuntu 22.04 build running on the Debian-based `pgvector:pg18` image. **Fix:** hard-code the SHA-256 of `v3.0.7` in the Dockerfile, failing on empty, or pass it via `build.args`. Pin `pgvector/pgvector:pg18` to a minor version or digest.

### M12. Local dev stack publishes every service on 0.0.0.0 with known credentials
`docker-compose.yml` publishes:
- `5432` with `postgres/postgres` (`:55-61`)
- `6432` (`:84`)
- `6379` Redis with no auth (`:93`)
- `8080` TEI (`:108`)
- `8100` speech (`:120`)
- LiveKit `devkey/secret` on 7880-7882 and 50000-50020 (`:127-132`)
- Stalwart SMTP 2525 and admin `8090` with `admin:local-dev-only` (`:139-142`)
- Phoenix `6006` (`:153`)
- api `8000` (`:174`)

On Windows with Docker Desktop these listen on all interfaces, so anyone on the same Wi-Fi or LAN, including during live tests, reaches the databases and the mail admin. **Fix:** prefix every mapping with `127.0.0.1:` (e.g. `"127.0.0.1:5432:5432"`).

### M13. Stalwart recovery admin and the SES SMTP password are handed to the app containers
`pack.env.example:83-84` sits in the **app** env file, which `compose.app.yml:9` loads into api, worker, beat, voice and migrate. It contains `KX_STALWART_RECOVERY_ADMIN=admin:CHANGE` and `KX_SES_SMTP_PASSWORD`. Those values belong only to the mail stack (`compose.mail.yml:26-27`). Removing the recovery admin after setup is a manual step (`GO_LIVE_CHECKLIST.md:46`). If it is forgotten, a full mail admin credential stays in two stacks' environments for good. **Fix:** move both into a separate `mail.env` used only by `compose.mail.yml`. Add a go-live check that `docker inspect` of stalwart shows an empty `STALWART_RECOVERY_ADMIN`. Use a long random recovery password.

### M14. Volume backups are crash-inconsistent and never restore-tested
`volumes-to-r2.sh:17-20` tars Stalwart's RocksDB directory while Stalwart keeps writing. RocksDB SST, WAL and MANIFEST files copied at different instants may not open cleanly. `runbooks/restore.md` drills only Postgres. **Fix:** `docker stop` Stalwart for the copy (seconds at 03:30), or use Stalwart's own export/backup command. Add a monthly volume restore into a scratch container that checks the store opens and the DKIM keys exist.

### M15. Container hardening is thin
- No service sets `cap_drop: [ALL]` or `security_opt: [no-new-privileges:true]`. Only the app services are `read_only`, which is good (`compose.app.yml:10-11`).
- Chromium runs with `--no-sandbox` in the worker (`packages/kx-core-crm/src/kx/crm/pdf.py:282`). Jinja autoescape is on (`pdf.py:337`), which lowers the risk. Still, any HTML-injection gap becomes renderer code execution plus `file:///proc/self/environ` disclosure (all secrets), because the page loads from a `file://` URL with JavaScript and network enabled.
- The SigNoz collector runs as `0:0` with `/var/run/docker.sock` (`signoz-overrides.yml:19-24`); `:ro` does not restrict the Docker API. That collector is root on the host and sits on `kx`, receiving OTLP from the app.

**Fix:** add `cap_drop: [ALL]` and `no-new-privileges` to every service (add back only what Postgres, Redis or LiveKit need). Render PDFs with `--blink-settings=scriptEnabled=false`, `--disable-remote-fonts`, a proxy-bypass to `127.0.0.1:0` (no network), and a Chromium seccomp profile, or run Chromium in a tiny separate sandboxed service. Put the collector's Docker access behind a read-only socket proxy.

### M16. Image freshness and dependency updates are manual
- Base images use floating tags without digests: `python:3.14-slim-trixie`, `python:3.12-slim-bookworm`, `pgvector:pg18`, `redis:8.2`, `cpu-1.8`.
- apt packages are unpinned (`Dockerfile:53-70`; `hadolint ignore`).
- No scheduled rebuild, no Trivy/Grype scan, no Dependabot/Renovate (`.github/` has only two workflows).
- The app image ships Chromium (`Dockerfile:57`), which takes high-severity CVEs most months.

**Fix:** a weekly `schedule:` workflow that rebuilds, scans with Trivy (failing on fixable CRITICAL), and opens a PR. Renovate for `uv.lock`, Docker digests and actions. Pin images by digest in the stacks (`KX_IMG_*@sha256:…`).

---

## LOW / INFO

| # | Finding | Where | Fix |
|---|---|---|---|
| L1 | Health check always healthy for worker, beat and voice (exit 0 if `KX_PROCESS != api`). A hung worker is never restarted; only the external `/healthz/beat` alert catches it. | `docker/healthcheck.py:17-18` | Worker: `celery inspect ping -d celery@$HOSTNAME`. Beat: heartbeat age in Redis. Voice: LiveKit worker health port. |
| L2 | No `logging:` on `cloudflare-bouncer` (unbounded json-file). Local compose services also lack rotation. | `compose.edge.yml:29-37` | Add the `*logs` anchor; set daemon-wide `log-opts` in `/etc/docker/daemon.json`. |
| L3 | `tmpfs: [/tmp]` has no explicit size. Chromium temp files and `HOME=/tmp` caches count against RAM. | `compose.app.yml:11`, `docker-compose.yml:41` | `tmpfs: ["/tmp:size=512m,mode=1777"]`. |
| L4 | FastAPI default `/docs` and `/openapi.json` are public. No HSTS, `X-Content-Type-Options` or `Referrer-Policy` set in the app; it relies on Cloudflare/SafeLine config that is not in the repo. | `apps/arifaa-pack/src/arifaa_pack/api.py:45` | `docs_url=None, openapi_url=None` in production. Set HSTS (preload), min TLS 1.2 and "Full (strict)" in Cloudflare and record it in the go-live checklist. |
| L5 | Speech image: only top-level pins, no hashes; `huggingface_hub` and other transitives float. Models fetched with no `revision=` pin. No request concurrency limit. | `services/speech/requirements.txt:3-9`, `services/speech/download.py:28,32`, `services/speech/Dockerfile:4,11` | `pip-compile --generate-hashes`; pin HF `revision` commit SHAs; add a semaphore for the endpoints. |
| L6 | Role passwords passed as `psql -v pw=…` arguments are visible in `ps` inside the container while bootstrap runs. On an error the formatted `ALTER ROLE … PASSWORD '…'` can land in the server log. | `infra/server/postgres/db_bootstrap.sh:61-64` | Pipe them through stdin / `\set` from env; set `log_min_error_statement=panic` for the session or pre-hash with `SCRAM-SHA-256$…`. |
| L7 | `${PUBLIC_API_URL:?}` is interpolated even when the `selfhost-web` profile is off, so a Coolify deploy without it fails. | `compose.app.yml:98,108` | `${PUBLIC_API_URL:-}`, or move the services to a separate file. |
| L8 | Beat singleton is enforced only by convention (one service, "never scale"). A second `kx beat` (a local run pointed at prod Redis, a Coolify scale) doubles every periodic job. | `compose.app.yml:65-72`, `celery_app.py:9` | A Redis lock in Beat startup (`SET kx:beat:lock NX EX`), or RedBeat with its lock. Periodic jobs are idempotent per the docstring, which reduces the impact. |
| L9 | `make lint-ci` uses unpinned `uvx` tools (CI pins them). | `Makefile:104-105` | Pin the same versions as `ci.yml:128,132`. |
| L10 | One env file for every app process: `migrate` and `beat` receive vendor API keys they never use. | `compose.app.yml:9` | Per-process env files, or at least keep mail/SES/LiveKit server secrets out of api, worker and beat. |
| L11 | PgBouncer details: `* = host=postgres` routes any DB name, including `postgres` (app roles keep default CONNECT on it); `max_db_connections=40` is never reached because pool 20 + reserve 5 = 25; `server_reset_query` is ignored in transaction mode. | `infra/server/postgres/pgbouncer.ini:6,15-22` | List the three DBs explicitly; `REVOKE CONNECT ON DATABASE postgres FROM PUBLIC`; fix the comment. |
| L12 | LiveKit TURN is UDP-only (no TURN/TLS on 443). Callers on corporate networks that block UDP cannot connect. | `infra/server/stacks/livekit.yaml:9-11` | Accept for v1, or add `turn.tls_port` with a real certificate on a separate hostname. |

---

## Connection-pool arithmetic (resilience check)

Defaults (`settings.py:106-107`; `pack.env.example` does not override them): `pool_size=5`, `max_overflow=5`, so up to 10 client connections per process per DB.

| Process | Processes | DBs | Max client conns |
|---|---|---|---|
| api (uvicorn, 1 worker) | 1 | 3 | 30 |
| worker (prefork, concurrency 3) | 3 | 3 | 90 |
| voice (idle 2 + up to 3 concurrent free calls) | ≈ 5 | 3 | ≈ 150 |
| beat | 0–1 | — | ≤ 30 |
| **Total through PgBouncer** | | | **≈ 270–300** of `max_client_conn=1000` |

Server side: PgBouncer allows `default_pool_size 20 + reserve 5 = 25` per (db,user), so 3 × 25 = 75. Add CMS direct (Payload pg pool, typically ≤ 10), migrate (1–3), `superuser_reserved_connections` 5, and the bootstrap and WAL-G sessions: **≈ 95 of `max_connections=200`.** There is headroom.

The real limit is 20–25 concurrent *transactions* per DB. Under bursts (worker plus voice), clients queue in PgBouncer, up to `query_wait_timeout` (120 s default). That shows up as tail latency, not errors. Add a SigNoz alert on PgBouncer `cl_waiting`, or `SHOW POOLS` through a small exporter.

A second project (MULTI_PROJECT.md) adds about 75 more server connections, which still fits. Re-check before the third.

Redis `maxmemory 512mb` with `noeviction` (`compose.app.yml:29`): when full, Celery publishes fail. The API's kicker raises and leaves the rows for the next sweep (`jobqueue.py:55-56`), which is a graceful degradation. Rate limits and spend counters also fail closed or open depending on the code (not reviewed). Alert on `used_memory / maxmemory > 0.8`.

---

## Done well

- **App image:** multi-stage build; `uv sync --frozen --no-dev --no-editable`; non-root `10001:10001`; root-owned, immutable venv; `read_only: true` + tmpfs in both compose files; `HEALTHCHECK`; `.dockerignore` excludes `.env*`, tests and infra (`Dockerfile`, `.dockerignore`).
- **Dependencies:** `uv.lock` present with hashes (1,489), used with `--frozen` in Docker and CI (`UV_FROZEN=1`). The uv and setup-uv versions are pinned, as are actionlint and hadolint in CI.
- **CI:** least-privilege default `permissions: contents: read`; no `pull_request_target`; release gated on the full CI via `workflow_call`; `concurrency` set; compose `config -q`, shellcheck, hadolint and actionlint run on every stack.
- **Network exposure:** server stacks publish only what must be public (25, 7881, 3478, 50000-50100). Postgres, PgBouncer, Redis, TEI, speech, Phoenix and Stalwart admin are `expose`/network-only. The OTLP collector and the CrowdSec LAPI are bound to `127.0.0.1`.
- **Database:** SCRAM everywhere (`password_encryption`, PgBouncer `auth_type`); per-company roles that own only their own DB; `REVOKE CONNECT/TEMP FROM PUBLIC`; `NOCREATEDB NOCREATEROLE`; per-role `statement_timeout`; extensions created by the superuser, not the app; the reporting role granted narrowly by migration (D5). `db_bootstrap.sh` uses `set -euo pipefail` and builds SQL with `format(%I,%L)`, so identifiers and literals are safely quoted.
- **Backups:** WAL-G continuous archiving plus nightly base backups and `retain FULL 7`; a documented monthly restore drill; `volumes-to-r2.sh` uses `set -euo pipefail`, quoted expansions and a `trap` cleanup.
- **Celery:** JSON-only serializer (no pickle), `acks_late` + `reject_on_worker_lost`, prefetch 1, idempotent job claims, long waits kept in Postgres rather than ETAs.
- **Log rotation:** 50 MB × 3 on nearly every service, with labels so SigNoz can skip duplicate app logs.
- **Secret guards in code:** JWT key and TOTP encryption key refuse to be missing in staging/production; `SecretStr` is used throughout; CORS uses an explicit origin list with credentials, not `*`.
- **Docs:** clear runbooks (restore, rotate, incident); third-party images pinned through `KX_IMG_*` variables, with a "never latest" rule for third-party images; the Stalwart recovery admin defaults to empty in the mail stack.
