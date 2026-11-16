#!/usr/bin/env bash
# Nightly off-site copy of the Docker volumes that hold data PostgreSQL does not (D49): Stalwart's mail store and
# config, Coolify's configuration. PostgreSQL itself is backed up continuously by WAL-G (compose.data.yml).
# Run from cron on the host (03:30 UTC):  30 3 * * *  /opt/kx/infra/server/backup/volumes-to-r2.sh
# Needs rclone with a remote named `r2` (type s3, provider Cloudflare) in /root/.config/rclone/rclone.conf.
set -euo pipefail

BUCKET="${R2_BUCKET:?set R2_BUCKET}"
KEEP_DAYS="${KEEP_DAYS:-14}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

VOLUMES=(kx-mail_stalwart-data kx-mail_stalwart-etc)
[[ -d /data/coolify ]] && tar -C /data -czf "$WORK/coolify-${STAMP}.tar.gz" coolify

for v in "${VOLUMES[@]}"; do
  docker run --rm -v "${v}:/src:ro" -v "$WORK:/out" alpine:3.22 \
    tar -C /src -czf "/out/${v}-${STAMP}.tar.gz" .
done

rclone copy "$WORK" "r2:${BUCKET}/backups/volumes/" --s3-no-check-bucket
rclone delete "r2:${BUCKET}/backups/volumes/" --min-age "${KEEP_DAYS}d"
echo "$(date -u +%FT%TZ) volumes backed up: ${VOLUMES[*]}"
