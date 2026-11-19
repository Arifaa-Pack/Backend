"""Container health check (Docker HEALTHCHECK).

api: GET http://127.0.0.1:$KX_API_PORT$KX_HEALTH_PATH must return 2xx (port falls back to KX_APP_API_PORT,
the one uvicorn binds, then 8000).
worker: unhealthy while its newest health mark is "runtime_failed" (live test 18 Nov, D-1: the worker said
healthy while every job failed because its runtime could not be built). arifaa_pack.celery_app writes the
marks into $KX_HEALTH_DIR (default /tmp/kx-health): runtime_ok after a finished task, runtime_failed when the
runtime cannot be built. A worker that is merely idle stays healthy; a stalled queue is caught by the
external /healthz/beat check (queue lag), not here.
voice: the LiveKit worker's own health endpoint (http://127.0.0.1:8081/ in production mode).
beat / migrate: the process being alive is the signal (Docker restarts it on exit).
"""

from __future__ import annotations

import os
import pathlib
import sys
import urllib.error
import urllib.request


def _http_ok(url: str) -> int:
    try:
        with urllib.request.urlopen(url, timeout=3) as resp:  # noqa: S310 - a fixed loopback URL
            return 0 if 200 <= resp.status < 300 else 1
    except urllib.error.URLError, OSError:
        return 1


def _mark(directory: pathlib.Path, name: str) -> float:
    try:
        return float((directory / name).read_text().strip())
    except OSError, ValueError:
        return 0.0


def worker_health(directory: pathlib.Path) -> int:
    """0 = healthy. Unhealthy only while the last thing that happened was a failed runtime build."""
    return 1 if _mark(directory, "runtime_failed") > _mark(directory, "runtime_ok") else 0


def main() -> int:
    process = os.environ.get("KX_PROCESS", "api")
    if process == "api":
        port = os.environ.get("KX_API_PORT") or os.environ.get("KX_APP_API_PORT") or "8000"
        path = os.environ.get("KX_HEALTH_PATH", "/healthz")
        return _http_ok(f"http://127.0.0.1:{port}{path}")
    if process == "worker":
        return worker_health(pathlib.Path(os.environ.get("KX_HEALTH_DIR", "/tmp/kx-health")))  # noqa: S108
    if process == "voice":
        return _http_ok(f"http://127.0.0.1:{os.environ.get('KX_VOICE_HEALTH_PORT', '8081')}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
