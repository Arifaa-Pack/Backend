# `kx.automation` and `kx.agents` feature slices: the mapping (ADR-0001 Phase 2)

Same method as `CRM_SLICES.md`: `git mv`, an alias shim at every old module path that stops existing, `src/`
imports rewritten, tests left on the old paths, every gate after every move, one commit and one tag per slice.

## The rule used here

Both packages are already flat and **feature-shaped**: almost every module is one feature (`alerts.py`,
`scraping.py`, `knowledge.py`, `memory.py`, `voice.py` ...), and the multi-module features already have a folder
(`kx/automation/channels/`, `kx/agents/graphs/`, `kx/agents/evals/`). Turning a one-file feature into a folder
with one `service.py` adds a level and a shim and makes nothing easier to find. So a slice folder is created only
where **one feature is spread over several top-level modules**.

When the folder takes the name of the feature's main module (`outbound.py` → `outbound/`), that module becomes the
package's `__init__.py`: a folder and a module cannot share a name, and this keeps `kx.automation.outbound` the
same module object (every import, private name and monkeypatch unchanged, no shim needed). Splitting it into
`outbound/service.py` is follow-up work once nothing imports the old helpers' paths.

## `kx.automation`

| # | Slice | Current module | Target |
| --- | --- | --- | --- |
| 1 | `outbound` | `outbound.py` (OutboundService, the single way out) | `outbound/__init__.py` |
| | | `contact_hours.py` (pure: when proactive messages may go) | `outbound/contact_hours.py` |
| | | `ratelimit.py` (per-recipient / per-channel token bucket) | `outbound/ratelimit.py` |
| | | `killswitch.py` (the D35 ladder seen from automation) | `outbound/killswitch.py` |
| 2 | `inbound` | `inbound.py` (recording what providers tell us) | `inbound/__init__.py` |
| | | `webhooks.py` (provider webhook routes) | `inbound/webhooks.py` |
| 3 | `optout` | `optout.py` (applying a stop, §8.1) | `optout/__init__.py` |
| | | `unsubscribe.py` (one-click unsubscribe page, §8.1 step 10) | `optout/unsubscribe.py` |

`OutboundService`'s code is moved, not edited (brief §3: its logic is contest-verified).

**Stay as they are:** `alerts.py`, `admin_api.py`, `cms.py`, `scraping.py`, `media.py`, `retention.py`
(one feature, one file); `channels/` (already the adapters folder: WhatsApp, email, Stalwart, calls, feedback);
the data layer `models.py` (ORM) and `repo.py` (queries); the package plumbing `ports.py`, `outbox.py`,
`settings.py`, `handlers.py` (event reactions, including the follow-ups the brief lists as `followups/`: they are
one table of handlers, splitting it is a logic-level change), `jobs.py`.

## `kx.agents`

No module moves. The brief's suggested slices already exist one-to-one:

| Brief's slice | Where it is |
| --- | --- |
| `router/` | `orchestrator.py` (deterministic router) |
| `graphs/` | `graphs/` (conversation, quotation, deal, compose) |
| `tools/` | `tools.py` |
| `guard/` | `guard.py` |
| `knowledge/` | `knowledge.py` |
| `voice/` | `voice.py` |
| `website/` | `website.py` |
| `models/` | `models.py` (the LLM model router, not ORM) |
| `memory/` | `memory.py` |
| `evals/` | `evals/` |

The data layer is `orm.py`; the runtime core is `service.py` + `runs.py` + `context.py` + `middleware.py` +
`registry.py` + `language.py`; `handlers.py`, `retention.py`, `tracing.py`, `testing.py` are package plumbing.

## Contracts added after the moves

- `layers` (CRM): in every slice with both, `router` sits above `service` (a service never imports a router).
- `layers` (package): `kx.crm` slices and `api` → `_shared` → `models`; the ORM and `_shared` never import a slice.
- `independence` between the CRM slices, with today's service-level calls listed in `ignore_imports`
  (`# baseline 2026-11-19: may only shrink`).
- `forbidden`: the app may not import ORM / query modules (`kx.crm.models`, `kx.automation.models`,
  `kx.automation.repo`, `kx.agents.orm`); the two existing imports are the baseline.
- `forbidden`: the pure pricing maths (`kx.crm.pricing.engine`, `.price_list`, `.types`) imports no database code.
