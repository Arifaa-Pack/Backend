# Local development and testing

The full free stack needs ≈ 28–32 GB RAM; a development laptop (here: Windows 11 Home, 7.3 GB RAM) runs **one group of services at a time** through compose profiles.

## 1. Docker Desktop on drive D: (Windows)

Docker Desktop needs WSL 2. Everything large (Docker's disk image, images, volumes, models) goes on **D:**, nothing on C: beyond the WSL kernel. Two scripts do the steps below (both **as Administrator**): `scripts/windows/1-enable-wsl.ps1`, restart Windows, then `scripts/windows/2-install-docker.ps1` (expects the installer in `D:\Docker\installer\`; checks its Docker Inc signature, writes `.wslconfig`, installs to D:).

1. Open PowerShell **as administrator**:
   ```powershell
   wsl --install --no-distribution
   ```
   Reboot when asked.
2. Download *Docker Desktop Installer.exe* from docker.com, then (admin PowerShell, in the download folder):
   ```powershell
   Start-Process -Wait ".\Docker Desktop Installer.exe" -ArgumentList 'install','--accept-license','--installation-dir=D:\Docker\DockerDesktop','--wsl-default-data-root=D:\Docker\wsl'
   ```
3. Limit WSL's memory so Windows stays usable. Create `%UserProfile%\.wslconfig`:
   ```ini
   [wsl2]
   memory=5GB
   swap=6GB
   swapFile=D:\\Docker\\wsl\\swap.vhdx
   ```
   then `wsl --shutdown` and start Docker Desktop.
4. Docker Desktop → Settings → Resources → *Disk image location*: confirm `D:\Docker\wsl`.
5. Model caches: create `D:\kx-models` (TEI, speech models) — the compose file mounts it (`KX_MODELS_DIR`, default `D:/kx-models`).

## 2. Python

```bash
uv sync --all-packages --all-extras
cp .env.example .env            # defaults work for local; KX_PLATFORM_PLAN=free needs no vendor keys
```

## 3. Profiles (one at a time on a small laptop)

| Command | Starts | RAM (approx.) | Use |
| --- | --- | --- | --- |
| `make up` | postgres, pgbouncer, redis | 0.5 GB | unit + DB tests, run the app with `uv run kx api` |
| `make up-all` | + migrate, api, worker (concurrency 2), beat | 2 GB | end-to-end chat / WhatsApp flows with fakes |
| `make up-voice` | livekit (dev mode), speech (faster-whisper Small + Piper), voice worker | 2.5 GB | website voice, free version |
| `make up-mail` | stalwart | 0.3 GB | email send + reply poll |
| `make up-obs` | phoenix + its postgres | 0.7 GB | AI audit trail |
| `make up-embed` | TEI with Qwen3 (≈ 3.1 GB) — BGE-M3 needs ≈ 4.8 GB, run alone | 3–5 GB | knowledge search with vectors (otherwise full-text fallback) |

SigNoz and SafeLine are not run on the laptop (4–8 GB / 1.6 GB); test them on the staging server.

Stop a group with `docker compose --profile <name> stop`; drop all local data with `make down`.

## 4. Tests

```bash
make up
export KX_TEST_PG_DSN=postgresql://postgres:postgres@localhost:5432/postgres
uv run pytest packages/kx-core-db -q      # narrowest first
make lint && make test
```

DB tests talk to Postgres directly (port 5432). To run the suite through PgBouncer: `KX_DB_PGBOUNCER=true KX_TEST_PG_DSN=postgresql://postgres:postgres@localhost:6432/postgres make test`.

## 5. Free version locally

With `KX_PLATFORM_PLAN=free` (the `.env.example` default for local) no paid key is needed except the Claude key for real AI replies; without it the AI gives the safe reply and hands over. Voice: `make up-voice`, then open the website's voice page against `http://localhost:8000`.

## 6. The website against the local stack (live test, 20 Nov)

The live-test stack (`live-test/compose/live.yml`, API on `:38000`) runs the website API, the CMS and the AI.

1. Rebuild the image after any code change: `docker build -t kxlive/app:local .`, then
   `docker compose -p kxlive -f live-test/compose/live.yml --env-file .env up -d --force-recreate api worker worker-ai beat`.
2. Load the content once: `docker exec kxlive-api-1 kx content import-site`; the ERP list:
   `cat export.tsv | docker exec -i kxlive-api-1 sh -c 'cat > /tmp/erp.tsv && kx catalogue import-erp /tmp/erp.tsv'`.
3. The website: clone Arifa-Pack **outside this repo**, `npm install`, `.env.local` with
   `NEXT_PUBLIC_ARIFA_API_URL=http://localhost:38000`, then `npx next dev --port 3100` (the stack allows that origin).
   To see the page move, `git apply docs/handoff/website-adapter/arifa-site-actions.patch` in that clone.
4. Tests: `python live-test/scripts/website_live_api.py`; the browser run `live-test/scripts/website_live_browser.mjs`
   (playwright-core outside the repo; `PW_DIR`, `CHROME`). Root tests that need Redis use `KX_TEST_REDIS_URL`
   (the whole-app test `tests/integration/test_whole_app_flow.py`).
