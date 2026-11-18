# Architecture restructuring: progress log

The plan: the restructuring brief (ADR-0001 once written). Resume from the last entry. Status labels: NOT STARTED ·
IN PROGRESS · BLOCKED · IN REVIEW · TESTING · COMPLETED.

**Scope of this run: Phase 0 only** (no file moves). Branch `arch/phase0-tooling`, from `main` at `ed9e32e`.

## How the gates are run on this machine (Windows, no `uv`)

The `Makefile` / `ci.yml` commands (`uv run …`), with the main checkout's virtualenv and `PYTHONPATH` pointing at
this worktree's `src` folders (verified: `kx.crm.__file__` is inside the worktree):

| Gate | Command |
| --- | --- |
| lint | `ruff check .` |
| format | `ruff format --check .` |
| types | `mypy packages/kx-core-db/src packages/kx-core-crm/src packages/kx-agent-framework/src packages/kx-core-automation/src apps/arifaa-pack/src` (strict, from `pyproject.toml`) |
| imports | `lint-imports` |
| unit tests (fast, Windows) | `python -m pytest tests/unit -p no:cacheprovider` |
| full suite incl. DB tests | `KX_TEST_NET=kairox-platform_default KX_TEST_ADMIN_DSN=postgresql://postgres:postgres@kx-arch-pg:5432/postgres KX_TEST_OUT_DIR=live-test/results/dev-arch sh live-test/scripts/pytest_in_linux.sh` (the app image on Linux, a Postgres of its own; totals from the junit file) |
| OpenAPI unchanged | `python scripts/gen_frontend_docs.py` then `git diff --exit-code docs/frontend` (`openapi-crm.json`, `openapi-website.json` and the two API docs) |
| `kx map` unchanged | from 0.1 on: `kx map --format json` compared with the output of the 0.1 commit |

Baseline before any change (`ed9e32e`): ruff clean, 334 files formatted, mypy clean (196 files), import-linter
3 kept / 0 broken, full suite **864 tests, 0 failures, 0 errors, 4 skipped** (the 4 need `redis-server`).

---

## 0.0 Recon report — COMPLETED

- **Done:** `docs/arch/CURRENT_STATE.md`: inventory per package, Protocol → implementation → file → wiring path,
  event → handlers, route → router → service functions, jobs and Beat, intra-package import tables for the rule-5
  baseline, app imports of package internals, brief-vs-repo differences.
- **Gates** (run on the commit's tree, before committing):
  - `ruff check .` PASS: All checks passed!
  - `ruff format --check .` PASS: 335 files already formatted
  - `mypy` PASS: Success: no issues found in 196 source files
  - `lint-imports` PASS: Contracts: 3 kept, 0 broken.
  - `pytest tests/unit` PASS: 94 passed
  - full suite PASS: tests="864" failures="0" errors="0" skipped="4", exit=0
  - OpenAPI PASS: `git diff docs/frontend` empty
- **Diffs:** OpenAPI none. `kx map` not yet committed (the recon used its working draft).
- **Deviations:** none from the brief's 4.0 list. The test count is 864, not the 856 quoted at launch.
- **Found, not fixed:** see `CURRENT_STATE.md` §6 (type-only import cycle `kx.automation.ports` ↔ `alerts`; six
  events with no subscriber; stale payload count in `docs/REUSE_CATALOGUE.md`).
- **Questions:** `kx.agents.models` is the LLM model router, not ORM: the Phase 2 forbidden contract on
  `kx.*.*.models` must name ORM modules explicitly, or the module is renamed in its slice move.
- **Next:** 0.1 `kx map`.

## 0.1 `kx map` CLI — COMPLETED

- **Done:** `kx map [--format md|json] [--feature NAME]` (`apps/arifaa-pack/src/arifaa_pack/cli.py`, logic in
  `arifaa_pack/featuremap.py`). It runs the app's own `create_app()` lifespan over the real `container.build()`
  with three I/O edges replaced (`Databases` by an empty in-memory stand-in, the LangGraph checkpointer by
  `InMemorySaver`, the Celery kicker by a no-op), then walks: Protocols in `kx.*` and the classes that satisfy them
  (composition-root namespace + live object graph, with the attribute path of each wired instance), event →
  `EventBus` subscribers → each package's handler table, every mounted route → endpoint → platform functions it
  references, the `JobRunner` task table and the Beat entries. JSON is sorted (byte-identical across runs,
  checked twice); logs go to stderr. No connection is opened.
- **Tests:** `tests/unit/test_featuremap.py` (12): every `kx.core.contracts` Protocol has an implementation;
  every event with a registered handler (CRM `HANDLERS`, automation `registry()`, agents `handlers()`, the app's
  two routes) is in the map with that handler and nothing else is invented; routes / jobs / Beat; sorted output;
  `--feature`; Markdown sections; the structural check (renamed positional parameters pass, keyword-only or sync
  methods and missing attributes fail). The map is built once per module in a subprocess (clean stdout, no
  settings cache or logging config leaking into the test process).
- **Gates** (run on the commit's tree, before committing):
  - `ruff check .` PASS: All checks passed!
  - `ruff format --check .` PASS: 338 files already formatted
  - `mypy` PASS: Success: no issues found in 197 source files
  - `lint-imports` PASS: Contracts: 3 kept, 0 broken.
  - `pytest tests/unit` PASS: 106 passed
  - full suite PASS: tests="876" failures="0" errors="0" skipped="4", exit=0 (864 + the 12 new tests, which also
    pass on Linux)
  - OpenAPI PASS: `git diff docs/frontend` empty
- **Diffs:** OpenAPI none. `kx map` json: this commit's output is the baseline for the next commits.
- **Deviations:** default format is `md` (the brief lists both, names no default). `injected_at` paths depend on
  optional settings (Chromium present, Stalwart on ...), so they are in the JSON only. "Calls" lists functions
  an endpoint names in its code; calls through injected objects (`deps.settings.update`) are not visible.
- **Found, not fixed:** none new.
- **Questions:** none.
- **Next:** 0.2 `docs/FEATURE_MAP.md` + CI staleness check.
