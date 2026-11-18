#!/usr/bin/env bash
# Load matrix after the D-6/D-7 fixes: send worker (queue kx, prefork 3) + AI worker (queue kx.ai, 10 threads).
# Same levels and pacing as run_load_matrix.sh; a clean queue before each level.
set -u
cd "$(dirname "$0")/../.."
export MSYS_NO_PATHCONV=1 PYTHONIOENCODING=utf-8
DC="docker compose -p kxlive --env-file .env -f live-test/compose/live.yml"
# one matrix at a time: two concurrent runs double the load and each one's drain empties the other's queues
LOCK=live-test/results/.load_matrix.lock
mkdir "$LOCK" 2>/dev/null || { echo "another load matrix is running ($LOCK)"; exit 1; }
trap 'rmdir "$LOCK"' EXIT
due() { docker exec kxlive-postgres-1 psql -U postgres -d pack_automation -tAc "select count(*) from automation.scheduled_jobs where status='todo' and run_at<=now()" < /dev/null; }
drain() {
  docker exec kxlive-redis-1 redis-cli -n 8 del kx kx.ai < /dev/null >/dev/null
  for i in $(seq 1 120); do [ "$(due)" = "0" ] && break; sleep 10; done
  echo "$(date +%T) drained, due=$(due)"
}
$DC up -d migrate api worker worker-ai beat < /dev/null >/dev/null 2>&1; sleep 40
curl -s -X POST localhost:39100/_mode -H 'content-type: application/json' -d '{"mode":"fake"}' >/dev/null
for n in 10 30 50; do
  drain
  curl -s -X POST localhost:39100/_reset >/dev/null
  echo "$(date +%T) === $n users, worker 3 + worker-ai 10 threads"
  .venv/Scripts/python.exe live-test/scripts/load_live.py --users $n --minutes 8 --label c${n}_w3a10 < /dev/null 2>&1 | head -1
done
echo "$(date +%T) done"
