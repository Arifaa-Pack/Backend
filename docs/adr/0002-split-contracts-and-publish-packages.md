# ADR-0002: Split the contracts by conversation and publish the packages (future, Phase 3)

- **Status:** proposed (not started; out of scope of the ADR-0001 restructuring)
- **Date:** 2026-11-19
- **Deciders:** Kairox engineering lead, when project 2 is formally split
- **References:** ADR-0001, `docs/MULTI_PROJECT.md`, `CONTRACTS.md`

## Context

Today one repository holds the shared packages and the first client app (`apps/arifaa-pack`). All Protocols live
in one module, `kx.core.contracts`, and all event payloads in `kx.core.event_payloads`. That is the right size
for one client. A second project (Arifaa Auto) is being built on the same packages; once it is a separate
delivery with its own release dates, a single repository and a single contracts module couple the two projects'
releases: any change to a Protocol one project uses forces the other to re-test and redeploy.

## Decision (to take when project 2 is formally split, not before)

1. **Split `kx.core.contracts` by conversation**: one module per pair of packages that talk (for example
   `contracts/crm_agents.py`, `contracts/crm_automation.py`, `contracts/agents_automation.py`), with the current
   module re-exporting all of them for one release.
2. **Publish the `kx-*` packages as versioned wheels** to a private index, with semantic versions: a Protocol or
   payload change that breaks a consumer is a major version; payloads keep the "add a new version, never change a
   field's meaning" rule.
3. **Move `apps/arifaa-pack` to its own repository**, pinned to released package versions; the platform
   repository keeps the packages, their tests and a reference app for the integration tests.

## Consequences

- Each client app upgrades the platform on its own schedule; a contract change shows up as a version bump.
- The platform needs release engineering: changelogs, a private package index, compatibility tests per contract.
- Cross-repository changes (a new Protocol and its first use) take two pull requests and a release in between.

## Alternatives rejected

- **Keep one repository for every client.** Simple while there is one client; with two it couples their release
  cycles and lets one client's change break the other unnoticed.
- **Git submodules or path dependencies across repositories.** No version boundary; the coupling stays, only
  hidden.
- **Do it now.** There is no second, separately released consumer yet; the cost (release engineering, two-PR
  changes) would buy nothing today. ADR-0001 keeps the code ready for it.
