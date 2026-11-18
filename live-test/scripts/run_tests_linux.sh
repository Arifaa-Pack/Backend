#!/bin/sh
# Runs the repo's full pytest suite on Linux (same OS/Chromium/WeasyPrint as production) with the locked dev deps.
set -eu
mkdir -p /work && cd /src && tar --exclude=./.venv --exclude=./resource-usage-bench --exclude=./live-test --exclude='*/__pycache__' --exclude=./.git -cf - . | tar -C /work -xf -
cd /work
export UV_PROJECT_ENVIRONMENT=/venv UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0
uv sync --frozen --all-packages --all-extras -q
export KX_APP_CHROMIUM_PATH=/usr/bin/chromium
/venv/bin/python -m pytest -p no:cacheprovider -q -ra --durations=10 --junitxml=/out/pytest_linux_junit.xml "$@"
