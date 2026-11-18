#!/bin/sh
# Usage (Git Bash, repo root): sh live-test/scripts/pytest_in_linux.sh <pytest args...>
# Runs pytest in the app image (Linux, Chromium, WeasyPrint as in production) against a throwaway test DSN.
export MSYS_NO_PATHCONV=1
# Two runs on one Postgres destroy each other (fixed test_* names, DROP ... FORCE): give a parallel run its own
# server with KX_TEST_NET / KX_TEST_ADMIN_DSN, and its own output with KX_TEST_OUT_DIR.
OUT_DIR="${KX_TEST_OUT_DIR:-live-test/results/dev}"
mkdir -p "$OUT_DIR"
OUT="$OUT_DIR/pytest_last.txt"
docker run --rm --network "${KX_TEST_NET:-kairox-platform_default}" -u 0:0 \
  -v "$(pwd -W)":/src:ro -v "$(pwd -W)/$OUT_DIR":/out -v kxlive-uvcache:/root/.cache/uv \
  -e KX_TEST_PG_DSN="${KX_TEST_ADMIN_DSN:-postgresql://postgres:postgres@postgres:5432/postgres}" \
  --entrypoint sh kxlive/app:local -c '(pip -q install uv==0.13.7 || /app/.venv/bin/python -m pip -q install uv==0.13.7) >/dev/null 2>&1; export PATH=$PATH:/app/.venv/bin; sh /src/live-test/scripts/run_tests_linux.sh "$@"' sh "$@" > "$OUT" 2>&1 < /dev/null
echo "exit=$?" >> "$OUT"
tail -25 "$OUT"
grep -o 'tests="[0-9]*"\|failures="[0-9]*"\|errors="[0-9]*"\|skipped="[0-9]*"' "$OUT_DIR/pytest_linux_junit.xml" | head -4 | tr '\n' ' '; echo
grep -E "^(FAILED|ERROR)" "$OUT" | head -20
