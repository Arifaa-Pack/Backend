# Architecture restructuring (ADR-0001): final report

Phases 0–2, 2b and 2c of the restructuring brief, 19 Nov 2026. The details of every step, with the real gate output, are in
`docs/arch/PROGRESS.md`. **No behaviour change:** moves, import-path renames and new tooling only.

## Done

**Branches.** `arch/phase0-tooling` (Phase 0, no file moves) and `arch/slices` (branched from it; Phases 1–2c,
one commit per slice; Phase 2c is three commits and no tag, as it moves no slice). 49 tags, one per completed slice: `arch-slice/crm-<slice>` (22),
`arch-slice/automation-<slice>` (16: 3 from Phase 2 and 13 from Phase 2b; the three features moved again carry a
`-2b` suffix) and `arch-slice/agents-<slice>` (11).

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
- Every old path stayed importable through an **alias shim** (the old module name was the same module object as
  the new one) until Phase 2c removed them.
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

**Phase 2b: every automation and agents feature in its own folder** (on request, so each feature can be reviewed
on its own; map: `docs/arch/AUTOMATION_AGENTS_SLICES.md`).
- `kx.automation`: 13 slice moves: `scraping/`, `cms/`, `admin/`, `calls/`, `whatsapp/`, `email/`, `alerts/`,
  `followups/`, `webhooks/`, `inbound/`, `optout/`, `outbound/` (their code now in `service.py` / `router.py` /
  `channel.py`), and `_shared/`. Public surface `kx/automation/__init__.py`: 33 names.
- `kx.agents`: 11 slice moves: `tracing/`, `memory/`, `knowledge/`, `guard/`, `tools/`, `voice/`, `website/`, `llm/`,
  `router/`, `roles/`, `runtime/` (`graphs/` and `evals/` were already folders). Public surface
  `kx/agents/__init__.py`: 25 names.
- The app imports both packages only through those surfaces.
- Contracts 10 → 12: both packages are layered (the automation one replaces the Phase 2 independence contract), and
  the app may not import a slice module. Five planted imports were caught.
- 6 more shims had no importer and were removed; 52 alias shims remained (33 CRM, 12 automation, 7 agents), all
  still imported by tests.

**Phase 2c: the old paths are gone** (19 Nov, on request: "move the tests to the new paths and remove the old shims").
- Tests, the frontend-doc generator and the bench scripts import the new modules: 69 files, import lines only. A
  module import keeps its local name (`from kx.crm.quotes import service as quotes`), so no test body changed.
- Removed: the 52 alias modules, with the then-empty `kx/crm/services/` and `kx/automation/channels/` packages; the
  re-exports in 14 feature `__init__.py` files (they now only describe the feature); `FollowUps` on
  `kx.automation.handlers`. Docstrings and comments that named an old path name the new one.
- Checked: each of the 69 old paths fails to import; the public surfaces (33 and 25 names) all resolve.
- A shim's removal is the one place a reader could see a difference: code outside this repository that still
  imports an old path now fails at import. Arifaa Auto is parked; its table is `docs/ARIFAA_AUTO_NOTES.md` §3.

## Gates

Each gate was run on every commit. Shown here: the final head (Phase 2c, `kx-shims` commits on `arch/slices`).

| Gate | Result |
| --- | --- |
| `ruff check .` | PASS: All checks passed! |
| `ruff format --check .` | PASS: 415 files already formatted (54 shim files fewer than Phase 2b) |
| `mypy` (strict, 5 source trees) | PASS: no issues found in 254 source files |
| `lint-imports` | PASS: Contracts: 12 kept, 0 broken |
| `pytest tests/unit` | PASS: 131 passed |
| Full suite (Linux app image, own PostgreSQL) | PASS: tests=992, failures=0, errors=0, skipped=4 (they need `redis-server`), on the Phase 2c test move and again on the shim removal. Every slice commit before it passed its own full suite before the next one was committed: 982 tests in Phases 1–2, 992 in Phase 2b (round-2 tests added in between); each result is in `PROGRESS.md` |
| OpenAPI (`scripts/gen_frontend_docs.py` + `git diff docs/frontend`) | PASS: `openapi-crm.json` and `openapi-website.json` byte-identical on every commit (see Deviations for how) |
| `kx map --check docs/FEATURE_MAP.md` | PASS |
| `kx map --format json`, compared after applying the renames | PASS: path-only on every commit, with 1 explained exception (below); identical before and after Phase 2c |
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
   classes set `__module__` back to their old path. The pin only names the component: the old module itself was
   removed in Phase 2c, and the OpenAPI output stayed byte-identical.
2. **Shims were aliases, not star re-exports.** A star re-export would have broken private names and
   monkeypatching on the old path. The tests stayed on the old paths through Phase 2b, so they exercised the
   shims; Phase 2c moved them and removed every shim.
3. **The ORM stays one shared layer** (`kx/crm/models/`), not split per slice. Splitting it would turn about 200
   imported names into rule-5 violations and make nothing easier to read. There are no `repository.py` modules yet, so
   the per-slice `layers` contract has two layers: routers above the service-side code.
4. **Automation and agents folders differ from the brief's list in names, not in intent.** Phase 2 first kept
   one-file features as files; Phase 2b reversed that on request, so every feature now has a folder, including
   `followups/` (the `FollowUps` class was cut out of `handlers.py` unchanged; the handler table stays there).
   Differences from the brief: the LLM model factory is `llm/`, not `models/` (in the other packages `models` is
   the ORM). There are also folders the brief does not list: `inbound/`, `scraping/`, `cms/`, `_shared/`,
   `runtime/` (the turn engine), `roles/` and `tracing/`.
5. **Three public names in `kx.crm` are renamed aliases:** `company_letterhead`, `retention_rules` and
   `load_text_edits` / `save_text_translation`. The plain module names collide with the slice folders.
6. **History is kept through copy detection.** Each old path still existed as a shim when its code moved, so git
   records the new file as added. `git log --follow` on a new file still reaches its history.

## Found, not fixed

- `apps/arifaa-pack/src/arifaa_pack/featuremap.py:234`: `calls()` can list a function that is never called, when
  another module in scope has an attribute of the same name (the `run_retention` case above).
- `packages/kx-core-crm/src/kx/crm/_shared/schemas.py` imports `kx.crm.quotes.service`, and `_shared/settings.py`
  imports `kx.crm.company.service`. Shared code depends on two slices; this is the `_shared` baseline.
- `kx/crm/handover/service.py` imports `kx.crm.gateway`: a slice depending on the package's outward-facing gateway.
- `kx/automation/_shared/sending.py` imports `TRANSIENT_REASONS` from `outbound.service`: shared code depending on a
  slice. Moving the constant into `_shared/` removes the baseline line (a code change, so not done here).
- The `runtime/` slice of `kx.agents` spans layers: `runtime.service` (the turn engine) is near the top, while
  `runtime.context` sits under the graphs, tools and guard that read it. The contract names the modules, not the
  folder.
- The rule-5 baseline has 74 lines: 68 direct service-level imports between CRM slices, and 6 edges through
  `deps`, `_shared` and `gateway`. The busiest targets are `quotes` (18), `deals` (16) and `pricing` (13), so a
  narrow read API in those slices would remove most of the baseline.
- From the recon, still open:
  - a type-only import cycle between `kx.automation._shared.ports` and `alerts.service` (a line of the automation
    baseline);
  - six events have no subscriber;
  - `docs/REUSE_CATALOGUE.md` gave a stale payload count (now corrected to 33).

## Questions for the lead

1. **Owners:** done (19 Nov): `@muhammadhamthan` owns the backend in `.github/CODEOWNERS`.
2. **Removed shims:** done (19 Nov, Phase 2c): the tests moved to the new paths and every shim was removed. What
   Arifaa Auto will need is in `docs/ARIFAA_AUTO_NOTES.md` §3.
3. **Model names:** do you want the two `ChangeOut` / `CompanyIn` pairs renamed to unique names? That changes four
   OpenAPI component names (a frontend-visible change), but it removes the `__module__` pins.
