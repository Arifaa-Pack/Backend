# Architecture decision records

One file per decision that changes how the code is organised or how packages talk to each other. Copy
[`0000-template.md`](0000-template.md), take the next number, and keep the record short. A decision is changed by
a new ADR that supersedes the old one, never by editing an accepted record (fixing typos and links is fine).

| ADR | Title | Status |
| --- | --- | --- |
| [0001](0001-modular-monolith-with-feature-slices.md) | Package-based modular monolith with feature slices | accepted |
| [0002](0002-split-contracts-and-publish-packages.md) | Split the contracts by conversation and publish the packages | proposed (future) |

Business rules are not ADRs: they live in the SRS and in `docs/V1_CHANGE_PLAN.md` (the D-numbered decisions).
