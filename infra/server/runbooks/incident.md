# Runbook: incidents

## Server unreachable (UptimeRobot: /healthz down)

1. Namecheap panel: power / network status; reboot from the panel if hung.
2. While down: switch the website's DNS (Cloudflare) to the static "contact us on WhatsApp" page; staff use WhatsApp Business on their phones.
3. Hardware lost: order a new server, follow `infra/server/README.md` §2–§3, restore with `restore.md`.

## Beat stale (UptimeRobot: /healthz/beat 503)

1. Coolify: is `beat` running? Restart it (exactly one). Is `worker` running? Is `redis` healthy?
2. `kx` logs in SigNoz: `heartbeat.redis_unavailable`, Celery errors.
3. Nothing is lost: due work waits in `automation.scheduled_jobs` and runs when Beat and a worker are back.

## A vendor is down (VendorSwitch alert email / SigNoz)

The chain already moved to the next vendor (paid → free alternative, or the last resort). Check the vendor's status page. If it will be down for hours, switch the field to the free version (`switch-mode.md`) so new calls do not wait for the breaker.

## Disk filling up

`df -h`; `docker system df`. Usual causes: SigNoz retention (lower it), Docker logs (rotation is 50 MB × 3), WAL piling up because WAL-G cannot reach R2 (check `wal-g` in the postgres logs; fix R2 credentials, archiving resumes).

## Price shown wrong / AI misbehaving

Kill-switch ladder in the CRM admin (`text_only` or `hold`), then investigate in Phoenix (the AI audit trail).
