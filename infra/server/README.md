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
    compose.mail.yml       stalwart (MX + JMAP; relays out through SES)
    compose.obs.yml        phoenix + phoenix-postgres; SigNoz is installed from its own pinned compose (see §5)
    compose.edge.yml       crowdsec + Cloudflare bouncer (+ crowdsec-acquis.yaml); SafeLine from its own compose (§6)
    compose.app.yml        migrate, api, worker, beat, voice, redis, cms; web / crm-web (profile selfhost-web)
    env/pack.env.example   the app environment for company `pack`
    livekit.yaml           LiveKit server config (ports; keys come from LIVEKIT_KEYS)
  postgres/
    Dockerfile             pgvector/pgvector:pg18 + WAL-G binary
    postgresql.conf        tuned for 64 GB (shared_buffers 8 GB, max_connections 200, WAL archiving)
    pgbouncer.ini          transaction mode, pool sizes
    db_bootstrap.sh        roles, databases (with company prefix), extensions, per-role settings
    (WAL-G → R2 settings are environment variables of the postgres service in compose.data.yml)
  backup/
    volumes-to-r2.sh       nightly tarball of Stalwart and Coolify volumes → R2 (rclone)
  runbooks/
    restore.md             database point-in-time restore + monthly restore test
    switch-mode.md         paid / free switch per field
    rotate-secrets.md      key rotation
    incident.md            server down, vendor down, disk full
    add-project.md         adding a second company (design: docs/MULTI_PROJECT.md)
```

All stacks share one external Docker network, `kx`, so services find each other by name (`postgres`, `pgbouncer`, `redis`, `embeddings`, `speech`, `livekit`, `stalwart`, `phoenix`, `signoz-otel-collector`).

## 2. First-time server setup

1. Ubuntu 24.04: `apt update && apt full-upgrade`, unattended-upgrades on, a non-root sudo user, SSH keys only, `PasswordAuthentication no`.
2. Firewall (ufw): allow 22/tcp from admin IPs; 80, 443/tcp from Cloudflare IP ranges only; 25/tcp (mail); 7881/tcp, 3478/udp, 50000–50100/udp (LiveKit media).
3. Install Coolify (official script), open its dashboard through an SSH tunnel only, set its proxy (Traefik) to listen on 127.0.0.1:8080 / 8443, because SafeLine owns 80 / 443.
4. `docker network create kx`.
5. In Coolify: one project per company (`pack`), environments `staging` and `production`; add each stack in `stacks/` as a *Docker Compose* resource from this Git repo.

## 3. Order of deployment

| Step | Stack | Check |
| --- | --- | --- |
| 1 | `compose.data.yml` | `pg_isready`; then run `postgres/db_bootstrap.sh` with `KX_BOOTSTRAP_PREFIXES=pack_`; `wal-g backup-list` shows a base backup |
| 2 | `compose.ai.yml` | `curl http://embeddings/health`, `curl http://speech:8100/healthz` (models download on first start, ≈ 3 GB) |
| 3 | `compose.voice.yml` | LiveKit `:7880` answers; UDP ports reachable from outside |
| 4 | `compose.mail.yml` | Stalwart web setup done: domain, DKIM, SES smarthost, the `sales@` account (the app's JMAP login; bounce reports land there too) |
| 5 | SigNoz, `compose.obs.yml` | SigNoz UI (tunnel), Phoenix UI (tunnel) |
| 6 | SafeLine, `compose.edge.yml` | test page through Cloudflare; WAF logs |
| 7 | `compose.app.yml` | `migrate` exits 0; `/healthz` and `/healthz/beat` return 200; `kx platform show` |

## 4. Traffic

```
Internet ─► Cloudflare (proxied DNS, TLS, DDoS) ─► SafeLine WAF :80/:443 ─► Coolify proxy 127.0.0.1:8080/8443 ─► api / cms / web
Internet ─► :25 Stalwart (MX)          Internet ─► LiveKit :7881/tcp, :3478/udp, :50000-50100/udp (voice. is DNS-only)
```

- SafeLine: site per host (`api.`, `cms.`, `crm.`, `www.`); **allow-list** `/webhooks/whatsapp`, `/webhooks/telnyx/*`, `/webhooks/cms` (no challenge; rate limit kept; the app checks signatures). Default OWASP rules blocked every WhatsApp webhook in the bench with BunkerWeb; SafeLine passed 86 / 86.
- The app trusts `CF-Connecting-IP` for the client IP (`KX_TRUSTED_CLIENT_IP_HEADER=CF-Connecting-IP`) because every web request comes through Cloudflare.
- CrowdSec reads SafeLine / Stalwart / SSH logs. Bans for proxied traffic go to Cloudflare (Cloudflare bouncer); bans for direct ports (22, 25) go to the host firewall bouncer. Never ban Cloudflare's own IPs at the host.

## 5. Monitoring

- **SigNoz:** install from its official Docker compose at a pinned release, on the `kx` network; retention 15 days logs, 30 days traces and metrics. The app sends OTLP to `http://signoz-otel-collector:4318` (`KX_APP_OTLP_ENDPOINT`); the collector's filelog receiver reads container logs (`/var/lib/docker/containers/*/*.log`). Alert rules: `VendorSwitch > 0`, `QueueLagSeconds > 300`, `SpendCap >= 80`, error-log rate; channel = email through Stalwart.
- **UptimeRobot** (free, outside): `https://api.<domain>/healthz` and `/healthz/beat` every 5 minutes; alerts by email / app.
- Docker log rotation: `max-size 50m`, 3 files (daemon.json).

## 6. Firewall: SafeLine

Install from SafeLine's official compose at a pinned release (9.2.x). It uses host networking on 80 / 443; upload the Cloudflare origin certificate; upstream = `http://127.0.0.1:8080` (Coolify proxy).

## 7. Backups

| What | How | Kept | Where |
| --- | --- | --- | --- |
| PostgreSQL (all app DBs) | WAL-G: continuous WAL push + nightly `backup-push` | 7 full backups + WAL between | R2 `backups/postgres/` |
| Stalwart mail, Coolify config | `backup/volumes-to-r2.sh` nightly (rclone) | 14 days | R2 `backups/volumes/` |
| Phoenix, SigNoz | not backed up (telemetry) | — | — |

Monthly: restore test (`runbooks/restore.md`). RAID 1 is not a backup.

## 8. Image tags

Third-party images are pinned through overridable variables (`KX_IMG_*` in the compose files): PgBouncer, TEI `cpu-1.8`, LiveKit, Stalwart, Phoenix, CrowdSec and its Cloudflare bouncer, WAL-G (`WALG_VERSION` build argument, with `WALG_SHA256` to verify the download). The defaults are the versions planned on 16 Nov; **confirm each tag exists and set the WAL-G checksum at the first deploy**, then keep them pinned (never `latest`).

## 9. Capacity (free version, measured / vendor figures, v2 §5)

≈ 24–29 GB of 64 GB RAM at peak (10 users + 10 voice calls), ≈ 31 GB of 960 GB disk plus growth of ≈ 0.7 / 3.2 / 11.5 GB a month (Light / Normal / Heavy). Biggest RAM: SigNoz 4–8 GB, BGE-M3 4.8 GB, voice 4.3 GB, Piper 2.1 GB. Re-index the knowledge base outside sales hours (BGE-M3 uses every core for ≈ 30 min per 2,500 chunks).
