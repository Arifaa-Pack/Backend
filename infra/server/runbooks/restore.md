# Runbook: restore the database (and the monthly restore test)

WAL-G keeps 7 nightly base backups plus every WAL segment in R2 (`backups/postgres/`), encrypted with `WALG_LIBSODIUM_KEY`. Any second inside that window can be restored. RAID 1 protects against one disk dying, not against deletes or corruption.

**Keys you need for any restore (kept offline, `infra/server/README.md` §7):** `WALG_LIBSODIUM_KEY` (database) and `KX_BACKUP_PASSPHRASE` (volumes). Without them the backups are unreadable.

## Nightly base backup (Coolify scheduled task on the `postgres` service, 02:00 UTC)

```bash
wal-g backup-push "$PGDATA"
```

## Retention (host cron, 02:45 UTC, the delete token)

The postgres container only holds the push token (H4). Old backups are deleted from the host with the separate delete token, kept in a root-only file `/opt/kx/secrets/walg-prune.env` (`WALG_S3_PREFIX`, `AWS_ENDPOINT`, `AWS_REGION=auto`, `AWS_S3_FORCE_PATH_STYLE=true`, `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` = the delete token, `WALG_LIBSODIUM_KEY`, `WALG_LIBSODIUM_KEY_TRANSFORM=hex`):

```bash
45 2 * * *  docker run --rm --env-file /opt/kx/secrets/walg-prune.env kairox/postgres:18 wal-g delete retain FULL 7 --confirm
```

The R2 bucket lock (7 days on `backups/postgres/`) refuses deletes of anything newer, whatever the token.

## Monthly restore test (first Monday, 30 minutes)

1. On the server: `docker run --rm -it --env-file <walg env, incl. WALG_LIBSODIUM_KEY + _TRANSFORM> -v /tmp/restore:/var/lib/postgresql/data kairox/postgres:18 bash` (default bridge network: it needs R2, nothing on `kx-*`)
2. Inside: `wal-g backup-fetch /var/lib/postgresql/data/pgdata LATEST`, then `touch /var/lib/postgresql/data/pgdata/recovery.signal` and set `restore_command = 'wal-g wal-fetch %f %p'`.
3. Start postgres in that container; check: `select count(*) from pack_crm.crm.deals;`, latest `audit_events.at`, `kx migrate --revision head` would be a no-op.
4. Write the result (date, backup name, minutes, row counts) in `docs/V1_PROGRESS.md` under "restore tests".

## Real restore (server lost or data corrupted)

1. Stop writers: Coolify → stop `api`, `worker`, `beat`, `voice` (the website shows its "contact us on WhatsApp" page).
2. New or wiped server: deploy `compose.data.yml` with an **empty** volume, but do not start the app.
3. `wal-g backup-fetch $PGDATA LATEST` (or a named backup), `recovery.signal`, and for point-in-time `recovery_target_time = '2026-11-20 10:42:00+00'`.
4. Start postgres, wait for recovery to finish, run `db_bootstrap.sh` (re-syncs role passwords and per-role settings).
5. Start the app stack (`migrate` first). Check `/healthz`, `/healthz/beat`, `kx platform show`.
6. Restore Stalwart volumes from `backups/volumes/` if the mail store was lost (below).

## Volume restore (Stalwart, Coolify config) and the quarterly drill

The nightly copies (`backup/volumes-to-r2.sh`) are encrypted with rclone crypt; file names in R2 are encrypted too.

1. As root, with `/opt/kx/secrets/backup.env` loaded (`set -a; . /opt/kx/secrets/backup.env; set +a`), define the decrypting remote the way the script does:
   ```bash
   export RCLONE_CONFIG_KXVOL_TYPE=crypt RCLONE_CONFIG_KXVOL_REMOTE="r2:${R2_BUCKET}/backups/volumes"
   export RCLONE_CONFIG_KXVOL_PASSWORD="$(printf '%s' "$KX_BACKUP_PASSPHRASE" | rclone obscure -)"
   rclone lsl kxvol: | sort -k2,3 | tail      # newest copies, real names
   rclone copy kxvol:stalwart-data-<STAMP>.tar.gz /tmp/restore/
   ```
2. Stop Stalwart (Coolify), find the volume by label (`docker volume ls -q --filter label=com.docker.compose.volume=stalwart-data`), and unpack into it:
   `docker run --rm -v <volume>:/dst -v /tmp/restore:/in:ro alpine:3.22 sh -c 'cd /dst && tar -xzf /in/stalwart-data-<STAMP>.tar.gz'`. Same for `stalwart-etc`. Start Stalwart; check the `sales@` mailbox over JMAP and the DKIM keys in the web admin.
3. Coolify config: unpack `coolify-<STAMP>.tar.gz` into `/data` on the new host before re-running the Coolify installer; `ssh/` is not in it (Coolify creates a new key pair; add its public key to `/root/.ssh/authorized_keys`).
4. **Drill every quarter** into a scratch volume (`docker volume create kx-restore-test`) instead of the live one, and record the date, file, size and the check in `docs/V1_PROGRESS.md` under "restore tests".
