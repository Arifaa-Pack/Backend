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

PG18 layout (M9): the volume is mounted at `/var/lib/postgresql` and the cluster lives in the image's `PGDATA`, `/var/lib/postgresql/18/docker`. Never mount at `/var/lib/postgresql/data`: PG18 images refuse to start with it.

1. On the server: `docker run --rm -it --env-file <walg env, incl. WALG_LIBSODIUM_KEY + _TRANSFORM> -v /tmp/restore:/var/lib/postgresql kairox/postgres:18 bash` (default bridge network: it needs R2, nothing on `kx-*`)
2. Inside: `wal-g backup-fetch "$PGDATA" LATEST && touch "$PGDATA/recovery.signal" && chown -R postgres:postgres /var/lib/postgresql`.
3. Start it **without archiving** (a test copy must never push WAL into the production prefix): `gosu postgres postgres -c config_file=/etc/postgresql/postgresql.conf -c archive_mode=off -c "restore_command=wal-g wal-fetch %f %p" &`. When recovery ends, check: `select count(*) from pack_crm.crm.deals;`, latest `audit_events.at`, `kx migrate --revision head` would be a no-op. Note the minutes it took: that is the restore part of the RTO (`infra/server/README.md` §5).
4. Write the result (date, backup name, minutes, row counts) in `docs/V1_PROGRESS.md` under "restore tests".

## Real restore (server lost or data corrupted)

1. Stop writers: Coolify → stop `api`, `worker`, `beat`, `voice` (the website shows its "contact us on WhatsApp" page).
2. New or wiped server: deploy `compose.data.yml` with an **empty** volume, but do not start the app.
3. `wal-g backup-fetch "$PGDATA" LATEST` (or a named backup; `PGDATA` = `/var/lib/postgresql/18/docker`), `recovery.signal`, `restore_command = 'wal-g wal-fetch %f %p'`, and for point-in-time `recovery_target_time = '2026-11-20 10:42:00+00'`.
4. Start postgres, wait for recovery to finish, run `db_bootstrap.sh` (per-role settings, missing roles / databases). The roles keep the passwords they had in the backup; if `pack.env` now holds different ones, run it with `KX_BOOTSTRAP_ROTATE=1`.
5. **Regenerate PgBouncer's `userlist.txt`** from the restored server, then restart `pgbouncer` (M6: PgBouncer passes SCRAM through only when its verifier is byte-identical to the server's, salt included; a stale line means "password authentication failed" for the app):
   ```bash
   docker compose -f infra/server/stacks/compose.data.yml exec -T postgres psql -At -c \
     "SELECT format('\"%s\" \"%s\"', rolname, rolpassword) FROM pg_authid WHERE rolname LIKE 'pack\_%' AND rolpassword IS NOT NULL" \
     > /opt/kx/secrets/userlist.txt && chmod 600 /opt/kx/secrets/userlist.txt
   ```
6. Start the app stack (`migrate` first). Check `/healthz`, `/healthz/beat`, `kx platform show`.
7. Restore Stalwart volumes from `backups/volumes/` if the mail store was lost (below).

## Volume restore (Stalwart, Coolify config), the monthly test and the quarterly drill

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
4. **Monthly, automatic (M14):** `backup/volumes-restore-test.sh` (first Monday 04:00 from root's cron, header has the line) fetches the newest `stalwart-etc` / `stalwart-data` copies, unpacks them into scratch volumes, checks `config.json` and the RocksDB files, and starts Stalwart on them on an `--internal` network (no egress: nothing in its outbound queue can be sent), failing if it does not listen or logs a panic. It pings `KX_RESTORE_HEARTBEAT_URL` on success.
5. **Quarterly, by hand:** the same with `KX_RESTORE_KEEP=1`, then look inside the restored store: the DKIM keys (RSA + Ed25519) and the `sales@` account exist (through the web admin of the scratch container: a relay on the default bridge, published on loopback only, then joined to the internal network: `docker run -d --name kx-restore-relay -p 127.0.0.1:18080:8080 alpine/socat tcp-listen:8080,fork,reuseaddr tcp:kx-restore-stalwart:8080 && docker network connect kx-restore-test kx-restore-relay`, then `ssh -L 18080:127.0.0.1:18080`, log in with the production admin account; remove the relay afterwards. Verify on the server). Remove the scratch container, network and volumes afterwards (script header), and record the date, file, size and the checks in `docs/V1_PROGRESS.md` under "restore tests".
