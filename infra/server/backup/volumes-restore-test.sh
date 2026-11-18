#!/usr/bin/env bash
# Monthly restore test of the Stalwart volume backups (M14, live-test infra review 18 Nov). backup/volumes-to-r2.sh
# proves an upload happened; this proves the newest copy can be read back and that Stalwart opens it:
#   1. fetch the newest stalwart-etc / stalwart-data tarballs from R2 and decrypt them (rclone crypt)
#   2. unpack them into two scratch volumes (never the live ones)
#   3. check config.json and the RocksDB files (CURRENT, MANIFEST-*) are there
#   4. start a scratch Stalwart on those volumes on an --internal network with no egress, so nothing it holds in
#      its outbound queue can be delivered, and wait until it listens on 8080 and has not exited
#   5. remove the scratch container, network and volumes; ping a heartbeat on success
# The DKIM keys live in the store: check them by hand once a quarter (runbooks/restore.md, KX_RESTORE_KEEP=1).
# Run as root on the host, first Monday of the month, env from the backup's file:
#   set -a; . /opt/kx/secrets/backup.env; set +a; bash /opt/kx/infra/server/backup/volumes-restore-test.sh
#
# Environment:
#   R2_BUCKET, KX_BACKUP_PASSPHRASE, KX_BACKUP_REMOTE    as for volumes-to-r2.sh
#   KX_IMG_STALWART              image to test with (default stalwartlabs/stalwart:v1.1.1, = compose.mail.yml)
#   KX_RESTORE_HEARTBEAT_URL     optional: heartbeat pinged on success (monthly period)
#   KX_RESTORE_KEEP              1 = leave the scratch container running for a manual look (remove it afterwards
#                                with: docker rm -f kx-restore-stalwart; docker network rm kx-restore-test;
#                                docker volume rm kx-restore-test-etc kx-restore-test-data)
set -euo pipefail

BUCKET="${R2_BUCKET:?set R2_BUCKET}"
PASSPHRASE="${KX_BACKUP_PASSPHRASE:?set KX_BACKUP_PASSPHRASE}"
REMOTE="${KX_BACKUP_REMOTE:-r2}"
IMAGE="${KX_IMG_STALWART:-stalwartlabs/stalwart:v1.1.1}"
HEARTBEAT_URL="${KX_RESTORE_HEARTBEAT_URL:-}"
KEEP="${KX_RESTORE_KEEP:-0}"
NAME="kx-restore-stalwart"
NET="kx-restore-test"
VOL_ETC="kx-restore-test-etc"
VOL_DATA="kx-restore-test-data"
WORK="$(mktemp -d)"

cleanup() {
  rm -rf "$WORK"
  [[ "$KEEP" == "1" ]] && return 0
  docker rm -f "$NAME" >/dev/null 2>&1 || true
  docker network rm "$NET" >/dev/null 2>&1 || true
  docker volume rm "$VOL_ETC" "$VOL_DATA" >/dev/null 2>&1 || true
}
trap cleanup EXIT
die() { echo "$(date -u +%FT%TZ) volume restore test FAILED: $*" >&2; exit 1; }

export RCLONE_CONFIG_KXVOL_TYPE=crypt
export RCLONE_CONFIG_KXVOL_REMOTE="${REMOTE}:${BUCKET}/backups/volumes"
RCLONE_CONFIG_KXVOL_PASSWORD="$(printf '%s' "$PASSPHRASE" | rclone obscure -)"
export RCLONE_CONFIG_KXVOL_PASSWORD

# leftovers of an earlier KX_RESTORE_KEEP=1 run would mix old and new data
for v in "$VOL_ETC" "$VOL_DATA"; do
  ! docker volume inspect "$v" >/dev/null 2>&1 || die "scratch volume ${v} exists: remove it first"
done

# 1. newest copy of each (names sort by their UTC stamp)
for key in stalwart-etc stalwart-data; do
  file="$(rclone lsf kxvol: --include "${key}-*.tar.gz" | sort | tail -n 1)"
  [[ -n "$file" ]] || die "no ${key} backup in R2"
  rclone copy "kxvol:${file}" "$WORK/" || die "download or decrypt of ${file} failed"
  echo "$(date -u +%FT%TZ) restore test: ${file} ($(stat -c %s "$WORK/$file") bytes)"
  mv "$WORK/$file" "$WORK/${key}.tar.gz"
done

# 2. + 3. unpack into scratch volumes and look for the files Stalwart needs
docker volume create "$VOL_ETC" >/dev/null
docker volume create "$VOL_DATA" >/dev/null
docker run --rm --network none -v "$VOL_ETC:/etc/stalwart" -v "$VOL_DATA:/var/lib/stalwart" -v "$WORK:/in:ro" \
  alpine:3.22 sh -c '
    set -e
    tar -C /etc/stalwart -xzf /in/stalwart-etc.tar.gz
    tar -C /var/lib/stalwart -xzf /in/stalwart-data.tar.gz
    test -s /etc/stalwart/config.json || { echo "config.json missing" >&2; exit 3; }
    find /var/lib/stalwart -name CURRENT | grep -q . || { echo "RocksDB CURRENT missing" >&2; exit 3; }
    find /var/lib/stalwart -name "MANIFEST-*" | grep -q . || { echo "RocksDB MANIFEST missing" >&2; exit 3; }
  ' || die "unpacked copy is incomplete"

# 4. start it where it cannot reach anything (no egress: the outbound queue stays put)
docker network create --internal "$NET" >/dev/null
docker run -d --name "$NAME" --network "$NET" --init \
  --security-opt no-new-privileges:true --cap-drop ALL --cap-add NET_BIND_SERVICE \
  -e SES_SMTP_PASSWORD=restore-test \
  -v "$VOL_ETC:/etc/stalwart" -v "$VOL_DATA:/var/lib/stalwart" "$IMAGE" >/dev/null

up=0
for _ in $(seq 1 30); do
  [[ "$(docker inspect -f '{{.State.Running}}' "$NAME")" == "true" ]] || break
  if docker run --rm --network "$NET" alpine:3.22 nc -z -w 3 "$NAME" 8080 >/dev/null 2>&1; then up=1; break; fi
  sleep 2
done
if (( up == 0 )); then
  docker logs --tail 30 "$NAME" >&2 || true
  die "the restored Stalwart did not start (store does not open?)"
fi
sleep 5
[[ "$(docker inspect -f '{{.State.Running}}' "$NAME")" == "true" ]] || { docker logs --tail 30 "$NAME" >&2; die "exited after start"; }
if docker logs "$NAME" 2>&1 | grep -qiE 'panic|corrupt'; then
  docker logs --tail 30 "$NAME" >&2
  die "Stalwart logged a panic / corruption on the restored store"
fi

echo "$(date -u +%FT%TZ) volume restore test ok: the newest Stalwart copy opens"
[[ "$KEEP" != "1" ]] || echo "kept running: ${NAME} on ${NET} (see the header to remove it)"
if [[ -n "$HEARTBEAT_URL" ]]; then
  curl -fsS -m 10 --retry 3 -o /dev/null "$HEARTBEAT_URL" || echo "heartbeat ping failed" >&2
fi
