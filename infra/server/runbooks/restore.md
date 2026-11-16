# Runbook: restore the database (and the monthly restore test)

WAL-G keeps 7 nightly base backups plus every WAL segment in R2 (`backups/postgres/`). Any second inside that window can be restored. RAID 1 protects against one disk dying, not against deletes or corruption.

## Nightly base backup (Coolify scheduled task on the `postgres` service, 02:00 UTC)

```bash
wal-g backup-push "$PGDATA" && wal-g delete retain FULL 7 --confirm
```

## Monthly restore test (first Monday, 30 minutes)

1. On the server: `docker run --rm -it --network kx --env-file <walg env> -v /tmp/restore:/var/lib/postgresql/data kairox/postgres:18 bash`
2. Inside: `wal-g backup-fetch /var/lib/postgresql/data/pgdata LATEST`, then `touch /var/lib/postgresql/data/pgdata/recovery.signal` and set `restore_command = 'wal-g wal-fetch %f %p'`.
3. Start postgres in that container; check: `select count(*) from pack_crm.crm.deals;`, latest `audit_events.at`, `kx migrate --revision head` would be a no-op.
4. Write the result (date, backup name, minutes, row counts) in `docs/V1_PROGRESS.md` under "restore tests".

## Real restore (server lost or data corrupted)

1. Stop writers: Coolify → stop `api`, `worker`, `beat`, `voice` (the website shows its "contact us on WhatsApp" page).
2. New or wiped server: deploy `compose.data.yml` with an **empty** volume, but do not start the app.
3. `wal-g backup-fetch $PGDATA LATEST` (or a named backup), `recovery.signal`, and for point-in-time `recovery_target_time = '2026-11-20 10:42:00+00'`.
4. Start postgres, wait for recovery to finish, run `db_bootstrap.sh` (re-syncs role passwords and per-role settings).
5. Start the app stack (`migrate` first). Check `/healthz`, `/healthz/beat`, `kx platform show`.
6. Restore Stalwart volumes from `backups/volumes/` if the mail store was lost.
