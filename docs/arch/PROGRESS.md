# Architecture restructuring: progress log

The plan: the restructuring brief (ADR-0001 once written). Resume from the last entry. Status labels: NOT STARTED ·
IN PROGRESS · BLOCKED · IN REVIEW · TESTING · COMPLETED.

**Scope of this run: Phase 0 only** (no file moves). Branch `arch/phase0-tooling`, from `main` at `2389b0d`.

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

Baseline before any change (`2389b0d`): ruff clean, 334 files formatted, mypy clean (196 files), import-linter
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

- **Done:** Phase 0 rebased onto `fix/live-test-defects` (head `e459273`). That round had removed
  `EventBus.subscribers` as unused, but `kx map` reads it: restored in `arifaa_pack/container.py` with its caller
  named. `docs/FEATURE_MAP.md` regenerated; `CURRENT_STATE.md` model counts corrected (9 model modules; names per
  group recounted on the rebased tree). Commit `7c0aeec`.
- **Gates:** ruff PASS; mypy (container.py) PASS; `kx map --check docs/FEATURE_MAP.md` PASS. Full suite: see 0.4.

## 0.3 Review routing — COMPLETED

- **Done:** `.github/CODEOWNERS` (one line per package, placeholders `@OWNER_CORE`, `@OWNER_CRM`,
  `@OWNER_AUTOMATION`, `@OWNER_AGENTS`, `@OWNER_APP`; `contracts*`, `event_payloads*` and `events.py` need every
  owner); `.github/pull_request_template.md` (packages touched, contract changed Y/N, payload version changed Y/N,
  migration and database, FEATURE_MAP / OpenAPI refreshed, tests run); `.github/workflows/pr-labels.yml` labels a
  PR `cross-package` when it touches more than one `packages/kx-*` folder or a contract file. Commit `2bddc13`.
- **Deviations:** the label workflow uses no third-party action (only `gh` and the GitHub API on
  `pull_request_target`, no checkout), so there is nothing to pin and no PR code runs with the write token.
  `tests/unit/test_no_removed_vendors.py` (exact set of workflow files) admits `pr-labels.yml`.
- **Gates:** see 0.4 (run once on the Phase 0 head). `test_no_removed_vendors.py` alone: 13 passed.

## 0.4 Decision records — COMPLETED

- **Done:** `docs/adr/` with `README.md` (index), `0000-template.md`, ADR-0001 "Package-based modular monolith
  with feature slices" (context, decision, consequences, alternatives rejected: single app, microservices,
  documentation only), ADR-0002 "Split the contracts by conversation and publish the packages" (Phase 3, future,
  proposed). Linked from `docs/ARCHITECTURE.md` §2 (one paragraph, nothing else rewritten). Commit `d258a9d`.
- **Gates** (on `d258a9d`, the Phase 0 head):
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

Mapping and order: `docs/arch/CRM_SLICES.md` (1.0, `e0bf66d`). How each slice is moved and checked:

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
- **Previous slice's full suite:** `texts` @ `a3e0d1f`: tests=982 failures=0 errors=0 skipped=4, exit=0 → tagged `arch-slice/crm-texts`, COMPLETED

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
- **Previous slice's full suite:** `company` @ `16ff472`: tests=982 failures=0 errors=0 skipped=4, exit=0 → tagged `arch-slice/crm-company`, COMPLETED

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
- **Previous slice's full suite:** `operations` @ `bc95af3`: errors=0, failures=0, skipped=4, tests=982, exit=0 → tagged `arch-slice/crm-operations`, COMPLETED

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
- **Previous slice's full suite:** `retention` @ `42cdf0b`: errors=0, failures=0, skipped=4, tests=982, exit=0 → tagged `arch-slice/crm-retention`, COMPLETED

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
- **Previous slice's full suite:** `reports` @ `d03c2d2`: errors=0, failures=0, skipped=4, tests=982, exit=0 → tagged `arch-slice/crm-reports`, COMPLETED

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
- **Previous slice's full suite:** `catalogue` @ `7c71ca1`: errors=0, failures=0, skipped=4, tests=982, exit=0 → tagged `arch-slice/crm-catalogue`, COMPLETED

## 1.8 slice `contacts` — TESTING (COMPLETED when tagged `arch-slice/crm-contacts`)

- **Moved:** `services/consent.py` → `contacts/consent.py`, `services/phone.py` → `contacts/phone.py`; shims at both old paths; imports rewritten in `api/deal_actions.py`, `gateway.py`, `jobs.py`, `services/issuing.py`, `services/summary.py`.
- **Gates:**
  - ruff check: All checks passed!
  - ruff format: 388 files already formatted
  - mypy: Success: no issues found in 232 source files
  - lint-imports: Contracts: 3 kept, 0 broken.
  - kx map json: kx map: identical after renames (path-only diff)
  - kx map --check: docs/FEATURE_MAP.md is current
  - OpenAPI diff: empty
  - unit: ============================ 124 passed in 22.38s =============================
  - full suite: run on this commit in the test worktree; result logged in the next entry
- **Previous slice's full suite:** `pricing` @ `7954b68`: errors=0, failures=0, skipped=4, tests=982, exit=0 → tagged `arch-slice/crm-pricing`, COMPLETED

## 1.9 slice `records` — TESTING (COMPLETED when tagged `arch-slice/crm-records`)

- **Moved:** `api/records.py` → `records/router.py`; shim at the old path; import rewritten in `api/__init__.py`; `CompanyIn` pins `__module__` (OpenAPI name).
- **Gates:**
  - ruff check: All checks passed!
  - ruff format: 390 files already formatted
  - mypy: Success: no issues found in 234 source files
  - lint-imports: Contracts: 3 kept, 0 broken.
  - kx map json: kx map: identical after renames (path-only diff)
  - kx map --check: docs/FEATURE_MAP.md is current
  - OpenAPI diff: empty
  - unit: ============================ 124 passed in 22.12s =============================
  - full suite: run on this commit in the test worktree; result logged in the next entry
- **Previous slice's full suite:** `contacts` @ `4c0c43a`: errors=0, failures=0, skipped=4, tests=982, exit=0 → tagged `arch-slice/crm-contacts`, COMPLETED

## 1.10 slice `auth` — TESTING (COMPLETED when tagged `arch-slice/crm-auth`)

- **Moved:** `services/auth.py` → `auth/service.py`, `api/auth.py` → `auth/router.py`, `api/session_guard.py` → `auth/session_guard.py`, `api/users.py` → `auth/users_router.py`, `throttle.py` → `auth/throttle.py`; shims at the five old paths; imports rewritten in `api/__init__.py`, `deps.py`, `arifaa_pack/{adapters,api,cli}.py` and inside the slice.
- **Gates:**
  - ruff check: All checks passed!
  - ruff format: 396 files already formatted
  - mypy: Success: no issues found in 240 source files
  - lint-imports: Contracts: 3 kept, 0 broken.
  - kx map json: kx map: identical after renames (path-only diff)
  - kx map --check: docs/FEATURE_MAP.md is current
  - OpenAPI diff: empty
  - unit: ============================ 124 passed in 21.90s =============================
  - full suite: run on this commit in the test worktree; result logged in the next entry
- **Previous slice's full suite:** `records` @ `63e3fe2`: errors=0, failures=0, skipped=4, tests=982, exit=0 → tagged `arch-slice/crm-records`, COMPLETED

## 1.11 slice `handover` — TESTING (COMPLETED when tagged `arch-slice/crm-handover`)

- **Moved:** `services/handover.py` → `handover/service.py`; shim at the old path; import rewritten in `api/deal_actions.py`.
- **Gates:**
  - ruff check: All checks passed!
  - ruff format: 398 files already formatted
  - mypy: Success: no issues found in 242 source files
  - lint-imports: Contracts: 3 kept, 0 broken.
  - kx map json: kx map: identical after renames (path-only diff)
  - kx map --check: docs/FEATURE_MAP.md is current
  - OpenAPI diff: empty
  - unit: ============================ 124 passed in 21.77s =============================
  - full suite: run on this commit in the test worktree; result logged in the next entry
- **Previous slice's full suite:** `auth` @ `28d7337`: errors=0, failures=0, skipped=4, tests=982, exit=0 → tagged `arch-slice/crm-auth`, COMPLETED

## 1.12 slice `assignment` — TESTING (COMPLETED when tagged `arch-slice/crm-assignment`)

- **Moved:** `services/assignment.py` → `assignment/service.py`, `api/assignment.py` → `assignment/router.py`; shims at both old paths; imports rewritten in `api/__init__.py`, `api/admin.py`, `api/deals.py`, `gateway.py` and inside the slice.
- **Gates:**
  - ruff check: All checks passed!
  - ruff format: 401 files already formatted
  - mypy: Success: no issues found in 245 source files
  - lint-imports: Contracts: 3 kept, 0 broken.
  - kx map json: kx map: identical after renames (path-only diff)
  - kx map --check: docs/FEATURE_MAP.md is current
  - OpenAPI diff: empty
  - unit: ============================ 124 passed in 22.36s =============================
  - full suite: run on this commit in the test worktree; result logged in the next entry
- **Previous slice's full suite:** `handover` @ `bd5562e`: errors=0, failures=0, skipped=4, tests=982, exit=0 → tagged `arch-slice/crm-handover`, COMPLETED

## 1.13 slice `change_requests` — TESTING (COMPLETED when tagged `arch-slice/crm-change_requests`)

- **Moved:** `services/change_requests.py` → `change_requests/service.py`, `api/change_requests.py` → `change_requests/router.py`; shims at both old paths; imports rewritten in 7 modules (`api/__init__.py`, `api/deals.py`, `assignment/service.py`, `gateway.py`, `jobs.py`, `services/approvals.py`, `services/inbox.py`).
- **Gates:**
  - ruff check: All checks passed!
  - ruff format: 404 files already formatted
  - mypy: Success: no issues found in 248 source files
  - lint-imports: Contracts: 3 kept, 0 broken.
  - kx map json: kx map: identical after renames (path-only diff)
  - kx map --check: docs/FEATURE_MAP.md is current
  - OpenAPI diff: empty
  - unit: ============================ 124 passed in 23.15s =============================
  - full suite: run on this commit in the test worktree; result logged in the next entry
- **Previous slice's full suite:** `assignment` @ `81ff471`: errors=0, failures=0, skipped=4, tests=982, exit=0 → tagged `arch-slice/crm-assignment`, COMPLETED

## 1.14 slice `orders` — TESTING (COMPLETED when tagged `arch-slice/crm-orders`)

- **Moved:** `services/orders.py` → `orders/service.py`; shim at the old path; imports rewritten in `api/deal_actions.py`, `api/screens.py`.
- **Gates:**
  - ruff check: All checks passed!
  - ruff format: 406 files already formatted
  - mypy: Success: no issues found in 250 source files
  - lint-imports: Contracts: 3 kept, 0 broken.
  - kx map json: kx map: identical after renames (path-only diff)
  - kx map --check: docs/FEATURE_MAP.md is current
  - OpenAPI diff: empty
  - unit: ============================ 124 passed in 20.67s =============================
  - full suite: run on this commit in the test worktree; result logged in the next entry
- **Previous slice's full suite:** `change_requests` @ `db70dc5`: errors=0, failures=0, skipped=4, tests=982, exit=0 → tagged `arch-slice/crm-change_requests`, COMPLETED

## 1.15 slice `approvals` — TESTING (COMPLETED when tagged `arch-slice/crm-approvals`)

- **Moved:** `services/approvals.py` → `approvals/service.py` (code byte-identical); shim at the old path; imports rewritten in 8 modules (`api/{admin,deals,quotes}.py`, `assignment/service.py`, `gateway.py`, `jobs.py`, `services/{inbox,quotes}.py`).
- **Gates:**
  - ruff check: All checks passed!
  - ruff format: 408 files already formatted
  - mypy: Success: no issues found in 252 source files
  - lint-imports: Contracts: 3 kept, 0 broken.
  - kx map json: kx map: identical after renames (path-only diff)
  - kx map --check: docs/FEATURE_MAP.md is current
  - OpenAPI diff: empty
  - unit: ============================ 124 passed in 23.74s =============================
  - full suite: run on this commit in the test worktree; result logged in the next entry
- **Previous slice's full suite:** `orders` @ `4bd8e6a`: errors=0, failures=0, skipped=4, tests=982, exit=0 → tagged `arch-slice/crm-orders`, COMPLETED

## 1.16 slice `negotiation` — TESTING (COMPLETED when tagged `arch-slice/crm-negotiation`)

- **Moved:** `services/negotiation.py` → `negotiation/service.py`; shim at the old path; imports rewritten in `api/deals.py`, `gateway.py`.
- **Gates:**
  - ruff check: All checks passed!
  - ruff format: 410 files already formatted
  - mypy: Success: no issues found in 254 source files
  - lint-imports: Contracts: 3 kept, 0 broken.
  - kx map json: kx map: identical after renames (path-only diff)
  - kx map --check: docs/FEATURE_MAP.md is current
  - OpenAPI diff: empty
  - unit: ============================ 124 passed in 22.20s =============================
  - full suite: run on this commit in the test worktree; result logged in the next entry
- **Previous slice's full suite:** `approvals` @ `ef0056f`: errors=0, failures=0, skipped=4, tests=982, exit=0 → tagged `arch-slice/crm-approvals`, COMPLETED

## 1.17 slice `inbox` — TESTING (COMPLETED when tagged `arch-slice/crm-inbox`)

- **Moved:** `services/inbox.py` → `inbox/service.py`; shim at the old path; import rewritten in `api/screens.py`.
- **Gates:**
  - ruff check: All checks passed!
  - ruff format: 412 files already formatted
  - mypy: Success: no issues found in 256 source files
  - lint-imports: Contracts: 3 kept, 0 broken.
  - kx map json: kx map: identical after renames (path-only diff)
  - kx map --check: docs/FEATURE_MAP.md is current
  - OpenAPI diff: empty
  - unit: ============================ 124 passed in 23.66s =============================
  - full suite: run on this commit in the test worktree; result logged in the next entry
- **Previous slice's full suite:** `negotiation` @ `a1d240a`: errors=0, failures=0, skipped=4, tests=982, exit=0 → tagged `arch-slice/crm-negotiation`, COMPLETED

## 1.18 slice `screens` — TESTING (COMPLETED when tagged `arch-slice/crm-screens`)

- **Moved:** `api/screens.py` → `screens/router.py`; shim at the old path; import rewritten in `api/__init__.py`; `ChangeOut` pins `__module__` (OpenAPI name).
- **Gates:**
  - ruff check: All checks passed!
  - ruff format: 414 files already formatted
  - mypy: Success: no issues found in 258 source files
  - lint-imports: Contracts: 3 kept, 0 broken.
  - kx map json: kx map: identical after renames (path-only diff)
  - kx map --check: docs/FEATURE_MAP.md is current
  - OpenAPI diff: empty
  - unit: ============================ 124 passed in 23.11s =============================
  - full suite: run on this commit in the test worktree; result logged in the next entry
- **Previous slice's full suite:** `inbox` @ `4e86747`: errors=0, failures=0, skipped=4, tests=982, exit=0 → tagged `arch-slice/crm-inbox`, COMPLETED

## 1.19 slice `admin` — TESTING (COMPLETED when tagged `arch-slice/crm-admin`)

- **Moved:** `api/admin.py` → `admin/router.py`; shim at the old path; import rewritten in `api/__init__.py`.
- **Gates:**
  - ruff check: All checks passed!
  - ruff format: 416 files already formatted
  - mypy: Success: no issues found in 260 source files
  - lint-imports: Contracts: 3 kept, 0 broken.
  - kx map json: kx map: identical after renames (path-only diff)
  - kx map --check: docs/FEATURE_MAP.md is current
  - OpenAPI diff: empty
  - unit: ============================ 124 passed in 24.24s =============================
  - full suite: run on this commit in the test worktree; result logged in the next entry
- **Previous slice's full suite:** `screens` @ `264aa7e`: errors=0, failures=0, skipped=4, tests=982, exit=0 → tagged `arch-slice/crm-screens`, COMPLETED

## 1.20 slice `deals` — TESTING (COMPLETED when tagged `arch-slice/crm-deals`)

- **Moved:** `services/deals.py` → `deals/service.py`, `services/summary.py` → `deals/summary.py`, `services/lost_proposals.py` → `deals/lost_proposals.py`, `api/deals.py` → `deals/router.py`, `api/deal_actions.py` → `deals/actions_router.py`; shims at the five old paths; 24 imports rewritten across 19 modules; `tests/unit/test_featuremap.py` asserts the new router path (the only test line changed: it checks `kx map` output, which names the real module).
- **Gates:**
  - ruff check: All checks passed!
  - ruff format: 422 files already formatted
  - mypy: Success: no issues found in 266 source files
  - lint-imports: Contracts: 3 kept, 0 broken.
  - kx map json: kx map: identical after renames (path-only diff)
  - kx map --check: docs/FEATURE_MAP.md is current
  - OpenAPI diff: empty
  - unit: ============================ 124 passed in 22.51s =============================
  - full suite: run on this commit in the test worktree; result logged in the next entry
- **Previous slice's full suite:** `admin` @ `9692a89`: errors=0, failures=0, skipped=4, tests=982, exit=0 → tagged `arch-slice/crm-admin`, COMPLETED

## 1.21 slice `quotes` — TESTING (COMPLETED when tagged `arch-slice/crm-quotes`)

- **Moved:** `services/quotes.py` → `quotes/service.py`, `services/issuing.py` → `quotes/issuing.py`, `services/grants.py` → `quotes/grants.py` (byte-identical), `pdf.py` → `quotes/pdf.py`, `api/quotes.py` → `quotes/router.py`; shims at the five old paths; 31 imports rewritten across 19 modules; `tests/unit/test_featuremap.py` asserts the new `revise_quote` path.
- **Gates:**
  - ruff check: All checks passed!
  - ruff format: 428 files already formatted
  - mypy: Success: no issues found in 272 source files
  - lint-imports: Contracts: 3 kept, 0 broken.
  - kx map json: kx map: identical after renames (path-only diff)
  - kx map --check: docs/FEATURE_MAP.md is current
  - OpenAPI diff: empty
  - unit: ============================ 124 passed in 22.41s =============================
  - full suite: run on this commit in the test worktree; result logged in the next entry
- **Previous slice's full suite:** `deals` @ `4c4b65e`: errors=0, failures=0, skipped=4, tests=982, exit=0 → tagged `arch-slice/crm-deals`, COMPLETED

## 1.22 slice `_shared` — TESTING (COMPLETED when tagged `arch-slice/crm-_shared`)

- **Moved:** `services/{journal,access,staff,settings}.py` → `_shared/{journal,access,staff,settings}.py`, `api/context.py` → `_shared/api_context.py`, `api/pagination.py` → `_shared/pagination.py`, `api/schemas.py` → `_shared/schemas.py`; shims at the seven old paths; 102 imports rewritten; `api/__init__.py` and `services/__init__.py` docstrings say they now hold the router assembly / shims. No `src/` import of an old path is left (grep): `kx/crm/services/*` and `kx/crm/api/*` (except `build_router`) are shims only.
- **Gates:**
  - ruff check: All checks passed!
  - ruff format: 436 files already formatted
  - mypy: Success: no issues found in 280 source files
  - lint-imports: Contracts: 3 kept, 0 broken.
  - kx map json: kx map: identical after renames (path-only diff)
  - kx map --check: docs/FEATURE_MAP.md is current
  - OpenAPI diff: empty
  - unit: ============================ 124 passed in 21.66s =============================
  - full suite: run on this commit in the test worktree; result logged in the next entry
- **Previous slice's full suite:** `quotes` @ `5691c11`: errors=0, failures=0, skipped=4, tests=982, exit=0 → tagged `arch-slice/crm-quotes`, COMPLETED

## 1.23 public surface `kx/crm/__init__.py` — TESTING

- **Previous slice's full suite:** `_shared` @ `061a513`: tests=982, failures=0, errors=0, skipped=4, exit=0 →
  tagged `arch-slice/crm-_shared`, COMPLETED. **All 22 CRM slices are COMPLETED.**
- **Done:** `kx/crm/__init__.py` names what the app uses, with an explicit `__all__` (21 names: the wiring
  classes, `jobs`, `build_router`, `SessionGuard`, `MemoryThrottle`, the quote-PDF engines, `Actor`,
  `create_user`, `company_letterhead`, `retention_rules`, `load_text_edits`, `save_text_translation`). Resolved
  lazily (PEP 562 `__getattr__`) so importing one CRM module does not import the whole package; the same names
  are imported under `TYPE_CHECKING` so mypy sees the real types. The app (`adapters.py`, `api.py`, `cli.py`,
  `container.py`, `texts_sync.py`) now imports only `kx.crm`, except the two ORM classes below.
- **Rule-3 baseline (app → ORM), marked in the code:** `container.py` `from kx.crm.models import Product`
  (`cms_product_draft` writes `cms_product_id`), `cli.py` `from kx.crm.models import User` (`first-run` checks the
  MD / admin exist). Re-exporting them from the surface would only hide the rule-3 import; moving the two queries
  into the CRM is a logic change, so they stay as the recorded baseline for Phase 2's `forbidden` contract.
- **Gates:**
  - ruff check: All checks passed!
  - ruff format: 436 files already formatted
  - mypy: Success: no issues found in 280 source files
  - lint-imports: Contracts: 3 kept, 0 broken.
  - kx map json: identical after renames except one explained entry (see Deviations)
  - kx map --check: docs/FEATURE_MAP.md is current
  - OpenAPI diff: empty
  - unit: ============================ 124 passed in 23.12s =============================
  - full suite: run on this commit; result logged in the next entry
- **Deviations:** three functions are exported under package-level names (`company_letterhead` for
  `company.service.letterhead`, `retention_rules` for `retention.service.plumbing_rules`, `load_text_edits` /
  `save_text_translation` for `texts.service.load_edits` / `save_translation`): bare `letterhead` or `load_edits`
  would be unclear at package level, and the modules themselves cannot be exported as `company` / `texts` /
  `retention` because those are the slice packages' names. **`kx map` diff:** one entry fewer in the calls of the
  `app.retention` job: `kx.crm.retention.service:run_retention` is gone. It was a false positive: `kx map` resolves
  every name in a function body against every module in its scope, and the job calls
  `agents_retention.run_retention`; while the CRM retention module was in the container's namespace, the same
  name matched it too. The job never called the CRM's `run_retention` (it calls `plumbing_rules`). Everything
  else is identical after the renames. This commit's map is the baseline for Phase 2.
- **Found, not fixed:** `arifaa_pack/featuremap.py:234` `calls()` can report a function that is not called when
  another module in scope has an attribute of the same name (the case above).
- **Full suite (1.23):** `5d3f965`: tests=982, failures=0, errors=0, skipped=4, exit=0 → COMPLETED.

**Phase 1: COMPLETED** (22 slice tags `arch-slice/crm-*`, public surface). `kx/crm/services/*` and `kx/crm/api/*`
(except `build_router`) are alias shims only; no `src/` module imports them.

---

# Phase 2 — `kx.automation`, `kx.agents`, then the import contracts

## 2.0 mapping — COMPLETED

- **Done:** `docs/arch/AUTOMATION_AGENTS_SLICES.md`. Three automation slices (`outbound`, `inbound`, `optout`)
  where one feature spans several top-level modules; the feature's main module becomes the slice package's
  `__init__.py` (same module object, no shim). `kx.agents`: no moves (the brief's slices already exist one-to-one
  as modules or folders).
- **Deviations:** the brief suggests ten folders per package; the mapping explains why one-file features stay
  files (no reader gain, an extra shim each). `followups/` is not created: the follow-ups are part of the one
  handler table in `kx/automation/handlers.py`, and splitting that table is more than a move.

## 2.1 slice `outbound` — TESTING (COMPLETED when tagged `arch-slice/automation-outbound`)

- **Moved:** `outbound.py` → `outbound/__init__.py` (the same module `kx.automation.outbound`; OutboundService code unchanged, only its two import lines now name the new paths), `contact_hours.py` / `ratelimit.py` / `killswitch.py` → `outbound/`; shims at those three old paths; imports rewritten in `arifaa_pack/{adapters,container}.py`. `kx map` compared with the Phase 1 map (`5d3f965`).
- **Gates:**
  - ruff check: All checks passed!
  - ruff format: 440 files already formatted
  - mypy: Success: no issues found in 283 source files
  - lint-imports: Contracts: 3 kept, 0 broken.
  - kx map json: kx map: identical after renames (path-only diff)
  - kx map --check: docs/FEATURE_MAP.md is current
  - OpenAPI diff: empty
  - unit: ============================ 124 passed in 21.15s =============================
  - full suite: run on this commit in the test worktree; result logged in the next entry
- **Previous slice's full suite:** Phase 1 public surface @ `5d3f965` (see 1.23)

## 2.2 slice `inbound` — TESTING (COMPLETED when tagged `arch-slice/automation-inbound`)

- **Moved:** `inbound.py` → `inbound/__init__.py` (same module, code identical), `webhooks.py` → `inbound/webhooks.py`; shim at `kx.automation.webhooks`; imports rewritten in `arifaa_pack/{api,container}.py`.
- **Gates:**
  - ruff check: All checks passed!
  - ruff format: 441 files already formatted
  - mypy: Success: no issues found in 284 source files
  - lint-imports: Contracts: 3 kept, 0 broken.
  - kx map json: kx map: identical after renames (path-only diff)
  - kx map --check: docs/FEATURE_MAP.md is current
  - OpenAPI diff: empty
  - unit: ============================ 124 passed in 22.03s =============================
  - full suite: run on this commit in the test worktree; result logged in the next entry
- **Previous slice's full suite:** `outbound` @ `bf7a75b`: errors=0, failures=0, skipped=4, tests=982, exit=0 → tagged `arch-slice/automation-outbound`, COMPLETED

## 2.3 slice `optout` — TESTING (COMPLETED when tagged `arch-slice/automation-optout`)

- **Moved:** `optout.py` → `optout/__init__.py` (same module, code identical), `unsubscribe.py` → `optout/unsubscribe.py`; shim at `kx.automation.unsubscribe`; imports rewritten in `arifaa_pack/{api,container}.py`.
- **Gates:**
  - ruff check: All checks passed!
  - ruff format: 442 files already formatted
  - mypy: Success: no issues found in 285 source files
  - lint-imports: Contracts: 3 kept, 0 broken.
  - kx map json: kx map: identical after renames (path-only diff)
  - kx map --check: docs/FEATURE_MAP.md is current
  - OpenAPI diff: empty
  - unit: ============================ 124 passed in 22.01s =============================
  - full suite: run on this commit in the test worktree; result logged in the next entry
- **Previous slice's full suite:** `inbound` @ `66df896`: errors=0, failures=0, skipped=4, tests=982, exit=0 → tagged `arch-slice/automation-inbound`, COMPLETED

## 2.1–2.3 automation slices — COMPLETED

| Slice | Commit | Full suite | Tag |
| --- | --- | --- | --- |
| `outbound` | `bf7a75b` | tests=982, failures=0, errors=0, skipped=4, exit=0 | `arch-slice/automation-outbound` |
| `inbound` | `66df896` | tests=982, failures=0, errors=0, skipped=4, exit=0 | `arch-slice/automation-inbound` |
| `optout` | `c408875` | tests=982, failures=0, errors=0, skipped=4, exit=0 | `arch-slice/automation-optout` |

The three feature modules (`outbound.py`, `inbound.py`, `optout.py`) are byte-identical as `__init__.py` except
`outbound`'s two import lines (`contact_hours`, `ratelimit` now under it), checked with `diff`.

## 2.4 import contracts — TESTING

- **Done:** seven new contracts in `pyproject.toml` (the existing three kept), generated from the real import graph
  (grimp) so every baseline line is an import that exists today:
  - CRM: the ORM (`kx.crm.models`) imports nothing above it (no baseline needed);
  - CRM: `_shared` imports no slice, router assembly, gateway or job table (2 baseline lines:
    `_shared.settings → company.service`, `_shared.schemas → quotes.service`);
  - CRM rule 4 (`layers`, one container per slice with routers): routers above the service-side modules;
  - CRM rule 5 (`independence` of the 21 slices): 74 baseline lines, each a direct service-level call between two
    slices today (plus the `deps` / `_shared` / `gateway` edges through which those chains run), marked
    `# baseline 2026-11-19: may only shrink`;
  - automation: `outbound`, `inbound`, `optout` independent (1 baseline line: `inbound → optout`);
  - rule 3: the app imports no ORM / query module (`kx.crm.models`, `kx.automation.models`, `kx.automation.repo`,
    `kx.agents.orm`); 2 baseline lines (`container → kx.crm.models`, `cli → kx.crm.models`);
  - CRM: the pricing maths (`engine`, `price_list`, `types`) imports no database, ORM, HTTP or Redis code.
  `include_external_packages = true` so the last contract can name `sqlalchemy`, `httpx`, `redis`.
- **Contracts proven to bite:** three deliberate violations (a service importing its router, `_shared` importing a
  slice, the ORM importing the job table) broke 4 contracts; the files were restored.
- **Gates:**
  - ruff check: All checks passed!
  - ruff format: 442 files already formatted
  - mypy: Success: no issues found in 285 source files
  - lint-imports: Contracts: 10 kept, 0 broken.
  - kx map json: kx map: identical after renames (path-only diff)
  - kx map --check: docs/FEATURE_MAP.md is current
  - OpenAPI diff: empty
  - unit: ============================ 124 passed in 21.07s =============================
  - full suite: run on this commit; result logged in the next entry
- **Deviations:** the brief's `layers` "router → service → repository → models" per slice: there are no
  repository modules and the ORM is the shared layer (`CRM_SLICES.md`), so the per-slice contract has two layers
  (routers; service-side modules) and the ORM rule is a separate `forbidden` contract. The `forbidden` contract on
  `*.models` names the ORM modules explicitly because `kx.agents.models` is the LLM model router, not ORM
  (`CURRENT_STATE.md` §4).
- **Full suite (2.4):** `24b1c0c`: tests=982, failures=0, errors=0, skipped=4, exit=0 → COMPLETED.

## 2.5 shims removed where nothing imports them — TESTING

- **Done:** every alias shim's old path searched (AST imports and string references, in `packages/`, `apps/`,
  `tests/`, `scripts/`, `services/`, `infra/`, `docs/`). Removed the 19 that no file uses:
  - `kx.crm.api.admin`, `.assignment`, `.auth`, `.catalogue_workbook`, `.change_requests`, `.context`,
    `.deal_actions`, `.deals`, `.operations`, `.pagination`, `.quotes`, `.schemas`, `.users`;
  - `kx.crm.services.consent`, `.dashboards`, `.grants`, `.retention`, `.staff`, `.summary`.
  **The Arifaa Auto team must switch these imports to the new paths** (table in `docs/arch/CRM_SLICES.md`).
- **Kept (still imported):** 38 shims: the 25 `kx.crm.services.*` ones, `kx.crm.api.reports`,
  `kx.crm.api.session_guard`, `kx.crm.pdf`, `kx.crm.throttle` and the five automation helpers (all used by
  tests), and `kx.crm.api.company`, `.records`, `.screens`, `.texts`, which the pinned OpenAPI `__module__` names
  point at (removing those would also need a component-name decision).
- **Gates:**
  - ruff check: All checks passed!
  - ruff format: 423 files already formatted
  - mypy: Success: no issues found in 266 source files
  - lint-imports: Contracts: 10 kept, 0 broken.
  - kx map json: kx map: identical after renames (path-only diff)
  - kx map --check: docs/FEATURE_MAP.md is current
  - OpenAPI diff: empty
  - unit: ============================ 124 passed in 22.22s =============================
  - full suite: run on this commit; result logged in the final entry
- **Full suite (2.5):** `f89f9cb`: tests=982, failures=0, errors=0, skipped=4, exit=0 → COMPLETED.

## 2.6 documentation and the final report — COMPLETED

- **Done:** `docs/arch/REPORT.md` (brief §8: done, gates, diffs, deviations, found-not-fixed, questions). Living docs
  now name the new paths: `CONTRACTS.md` §1 (the in-package rules), `CLAUDE.md` (layout, rules),
  `docs/ARCHITECTURE.md` §2, `docs/REUSE_CATALOGUE.md` (also: payload count 23 → 33), `docs/FEATURE_MAP.md`
  (the SRS table's slice column), `docs/DELIVERY.md`, `docs/CONTESTS.md`, `scripts/frontend_heads/crm_head.md` →
  `docs/frontend/CRM_API.md` (head text only; the OpenAPI JSON is unchanged). Dated history (`V1_PROGRESS.md`,
  `OPEN_QUESTIONS.md`, `V1_CHANGE_PLAN.md`) keeps the paths of its time.
- **Gates** (docs only):
  - ruff check: All checks passed!
  - ruff format: 424 files already formatted
  - mypy: Success: no issues found in 266 source files
  - lint-imports: Contracts: 10 kept, 0 broken.
  - kx map json: kx map: identical after renames (path-only diff)
  - kx map --check: docs/FEATURE_MAP.md is current
  - OpenAPI diff:  1 file changed, 1 insertion(+), 1 deletion(-)
  - unit: ============================ 124 passed in 21.08s =============================

**Phase 2: COMPLETED. ADR-0001 Phases 0–2: COMPLETED.**

---

## After the restructure: fixes found by the 19 Nov live re-test (not part of ADR-0001)

These commits change behaviour, so they are separate `fix:` commits after the restructure, each with a test that
fails without it, every gate run, and the full suite:

| Commit | Fix | Full suite |
| --- | --- | --- |
| `a1e96c0` | an admin unlock also lifts the account's per-IP sign-in lockouts (M3) | tests=982, failures=0, errors=0, skipped=4 |
| `2810330` | a known IP is not held back by attempts on the account from elsewhere (M3 lockout DoS); logout ends the access token's session at once (L1) | tests=984, failures=0, errors=0, skipped=4 |
| `6a8a3c3` | NUL bytes refused with 400 before PostgreSQL (was 500); security headers on every answer; no API docs in production | tests=989, failures=0, errors=0, skipped=4 |

`kx map` stayed path-only identical and the OpenAPI changed only in the logout description (`2810330`).

---

# Phase 2b — the full slice layout for `kx.automation` and `kx.agents` (client decision, 19 Nov)

The client asked for the brief's slices exactly, so the whole codebase can be reviewed one feature at a time
(the 2.0 mapping had kept one-file features as files). Rules, the same in the three packages: one feature, one
folder (`router.py` for HTTP, `service.py` for logic, helpers when needed); the data layer at the package root
(`models.py` / `repo.py`, `orm.py`); shared code in `_shared/`; only wiring at the root; no logic in
`__init__.py`. Map: `docs/arch/AUTOMATION_AGENTS_SLICES.md` (rewritten at the end of this phase). Same method and
gates as Phase 1, with a new `kx map` baseline (the head of Phase 2, `917648d`).

When a folder takes the name of the old module (`scraping.py` → `scraping/`), the file moves into the folder and
the folder's `__init__.py` re-exports it (star import for public names, module `__getattr__` for private ones),
so the old import path keeps working; every name of the moved module is checked to be reachable on the old path.

## 2b.1 slice `scraping` — TESTING (COMPLETED when tagged `arch-slice/automation-scraping`)

- **Moved:** `scraping.py` → `scraping/service.py`; `scraping/__init__.py` re-exports it (old path checked: 36 of 36 names reachable); imports rewritten in `jobs.py`, `arifaa_pack/container.py`.
- **Gates:**
  - ruff check: All checks passed!
  - ruff format: 430 files already formatted
  - mypy: Success: no issues found in 268 source files
  - lint-imports: Contracts: 10 kept, 0 broken.
  - kx map json: kx map: identical after renames (path-only diff)
  - kx map --check: docs/FEATURE_MAP.md is current
  - OpenAPI diff: empty
  - unit: ============================ 131 passed in 21.77s =============================
  - full suite: run on this commit in the test worktree; result logged in the next entry
- **Previous slice's full suite:** n/a (first slice of 2b; the previous head `917648d` passed: 992 tests)

## 2b.2 slice `cms` — TESTING (COMPLETED when tagged `arch-slice/automation-cms`)

- **Moved:** `cms.py` → `cms/service.py`; `cms/__init__.py` re-exports it (old path checked: 34 of 34 names); import rewritten in `arifaa_pack/api.py`.
- **Gates:**
  - ruff check: All checks passed!
  - ruff format: 431 files already formatted
  - mypy: Success: no issues found in 269 source files
  - lint-imports: Contracts: 10 kept, 0 broken.
  - kx map json: kx map: identical after renames (path-only diff)
  - kx map --check: docs/FEATURE_MAP.md is current
  - OpenAPI diff: empty
  - unit: ============================ 131 passed in 23.61s =============================
  - full suite: run on this commit in the test worktree; result logged in the next entry
- **Previous slice's full suite:** `scraping` @ `34e6b67`: errors=0, failures=0, skipped=4, tests=992, exit=0 → tagged `arch-slice/automation-scraping`, COMPLETED

## 2b.3 slice `admin` — TESTING (COMPLETED when tagged `arch-slice/automation-admin`)

- **Moved:** `admin_api.py` → `admin/router.py`; alias shim at `admin_api.py` (old path checked: 36 of 36 names); import rewritten in `arifaa_pack/api.py`.
- **Gates:**
  - ruff check: All checks passed!
  - ruff format: 433 files already formatted
  - mypy: Success: no issues found in 271 source files
  - lint-imports: Contracts: 10 kept, 0 broken.
  - kx map json: kx map: identical after renames (path-only diff)
  - kx map --check: docs/FEATURE_MAP.md is current
  - OpenAPI diff: empty
  - unit: ============================ 131 passed in 21.67s =============================
  - full suite: run on this commit in the test worktree; result logged in the next entry
- **Previous slice's full suite:** `cms` @ `3c0f7d2`: errors=0, failures=0, skipped=4, tests=992, exit=0 → tagged `arch-slice/automation-cms`, COMPLETED

## 2b.4 slice `calls` — TESTING (COMPLETED when tagged `arch-slice/automation-calls`)

- **Moved:** `channels/calls.py` → `calls/channel.py`; alias shim at the old path (40 of 40 names); imports rewritten in `alerts.py`, `inbound/webhooks.py`, `arifaa_pack/container.py`.
- **Gates:**
  - ruff check: All checks passed!
  - ruff format: 435 files already formatted
  - mypy: Success: no issues found in 273 source files
  - lint-imports: Contracts: 10 kept, 0 broken.
  - kx map json: kx map: identical after renames (path-only diff)
  - kx map --check: docs/FEATURE_MAP.md is current
  - OpenAPI diff: empty
  - unit: ============================ 131 passed in 19.71s =============================
  - full suite: run on this commit in the test worktree; result logged in the next entry
- **Previous slice's full suite:** `admin` @ `ace553a`: errors=0, failures=0, skipped=4, tests=992, exit=0 → tagged `arch-slice/automation-admin`, COMPLETED

## 2b.5 slice `whatsapp` — TESTING (COMPLETED when tagged `arch-slice/automation-whatsapp`)

- **Moved:** `channels/whatsapp.py` → `whatsapp/channel.py`; alias shim at the old path (58 of 58 names); imports rewritten in `inbound`, `inbound/webhooks.py`, `outbound` (import line only), `arifaa_pack/container.py`.
- **Gates:**
  - ruff check: All checks passed!
  - ruff format: 437 files already formatted
  - mypy: Success: no issues found in 275 source files
  - lint-imports: Contracts: 10 kept, 0 broken.
  - kx map json: kx map: identical after renames (path-only diff)
  - kx map --check: docs/FEATURE_MAP.md is current
  - OpenAPI diff: empty
  - unit: ============================ 131 passed in 21.49s =============================
  - full suite: run on this commit in the test worktree; result logged in the next entry
- **Previous slice's full suite:** `calls` @ `b29e430`: errors=0, failures=0, skipped=4, tests=992, exit=0 → tagged `arch-slice/automation-calls`, COMPLETED

## 2b.6 slice `email` — TESTING (COMPLETED when tagged `arch-slice/automation-email`)

- **Moved:** `channels/email.py` → `email/channel.py`, `channels/stalwart.py` → `email/stalwart.py`, `channels/email_chain.py` → `email/chain.py`, `channels/feedback.py` → `email/feedback.py`; alias shims at the four old paths (all names reachable); 11 imports rewritten. The standard library `email` imports inside the folder still resolve to the standard library (absolute imports).
- **Gates:**
  - ruff check: All checks passed!
  - ruff format: 442 files already formatted
  - mypy: Success: no issues found in 280 source files
  - lint-imports: Contracts: 10 kept, 0 broken.
  - kx map json: kx map: identical after renames (path-only diff)
  - kx map --check: docs/FEATURE_MAP.md is current
  - OpenAPI diff: empty
  - unit: ============================ 131 passed in 18.72s =============================
  - full suite: run on this commit in the test worktree; result logged in the next entry
- **Previous slice's full suite:** `whatsapp` @ `d76293e`: errors=0, failures=0, skipped=4, tests=992, exit=0 → tagged `arch-slice/automation-whatsapp`, COMPLETED

## 2b.7 slice `alerts` — TESTING (COMPLETED when tagged `arch-slice/automation-alerts`)

- **Moved:** `alerts.py` → `alerts/service.py`; `alerts/__init__.py` re-exports it (67 of 67 names); imports rewritten in `handlers.py`, `inbound/webhooks.py`, `jobs.py`, `ports.py` (type-only), `arifaa_pack/container.py`.
- **Gates:**
  - ruff check: All checks passed!
  - ruff format: 443 files already formatted
  - mypy: Success: no issues found in 281 source files
  - lint-imports: Contracts: 10 kept, 0 broken.
  - kx map json: kx map: identical after renames (path-only diff)
  - kx map --check: docs/FEATURE_MAP.md is current
  - OpenAPI diff: empty
  - unit: ============================ 131 passed in 19.94s =============================
  - full suite: run on this commit in the test worktree; result logged in the next entry
- **Previous slice's full suite:** `email` @ `59f475e`: errors=0, failures=0, skipped=4, tests=992, exit=0 → tagged `arch-slice/automation-email`, COMPLETED

## 2b.8 slice `followups` — TESTING (COMPLETED when tagged `arch-slice/automation-followups`)

- **Moved:** the `FollowUps` class moved out of `handlers.py` into `followups/service.py`, and its send helper `_send_or_skip` (used by the other handlers too) into `_shared/sending.py`. Code unchanged: all 8 top-level definitions compare equal (syntax tree) before and after. `handlers.py` imports `FollowUps` back, so `from kx.automation.handlers import FollowUps` keeps working; `jobs.py` and `arifaa_pack/container.py` import it from the slice. The one exception to whole-file moves, as agreed (client decision 19 Nov).
- **Gates:**
  - ruff check: All checks passed!
  - ruff format: 447 files already formatted
  - mypy: Success: no issues found in 285 source files
  - lint-imports: Contracts: 10 kept, 0 broken.
  - kx map json: kx map: identical after renames (path-only diff)
  - kx map --check: docs/FEATURE_MAP.md is current
  - OpenAPI diff: empty
  - unit: ============================ 131 passed in 17.23s =============================
  - full suite: run on this commit in the test worktree; result logged in the next entry
- **Previous slice's full suite:** `alerts` @ `9b7342b`: errors=0, failures=0, skipped=4, tests=992, exit=0 → tagged `arch-slice/automation-alerts`, COMPLETED

## 2b.9 slice `webhooks` — TESTING (COMPLETED when tagged `arch-slice/automation-webhooks`)

- **Moved:** `inbound/webhooks.py` → `webhooks/router.py`; alias shim at `inbound/webhooks.py`; the old root alias file `webhooks.py` gave way to the folder, whose `__init__.py` re-exports the router (both old paths checked: 26 of 26 names); imports rewritten in `arifaa_pack/{api,container}.py`.
- **Gates:**
  - ruff check: All checks passed!
  - ruff format: 448 files already formatted
  - mypy: Success: no issues found in 286 source files
  - lint-imports: Contracts: 10 kept, 0 broken.
  - kx map json: kx map: identical after renames (path-only diff)
  - kx map --check: docs/FEATURE_MAP.md is current
  - OpenAPI diff: empty
  - unit: ============================ 131 passed in 17.97s =============================
  - full suite: run on this commit in the test worktree; result logged in the next entry
- **Previous slice's full suite:** `followups` @ `58559e2`: errors=0, failures=0, skipped=4, tests=992, exit=0 → tagged `arch-slice/automation-followups`, COMPLETED

## 2b.10 slice `inbound` — TESTING (COMPLETED when tagged `arch-slice/automation-inbound`)

- **Moved:** the code of `inbound/__init__.py` → `inbound/service.py` (byte-identical); `inbound/__init__.py` re-exports it (52 of 52 names); imports rewritten in `jobs.py`, `webhooks/router.py`; the automation independence baseline line names the importer `inbound.service` now.
- **Gates:**
  - ruff check: All checks passed!
  - ruff format: 449 files already formatted
  - mypy: Success: no issues found in 287 source files
  - lint-imports: Contracts: 10 kept, 0 broken.
  - kx map json: kx map: identical after renames (path-only diff)
  - kx map --check: docs/FEATURE_MAP.md is current
  - OpenAPI diff: empty
  - unit: ============================ 131 passed in 16.57s =============================
  - full suite: run on this commit in the test worktree; result logged in the next entry
- **Previous slice's full suite:** `webhooks` @ `06ca020`: errors=0, failures=0, skipped=4, tests=992, exit=0 → tagged `arch-slice/automation-webhooks`, COMPLETED

## 2b.11 slice `optout` — TESTING (COMPLETED when tagged `arch-slice/automation-optout`)

- **Moved:** the code of `optout/__init__.py` → `optout/service.py` (byte-identical), `optout/unsubscribe.py` → `optout/router.py`; `optout/__init__.py` re-exports the service, alias shims at `optout/unsubscribe.py` and `unsubscribe.py` (all three old paths checked); 5 imports rewritten; baseline line names `optout.service`.
- **Gates:**
  - ruff check: All checks passed!
  - ruff format: 451 files already formatted
  - mypy: Success: no issues found in 289 source files
  - lint-imports: Contracts: 10 kept, 0 broken.
  - kx map json: kx map: identical after renames (path-only diff)
  - kx map --check: docs/FEATURE_MAP.md is current
  - OpenAPI diff: empty
  - unit: ============================ 131 passed in 18.07s =============================
  - full suite: run on this commit in the test worktree; result logged in the next entry
- **Previous slice's full suite:** `inbound` @ `7c622da`: tests=992, failures=0, errors=0, skipped=4, exit=0 → tagged `arch-slice/automation-inbound-2b` (the first-round tag `arch-slice/automation-inbound` stays), COMPLETED

## 2b.12 slice `outbound` — TESTING (COMPLETED when tagged `arch-slice/automation-outbound`)

- **Moved:** the code of `outbound/__init__.py` (OutboundService) → `outbound/service.py` (byte-identical); `outbound/__init__.py` re-exports it (78 of 78 names); 4 imports rewritten; `tests/unit/test_featuremap.py` asserts the new implementation path of MessageSender (kx map names the real module).
- **Gates:**
  - ruff check: All checks passed!
  - ruff format: 452 files already formatted
  - mypy: Success: no issues found in 290 source files
  - lint-imports: Contracts: 10 kept, 0 broken.
  - kx map json: kx map: identical after renames (path-only diff)
  - kx map --check: docs/FEATURE_MAP.md is current
  - OpenAPI diff: empty
  - unit: ============================ 131 passed (after the path update of tests/unit/test_featuremap.py) =============================
  - full suite: run on this commit in the test worktree; result logged in the next entry
- **Previous slice's full suite:** `optout` @ `8cad49c`: tests=992, failures=0, errors=0, skipped=4, exit=0 → tagged `arch-slice/automation-optout-2b`, COMPLETED

## 2b.13 slice `_shared` — TESTING (COMPLETED when tagged `arch-slice/automation-_shared`)

- **Moved:** `outbox.py`, `ports.py`, `settings.py`, `media.py` → `_shared/`; the helpers in `channels/__init__.py` (ChannelError, the email transport Protocol, HTTP retry) → `_shared/channels.py` (`channels/__init__.py` re-exports them; its adapters' old paths stay alias shims); `_shared/sending.py` came with `followups`. All five old paths checked (every name reachable); 35 imports rewritten. Docstrings of `channels/__init__.py` and `_shared/channels.py` say where things are now.
- **Gates:**
  - ruff check: All checks passed!
  - ruff format: 457 files already formatted
  - mypy: Success: no issues found in 295 source files
  - lint-imports: Contracts: 10 kept, 0 broken.
  - kx map json: kx map: identical after renames (path-only diff)
  - kx map --check: docs/FEATURE_MAP.md is current
  - OpenAPI diff: empty
  - unit: ============================ 131 passed in 19.80s =============================
  - full suite: run on this commit in the test worktree; result logged in the next entry
- **Previous slice's full suite:** `outbound` @ `b6a0d44`: errors=0, failures=0, skipped=4, tests=992, exit=0 → tagged `arch-slice/automation-outbound-2b`, COMPLETED

## 2b.14 automation public surface `kx/automation/__init__.py` — TESTING

- **Previous slice's full suite:** `_shared` @ `855014e`: errors=0, failures=0, skipped=4, tests=992, exit=0 → tagged `arch-slice/automation-_shared`, COMPLETED.
- **Done:** `kx/automation/__init__.py` names the 33 things the app uses (explicit `__all__`, lazy PEP 562
  resolution, `TYPE_CHECKING` imports for mypy): the wiring (`jobs`, `retention`, `AutomationRuntime`,
  `JobScheduler`, `EventHandlers`, settings, `S3MediaStore`), sending (`OutboundService`, `KillSwitch`, the
  rate limiter, the WhatsApp / email / Telnyx clients), `ApprovalLadder` / `CallBridge` / `PauseAlerts` /
  `VoiceAlerts`, `FollowUps`, `PriceScraper`, and the four routers under distinct names (`webhook_router`,
  `unsubscribe_router`, `cms_router`, `admin_router`). The app (`adapters.py`, `api.py`, `container.py`)
  now imports automation only through it (22 imports pointed at it; nothing left unexported).
- **Gates:**
  - ruff check: All checks passed!
  - ruff format: 457 files already formatted
  - mypy: Success: no issues found in 295 source files
  - lint-imports: Contracts: 10 kept, 0 broken.
  - kx map json: kx map: identical after renames (path-only diff)
  - kx map --check: docs/FEATURE_MAP.md is current
  - OpenAPI diff: empty
  - unit: ============================ 131 passed in 17.02s =============================
  - full suite: run on this commit; result logged in the next entry

## 2b.15 slice `tracing` — TESTING (COMPLETED when tagged `arch-slice/agents-tracing`)

- **Moved:** `tracing.py` → `tracing/service.py`; `tracing/__init__.py` re-exports it (17 of 17 names); imports rewritten in `service.py`, `arifaa_pack/container.py`.
- **Gates:**
  - ruff check: All checks passed!
  - ruff format: 458 files already formatted
  - mypy: Success: no issues found in 296 source files
  - lint-imports: Contracts: 10 kept, 0 broken.
  - kx map json: kx map: identical after renames (path-only diff)
  - kx map --check: docs/FEATURE_MAP.md is current
  - OpenAPI diff: empty
  - unit: ============================ 131 passed in 26.44s =============================
  - full suite: run on this commit in the test worktree; result logged in the next entry
- **Previous slice's full suite:** `auto-surface` @ `d5a5e1e`: errors=0, failures=0, skipped=4, tests=992, exit=0 → tagged `arch-slice/agents-auto-surface`, COMPLETED
