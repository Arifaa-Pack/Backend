# `kx.automation` and `kx.agents` feature slices: the map (ADR-0001 Phases 2 and 2b)

Same method as `CRM_SLICES.md`. Each feature was moved with `git mv`, and an old import path that stopped existing
got an alias shim. `src/` imports were rewritten, and tests stayed on the old paths until Phase 2c (19 Nov) moved
them and removed every shim. Every gate ran after every
move, with one commit and one tag per slice (`arch-slice/automation-*`, `arch-slice/agents-*`). Log:
`docs/arch/PROGRESS.md` §2b.

## The rule used here

**Every feature has its own folder, even a feature that is one file today.** The goal is that a reviewer can
open one folder and read one feature end to end. Phase 2 first moved only the features spread over several
modules (`outbound/`, `inbound/`, `optout/`). Phase 2b (19 Nov, on request) gave every feature a folder:

- a slice's main code is `service.py`. A slice that is mainly HTTP routes has `router.py` instead (`admin/`,
  `webhooks/`, `website/`). A channel adapter is `channel.py` (`whatsapp/`, `email/`, `calls/`);
- when the folder took the name of the old module (`alerts.py` → `alerts/`), the folder's `__init__.py` only
  describes the feature (its re-exports were removed in Phase 2c): import from `service.py` / `router.py`;
- code that several slices use lives in `_shared/`. The ORM (`models.py`, `orm.py`) and the query module
  (`repo.py`) are the data layer under every slice;
- the app imports each package only through its public surface (`kx/automation/__init__.py`, 33 names;
  `kx/agents/__init__.py`, 25 names), resolved lazily (PEP 562).

The code was moved, not edited: no behaviour change. In a moved file only import lines changed
(`OutboundService`'s file is byte-identical), and `FollowUps` was cut out of `handlers.py` unchanged (checked by
diff).

## `kx.automation`: where each feature lives

| Slice | What it is (SRS) | Files | Was |
| --- | --- | --- | --- |
| `outbound/` | The single way out: grant, kill switch, suppression, rate limit, 2/week cap, buyer hours (§8.1, §11, D35) | `service.py` (OutboundService), `contact_hours.py`, `ratelimit.py`, `killswitch.py` | `outbound.py`, `contact_hours.py`, `ratelimit.py`, `killswitch.py` |
| `inbound/` | Recording what providers tell us: messages, statuses, the quick-message merge | `service.py` | `inbound.py` |
| `webhooks/` | Provider webhook routes (WhatsApp / Meta, Telnyx; email is polled from Stalwart, not pushed) | `router.py` | `webhooks.py` → `inbound/webhooks.py` |
| `optout/` | Applying a stop, and the one-click unsubscribe page (§8.1) | `service.py`, `router.py` | `optout.py`, `unsubscribe.py` → `optout/unsubscribe.py` |
| `whatsapp/` | WhatsApp Cloud API client: send, signature check, webhook parsing, media | `channel.py` | `channels/whatsapp.py` |
| `email/` | Email (D44): SES sender, Stalwart JMAP send and mailbox poll, the Stalwart → SES chain, bounce / complaint reports | `channel.py`, `stalwart.py`, `chain.py`, `feedback.py` | `channels/email.py`, `channels/stalwart.py`, `channels/email_chain.py`, `channels/feedback.py` |
| `calls/` | Telnyx TeXML staff alert calls (D43) | `channel.py` | `channels/calls.py` |
| `alerts/` | Staff alerts: approval ladder, click-to-call bridge, pause and voice alerts, `notify_staff` (§14, §16, §17) | `service.py` | `alerts.py` |
| `followups/` | §16 follow-ups: the day 2 / 5 / 10 (or re-order) reminder | `service.py` (FollowUps) | the `FollowUps` class in `handlers.py` |
| `scraping/` | Competitor price scraping → price proposals (D14) | `service.py` | `scraping.py` |
| `cms/` | CMS publish webhook (§7) | `service.py` | `cms.py` |
| `admin/` | Admin routes: regional numbers, templates | `router.py` | `admin_api.py` |
| `_shared/` | Outbox writer, ports (Protocols the app wires), deploy settings, object store, channel errors and HTTP retry, the send helper | `outbox.py`, `ports.py`, `settings.py`, `media.py`, `channels.py`, `sending.py` | `outbox.py`, `ports.py`, `settings.py`, `media.py`, `channels/__init__.py`, part of `handlers.py` |

**Package level:** `handlers.py` (the event reactions; it wires the slices), `jobs.py` (job bodies and the
schedule table), `models.py` (ORM), `repo.py` (queries), `retention.py`, `__init__.py` (public surface).

## `kx.agents`: where each feature lives

| Slice | What it is (SRS) | Files | Was |
| --- | --- | --- | --- |
| `runtime/` | The turn engine: one inbound buyer turn → route → one idempotent run → guarded reply; run bookkeeping, per-run context, the role middleware | `service.py` (AgentService, GraphRunner), `runs.py`, `context.py`, `middleware.py` | `service.py`, `runs.py`, `context.py`, `middleware.py` |
| `router/` | The deterministic router (the Orchestrator role): which graph answers; no LLM decides | `service.py` | `orchestrator.py` |
| `graphs/` | LangGraph graphs: conversation, quotation (interrupt / resume), deal | `conversation.py`, `quotation.py`, `deal.py`, `compose.py` | (unchanged) |
| `tools/` | Agent tools over the CrmGateway and the knowledge base | `service.py` | `tools.py` |
| `guard/` | The reply guard: prices, cost wording, injection, length (§11.1; fails closed) | `service.py` | `guard.py` |
| `llm/` | Model factory: tier → chat model, the provider fallback (D27, D51) | `service.py` | `models.py` (the LLM router, not the ORM) |
| `roles/` | The 12 sales roles as data, and the buyer languages (T17) | `registry.py`, `language.py` | `registry.py`, `language.py` |
| `knowledge/` | Hybrid knowledge search over approved CMS content, and the indexer | `service.py` | `knowledge.py` |
| `memory/` | Conversation memory: recent turns, rolling summary, the LangGraph checkpointer | `service.py` | `memory.py` |
| `voice/` | Speech vendor routing by language, the LiveKit worker entrypoint (D52) | `service.py` | `voice.py` |
| `website/` | §6 website conversation API (D39 public-endpoint protection) | `router.py` | `website.py` |
| `tracing/` | AI audit trail over OpenTelemetry (D46) | `service.py` | `tracing.py` |
| `evals/` | §23 eval scenarios and trick prompts | `harness.py`, `scenarios/` | (unchanged) |

**Package level:** `handlers.py` (event reactions), `orm.py` (ORM), `retention.py`, `testing.py` (test doubles
shipped with the package), `__init__.py` (public surface).

## The import rules (import-linter, `pyproject.toml`)

A slice imports only slices below it. Slices on the same line never import each other.

```
kx.automation                                  kx.agents
  jobs                                           handlers | evals
  handlers                                       website
  webhooks | admin | cms | scraping              voice
  inbound | outbound | alerts | followups        runtime.service
  optout                                         graphs
  whatsapp | email | calls                       tools | guard | router
  _shared                                        runtime.runs | runtime.middleware | memory
  repo                                           runtime.context
  models                                         knowledge | llm | tracing
                                                 roles
```

- Automation baseline (may only shrink): `_shared.sending → outbound.service`, needed for `TRANSIENT_REASONS`, and
  two `TYPE_CHECKING`-only imports (`_shared.channels → email.channel`, `_shared.ports → alerts.service`).
- Agents: no baseline; today's code already follows the layers. `runtime/` is split by module because the turn
  engine (`service.py`) is at the top, while the per-run `context.py` is near the bottom (graphs, tools and the
  guard read it).
- `arifaa_pack` imports no automation or agents slice module, only `kx.automation`, `kx.agents` and the `jobs` /
  `retention` modules they export.

Each rule was proved to fail on a planted import (PROGRESS §2b.28).

## Old import paths

None of them works any more. The table of every old path and its new path is in `docs/ARIFAA_AUTO_NOTES.md` §3.

- **Phase 2b removed** the 6 that nothing imported: `kx.agents.middleware`, `kx.automation.channels.email_chain`,
  `kx.automation.inbound.webhooks`, `kx.automation.optout.unsubscribe`, `kx.automation.outbox`,
  `kx.automation.ports`.
- **Phase 2c (19 Nov) removed the rest**, after the tests, the frontend-doc generator and the bench scripts moved
  to the new paths: the alias modules `kx.agents.context`, `.language`, `.models`, `.orchestrator`, `.registry`,
  `.runs`, `.service`; `kx.automation.admin_api`, `.media`, `.settings`, `.contact_hours`, `.killswitch`,
  `.ratelimit`, `.unsubscribe`, the whole `kx.automation.channels` package; the re-exports in the 14 folder
  `__init__.py` files; and `FollowUps` on `kx.automation.handlers`.

## Reviewing one feature

1. Open the slice folder. Read `service.py` (or `router.py` / `channel.py`) top to bottom.
2. `uv run kx map --feature <slice>` lists its routes, the events it handles and its jobs.
3. Its tests: search the package's `tests/` folder (and `tests/` at the root) for the slice's import path,
   e.g. `grep -rln "kx.automation.alerts" packages/kx-core-automation/tests tests`. Where it is wired:
   `apps/arifaa-pack/src/arifaa_pack/container.py`.
