#!/usr/bin/env bash
# PostgreSQL archiving and backup watch (M10, live-test infra review 18 Nov). If WAL-G cannot push (R2 down, wrong
# key, token revoked), WAL piles up in pg_wal until the disk fills and Postgres stops, taking every tier with it,
# and nothing in SigNoz's app alerts would say so. This script checks, from the host:
#   * archiving works now: pg_stat_archiver has no failure newer than the last success
#   * archiving keeps up: few segments waiting in pg_wal/archive_status (*.ready)
#   * a base backup is recent: newest `wal-g backup-list` entry younger than KX_PG_BACKUP_MAX_AGE_HOURS
#   * the Docker disk is below KX_DISK_MAX_PCT
# and pings a Healthchecks.io check on success, or `<url>/fail` with the reasons. A silent host (cron stopped,
# server down) is a missed ping, so Healthchecks alerts on that too.
# Run from cron on the host every 10 minutes, env from a root-only file outside the checkout:
#   */10 * * * *  set -a; . /opt/kx/secrets/pg-health.env; set +a; bash /opt/kx/infra/server/backup/pg-health-check.sh \
#                 >>/var/log/kx-pg-health.log 2>&1
#
# Environment:
#   KX_PG_HEALTH_URL             Healthchecks.io ping URL (period 10 min, grace 10 min); empty = print only
#   KX_DATA_PROJECT              optional: compose project label of the data stack, when more than one runs here
#   KX_PG_READY_WAL_MAX          most WAL segments allowed to wait for archiving (default 20 = 320 MB)
#   KX_PG_BACKUP_MAX_AGE_HOURS   newest base backup older than this = failure (default 26: nightly at 02:00 + slack)
#   KX_DISK_PATH                 filesystem to watch (default /var/lib/docker)
#   KX_DISK_MAX_PCT              default 80
set -euo pipefail

HEALTH_URL="${KX_PG_HEALTH_URL:-}"
DATA_PROJECT="${KX_DATA_PROJECT:-}"
READY_MAX="${KX_PG_READY_WAL_MAX:-20}"
BACKUP_MAX_AGE_H="${KX_PG_BACKUP_MAX_AGE_HOURS:-26}"
DISK_PATH="${KX_DISK_PATH:-/var/lib/docker}"
DISK_MAX="${KX_DISK_MAX_PCT:-80}"

problems=()
problem() { problems+=("$*"); }

report() {
  local now
  now="$(date -u +%FT%TZ)"
  if (( ${#problems[@]} == 0 )); then
    echo "${now} pg health: ok"
    [[ -z "$HEALTH_URL" ]] || curl -fsS -m 10 --retry 3 -o /dev/null "$HEALTH_URL" || echo "ping failed" >&2
    exit 0
  fi
  printf '%s pg health FAILED: %s\n' "$now" "${problems[*]}" >&2
  [[ -z "$HEALTH_URL" ]] || curl -fsS -m 10 --retry 3 -o /dev/null --data-raw "${problems[*]}" "${HEALTH_URL}/fail" \
    || echo "fail ping failed" >&2
  exit 1
}

# the one running postgres container of the data stack (compose service label; Coolify prefixes names)
filters=(--filter "label=com.docker.compose.service=postgres")
[[ -z "$DATA_PROJECT" ]] || filters+=(--filter "label=com.docker.compose.project=${DATA_PROJECT}")
mapfile -t ids < <(docker ps -q "${filters[@]}")
if (( ${#ids[@]} != 1 )); then
  problem "expected 1 running postgres container, found ${#ids[@]} (set KX_DATA_PROJECT?)"
  report
fi
cid="${ids[0]}"

# psql inside the container as root over the local socket (pg_ident.conf maps root -> postgres)
pg() { docker exec "$cid" psql -X -At -v ON_ERROR_STOP=1 -c "$1"; }

if ! archiver="$(pg "SELECT failed_count,
    COALESCE(EXTRACT(EPOCH FROM last_failed_time)::bigint, 0),
    COALESCE(EXTRACT(EPOCH FROM last_archived_time)::bigint, 0),
    COALESCE(last_failed_wal, '')
  FROM pg_stat_archiver")"; then
  problem "cannot query pg_stat_archiver"
  report
fi
IFS='|' read -r failed_count last_failed last_archived failed_wal <<<"$archiver"
if (( last_failed > last_archived )); then
  problem "WAL archiving is failing (failed_count=${failed_count}, last failed segment ${failed_wal}): check the wal-g lines in the postgres log, R2 keys, WALG_LIBSODIUM_KEY"
fi

if ready="$(pg "SELECT count(*) FROM pg_ls_archive_statusdir() WHERE name LIKE '%.ready'")"; then
  (( ready <= READY_MAX )) || problem "${ready} WAL segments waiting for archiving (max ${READY_MAX}): pg_wal is growing"
else
  problem "cannot list pg_wal/archive_status"
fi

# newest base backup (wal-g uses the container's WALG_* / AWS_* environment; R2 list call)
if list="$(docker exec "$cid" wal-g backup-list --json 2>/dev/null)"; then
  newest="$(grep -o '"time": *"[^"]*"' <<<"$list" | sed 's/.*"\([^"]*\)"$/\1/' | sort | tail -n 1 || true)"
  if [[ -z "$newest" ]]; then
    problem "no WAL-G base backup in R2"
  else
    age_h=$(( ( $(date -u +%s) - $(date -u -d "$newest" +%s) ) / 3600 ))
    (( age_h <= BACKUP_MAX_AGE_H )) || problem "newest base backup is ${age_h} h old (${newest}; max ${BACKUP_MAX_AGE_H} h)"
  fi
else
  problem "wal-g backup-list failed (R2 unreachable or credentials wrong)"
fi

used="$(df -P "$DISK_PATH" | awk 'NR == 2 { sub("%", "", $5); print $5 }')"
(( used <= DISK_MAX )) || problem "${DISK_PATH} is ${used}% full (max ${DISK_MAX}%)"

report
