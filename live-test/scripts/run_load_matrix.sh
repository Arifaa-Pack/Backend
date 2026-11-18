#!/usr/bin/env bash
# Runs the load matrix with a clean queue before each level: drain (worker at concurrency 10), then the level at
# the concurrency under test. Broker duplicates are purged (the schedule table is the source of truth).
set -u
cd "$(dirname "$0")/../.."
DC="docker compose -p kxlive --env-file .env -f live-test/compose/live.yml"
due() { docker exec kxlive-postgres-1 psql -U postgres -d pack_automation -tAc "select count(*) from automation.scheduled_jobs where status='todo' and run_at<=now()" < /dev/null; }
drain() {
  docker exec kxlive-redis-1 redis-cli -n 8 del kx < /dev/null >/dev/null
  WORKER_CONCURRENCY=10 $DC up -d worker < /dev/null >/dev/null 2>&1
  for i in $(seq 1 120); do [ "$(due)" = "0" ] && break; sleep 10; done
  echo "$(date +%T) drained, due=$(due)"
}
export PYTHONIOENCODING=utf-8
curl -s -X POST localhost:39100/_mode -H 'content-type: application/json' -d '{"mode":"fake"}' >/dev/null
for spec in "10 3" "30 3" "50 3" "50 10"; do
  set -- $spec; n=$1; conc=$2
  drain
  WORKER_CONCURRENCY=$conc $DC up -d worker < /dev/null >/dev/null 2>&1; sleep 25
  curl -s -X POST localhost:39100/_reset >/dev/null
  echo "$(date +%T) === $n users, worker concurrency $conc"
  .venv/Scripts/python.exe live-test/scripts/load_live.py --users $n --minutes 8 --label c${n}_w${conc} < /dev/null 2>&1 | head -1
done
WORKER_CONCURRENCY=3 $DC up -d worker < /dev/null >/dev/null 2>&1
echo "$(date +%T) done"
