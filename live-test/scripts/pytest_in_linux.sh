#!/bin/sh
# Usage (Git Bash, repo root): sh live-test/scripts/pytest_in_linux.sh <pytest args...>
# Runs pytest in the app image (Linux, Chromium, WeasyPrint as in production) against a throwaway test DSN.
export MSYS_NO_PATHCONV=1
OUT="live-test/results/dev/pytest_last.txt"
docker run --rm --network kairox-platform_default -u 0:0 \
  -v "$(pwd -W)":/src:ro -v "$(pwd -W)/live-test/results/dev":/out -v kxlive-uvcache:/root/.cache/uv \
  -e KX_TEST_PG_DSN=postgresql://postgres:postgres@postgres:5432/postgres \
  --entrypoint sh kxlive/app:local -c '(pip -q install uv==0.13.7 || /app/.venv/bin/python -m pip -q install uv==0.13.7) >/dev/null 2>&1; export PATH=$PATH:/app/.venv/bin; sh /src/live-test/scripts/run_tests_linux.sh "$@"' sh "$@" > "$OUT" 2>&1 < /dev/null
echo "exit=$?" >> "$OUT"
tail -25 "$OUT"
grep -o 'tests="[0-9]*"\|failures="[0-9]*"\|errors="[0-9]*"\|skipped="[0-9]*"' live-test/results/dev/pytest_linux_junit.xml | head -4 | tr '\n' ' '; echo
grep -E "^(FAILED|ERROR)" "$OUT" | head -20
