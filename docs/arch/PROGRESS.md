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

## 0.2 `docs/FEATURE_MAP.md` + CI staleness check — COMPLETED

- **Done:** `docs/FEATURE_MAP.md`: a hand-maintained head (how to read and refresh it; SRS §8, §9, §10–11, §12,
  §13, §14, §15, §16, §17 → main code today, events, planned slice) and a generated region between two markers,
  filled by `kx map --write docs/FEATURE_MAP.md`. `kx map --check FILE` exits 1 when the region is stale (line
  endings ignored). New workflow `.github/workflows/feature-map.yml` (pull requests and pushes to `main`) runs
  `uv sync --frozen --all-packages` and `uv run kx map --check docs/FEATURE_MAP.md`; actions pinned to commit SHAs
  looked up with `git ls-remote` (`actions/checkout` v6.1.0 = `d23441a4…`, `astral-sh/setup-uv` v8.1.0 =
  `08807647…`). `ci.yml` is not touched.
- **Tests:** 3 more in `tests/unit/test_featuremap.py`: the splice keeps the hand-written part and ignores CRLF;
  `kx map --write` then `--check` through the CLI (stale → exit 1, `--feature` refused with them); the committed
  file keeps both markers and every SRS row.
- **Gates** (run on the commit's tree, before committing):
  - `ruff check .` PASS: All checks passed!
  - `ruff format --check .` PASS: 339 files already formatted
  - `mypy` PASS: Success: no issues found in 197 source files
  - `lint-imports` PASS: Contracts: 3 kept, 0 broken.
  - `pytest tests/unit` PASS: 109 passed
  - full suite PASS: tests="879" failures="0" errors="0" skipped="4", exit=0
  - OpenAPI PASS: `git diff docs/frontend` empty
  - `kx map --format json` PASS: identical to the 0.1 baseline
  - `kx map --check docs/FEATURE_MAP.md` (Windows) PASS: "docs/FEATURE_MAP.md is current"
  - cross-platform: `kx map --check docs/FEATURE_MAP.md` in the Linux app image (`kxlive/app:local`, Chromium
    present, no network) on the Windows-generated file PASS: "docs/FEATURE_MAP.md is current"
  - actionlint on the new workflow NOT RUN: not installed on this machine and pulling its image was refused here;
    `ci.yml`'s `stacks` job runs actionlint over every workflow, the new ones included. The YAML parses (PyYAML).
- **Diffs:** OpenAPI none. `kx map --format json` identical to the 0.1 baseline.
- **Deviations:** `tests/unit/test_no_removed_vendors.py` pins the exact set of workflow files (it guards against
  the AWS / Terraform workflows coming back); `feature-map.yml` is added to that set (the first full run failed
  on it: 879 tests, 1 failure, fixed before committing). The CI job is a new workflow file (`ci.yml` is
  being edited on another branch). It syncs with `--all-packages` (a superset of `ci.yml`'s `uv sync --frozen`)
  so every workspace package imports.
- **Found, not fixed:** none.
- **Questions:** none.
- **Next:** 0.3 CODEOWNERS, PR template, `cross-package` label workflow.

## Rebase onto the 19 Nov fix round — COMPLETED

- **Done:** Phase 0 rebased onto `fix/live-test-defects` (head `bdf2735`). That round had removed
  `EventBus.subscribers` as unused, but `kx map` reads it: restored in `arifaa_pack/container.py` with its caller
  named. `docs/FEATURE_MAP.md` regenerated; `CURRENT_STATE.md` model counts corrected (9 model modules; names per
  group recounted on the rebased tree). Commit `782f53b`.
- **Gates:** ruff PASS; mypy (container.py) PASS; `kx map --check docs/FEATURE_MAP.md` PASS. Full suite: see 0.4.

## 0.3 Review routing — COMPLETED

- **Done:** `.github/CODEOWNERS` (one line per package, placeholders `@OWNER_CORE`, `@OWNER_CRM`,
  `@OWNER_AUTOMATION`, `@OWNER_AGENTS`, `@OWNER_APP`; `contracts*`, `event_payloads*` and `events.py` need every
  owner); `.github/pull_request_template.md` (packages touched, contract changed Y/N, payload version changed Y/N,
  migration and database, FEATURE_MAP / OpenAPI refreshed, tests run); `.github/workflows/pr-labels.yml` labels a
  PR `cross-package` when it touches more than one `packages/kx-*` folder or a contract file. Commit `b750070`.
- **Deviations:** the label workflow uses no third-party action (only `gh` and the GitHub API on
  `pull_request_target`, no checkout), so there is nothing to pin and no PR code runs with the write token.
  `tests/unit/test_no_removed_vendors.py` (exact set of workflow files) admits `pr-labels.yml`.
- **Gates:** see 0.4 (run once on the Phase 0 head). `test_no_removed_vendors.py` alone: 13 passed.

## 0.4 Decision records — COMPLETED

- **Done:** `docs/adr/` with `README.md` (index), `0000-template.md`, ADR-0001 "Package-based modular monolith
  with feature slices" (context, decision, consequences, alternatives rejected: single app, microservices,
  documentation only), ADR-0002 "Split the contracts by conversation and publish the packages" (Phase 3, future,
  proposed). Linked from `docs/ARCHITECTURE.md` §2 (one paragraph, nothing else rewritten). Commit `e46558e`.
- **Gates** (on `e46558e`, the Phase 0 head):
  - `ruff check .` PASS: All checks passed!
  - `ruff format --check .` PASS: 362 files already formatted
  - `mypy` PASS: Success: no issues found in 207 source files
  - `lint-imports` PASS: Contracts: 3 kept, 0 broken.
  - `kx map --check docs/FEATURE_MAP.md` PASS: "docs/FEATURE_MAP.md is current"
  - OpenAPI PASS: `scripts/gen_frontend_docs.py` then `git diff docs/frontend` empty
  - full suite (Linux, own Postgres `kx-verify-pg`) PASS: tests="982" failures="0" errors="0" skipped="4", exit=0
- **Diffs:** OpenAPI none; `kx map --format json` saved as the Phase 1 baseline.
- **Found, not fixed:** none new.
- **Questions:** the real owners for CODEOWNERS.
- **Next:** Phase 1 on branch `arch/slices`: `docs/arch/CRM_SLICES.md`, then one slice per commit.

**Phase 0: COMPLETED.**

---

# Phase 1 — feature slices inside `kx.crm` (branch `arch/slices`)

Mapping and order: `docs/arch/CRM_SLICES.md` (1.0, `9257af7`). How each slice is moved and checked:

- `git mv` per module; an **alias shim** at the old path (`sys.modules[old] = new`, plus a star import for type
  checkers), so old imports, private names and monkeypatching keep working; `src/` imports rewritten to the new
  paths by an AST-based script (tests stay on the old paths and so exercise the shims).
- Gates per slice commit: ruff, ruff format, mypy (strict), lint-imports, `tests/unit`, OpenAPI regenerated and
  diffed, `kx map --format json` compared with the Phase 0 baseline **after applying the renames** (equal = the
  diff is path-only), `kx map --write` then `--check` on `docs/FEATURE_MAP.md`. The full Linux suite runs on
  each slice commit in a separate worktree; the slice is tagged `arch-slice/crm-<slice>` (COMPLETED) only after
  it passes, and the next slice is committed only after that.
- **OpenAPI component names:** when two response / request models share a class name (`ChangeOut` in
  `api/texts` and `api/screens`; `CompanyIn` in `api/company` and `api/records`), FastAPI names the component
  after the module path (`kx__crm__api__texts__ChangeOut`). Moving the module would rename the component, so
  those four classes pin `__module__` to their pre-move path (an alias of the same module) and the published
  OpenAPI stays byte-identical.

## 1.1 slice `texts` — COMPLETED

- **Moved:** `services/texts.py` → `texts/service.py`, `api/texts.py` → `texts/router.py`. Shims at both old
  paths. Imports rewritten: `api/__init__.py`, `arifaa_pack/texts_sync.py`. `texts/router.py` `ChangeOut` pins its
  `__module__` (see above).
- **Gates:**
  - ruff check: All checks passed!
  - ruff format: 366 files already formatted
  - mypy: Success: no issues found in 210 source files
  - lint-imports: Contracts: 3 kept, 0 broken.
  - kx map json: kx map: identical after renames (path-only diff)
  - kx map --check: docs/FEATURE_MAP.md is current
  - OpenAPI diff: empty
  - unit: ============================ 124 passed in 24.12s =============================
  - full suite: see the tag `arch-slice/crm-texts` (logged in the next entry)

## 1.2 slice `company` — TESTING (COMPLETED when tagged `arch-slice/crm-company`)

- **Moved:** `services/company.py` → `company/service.py`, `api/company.py` → `company/router.py`; shims at both old paths; imports rewritten in `api/__init__.py`, `services/{handover,issuing,settings,summary}.py`, `arifaa_pack/cli.py`; `CompanyIn` pins `__module__` (OpenAPI name).
- **Gates:**
  - ruff check: All checks passed!
  - ruff format: 369 files already formatted
  - mypy: Success: no issues found in 213 source files
  - lint-imports: Contracts: 3 kept, 0 broken.
  - kx map json: kx map: identical after renames (path-only diff)
  - kx map --check: docs/FEATURE_MAP.md is current
  - OpenAPI diff: empty
  - unit: ============================ 124 passed in 17.49s =============================
  - full suite: run on this commit in the test worktree; result logged in the next entry
- **Previous slice's full suite:** `texts` @ `32daf5d`: tests=982 failures=0 errors=0 skipped=4, exit=0 → tagged `arch-slice/crm-texts`, COMPLETED

## 1.3 slice `operations` — TESTING (COMPLETED when tagged `arch-slice/crm-operations`)

- **Moved:** `api/operations.py` → `operations/router.py`; shim at the old path; import rewritten in `api/__init__.py`.
- **Gates:**
  - ruff check: All checks passed!
  - ruff format: 371 files already formatted
  - mypy: Success: no issues found in 215 source files
  - lint-imports: Contracts: 3 kept, 0 broken.
  - kx map json: kx map: identical after renames (path-only diff)
  - kx map --check: docs/FEATURE_MAP.md is current
  - OpenAPI diff: empty
  - unit: ============================ 124 passed in 18.09s =============================
  - full suite: run on this commit in the test worktree; result logged in the next entry
- **Previous slice's full suite:** `company` @ `2e3f95c`: tests=982 failures=0 errors=0 skipped=4, exit=0 → tagged `arch-slice/crm-company`, COMPLETED

## 1.4 slice `retention` — TESTING (COMPLETED when tagged `arch-slice/crm-retention`)

- **Moved:** `services/retention.py` → `retention/service.py`; shim at the old path; imports rewritten in `jobs.py`, `arifaa_pack/container.py`.
- **Gates:**
  - ruff check: All checks passed!
  - ruff format: 373 files already formatted
  - mypy: Success: no issues found in 217 source files
  - lint-imports: Contracts: 3 kept, 0 broken.
  - kx map json: kx map: identical after renames (path-only diff)
  - kx map --check: docs/FEATURE_MAP.md is current
  - OpenAPI diff: empty
  - unit: ============================ 124 passed in 17.01s =============================
  - full suite: run on this commit in the test worktree; result logged in the next entry
- **Previous slice's full suite:** `operations` @ `8b4f851`: errors=0, failures=0, skipped=4, tests=982, exit=0 → tagged `arch-slice/crm-operations`, COMPLETED

## 1.5 slice `reports` — TESTING (COMPLETED when tagged `arch-slice/crm-reports`)

- **Moved:** `services/dashboards.py` → `reports/dashboards.py`, `services/scheduled_reports.py` → `reports/scheduled.py`, `api/reports.py` → `reports/router.py`; shims at the three old paths; imports rewritten in `api/__init__.py`, `api/screens.py`, `jobs.py` and inside the slice.
- **Gates:**
  - ruff check: All checks passed!
  - ruff format: 377 files already formatted
  - mypy: Success: no issues found in 221 source files
  - lint-imports: Contracts: 3 kept, 0 broken.
  - kx map json: kx map: identical after renames (path-only diff)
  - kx map --check: docs/FEATURE_MAP.md is current
  - OpenAPI diff: empty
  - unit: ============================ 124 passed in 21.39s =============================
  - full suite: run on this commit in the test worktree; result logged in the next entry
- **Previous slice's full suite:** `retention` @ `49e061e`: errors=0, failures=0, skipped=4, tests=982, exit=0 → tagged `arch-slice/crm-retention`, COMPLETED

## 1.6 slice `catalogue` — TESTING (COMPLETED when tagged `arch-slice/crm-catalogue`)

- **Moved:** `services/catalogue.py` → `catalogue/service.py`, `services/workbook.py` → `catalogue/workbook.py`, `api/catalogue_workbook.py` → `catalogue/router.py`; shims at the three old paths; imports rewritten in `api/__init__.py`, `jobs.py` and inside the slice.
- **Gates:**
  - ruff check: All checks passed!
  - ruff format: 381 files already formatted
  - mypy: Success: no issues found in 225 source files
  - lint-imports: Contracts: 3 kept, 0 broken.
  - kx map json: kx map: identical after renames (path-only diff)
  - kx map --check: docs/FEATURE_MAP.md is current
  - OpenAPI diff: empty
  - unit: ============================ 124 passed in 21.98s =============================
  - full suite: run on this commit in the test worktree; result logged in the next entry
- **Previous slice's full suite:** `reports` @ `bf06f6d`: errors=0, failures=0, skipped=4, tests=982, exit=0 → tagged `arch-slice/crm-reports`, COMPLETED

## 1.7 slice `pricing` — TESTING (COMPLETED when tagged `arch-slice/crm-pricing`)

- **Moved:** `services/pricing.py` → `pricing/service.py`, `services/price_lists.py` → `pricing/price_list_service.py`, `services/fx.py` → `pricing/fx.py`, `services/price_proposals.py` → `pricing/proposals.py`; shims at the four old paths; imports rewritten in 10 modules; `pricing/__init__.py` docstring now describes the pure maths and the I/O around it. `engine.py`, `price_list.py`, `types.py` untouched.
- **Gates:**
  - ruff check: All checks passed!
  - ruff format: 385 files already formatted
  - mypy: Success: no issues found in 229 source files
  - lint-imports: Contracts: 3 kept, 0 broken.
  - kx map json: kx map: identical after renames (path-only diff)
  - kx map --check: docs/FEATURE_MAP.md is current
  - OpenAPI diff: empty
  - unit: ============================ 124 passed in 20.32s =============================
  - full suite: run on this commit in the test worktree; result logged in the next entry
- **Previous slice's full suite:** `catalogue` @ `43ebedc`: errors=0, failures=0, skipped=4, tests=982, exit=0 → tagged `arch-slice/crm-catalogue`, COMPLETED
