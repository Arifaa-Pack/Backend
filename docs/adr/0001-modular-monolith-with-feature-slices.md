# ADR-0001: Package-based modular monolith with feature slices

- **Status:** accepted
- **Date:** 2026-11-19
- **Deciders:** Kairox engineering lead
- **References:** `docs/ARCHITECTURE.md` §2, `CONTRACTS.md`, `docs/arch/CURRENT_STATE.md` (recon),
  `docs/arch/PROGRESS.md` (the log of this change), ADR-0002 (the later split)

## Context

The platform is a uv workspace of five layers: `kx.core`, three domain packages (`kx.crm`, `kx.automation`,
`kx.agents`) and the client app `apps/arifaa-pack`. The package walls are sound and enforced by import-linter:
`kx.core` imports no other `kx.*`, the three packages never import each other, nothing imports the app, only the
app imports Celery. Packages meet only through `kx.core.contracts` Protocols and versioned outbox events.

Inside each package, code is grouped by technical layer (`services/`, `api/`, `models/`, `schemas/`). One
business feature (for example, a quote: price it, approve it, send it, change it) is spread over files in
four folders, and nothing in the code says which handlers react to which event or which class implements which
Protocol; that lives only in `container.py`. A reviewer cannot answer "where is this implemented, and who reacts
to this event?" without reading the wiring by hand. The recon (`docs/arch/CURRENT_STATE.md`) counted the
intra-package imports this causes.

A second team (Arifaa Auto) is building on these packages at the same time, so old import paths must keep
working while the inside is reorganised.

## Decision

1. **Keep the package walls exactly as they are** (the four import rules, one database per package, `*_ref uuid`
   between databases, outbox/inbox events with versioned payloads, the safety backbone: price guard, send grants,
   `OutboundService`, spend caps, kill switch, MFA approvals; Alembic history never rewritten).
2. **Make the indirection visible** (Phase 0): `kx map` prints, from the real container, every Protocol and its
   implementation, every event and its handlers, every route and the functions it calls, every job and Beat
   entry. `docs/FEATURE_MAP.md` is generated from it and checked in CI. CODEOWNERS, a PR template and a
   `cross-package` label route review.
3. **Feature slices inside each package** (Phases 1 and 2): one business feature per folder
   (`kx/crm/quotes/`, `kx/crm/approvals/`, …), each with the files it needs among `router.py`, `service.py`,
   `repository.py`, `models.py`, `schemas.py`; code used by several slices goes to `_shared/`. Every move is a
   `git mv` with a re-export shim at the old path, one slice per commit, with no behaviour change.
4. **New rules, enforced by import-linter once the slices exist:**
   - the app imports only a package's public surface (`kx/<pkg>/__init__.py`), never `*.repository` or ORM
     `*.models`;
   - inside a slice, `router → service → repository → models`;
   - slices do not import another slice's `repository` or `models`; the violations of the day are listed as a
     baseline that may only shrink.
5. **Policy:** something enters `kx.core` only when two packages need it today; client-specific behaviour lives
   in the app or in `crm.settings` rows, never in `if company == …`.

## Consequences

- One folder answers "what does the quote feature do?"; `kx map` answers "who reacts to this?". New engineers
  and reviewers read less to change one feature.
- Every change must keep passing the gates: ruff, `mypy --strict`, import-linter, the full suite on PostgreSQL,
  an unchanged OpenAPI, and a `kx map` output that changes only in file paths.
- The shims are temporary debt: they are removed only where `grep` shows no importer left in this repo, and the
  removals are announced to the Arifaa Auto team.
- The baseline of cross-slice imports is visible in `.importlinter`/`pyproject.toml`; it can only shrink.
- If slices keep needing each other's internals (the baseline grows, or most changes touch three slices), the
  slice cut is wrong and should be revisited with a new ADR.

## Alternatives rejected

- **Back to a single application package.** Loses the package walls that let the CRM, automation and agents be
  reused by the next client (`docs/MULTI_PROJECT.md`) and that the safety rules depend on (the AI package can
  never see cost or the price ladder because it cannot import the CRM).
- **Microservices now.** Adds network hops, distributed transactions and per-service deployment to a
  single-tenant system run by a small team on one dedicated server (server plan v2). The outbox events already
  give the decoupling a split would need; ADR-0002 records when a split becomes worth it.
- **Leave the inside as is and only document it.** Documentation drifts; the generated map and the import rules
  keep the structure true without relying on people remembering.
