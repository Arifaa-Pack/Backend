# Dedicated server (Namecheap + Coolify)

Server plan v2 §5: Intel Xeon E-2236 (6 cores / 12 threads), 64 GB RAM, 2 × 960 GB SSD in RAID 1, Ubuntu Server 24.04 LTS, one public IPv4 with reverse DNS, managed with **Coolify**. Decisions D45–D53; design in `docs/ARCHITECTURE.md`, databases in `docs/DATABASE.md`, the paid / free switch in `docs/PLATFORM_MODES.md`.

⚠ All Namecheap dedicated servers are in Phoenix, Arizona (USA). Arifa must approve the location (OPEN #69). Nothing here is Namecheap-specific: the same stacks run on any Ubuntu 24.04 x86_64 server.

## 1. Layout

```
infra/server/
  README.md                this file
  stacks/
    compose.data.yml       postgres (pgvector:pg18 + WAL-G), pgbouncer
    compose.ai.yml         embeddings (TEI cpu-1.8: BGE-M3 or Qwen3), speech (faster-whisper Small + Piper)
    compose.voice.yml      livekit server (+ livekit.yaml)
    compose.mail.yml       stalwart v1.1.1 (MX + JMAP; relays out through SES)
    compose.obs.yml        phoenix + phoenix-postgres; SigNoz is installed from its own pinned compose (see §5)
    compose.edge.yml       crowdsec + Cloudflare bouncer (+ crowdsec-acquis.yaml); SafeLine from its own compose (§6)
    compose.app.yml        migrate, api, worker, beat, voice, redis, cms; web / crm-web (profile selfhost-web)
    env/pack.env.example   the app environment for company `pack`
    livekit.yaml           LiveKit server config (ports; keys come from LIVEKIT_KEYS)
  postgres/
    Dockerfile             pgvector/pgvector:pg18 + WAL-G binary
    postgresql.conf        tuned for 64 GB (shared_buffers 8 GB, max_connections 200, WAL archiving)
    pg_hba.conf            superuser over the local socket only; app roles over TCP from kx-data only (+ pg_ident.conf)
    pgbouncer.ini          transaction mode, pool sizes
    db_bootstrap.sh        roles, databases (with company prefix), extensions, per-role settings
    (WAL-G → R2 settings are environment variables of the postgres service in compose.data.yml)
  backup/
    volumes-to-r2.sh       nightly encrypted tarball of Stalwart volumes (Stalwart stopped for the copy) and Coolify config → R2
    volumes-restore-test.sh  monthly: newest Stalwart copy restored into scratch volumes and started offline (M14)
    pg-health-check.sh     every 10 min: WAL archiving, WAL backlog, base-backup age, disk → Healthchecks.io (M10)
  runbooks/
    restore.md             database point-in-time restore + monthly restore test
    switch-mode.md         paid / free switch per field
    rotate-secrets.md      key rotation
    incident.md            server down, vendor down, disk full
    add-project.md         adding a second company (design: docs/MULTI_PROJECT.md)
```

Stacks meet on small external Docker networks, one per trust zone (H1, live-test review 18 Nov), so a foothold in one public-facing container (CMS, Stalwart :25, LiveKit, the SigNoz collector) does not reach everything else. Services still find each other by name.

| Network | Created with | Members | Why |
| --- | --- | --- | --- |
| `kx-data` | `--internal --subnet 10.201.0.0/24` | postgres, pgbouncer; the app services, cms | the database; `pg_hba.conf` trusts this subnet only |
| `kx-ai` | `--internal` | embeddings, speech; the app services | TEI and speech have no authentication |
| `kx-obs` | `--internal` | phoenix, signoz-otel-collector; the app services | traces and logs (Phoenix also requires a login) |
| `kx-mail` | `--internal` | stalwart; the app services | JMAP / admin :8080 |
| `kx-proxy` | `--subnet 10.200.0.0/24 --ip-range 10.200.0.128/25` | coolify-proxy (fixed `10.200.0.2`), api, cms, web, crm-web, livekit | what the Coolify proxy routes to; the only peer the api trusts for `X-Forwarded-*` |
| stack-local | by compose | redis (internal, app stack only); phoenix-postgres (internal); each stack's `default` | Redis is never on a shared network; `default` gives a stack egress (R2, SES, model downloads, vendors) and carries published ports |

`internal` cannot be set from compose on an external network: create the networks with the flags above. If you change a subnet, change `postgres/pg_hba.conf` (kx-data) or `KX_APP_FORWARDED_ALLOW_IPS` (kx-proxy) with it.

## 2. First-time server setup

1. Ubuntu 24.04: `apt update && apt full-upgrade`, unattended-upgrades on, a non-root sudo user, SSH keys only, `PasswordAuthentication no`.
2. Firewall (ufw): allow 22/tcp from admin IPs; 80, 443/tcp from Cloudflare IP ranges only; 25/tcp (mail); 7881/tcp, 3478/udp, 50000–50100/udp (LiveKit media).
   **ufw does not see Docker-published ports** (H2): Docker DNATs them in `nat PREROUTING` and filters them in `FORWARD`, ahead of ufw's `INPUT` rules. ufw only covers what listens on the host itself (sshd, SafeLine's host-network 80 / 443). For containers, add a `DOCKER-USER` backstop that lets in only the public container ports, at the end of `/etc/ufw/after.rules` (`eth0` = the public interface, check with `ip -br a`), then `ufw reload`:
   ```
   *filter
   :DOCKER-USER - [0:0]
   -A DOCKER-USER -m conntrack --ctstate RELATED,ESTABLISHED -j RETURN
   -A DOCKER-USER -i eth0 -p tcp -m conntrack --ctorigdstport 25 --ctdir ORIGINAL -j RETURN
   -A DOCKER-USER -i eth0 -p tcp -m conntrack --ctorigdstport 7881 --ctdir ORIGINAL -j RETURN
   -A DOCKER-USER -i eth0 -p udp -m conntrack --ctorigdstport 3478 --ctdir ORIGINAL -j RETURN
   -A DOCKER-USER -i eth0 -p udp -m conntrack --ctorigdstport 50000:50100 --ctdir ORIGINAL -j RETURN
   -A DOCKER-USER -i eth0 -j DROP
   -A DOCKER-USER -j RETURN
   COMMIT
   ```
   (`ufw-docker`, github.com/chaifeng/ufw-docker, writes equivalent rules if you prefer a tool.) Everything else a container publishes then answers on loopback only, even if a compose file forgets `127.0.0.1:`.
3. Install Coolify (official script). Its installer publishes the dashboard (8000) and realtime (6001, 6002) on every interface, and Coolify is root on the host: bind them to loopback with `/data/coolify/source/docker-compose.custom.yml` (read by Coolify's installer / upgrade; check the service names against `/data/coolify/source/docker-compose.prod.yml` of your version), then re-run the installer or `docker compose ... up -d` as Coolify's docs say:
   ```yaml
   services:
     coolify:
       ports: !override ["127.0.0.1:8000:8080"]
     soketi:
       ports: !override ["127.0.0.1:6001:6001", "127.0.0.1:6002:6002"]
   ```
   Open the dashboard through a tunnel only: `ssh -L 8000:127.0.0.1:8000 -L 6001:127.0.0.1:6001 -L 6002:127.0.0.1:6002 <server>`.
   **Proxy (Traefik), Servers → Proxy → configuration:** SafeLine owns 80 / 443, so the proxy's entry points go to loopback, and its own dashboard port (container 8080, published as `8080:8080` by default) is removed so nothing else wants host 8080 (SigNoz's UI is moved to 3301, §5). Ports block: `"127.0.0.1:8080:80"`, `"127.0.0.1:8443:443"`, nothing else. Join the proxy to `kx-proxy` at its fixed address in the same file (`networks: { kx-proxy: { ipv4_address: 10.200.0.2 } }` on the proxy service, `kx-proxy: { external: true }` under `networks:`), so it survives a proxy restart. App services carry `traefik.docker.network: kx-proxy`, so Traefik reaches them over that network and the api sees `10.200.0.2` as the peer (`KX_APP_FORWARDED_ALLOW_IPS`).
4. Networks (§1):
   ```bash
   docker network create --internal --subnet 10.201.0.0/24 kx-data
   docker network create --internal kx-ai
   docker network create --internal kx-obs
   docker network create --internal kx-mail
   docker network create --subnet 10.200.0.0/24 --ip-range 10.200.0.128/25 kx-proxy   # .2 stays free for the proxy
   ```
5. Secrets outside the Git checkout (H5): Coolify re-clones the repo on each deploy, so nothing secret may live in it.
   ```bash
   install -d -m 0700 -o root -g root /opt/kx/secrets
   # pack.env / pack-cms.env (from stacks/env/*.example), userlist.txt (PgBouncer), backup.env (§7),
   # pg-health.env (§5), crowdsec-cloudflare-bouncer.yaml (step 7): each 0600 root
   (umask 077; openssl rand -hex 32 > /opt/kx/secrets/postgres_password)   # M8: read once by initdb
   ```
   The compose files read `/opt/kx/secrets/pack.env`, `/opt/kx/secrets/pack-cms.env`, `/opt/kx/secrets/userlist.txt`, `/opt/kx/secrets/postgres_password` and `/opt/kx/secrets/crowdsec-cloudflare-bouncer.yaml` by default (`KX_ENV_FILE`, `KX_CMS_ENV_FILE`, `PGBOUNCER_USERLIST`, `KX_PG_SUPERUSER_PASSWORD_FILE`, `KX_CF_BOUNCER_CONFIG` override them). The Postgres superuser password is a file, not an environment variable (M8): it never shows in `docker inspect`, and the superuser logs in over the local socket only, so the password is never used for a login. Other compose-level secrets (`REDIS_PASSWORD`, `WALG_LIBSODIUM_KEY`, `PHOENIX_SECRET`, R2 keys, …) are Coolify environment variables marked encrypted, each on the one resource that uses it: `KX_SES_SMTP_PASSWORD` and `KX_STALWART_RECOVERY_ADMIN` on the mail resource only, never in `pack.env` (M13). The app resource also needs `KX_APP_IMAGE` and `KX_CMS_IMAGE` (immutable `…:<tag>@sha256:<digest>`; the release workflow updates `KX_APP_IMAGE`, M1). `stacks/env/*.env`, `stacks/secrets/` and every `userlist.txt` are git-ignored as a backstop.
6. In Coolify: one project per company (`pack`), environments `staging` and `production`; add each stack in `stacks/` as a *Docker Compose* resource from this Git repo. Coolify may add its own per-resource network to every service: after the first deploy, check that each `kx-*` network holds only the members in §1 (`docker network inspect kx-data --format '{{range .Containers}}{{.Name}} {{end}}'`, same for the others).
7. CrowdSec host firewall bouncer (`apt install crowdsec-firewall-bouncer-iptables`), `/etc/crowdsec/bouncers/crowdsec-firewall-bouncer.yaml`: `api_url: http://127.0.0.1:8081/`, `api_key: <CROWDSEC_FW_BOUNCER_KEY>`, and `iptables_chains: [INPUT, DOCKER-USER]`. By default it only fills `INPUT`, and the direct container ports (25, 7881, 3478, 50000–50100) pass `FORWARD` / `DOCKER-USER`, so its bans would not reach Stalwart or LiveKit.
   **CrowdSec, the rest of M7** (verify each against the installed versions):
   - *Scope the firewall bouncer* to the direct ports, so an HTTP decision (whose IP may be a Cloudflare edge if real-IP is wrong) can never cut the site off: in the same file, `scenarios_containing: ["ssh", "smtp", "imap", "auth"]` (or `origins: [crowdsec, cscli]` plus that list). HTTP bans go to Cloudflare only (the Cloudflare bouncer).
   - *Cloudflare IPs are whitelisted*: `crowdsecurity/whitelist-good-actors` (in `compose.edge.yml`) installs the `crowdsecurity/cdn-whitelist` postoverflow. Check after the first start: `docker compose exec crowdsec cscli postoverflows list` shows it.
   - *SafeLine logs the real client IP*: in SafeLine's console, per site, take the source IP from the `CF-Connecting-IP` header (SafeLine 9.2: site → advanced → "source IP" / X-Forwarded-For setting; verify the name in the pinned release). Check one SafeLine log line shows a non-Cloudflare client IP.
   - *Cloudflare bouncer config file* (the image does not configure itself from environment variables): `docker run --rm crowdsecurity/cloudflare-bouncer:v0.3.0 -g <CLOUDFLARE_API_TOKEN> > /opt/kx/secrets/crowdsec-cloudflare-bouncer.yaml`, then edit it: `crowdsec_lapi_url: http://crowdsec:8080/`, `crowdsec_lapi_key: <CROWDSEC_CF_BOUNCER_KEY>`, `log_mode: stdout`, and check the account / zone ids; `chmod 600`. The stack mounts it (`KX_CF_BOUNCER_CONFIG`).
   - *Stalwart*: CrowdSec now reads Stalwart's container log through a read-only Docker API proxy (`docker-socket-proxy`: containers, events, info; POST refused; tested 18 Nov), with `type: stalwart`. The hub has **no Stalwart parser** (checked 18 Nov), so these lines are read but not parsed: SMTP / IMAP password guessing is not banned by CrowdSec until a parser exists (write one, or check the hub again at each upgrade). Meanwhile make sure Stalwart's own authentication-failure ban (fail2ban-style setting in its web admin) is on.
   - `cscli metrics show acquisition` lists `docker:/<stalwart container>`, `file:/var/log/safeline/...` and `file:/var/log/host/auth.log` with lines read.
8. After the first full deploy, check what listens and what is reachable, and record both in `docs/GO_LIVE_CHECKLIST.md` (C9):
   - on the server: `ss -lntup`. On `0.0.0.0` / `[::]` only: 22, 25, 80, 443, 7881, 3478/udp, 50000–50100/udp. On `127.0.0.1` only: 8000, 6001, 6002, 8080, 8443, 8081, 3301, 4317, 4318, 6006, 9443.
   - from a machine outside (not an admin IP, not Cloudflare): `nmap -Pn -p- <server ip>` and `sudo nmap -Pn -sU -p 3478,50000-50100 <server ip>`. Open: 25, 7881 (tcp), 3478 and the media range (udp, often shown `open|filtered`). Everything else, 22 / 80 / 443 included, filtered.
9. **Coolify API for the release workflow (M2).** GitHub's runners must reach Coolify's `/api/v1/...` (update an env variable, start a deployment, read its status), but the dashboard stays on 127.0.0.1 (step 3): never publish it. Expose only the API through a Cloudflare Tunnel with Cloudflare Access in front (verify each setting in the current Cloudflare UI):
   - `cloudflared` on the host (apt package or its container on the host network), a tunnel with one public hostname, e.g. `coolify-api.<domain>` → `http://127.0.0.1:8000`, path `^/api/` only (a second rule for everything else: `http_status:404`).
   - Cloudflare Zero Trust → Access → Applications: a self-hosted app on that hostname with a policy *Service Auth* allowing one **service token** only (no identity login). Its client id / secret go to the GitHub secrets `CF_ACCESS_CLIENT_ID` / `CF_ACCESS_CLIENT_SECRET`; the workflow sends them as `CF-Access-Client-Id` / `CF-Access-Client-Secret`. `COOLIFY_URL` = `https://coolify-api.<domain>`.
   - Coolify → Keys & Tokens: an API token for the workflow with **write + deploy** only (not root, not read:sensitive): `deploy` starts deployments, `write` updates `KX_APP_IMAGE`. Create the `KX_APP_IMAGE` variable on the app resource once by hand (the workflow updates it; it does not create it).
   - Check: `curl -sS https://coolify-api.<domain>/api/v1/version` without the Access headers answers Cloudflare's 403; with them and the token it answers the version. `https://coolify-api.<domain>/` (the dashboard) is 404.
   - Alternative if Access is not wanted: a self-hosted GitHub runner on the server (then the API stays on loopback; the runner must not run PR workflows).

## 3. Order of deployment

| Step | Stack | Check |
| --- | --- | --- |
| 1 | `compose.data.yml` | `pg_isready`; then run `postgres/db_bootstrap.sh` with `KX_BOOTSTRAP_PREFIXES=pack_` inside the container (`docker compose exec postgres /kx/db_bootstrap.sh`: the superuser logs in over the local socket only); `wal-g backup-list` shows a base backup; from the app stack, `psql -h postgres -U postgres` is refused; `docker inspect` of postgres shows no `POSTGRES_PASSWORD` (only `POSTGRES_PASSWORD_FILE`). Re-running `db_bootstrap.sh` later leaves existing passwords alone (M6); `KX_BOOTSTRAP_ROTATE=1` changes them, after which `userlist.txt` must be regenerated (`runbooks/rotate-secrets.md`) |
| 2 | `compose.ai.yml` | `curl http://embeddings/health`, `curl http://speech:8100/healthz` (models download on first start, ≈ 3 GB) |
| 3 | `compose.voice.yml` | `https://voice.<domain>` answers through Cloudflare (Coolify routes it to `livekit:7880`); UDP ports reachable from outside |
| 4 | `compose.mail.yml` | Stalwart v1.1.1 web setup done (steps in the file's header): domain, DKIM (RSA + Ed25519), the `ses` relay route + outbound strategy, the `sales@` account (the app's JMAP login; bounce reports land there too); then delete `KX_STALWART_RECOVERY_ADMIN` from the mail resource, redeploy, and `docker inspect <stalwart> --format '{{.Config.Env}}'` shows `STALWART_RECOVERY_ADMIN=` empty (M13) |
| 5 | SigNoz, `compose.obs.yml` | SigNoz UI (tunnel to 127.0.0.1:3301), Phoenix UI (tunnel to 127.0.0.1:6006): sign in as Phoenix's default admin, change its password at once, create a system API key for `KX_APP_AI_TRACE_HEADERS` |
| 6 | SafeLine, `compose.edge.yml` | test page through Cloudflare; WAF logs |
| 7 | `compose.app.yml` | `migrate` exits 0; `/healthz` and `/healthz/beat` return 200; `kx platform show`; Redis without the password answers `NOAUTH` (`docker compose exec redis env -u REDISCLI_AUTH redis-cli ping`) |

## 4. Traffic

```
Internet ─► Cloudflare (proxied DNS, TLS, DDoS) ─► SafeLine WAF :80/:443 ─► Coolify proxy 127.0.0.1:8080/8443 ─► api / cms / web / livekit:7880 (kx-proxy)
Internet ─► :25 Stalwart (MX)          Internet ─► server IP: LiveKit media :7881/tcp, :3478/udp, :50000-50100/udp
```

- **Voice (H7).** `voice.` is a *proxied* Cloudflare record like `api.`: browsers open the LiveKit signalling WebSocket `wss://voice.<domain>` through Cloudflare (which carries WebSockets) → SafeLine → Coolify proxy → `livekit:7880`. Only the media is direct: LiveKit tells the browser to send it to the server IP (`--node-ip`), not to the hostname, on 7881/tcp, 3478/udp and 50000–50100/udp. So 443 stays open to Cloudflare only and no browser ever sees the Cloudflare origin certificate. (Before 18 Nov `voice.` was DNS-only; that cannot work: 443 drops non-Cloudflare IPs, and the origin certificate is not browser-trusted. Do not "fix" it by opening 443 to everyone: that removes the origin lock the next point depends on.) Check in the go-live voice test that calls stay up past Cloudflare's WebSocket idle limit.
- SafeLine: site per host (`api.`, `cms.`, `crm.`, `www.`, `voice.`, WebSocket upgrade allowed on `voice.`); **allow-list** `/webhooks/whatsapp`, `/webhooks/telnyx/*`, `/webhooks/cms` (no challenge; rate limit kept; the app checks signatures). Default OWASP rules blocked every WhatsApp webhook in the bench with BunkerWeb; SafeLine passed 86 / 86.
- The app trusts `CF-Connecting-IP` for the client IP (`KX_TRUSTED_CLIENT_IP_HEADER=CF-Connecting-IP`) because every web request comes through Cloudflare. That is only safe while nothing but Cloudflare can reach 443: keep the Cloudflare-only ufw rule, and turn on Cloudflare **Authenticated Origin Pulls** (mTLS) with SafeLine requiring Cloudflare's origin-pull client certificate (check SafeLine 9.2's client-certificate setting). The app itself must accept `CF-Connecting-IP` only when the request's peer, after the proxy, is in Cloudflare's IP ranges (being added in the app code, separately from these files). uvicorn takes `X-Forwarded-*` only from the Coolify proxy's fixed address (`KX_APP_FORWARDED_ALLOW_IPS=10.200.0.2`), never `*`.
- CrowdSec reads SafeLine / Stalwart / SSH logs. Bans for proxied traffic go to Cloudflare (Cloudflare bouncer); bans for direct ports (22, 25, LiveKit) go to the host firewall bouncer, on `INPUT` and `DOCKER-USER` (§2). Never ban Cloudflare's own IPs at the host.

## 5. Monitoring

- **SigNoz:** retention 15 days logs, 30 days traces and metrics. Alert rules on the metrics `VendorSwitch`, `QueueLagSeconds`, `SpendCap` and `OutboundStuck`, which SigNoz stores lowercase: `vendorswitch > 0`, `queuelagseconds > 300`, `spendcap >= 80`, `outboundstuck > 0` (D6: a message the worker died on; the salesperson is told too), error-log rate; channel = email through Stalwart.
  - **How to install.** SigNoz dropped its Docker Compose files after **v0.129.0** (from v0.130 the supported installer is *Foundry*, `foundryctl cast`). Use the official `deploy/docker/docker-compose.yaml` of tag **v0.129.0** (SigNoz v0.129.0, otel-collector v0.144.5, ClickHouse 25.5.6, ZooKeeper 3.7.1; ≈ 0.7 GB RAM idle) **with our override**:
    `docker compose -f docker-compose.yaml -f /opt/kairox/infra/server/signoz/compose.signoz-overrides.yml up -d`
    (env `KX_SIGNOZ_CONFIG_DIR`, `KX_HOST_NAME`; the override puts the collector on the `kx-obs` network, binds OTLP 4317 / 4318 to 127.0.0.1 only, moves the SigNoz UI from `8080` on every interface to `127.0.0.1:3301` (tunnel: `ssh -L 3301:127.0.0.1:3301`; host 8080 is the Coolify proxy's), mounts `/` read-only at `/hostfs`, the Docker socket and `/var/lib/docker/containers` read-only, and runs the collector as root because those files are root-only).
  - **Create the admin account straight after the first start** (web UI, or `POST /api/v1/register`). Until an organisation exists the collector gets no configuration over OpAMP and **does not open 4317 / 4318**: the app's data is refused (connection refused, retried, then dropped).
  - **What arrives** (tested live 17 Nov, `infra/server/signoz/otel-collector-config.yaml`):
    - *App traces:* one span per API request (`/healthz*` excluded) and Celery spans for publishing and running jobs, so Beat → `apply_async` → `kx.run_job` is one trace; model calls appear as `anthropic.messages.create` spans (model, error type; no prompt text). `service.name` = `kx-api|kx-worker|kx-voice` (Beat itself sends nothing; its jobs show up as worker traces), `service.namespace` = the company code.
    - *App logs:* every structlog line after PII masking, with the trace id of its request or job, and `exception.type / message / stacktrace` for `log.exception` (masked). Library warnings / errors (uvicorn, Celery, SQLAlchemy) too.
    - *Server:* `system.*` metrics every 30 s (CPU, load, memory, paging, disks, filesystems, network).
    - *Containers:* `container.*` CPU / memory / network / block I/O per container every 60 s (`docker_stats`; on Docker Desktop some one-shot stats calls are cancelled, so a few points are missing; check on the server).
    - *Other containers' logs* (Postgres, Stalwart, LiveKit, ClickHouse, …) from their json-file logs, named by compose service (`labels` logging option, set in every stack). App containers carry the label `kx.logs=otlp` and are skipped there, so their lines are not stored twice.
  - Metric names arrive **lowercase** (`vendorswitch`, `spendcap`, `queuelagseconds`).
  - Volume measured idle: ≈ 35 Celery spans a minute from Beat's relay / sweep jobs (≈ 50,000 a day, small in ClickHouse).
- **UptimeRobot** (free, outside): `https://api.<domain>/healthz` and `/healthz/beat` every 5 minutes; alerts by email / app.
- **Database backups and the disk (M10).** Everything runs on this one server, so a failing WAL archive (R2 down, key wrong) fills `pg_wal` until the disk is full and every tier stops. `backup/pg-health-check.sh` runs from root's cron every 10 minutes (cron line and variables in its header; env in `/opt/kx/secrets/pg-health.env`) and pings a **Healthchecks.io** check (free; period 10 min, grace 10 min) on success, or `/fail` with the reason when: archiving failed after its last success (`pg_stat_archiver`), more than 20 segments wait in `pg_wal/archive_status`, the newest WAL-G base backup is older than 26 h, or `/var/lib/docker` is over 80 % full. A stopped cron or a dead server is a missed ping, so it alerts too. Also add a SigNoz alert on the host metrics already collected: `system.filesystem.utilization > 0.8` for `/hostfs` (or `/hostfs/var/lib/docker` if it is a separate mount).
  - **RPO / RTO (record in the go-live sign-off, docs/GO_LIVE_CHECKLIST.md C11).** RPO ≈ 5 minutes (`archive_timeout = 300`: at most the last unarchived segment is lost if the server dies). RTO after losing the server ≈ order + provision a new one (Namecheap: hours to a day) + `README §2–§3` + `wal-g backup-fetch` (≈ 30–60 minutes for the current database size; measure it in the monthly restore test). Both are acceptable for v1 only if the client agrees in writing.
- **Memory (M4).** Every service has a memory ceiling (§9). `docker stats --no-stream` after the first full deploy: each container's `MEM USAGE / LIMIT` shows the limit (not the host's 62 GiB); a container that keeps restarting with `OOMKilled: true` (`docker inspect`) has outgrown its ceiling.
- Docker log rotation: `max-size 50m`, 3 files (set per service in the compose files, with the `labels` option above).

## 6. Firewall: SafeLine

Install from SafeLine's official compose at a pinned release (9.2.x). It uses host networking on 80 / 443; upload the Cloudflare origin certificate; upstream = `http://127.0.0.1:8080` (Coolify proxy).
Its management console (9443) is published on every interface by default (H2): set `MGT_PORT=127.0.0.1:9443` in SafeLine's `.env` before the first `up` (check how its compose uses `MGT_PORT` in the pinned release), and open it through a tunnel (`ssh -L 9443:127.0.0.1:9443`). The `DOCKER-USER` backstop (§2) drops it from outside in any case.

## 7. Backups

| What | How | Kept | Where |
| --- | --- | --- | --- |
| PostgreSQL (all app DBs) | WAL-G: continuous WAL push + nightly `backup-push`, encrypted (`WALG_LIBSODIUM_KEY`) | 7 full backups + WAL between | R2 `backups/postgres/` |
| Stalwart mail, Coolify config (without `/data/coolify/ssh`) | `backup/volumes-to-r2.sh` nightly, encrypted (rclone crypt, `KX_BACKUP_PASSPHRASE`); Stalwart is stopped for the seconds the copy takes (M14: a RocksDB copied while it writes may not open) | 14 days | R2 `backups/volumes/` |
| Phoenix, SigNoz | not backed up (telemetry) | — | — |

Monthly: restore tests (`runbooks/restore.md`): the database, and `backup/volumes-restore-test.sh`, which restores the newest Stalwart copy into scratch volumes and starts Stalwart on it with no network egress (cron: first Monday 04:00, its own heartbeat `KX_RESTORE_HEARTBEAT_URL`). RAID 1 is not a backup.

Backups hold every customer row and Coolify's `APP_KEY`, so (H4):
- **Encrypted before upload.** `WALG_LIBSODIUM_KEY` (compose.data.yml, required) and `KX_BACKUP_PASSPHRASE` (volumes) are both `openssl rand -hex 32`. Keep each **offline** too (password manager + a printed copy in the safe): a backup without its key cannot be restored. `/data/coolify/ssh` (the private keys Coolify logs in to this server with) is never copied; after a rebuild Coolify gets a new key pair.
- **Split R2 tokens, all scoped to the backup bucket only** (never the app's `KX_STORAGE__*` bucket): (1) a push token for WAL-G (postgres container env) and (2) one for `volumes-to-r2.sh` (`r2` remote in `/root/.config/rclone/rclone.conf`); (3) a separate *delete* token used only by the retention jobs: the `wal-g delete` cron and the `KX_BACKUP_PRUNE_REMOTE` remote of the volume script, both run from the host with a root-only file (`/opt/kx/secrets/walg-prune.env`, `runbooks/restore.md`). Give the push tokens the narrowest object permission R2 offers; if your account cannot issue a write-without-delete token (check the R2 token options), the bucket lock below is what stops a leaked push token, or a Postgres superuser reading the container env, from wiping backups.
- **R2 bucket lock** on the backup bucket: a retention rule on `backups/postgres/` of 7 days and on `backups/volumes/` of 14 days (no longer than the retention windows above, or the prune jobs fail). Objects younger than that cannot be deleted or overwritten by any token.
- **Heartbeat.** Set `KX_BACKUP_HEARTBEAT_URL` (Healthchecks.io, or an UptimeRobot heartbeat monitor) in `/opt/kx/secrets/backup.env`: the script pings it only after a complete, verified run, so a failed or silent night alerts. Cron line and environment: the script's header.

## 8. Image tags

Third-party images are pinned through overridable variables (`KX_IMG_*` in the compose files): Redis `8.2.3` (≥ 8.2.2: CVE-2025-49844), PgBouncer, TEI `cpu-1.8`, LiveKit, Stalwart, Phoenix, CrowdSec and its Cloudflare bouncer, the Docker API proxy (`tecnativa/docker-socket-proxy:v0.5.0`), Phoenix's Postgres (`pgvector/pgvector:0.8.7-pg18-bookworm`). Never `latest`.

- **Built here, pinned by digest (M11, M16):** the server Postgres image (`postgres/Dockerfile`: `pgvector/pgvector:0.8.7-pg18-bookworm@sha256:…`, the image `pg18` pointed to on 18 Nov), the app (`python:3.14-slim-trixie@sha256:…`, `uv:0.13.7@sha256:…`) and speech (`python:3.12-slim-bookworm@sha256:…`) images. WAL-G `v3.0.7` is verified against a SHA-256 in the Dockerfile (the release's own `.sha256`, checked against a fresh download on 18 Nov); the build fails without one. To upgrade WAL-G, change `WALG_VERSION` and `WALG_SHA256` together.
- **App images (M1):** `KX_APP_IMAGE` / `KX_CMS_IMAGE` are required, as `ghcr.io/<owner>/app:<tag>@sha256:<digest>`; the release workflow sets `KX_APP_IMAGE` through the Coolify API before each deploy (§2 step 9, `docs/DEPLOYMENT.md`).
- **Keeping them fresh (M16):** `.github/dependabot.yml` opens weekly PRs for action SHAs, base-image digests, `uv.lock` and the speech requirements; `.github/workflows/image-scan.yml` rebuilds the app, speech and postgres images every Monday without cache and fails on a CRITICAL vulnerability that has a fix (Chromium in the app image is the usual one): then merge the digest PRs and release. The `KX_IMG_*` defaults in `stacks/` may not be picked up by Dependabot (a tag inside `${…:-…}`): review them by hand monthly against each project's release notes, and pin by digest in Coolify (`KX_IMG_REDIS=redis:8.2.3@sha256:…`) if you want them immutable.

## 9. Capacity (free version, measured / vendor figures, v2 §5)

≈ 24–29 GB of 64 GB RAM at peak (10 users + 10 voice calls), ≈ 31 GB of 960 GB disk plus growth of ≈ 0.7 / 3.2 / 11.5 GB a month (Light / Normal / Heavy). Biggest RAM: SigNoz 4–8 GB, BGE-M3 4.8 GB, voice 4.3 GB, Piper 2.1 GB. Re-index the knowledge base outside sales hours (BGE-M3 uses every core for ≈ 30 min per 2,500 chunks; its container is capped at 6 of the 12 threads).

**Memory ceilings (M4, `deploy.resources.limits` in each stack).** Ceilings, not reservations: each leaves headroom over the bench figures above (check them against `docker stats` after go-live), so the sum over the running services (≈ 58 GB) is well above the measured total (24–29 GB) and close to the 64 GB the host has (with the OS, page cache, Coolify, SafeLine). That only bites if several services run away at once, and then Postgres's `oom_score_adj` keeps it out of the kernel's pick. A container that exceeds its ceiling is killed and restarted on its own (`restart: unless-stopped`), instead of the kernel's host-wide OOM killer choosing the largest process, usually Postgres. Postgres also has `oom_score_adj: -900` and Redis `-500`.

| Stack | Service | Memory | CPUs |
| --- | --- | --- | --- |
| data | postgres / pgbouncer | 16 GB / 256 MB | — / 1 |
| app | api / worker / worker-ai / beat / voice / migrate | 2 / 3 / 2 GB / 512 MB / 6 GB / 1 GB | 2 / 4 / 2 / 0.5 / 4 / 2 |
| app | redis / cms / web / crm-web | 1.5 GB / 1 GB / 512 MB / 512 MB | 1 / 1 / 1 / 1 |
| ai | embeddings / speech | 6 GB / 4 GB | 6 / 4 |
| voice, mail | livekit / stalwart | 2 GB / 2 GB | 2 / 2 |
| obs | phoenix / phoenix-postgres | 2 GB / 1 GB | 2 / 1 |
| SigNoz override | clickhouse / zookeeper / signoz / otel-collector | 6 GB / 512 MB / 1 GB / 1 GB | 4 / 1 / 1 / 2 |
| edge | crowdsec / cloudflare-bouncer / socket proxies | 512 / 128 / 64 MB | 1 / 0.5 / 0.25 |

The worker's ceiling assumes 3 prefork children, each with headless Chromium while rendering a PDF. Celery's `worker_max_memory_per_child` / `worker_max_tasks_per_child` (app code, `arifaa_pack/jobqueue.py`) recycle a child before a slow leak reaches the ceiling.

## 10. Deploys, restarts and container hardening

- **Not zero-downtime (M5).** Coolify deploys a Docker Compose resource with `docker compose up -d`: each changed container is stopped and recreated in place, not rolled. `migrate` runs first, then the api is down for the few seconds its container restarts (Cloudflare shows its error page; webhooks from Meta / Telnyx are retried by the senders). Deploy outside sales hours, and keep migrations expand / contract so the old code still runs against the new schema while it is being replaced. Two api replicas behind the Coolify proxy with health checks would remove the gap; not done for v1.
- **Stopping (M5).** `stop_grace_period` per service: api 30 s, worker / worker-ai 120 s (Celery's warm shutdown finishes running jobs), voice 300 s (`KX_VOICE_STOP_GRACE`: calls longer than that are cut), Postgres 120 s (shutdown checkpoint), Stalwart 30 s. A job killed anyway stays `running` in `automation.scheduled_jobs` and is re-sent by Beat's sweep after 15 minutes (`STUCK_AFTER`), so a deploy delays it, it does not park it for the broker's 2-hour visibility timeout.
- **Hardening (M15).** Every service: `no-new-privileges`, and `cap_drop: [ALL]` with only what the image needs added back (tested 18 Nov on each image): Postgres `CHOWN, DAC_OVERRIDE, DAC_READ_SEARCH, FOWNER, SETUID, SETGID` (entrypoint, root `exec` for WAL-G / `db_bootstrap.sh`), Redis `CHOWN, SETUID, SETGID`, Stalwart `NET_BIND_SERVICE` (file capability on its binary). Read-only root filesystem: the app services, Redis, PgBouncer, LiveKit. The CMS / web images come from other repos: only `no-new-privileges` until their users are known. The SigNoz collector no longer holds the Docker socket: container metrics go through a read-only Docker API proxy (§5); it still runs as root to read the container log files.
