#!/usr/bin/env bash
# Nightly off-site copy of the Docker volumes that hold data PostgreSQL does not (D49): Stalwart's mail store and
# config, Coolify's configuration. PostgreSQL itself is backed up continuously by WAL-G (compose.data.yml).
# Run from cron on the host (03:30 UTC), env from a root-only file outside the checkout:
#   30 3 * * *  set -a; . /opt/kx/secrets/backup.env; set +a; /opt/kx/infra/server/backup/volumes-to-r2.sh \
#               >>/var/log/kx-volumes-backup.log 2>&1
# Needs docker, rclone (remote `r2`, type s3, provider Cloudflare, in /root/.config/rclone/rclone.conf) and curl.
#
# Safe by construction (H3, H4):
#   * volumes are found by their compose label, not by name (Coolify prefixes names with its resource UUID);
#     exactly one match per label, else the run fails; an empty volume or a tiny tarball fails the run too
#   * everything is encrypted on this host before upload (rclone crypt, key KX_BACKUP_PASSPHRASE);
#     /data/coolify/ssh (the keys Coolify logs in to this server with) is never copied
#   * a heartbeat URL is pinged only after a complete run, so a silent failure becomes a missed heartbeat
#
# Environment:
#   R2_BUCKET                 bucket (objects under backups/volumes/)
#   KX_BACKUP_PASSPHRASE      encryption passphrase (openssl rand -hex 32); keep a copy offline: no restore without it
#   KX_BACKUP_REMOTE          rclone remote used to upload (default r2; the push key)
#   KX_BACKUP_PRUNE_REMOTE    rclone remote used to delete copies older than KEEP_DAYS (default: KX_BACKUP_REMOTE;
#                             set a second remote with the delete key, infra/server/README.md §7)
#   KEEP_DAYS                 default 14
#   KX_BACKUP_MIN_BYTES       smallest acceptable tarball (default 1024; an empty volume compresses to ~100 bytes)
#   KX_BACKUP_HEARTBEAT_URL   optional: Healthchecks.io / UptimeRobot heartbeat, pinged on success
#   KX_MAIL_PROJECT           optional: compose project label of the mail stack, when more than one stack on this
#                             host has volumes named stalwart-etc / stalwart-data (staging + production)
#   KX_BACKUP_STOP_MAIL       default 1: stop Stalwart while its volumes are copied (M14: RocksDB files copied while
#                             it writes may not open). Mail is down for the seconds the tar takes; senders retry
#                             (SMTP queues), the app's JMAP calls retry. 0 = copy live (crash-consistent at best).
#
# Restore test: backup/volumes-restore-test.sh (monthly; runbooks/restore.md).
#
# Restore (runbooks/restore.md): export the same R2_BUCKET / KX_BACKUP_PASSPHRASE, define the `kxvol` remote as
# below (or run this script's first lines), `rclone copy kxvol:<file> /tmp/restore`, then tar -xzf into the volume.
set -euo pipefail

BUCKET="${R2_BUCKET:?set R2_BUCKET}"
PASSPHRASE="${KX_BACKUP_PASSPHRASE:?set KX_BACKUP_PASSPHRASE (encryption key, keep a copy offline)}"
REMOTE="${KX_BACKUP_REMOTE:-r2}"
PRUNE_REMOTE="${KX_BACKUP_PRUNE_REMOTE:-$REMOTE}"
KEEP_DAYS="${KEEP_DAYS:-14}"
MIN_BYTES="${KX_BACKUP_MIN_BYTES:-1024}"
HEARTBEAT_URL="${KX_BACKUP_HEARTBEAT_URL:-}"
MAIL_PROJECT="${KX_MAIL_PROJECT:-}"
STOP_MAIL="${KX_BACKUP_STOP_MAIL:-1}"
DEST="backups/volumes"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
WORK="$(mktemp -d)"   # 0700, root: the plaintext tarballs never leave it
STOPPED=""            # the Stalwart container this run stopped; started again on any exit
cleanup() {
  if [[ -n "$STOPPED" ]]; then
    docker start "$STOPPED" >/dev/null \
      || echo "$(date -u +%FT%TZ) volume backup: FAILED to start Stalwart (${STOPPED}) again: start it now" >&2
  fi
  rm -rf "$WORK"
}
trap cleanup EXIT

# rclone crypt remote over R2, defined from the environment (no key on disk in rclone.conf).
export RCLONE_CONFIG_KXVOL_TYPE=crypt
export RCLONE_CONFIG_KXVOL_REMOTE="${REMOTE}:${BUCKET}/${DEST}"
RCLONE_CONFIG_KXVOL_PASSWORD="$(printf '%s' "$PASSPHRASE" | rclone obscure -)"
export RCLONE_CONFIG_KXVOL_PASSWORD

die() { echo "$(date -u +%FT%TZ) volume backup FAILED: $*" >&2; exit 1; }

# compose volume key (label com.docker.compose.volume) -> the one Docker volume that carries it
volume_for() {
  local key="$1" names
  local filters=(--filter "label=com.docker.compose.volume=${key}")
  [[ -z "$MAIL_PROJECT" ]] || filters+=(--filter "label=com.docker.compose.project=${MAIL_PROJECT}")
  mapfile -t names < <(docker volume ls -q "${filters[@]}")
  (( ${#names[@]} == 1 )) \
    || die "expected exactly 1 volume labelled ${key}, found ${#names[@]}: ${names[*]:-none} (set KX_MAIL_PROJECT?)"
  docker volume inspect "${names[0]}" >/dev/null || die "cannot inspect volume ${names[0]}"
  printf '%s' "${names[0]}"
}

check_size() {
  local file="$1" size
  size="$(stat -c %s "$file")"
  (( size >= MIN_BYTES )) || die "$(basename "$file") is ${size} bytes (< ${MIN_BYTES}): empty or wrong volume?"
}

# the running Stalwart container (compose service label), or nothing if it is not running
mail_container() {
  local ids
  local filters=(--filter "label=com.docker.compose.service=stalwart")
  [[ -z "$MAIL_PROJECT" ]] || filters+=(--filter "label=com.docker.compose.project=${MAIL_PROJECT}")
  mapfile -t ids < <(docker ps -q "${filters[@]}")
  (( ${#ids[@]} <= 1 )) || die "expected at most 1 running Stalwart container, found ${#ids[@]} (set KX_MAIL_PROJECT?)"
  printf '%s' "${ids[0]:-}"
}

# M14: a consistent copy of RocksDB needs Stalwart stopped (its own stop flushes the memtables)
if [[ "$STOP_MAIL" == "1" ]]; then
  cid="$(mail_container)"
  if [[ -n "$cid" ]]; then
    STOPPED="$cid"
    docker stop -t 30 "$cid" >/dev/null || die "could not stop Stalwart (${cid})"
  else
    echo "$(date -u +%FT%TZ) volume backup: Stalwart is not running; copying its volumes as they are" >&2
  fi
fi

# Stalwart v1: config.json (stalwart-etc) + RocksDB with mail, accounts, settings, DKIM keys (stalwart-data)
VOLUMES=()
for key in stalwart-etc stalwart-data; do
  vol="$(volume_for "$key")"
  file="${key}-${STAMP}.tar.gz"
  # shellcheck disable=SC2016  # $1 expands inside the container's shell
  docker run --rm --network none -v "${vol}:/src:ro" -v "$WORK:/out" alpine:3.22 sh -c \
    'find /src -mindepth 1 -type f | grep -q . || { echo "volume has no files" >&2; exit 3; }; tar -C /src -czf "/out/$1" .' \
    sh "$file" || die "${vol}: empty volume or tar failed"
  check_size "$WORK/$file"
  VOLUMES+=("$vol")
done

# mail back up as soon as its volumes are copied (the upload below can take minutes)
if [[ -n "$STOPPED" ]]; then
  docker start "$STOPPED" >/dev/null || die "could not start Stalwart (${STOPPED}) again: start it now"
  STOPPED=""
fi

if [[ -d /data/coolify ]]; then
  # ssh/ = Coolify's private keys for this server: excluded (H4); after a rebuild Coolify gets a new key pair.
  # source/.env (APP_KEY) is kept: keep a copy of APP_KEY in the password manager as well.
  tar -C /data --exclude=coolify/ssh -czf "$WORK/coolify-${STAMP}.tar.gz" coolify
  check_size "$WORK/coolify-${STAMP}.tar.gz"
fi

expected="$(find "$WORK" -maxdepth 1 -name "*-${STAMP}.tar.gz" | wc -l)"
rclone copy "$WORK" kxvol: --s3-no-check-bucket
uploaded="$(rclone lsf kxvol: --include "*-${STAMP}.tar.gz" | wc -l)"
(( uploaded == expected )) || die "uploaded ${uploaded} of ${expected} files"

# retention on the raw (encrypted) objects: no key needed, only the delete permission
rclone delete "${PRUNE_REMOTE}:${BUCKET}/${DEST}/" --min-age "${KEEP_DAYS}d"

if [[ -n "$HEARTBEAT_URL" ]]; then
  curl -fsS -m 10 --retry 3 -o /dev/null "$HEARTBEAT_URL" || echo "heartbeat ping failed" >&2
fi
echo "$(date -u +%FT%TZ) volumes backed up (encrypted, ${expected} files): ${VOLUMES[*]}"
