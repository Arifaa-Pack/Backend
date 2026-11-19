# Architecture restructuring (ADR-0001): final report

Phases 0–2 of the restructuring brief, 19 Nov 2026. The details of every step, with the real gate output, are in
`docs/arch/PROGRESS.md`. **No behaviour change:** moves, import-path renames and new tooling only.

## Done

**Branches.** `arch/phase0-tooling` (Phase 0, no file moves) and `arch/slices` (branched from it; Phases 1–2,
one commit per slice). 25 tags, one per completed slice: `arch-slice/crm-<slice>` (22) and
`arch-slice/automation-<slice>` (3).

**Phase 0: make the wiring visible.**
- `docs/arch/CURRENT_STATE.md`: the recon (inventory, Protocol → implementation, event → handlers, route →
  service, the intra-package import tables).
- `kx map` (`apps/arifaa-pack/src/arifaa_pack/featuremap.py`, wired in `cli.py`). It builds the map from the real
  container, in Markdown or JSON, with `--feature`, `--write` and `--check`. 15 unit tests.
- `docs/FEATURE_MAP.md`: the SRS-section table plus the generated map. The CI job `.github/workflows/feature-map.yml`
  fails when the file is stale.
- Review routing:
  - `.github/CODEOWNERS`, with placeholder owners `@OWNER_*`;
  - `.github/pull_request_template.md`;
  - `.github/workflows/pr-labels.yml`, which adds the label `cross-package`.
- Decision records: `docs/adr/` (template, ADR-0001, ADR-0002), linked from `docs/ARCHITECTURE.md`.

**Phase 1: CRM feature slices.**
- `docs/arch/CRM_SLICES.md` maps every module to its slice.
- 22 slice moves. 21 feature folders:
  - `texts`, `company`, `operations`, `retention`, `reports`, `catalogue`, `pricing`;
  - `contacts`, `records`, `auth`, `handover`, `assignment`, `change_requests`, `orders`;
  - `approvals`, `negotiation`, `inbox`, `screens`, `admin`, `deals`, `quotes`.

  Plus the 22nd move into `_shared/`, the code several slices use.
- Every old path stays importable through an **alias shim**: the old module name is the same module object as
  the new one.
- Public surface: `kx/crm/__init__.py`, an explicit `__all__` of 21 names, resolved lazily. The app imports only
  `kx.crm`, except two ORM imports that are kept as the recorded baseline.

**Phase 2: automation, agents, import contracts.**
- `docs/arch/AUTOMATION_AGENTS_SLICES.md` maps the two packages.
- Three automation slices: `outbound/`, `inbound/` and `optout/`. Each feature module became its package's
  `__init__.py`, so it is the same module.
- `kx.agents` needed no moves: every slice the brief suggests already exists, as a module or a folder.
- Seven new import-linter contracts, so there are now 10. Their baselines were generated from the import graph.
  Three deliberate violations were checked to break them.
- 19 shims had no importer left in the repository and were removed. 38 are kept.

## Gates

Each gate was run on every commit. Shown here: the final head (`f89f9cb`), plus the documentation commit after it.

| Gate | Result |
| --- | --- |
| `ruff check .` | PASS: All checks passed! |
| `ruff format --check .` | PASS: 423 files already formatted |
| `mypy` (strict, 5 source trees) | PASS: no issues found in 266 source files |
| `lint-imports` | PASS: Contracts: 10 kept, 0 broken |
| `pytest tests/unit` | PASS: 124 passed |
| Full suite (Linux app image, own PostgreSQL) | PASS: tests=982, failures=0, errors=0, skipped=4 (they need `redis-server`). The same result on every one of the 25 slice commits and on 1.23, 2.4 and 2.5 |
| OpenAPI (`scripts/gen_frontend_docs.py` + `git diff docs/frontend`) | PASS: `openapi-crm.json` and `openapi-website.json` byte-identical on every commit (see Deviations for how) |
| `kx map --check docs/FEATURE_MAP.md` | PASS |
| `kx map --format json`, compared after applying the renames | PASS: path-only on every commit, with 1 explained exception (below) |
| actionlint on the new workflows | NOT RUN locally: not installed. `ci.yml` runs it on every workflow |

## Diffs

- **OpenAPI:** empty on every commit.
- **`kx map`:** only paths changed, except in one place. The `app.retention` job no longer lists
  `kx.crm.retention.service:run_retention`. That entry was a false positive:
  - `kx map` matches every name in a function against every module in scope;
  - the job calls `agents_retention.run_retention`;
  - while the CRM module was in the container's namespace, that name matched the CRM's `run_retention` too.

## Deviations

1. **OpenAPI component names.** Two pairs of models share a class name: `ChangeOut` and `CompanyIn`. FastAPI
   therefore names their components after the module path. To keep the published schema byte-identical, those four
   classes set `__module__` back to their old path, which is an alias of the same module.
2. **Shims are aliases, not star re-exports.** A star re-export would break private names and monkeypatching on
   the old path. The tests deliberately stay on the old paths, so they exercise the shims.
3. **The ORM stays one shared layer** (`kx/crm/models/`), not split per slice. Splitting it would turn about 200
   imported names into rule-5 violations and make nothing easier to read. There are no `repository.py` modules yet, so
   the per-slice `layers` contract has two layers: routers above the service-side code.
4. **Automation and agents get fewer folders** than the brief lists. A feature that is one file stays one file.
   `followups/` is not created: the follow-ups are part of the one handler table in `kx/automation/handlers.py`.
5. **Three public names in `kx.crm` are renamed aliases:** `company_letterhead`, `retention_rules` and
   `load_text_edits` / `save_text_translation`. The plain module names collide with the slice folders.
6. **History is kept through copy detection.** Each old path still exists as a shim, so git records the new file
   as added. `git log --follow` on a new file still reaches its history.

## Found, not fixed

- `apps/arifaa-pack/src/arifaa_pack/featuremap.py:234`: `calls()` can list a function that is never called, when
  another module in scope has an attribute of the same name (the `run_retention` case above).
- `packages/kx-core-crm/src/kx/crm/_shared/schemas.py` imports `kx.crm.quotes.service`, and `_shared/settings.py`
  imports `kx.crm.company.service`. Shared code depends on two slices; this is the `_shared` baseline.
- `kx/crm/handover/service.py` imports `kx.crm.gateway`: a slice depending on the package's outward-facing gateway.
- The rule-5 baseline has 74 lines: 68 direct service-level imports between CRM slices, and 6 edges through
  `deps`, `_shared` and `gateway`. The busiest targets are `quotes` (18), `deals` (16) and `pricing` (13), so a
  narrow read API in those slices would remove most of the baseline.
- From the recon, still open:
  - a type-only import cycle between `kx.automation.ports` and `alerts`;
  - six events have no subscriber;
  - `docs/REUSE_CATALOGUE.md` gave a stale payload count (now corrected to 33).

## Questions for the lead

1. **Owners:** done (19 Nov): `@muhammadhamthan` owns the backend in `.github/CODEOWNERS`.
2. **Removed shims:** Arifaa Auto is parked (19 Nov); what it will need is in `docs/ARIFAA_AUTO_NOTES.md`. Should the remaining 38 be removed once the tests move to the new paths?
3. **Model names:** do you want the two `ChangeOut` / `CompanyIn` pairs renamed to unique names? That changes four
   OpenAPI component names (a frontend-visible change), but it removes the `__module__` pins.
