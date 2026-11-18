# Maintainability, efficiency and duplication review: Kairox platform

Date: 18 Nov 2026. Branch: `v1-11nov-work-and-architecture-guide` (working tree, uncommitted changes included).
This was a read-only review. No repo file was changed except this report. No servers were started and Docker was not touched.

**Scope:** `packages/kx-core-db/src`, `packages/kx-core-crm/src`, `packages/kx-core-automation/src`, `packages/kx-agent-framework/src`, `apps/arifaa-pack/src`.

| Measure | Value |
|---|---|
| Python files | 196 (163 source files + 33 Alembic migration files) |
| LOC | 35,043 |
| SLOC | 27,203 |
| LLOC | 21,213 |
| Comment ratio, (C + M) / L | 8 % |

**Tools.** All tools ran from a throwaway Python 3.14 venv in the scratchpad, built from the same uv CPython 3.14.10 as `.venv`. Nothing was installed into the project venv.

- radon 6.0.1: `cc`, `mi`, `raw`.
- vulture 2.16: run at confidence 80, and at 60 with the tests counted as callers.
- pylint `duplicate-code`: min 6 lines, imports and docstrings ignored.
- `npx jscpd@4`: run twice, at 6 lines / 60 tokens and at 4 lines / 40 tokens.
- Two small AST and settings scripts of my own:
  - **N+1 scan:** finds DB awaits inside loops.
  - **Env check:** lists every settings field and compares it with `.env.example` and `infra/server/stacks/env/pack.env.example`.

**The package-boundary rule.** CLAUDE.md and CONTRACTS.md §1 say packages never import each other; they cross only through `kx.core.contracts` and events.

- Every merge below stays **inside one package**, or moves code **down into `kx.core`**, which every package may import.
- Duplication **across** `kx.crm`, `kx.automation` and `kx.agents` is marked "keep" when it is a Protocol and its implementation, or per-database ORM mirrors.

---

## 0. Headline numbers

| Metric | Value |
|---|---|
| Average cyclomatic complexity | **A (2.75)** over 1,872 blocks |
| Blocks graded C or worse | **53** (44 C, 4 D, 3 E, 2 F) |
| Maintainability Index (MI), median file | 61.5 (A) |
| MI outliers | `workbook.py` **C (0.89)**, `price_guard.py` **B (12.1)**, `quotes.py` **B (18.5)**; every other file is A |
| Duplication, jscpd 6 lines / 60 tokens | **0.26 %** (9 clones, 91 lines) |
| Duplication, jscpd 4 lines / 40 tokens | **0.97 %** (45 clones, 340 lines) |
| Duplication, pylint (≥ 6 similar lines) | 16 similar-block pairs (7 of them cross-package Protocol mirrors) |
| vulture, confidence ≥ 80 | 6 hits, **all false positives** (parameters required by a framework interface) |
| Verified dead code (vulture 60 + grep over the whole repo, docs and tests) | **9 functions / methods, ≈ 57 lines** |
| Unused ORM mirrors of v2 tables | 3 (kept on purpose) |
| Settings fields never read | **0** |
| `KX_*` keys in `.env.example` that nothing reads | **0**. 9 keys are read outside the settings classes: `os.environ`, Docker health check, CMS container. |
| `type: ignore` | 20 (7 of them `import-untyped`: openpyxl, yaml) |
| Dataclasses without `slots=True` | 5 of 120 |
| N+1 patterns on request paths | 4 real (§5.2) |
| Missing or redundant indexes | 1 high-traffic missing, 1 redundant, 3 lesser (§5.1) |
| Blocking CPU work on the event loop | 3 places (§5.3) |
| **Estimated removable lines** | **≈ 370** (≈ 315 from merges + ≈ 57 dead code), net of the helpers added |

Overall the code is already in good shape:

- Small functions, strict typing, slots dataclasses.
- The shared helpers are used:
  - pagination (`kx/crm/api/pagination.py`);
  - HTTP retry (`kx/automation/channels/__init__.py` `HttpRetry`, built on tenacity);
  - one shared `httpx.AsyncClient`;
  - one aioboto3 session.
- No `time.sleep`, `requests`, sync boto3 or per-request HTTP clients.

The value left is mostly in **5 efficiency fixes** and in **about 15 merges inside one package each**.

---

## 1. Size and complexity

### 1.1 The 15 most complex functions (radon cc; C or worse)

| # | CC | Rank | Function | Lines | Comment |
|---|---|---|---|---|---|
| 1 | 80 | F | `kx/crm/services/workbook.py:276` `plan` | 147 | Pure validator of 3 sheets in one function (`noqa: PLR0915`). Split it (§3.14). |
| 2 | 42 | F | `kx/core/price_guard.py:390` `_probe` | 57 | Contest winner, benchmarked fast path, safety-critical. **Keep.** |
| 3 | 39 | E | `kx/core/price_guard.py:567` `_scan` | 83 | Same: keep. |
| 4 | 37 | E | `kx/agents/evals/harness.py:87` `run_scenario` | 73 | Test kit only; low priority. |
| 5 | 32 | E | `kx/crm/services/quotes.py:796` `revise_quote` | 95 | Can be split into line edits, terms edits and the special-price branch. |
| 6 | 27 | D | `kx/crm/services/price_lists.py:64` `parse_rows` | 35 | Dense validation; acceptable. |
| 7 | 25 | D | `kx/crm/services/workbook.py:572` `apply` | 124 | One apply loop per sheet. Split together with `plan`. |
| 8 | 23 | D | `kx/agents/orchestrator.py:90` `route` | 43 | A table of rules; acceptable. |
| 9 | 21 | D | `kx/core/price_guard.py:351` `_parse` | 33 | Keep. |
| 10 | 19 | C | `kx/crm/jobs.py:258` `on_message_status` | 23 | |
| 11 | 18 | C | `kx/core/price_guard.py:456` `_compound` | 24 | Keep. |
| 12 | 17 | C | `kx/crm/services/workbook.py:240` `_cell` | 29 | |
| 13 | 17 | C | `kx/crm/services/negotiation.py:323` `_evaluate_ladder` | 92 | Shares about 35 lines with `evaluate_counter` (§2, D3). |
| 14 | 16 | C | `kx/crm/services/fx.py:73` `update_rates` | 64 | |
| 15 | 16 | C | `kx/crm/services/deals.py:388` `move_stage` | 71 | |

Next in line, at CC 15: `deals.patch_contact`, `assignment.assign`, `approvals.decide` (118 lines), `engine.evaluate_ladder_counter`, `catalogue_workbook._preview_out`, `OutboundService._transmit` and `middleware.deal_facts`.

`arifaa_pack/container.py:324` `build` has only CC 13, but it is **248 lines** long (`noqa: PLR0915`).

### 1.2 The 10 largest modules

| Lines | Module | MI |
|---|---|---|
| 941 | `kx/crm/services/quotes.py` | B 18.5 |
| 833 | `kx/crm/services/workbook.py` | **C 0.9** |
| 717 | `kx/core/price_guard.py` | B 12.1 |
| 684 | `arifaa_pack/container.py` | A 34.5 |
| 656 | `kx/crm/services/deals.py` | A 24.6 |
| 622 | `kx/automation/handlers.py` | A 28.7 |
| 610 | `kx/automation/outbound.py` | A 20.6 |
| 563 | `kx/crm/services/negotiation.py` | A 30.8 |
| 523 | `kx/crm/services/pricing.py` | A 33.3 |
| 523 | `kx/automation/alerts.py` | A 34.1 |

**Lines per package:**

| Package | Lines |
|---|---|
| crm | 15,877 |
| automation | 5,984 |
| agents | 4,728 |
| core | 4,472 |
| app | 3,982 |

**Maintainability Index outliers:**

- **`workbook.py` (C).** Driven by `plan` and `apply`; fix it with §3.14.
- **`price_guard.py` (B).** Deliberate: a hand-tuned scanner with tables of currency words. Leave it alone. Every change there needs the bench and contest suites.
- **`quotes.py` (B).** The v1 price-list path and the v2 cost path sit side by side. §3.12 removes the shared prelude, and splitting `revise_quote` lifts it to A.

---

## 2. Duplication (jscpd + pylint), with a verdict for each

Line numbers are from the working tree. "Merge" means a shared helper **inside the same package** unless the table says otherwise.

| # | Block A | Block B | Lines | Verdict |
|---|---|---|---|---|
| D1 | `automation/outbound.py:98-112` `EmailTransport` | `automation/channels/email_chain.py:20-34` `EmailVendor` | 15 | **Merge.** The two Protocols are identical. Keep one, `EmailTransport`, in `kx/automation/channels/__init__.py` and import it in both places. Saves 13 lines. |
| D2 | `automation/channels/email.py:99-111` `EmailSender.send` signature + `build_mime(...)` call | `automation/channels/stalwart.py:158-178` | 13 | Keep the signatures: they implement D1's Protocol. Optional: a frozen `MailParts` dataclass passed to `build_mime(**parts)`. Saves about 8 lines. Low value. |
| D3 | `crm/services/negotiation.py:70-86` (rounds query + offers + traded_qty) | `negotiation.py:181-197` | 17 | **Merge** into `_rounds_of(session, deal_id, version_id) -> tuple[list[NegotiationRound], list[Decimal], list[Decimal]]`. |
| D4 | `negotiation.py:254-292` (expiry check, `NegotiationRound` add, stage move, `record`) | `negotiation.py:335-381` | 11+10+7 | **Merge** into `_record_round(...)`. See §3.3. |
| D5 | `crm/services/change_requests.py:152-162` (`ChangeRequested(...)` payload) | `change_requests.py:194-204` | 11 | **Merge** into `_requested_event(deal, cr, assignee, manager, s, window, now)`. |
| D6 | `crm/services/change_requests.py:74-84` `_quote_version` | `crm/services/quotes.py:175-185` `latest_version` | 11 | **Merge.** `_quote_version` becomes `await latest_version(s, id, _ISSUED) or await latest_version(s, id)`. `change_requests` does not import `quotes` today, and `quotes` does not import `change_requests`, so there is no cycle. Saves 11 lines. |
| D7 | `change_requests.py:224-230` / `251-257` / `243-248` / `319-324` (version-belongs-to-deal check) | | 7+6 | **Merge** into `_version_of_deal(session, version_id, deal, *, issued: bool)`. |
| D8 | `crm/api/change_requests.py:71-80` | `crm/api/deals.py:241-250` (`QuoteEditsIn` → `QuoteEdits`) | 10 | **Merge.** Add a `QuoteEditsIn.to_edits(note: str \| None)` method in `api/schemas.py`. |
| D9 | `crm/pdf.py:446-453` `summary_phrase` | `pdf.py:458-465` `summary_caption` | 8 | **Merge.** The caption calls `summary_phrase(doc)`. Saves 7 lines, zero risk. |
| D10 | `crm/jobs.py:325-334` `_latest_deal` | `jobs.py:342-351` (inside `_deal_for_message`) | 10 | **Merge.** End `_deal_for_message` with `return await _latest_deal(session, contact_id)`. Saves 7 lines, zero risk. |
| D11 | `crm/services/assignment.py:290-299` | `:321-330`, `:348-357` (`_announce` + `reroute_open` + `reroute`) | 3×10 | **Merge** into `_announce_and_reroute(...)`. See §3.5. |
| D12 | `crm/services/phone.py:94-102` | `phone.py:123-131` (and 5 more `StaffNotice` emits) | 9 | **Merge** into a small helper. See §3.11. |
| D13 | `crm/services/quotes.py:400-406` / `464-470`, `441-445` / `655-659` | | 7+5 | **Merge** into the version prelude helper (§3.12). |
| D14 | `agents/service.py:278-287` / `325-334` / `232-237` / `357-362` | | 10+6 | **Merge** into `_open_conversation(s, turn, language, deal_ref)` and `_claim(...)`. |
| D15 | `agents/memory.py:110-117` / `159-166` (max `turn_no`), plus the summary read at 132 / 155 | | 8 | **Merge** into `_last_turn_no(s, conv_id)` and `_summary_of(s, conv_id)`. |
| D16 | `automation/handlers.py:186-195` / `212-221` (`OutboundRequest` from a priced payload) | | 10 | **Merge** into `_priced_request(p, kind, requested_by, key)`. The payload types differ (`QuoteApproved` and `QuoteResendRequested`), so the helper takes the shared fields. |
| D17 | `crm/models/plumbing.py:47-69` `OutboxEvent` / `InboxEvent` | `automation/models.py:327-354` (and the agents ORM) | 6 (pylint) | **Cross-package: keep the copies, or move them down.** A declarative mixin in `kx.core.events` (`OutboxColumns`, `InboxColumns`) is allowed because every package imports `kx.core`. Careful: crm uses the Python-side `default=1` and automation uses `server_default`. Pick `server_default` for both; the stored values are the same. Saves about 25 lines. Medium risk (mapper and DDL-check test). |
| D18 | `core/contracts.py:245-263` `CrmGateway` Protocol | `crm/gateway.py:288-320` impl, `agents/testing.py:238-264` fake | 8-13 | **Keep.** Protocol, implementation and test fake are the boundary rule working as intended. |
| D19 | `core/contracts.py:334-342` | `automation/alerts.py:87-96` | 9 | **Keep** (Protocol and implementation). |
| D20 | `automation/jobs.py:329-337` | `automation/ports.py:48-55` | 9 | **Keep** (Protocol `TaskScheduler` and `JobScheduler`). |
| D21 | `automation/outbound.py:86-93` | `automation/channels/whatsapp.py:336-344` | 8 | **Keep** (`WhatsAppTransport` Protocol and `WhatsAppClient`). |
| D22 | `agents/guard.py:132-137` | `agents/middleware.py:69-74` | 6 | **Keep** (LangChain `awrap_model_call` signature). |
| D23 | `automation/alerts.py:469-477` / `492-500` | | 9 | **Keep** (`notify_staff` and `_notify` share a parameter list; that is fine). |
| D24 | `crm/api/screens.py:36-45` | `crm/services/inbox.py:37-46` (`InboxItemOut` mirrors the `InboxItem` dataclass) | 9 | Keep (API schema and domain type). Optional: `TypeAdapter` over the dataclass. |
| D25 | `crm/models/customers.py:9-16` / `people.py:9-16`, `api/records.py:21-26` / `users.py:12-17`, `api/admin.py:13-17` / `deal_actions.py:14-18` | | 5-8 | Import headers: false positives. |
| D26 | `crm/api/screens.py:194-200` / `207-213`, `api/reports.py:203-216`, `negotiation.py:511-554`, `crm/services/auth.py:158-186`, `agents/handlers.py:135-175` | | 5-7 | Small statement-shape matches; not worth a helper. |

---

## 3. Repeated patterns that can become one helper (same behaviour)

Each item lists: file:line, current code, proposed code, estimated lines saved, risk, and whether behaviour and speed stay the same.

### 3.1 Router boilerplate in `kx.crm.api`: role aliases and `Session`

**Where:**

- 14 router modules repeat `Session = Annotated[AsyncSession, Depends(ctx.session)]  # noqa: N806`.
- `Staff` is defined 8 times, `Manager` 6 times, `HumanManager` 4 times, `HumanStaff` and `MdOnly` 2 times each. Examples: `api/deals.py:47-50`, `api/quotes.py:24-28`, `api/records.py:32-34`, `api/deal_actions.py:37-40`, `api/admin.py:34-39`, `api/reports.py:54-56`, `api/assignment.py:20-22`, `api/change_requests.py:23`, `api/screens.py:24`.

```python
# current (in every module)
Staff = Annotated[Principal, Depends(require_role(*STAFF))]
...
def router(ctx: ApiContext) -> APIRouter:
    Session = Annotated[AsyncSession, Depends(ctx.session)]  # noqa: N806
```

```python
# proposed: kx/crm/api/context.py (it already holds STAFF / MANAGERS)
Staff = Annotated[Principal, Depends(require_role(*STAFF))]
Manager = Annotated[Principal, Depends(require_role(*MANAGERS))]
HumanManager = Annotated[Principal, Depends(require_human_mfa(Role.MANAGER))]
HumanStaff = Annotated[Principal, Depends(require_human_mfa(Role.SALES, Role.MANAGER))]
MdOnly = Annotated[Principal, Depends(require_role(Role.MD))]

@dataclass(frozen=True, slots=True)
class ApiContext:
    ...
    Session: Any = field(init=False)  # Annotated[AsyncSession, Depends(session)]
    def __post_init__(self) -> None:
        dep = self.databases.dependency(LogicalDb.CRM)
        object.__setattr__(self, "session", dep)
        object.__setattr__(self, "Session", Annotated[AsyncSession, Depends(dep)])
# routers: `Session = ctx.Session` or use `ctx.Session` directly in signatures
```

- **Lines saved:** about 35.
- **Risk:** low. `require_role(...)` builds a new dependency callable each time. FastAPI de-duplicates dependencies per request by callable identity, so sharing one alias even saves one dependency call per request where two aliases are used.
- **Behaviour:** preserved. **Speed:** preserved or slightly better.

Leave the module-specific aliases (`SettingsReader`, `PriceEditor` and so on) where they are.

### 3.2 Get-or-404: about 25 sites

**Where:**

- `crm/api/change_requests.py:54,68`; `api/quotes.py:36`; `api/records.py:226,253,263`; `api/users.py:112,134`.
- `crm/services/approvals.py:162`; `assignment.py:261,307,370`; `auth.py:509`; `change_requests.py:208`; `orders.py:164,256`; `price_lists.py:156,179,197`; `price_proposals.py:76`; `settings.py:209,235`.
- `automation/admin_api.py:131,222`.

```python
# current
cr = await session.get(ChangeRequest, cr_id, with_for_update=lock)
if cr is None:
    raise NotFound(f"change request {cr_id} not found")
```

```python
# proposed: kx/core/db.py (allowed for every package)
async def get_or_404[M](s: AsyncSession, model: type[M], key: Any, what: str, *, lock: bool = False) -> M:
    row = await s.get(model, key, with_for_update=lock)
    if row is None:
        raise NotFound(f"{what} not found")
    return row
# use: cr = await get_or_404(session, ChangeRequest, cr_id, f"change request {cr_id}", lock=lock)
```

- **Lines saved:** about 45.
- **Risk:** low. Pass the exact message text so the problem JSON stays the same.
- **Behaviour and speed:** preserved.

### 3.3 Negotiation round recording (D3 + D4)

**Where:** `crm/services/negotiation.py`:

- `evaluate_counter` 222-317;
- `_evaluate_ladder` 323-415;
- the shared rounds query at 70-86 and 181-197.

```python
# proposed (same module)
async def _rounds_of(s, deal_id, version_id) -> tuple[list[NegotiationRound], list[Decimal], list[Decimal]]:
    rounds = list((await s.execute(select(NegotiationRound).where(...).order_by(NegotiationRound.round_no))).scalars())
    offers = [r.offered_unit_price for r in rounds if r.offered_unit_price is not None]
    traded = [r.buyer_ask_quantity for r in rounds
              if r.traded_for == "quantity_increase" and r.buyer_ask_quantity is not None]
    return rounds, offers, traded

def _ensure_valid(version: QuoteVersion, now: datetime, s: BusinessSettings) -> None:
    if version.valid_until is not None and version.valid_until < local_today(now, s):
        raise RuleViolation("the quote has expired; request a fresh quote")

async def _record_round(s, deal, version, verdict, *, ask, qty, offered, concession_no, channel, actor, now) -> None:
    s.add(NegotiationRound(deal_id=deal.id, quote_version_id=version.id,
                           round_no=await _next_round_no(s, deal.id), channel=channel,
                           buyer_ask_unit_price=ask, buyer_ask_quantity=qty,
                           verdict=round_verdict(verdict), offered_unit_price=offered,
                           concession_no=concession_no, traded_for=verdict.traded_for,
                           ai_reasoning=verdict.reason or None))
    if DealStage(deal.stage) is DealStage.QUOTATION:
        await deal_svc.move_stage(s, deal, DealStage.NEGOTIATION, actor, now=now, reason="buyer countered")
    await record(s, actor, "negotiation.round", "deal", deal.id,
                 after={"ask": ask, "qty": qty, "verdict": verdict.kind.value, "offer": offered},
                 timeline=Timeline("negotiation", _round_summary(verdict, ask), deal.id,
                                   deal.primary_contact_id, channel=channel))
```

- **Keep the calling order exactly as it is:**
  - The v2 path calls `pause_ai` **before** `flush`.
  - The ladder path calls `flush` **before** `pause_ai`.
  - The helper does only add, stage move and record. Each caller keeps its own `deal.concessions_used` update and its own post-processing.
- **Lines saved:** about 35.
- **Risk:** low to medium (money path). The CRM negotiation tests and the §23 eval scenarios cover it.
- **Behaviour and speed:** preserved.

### 3.4 Change-request event and version checks (D5, D6, D7)

**Where:** `crm/services/change_requests.py:131-162` (`raise_change_request`), `166-204` (`reroute`), `234-243` (`link_revision`) and `315-327` (`resolve`).

```python
def _requested_event(deal, cr, assignee, manager, s, window, now) -> Emit:
    return Emit(ev.CHANGE_REQUESTED, "change_request", cr.id, ChangeRequested(
        deal_ref=deal.id, deal_number=deal.number, change_request_ref=cr.id, type=cr.type,
        assigned_to=staff.staff_contact(assignee),
        escalate_to=None if manager.id == assignee.id else staff.staff_contact(manager),
        sla_due_at=cr.sla_due_at,
        remind_at=tuple(window.add_open_minutes(now, m) for m in s.reminder_minutes[:-1]),
        escalate_at=window.add_open_minutes(now, s.escalate_after_minutes()), alert_ref=cr.alert_ref))

async def _version_of_deal(s, version_id, deal, *, issued: bool, msg: str) -> QuoteVersion:
    version = await s.get(QuoteVersion, version_id)
    quote = await s.get(Quote, version.quote_id) if version else None
    if version is None or quote is None or quote.deal_id != deal.id or (issued and version.status not in _ISSUED):
        raise RuleViolation(msg)
    return version
```

`raise_change_request` passes `type=type`, which equals `cr.type` after the insert.

- **Lines saved:** about 35, D6 included.
- **Risk:** low.
- **Behaviour and speed:** preserved.

### 3.5 Assignment hand-offs (D11)

**Where:** `crm/services/assignment.py`: `reassign` ending at about 285-296, `take_over` 316-327 and `hand_back` 343-354.

```python
async def _announce_and_reroute(s, deps, deal, *, to, previous, reason, actor) -> Deal:
    await _announce(s, deal, to=to, previous=previous, reason=reason, actor=actor,
                    notify=(await staff.manager_user(s),))
    await approvals.reroute_open(s, deps, deal, actor)
    await change_requests.reroute(s, deps, deal, actor)
    return deal
```

- **Lines saved:** about 24.
- **Risk:** low.
- **Behaviour and speed:** preserved.

### 3.6 Trivial merges inside one package

| Where | Change | Lines saved | Risk |
|---|---|---|---|
| `crm/jobs.py:342-351` | `return await _latest_deal(session, contact_id)` | 7 | none |
| `crm/pdf.py:458-465` | `summary=summary_phrase(doc)` | 7 | none |
| `crm/api/change_requests.py:71-80`, `api/deals.py:241-250` | `body.to_edits(note=...)` on `QuoteEditsIn` | 10 | none |
| `automation/outbound.py:98-112`, `channels/email_chain.py:20-34` | one `EmailTransport` Protocol | 13 | none |
| `crm/services/quotes.py:403-407` (and the v2 twin at 467-471) | `has_versions = await s.scalar(select(exists().where(QuoteVersion.quote_id == quote.id)))`. With `exists`, the database stops at the first row instead of counting them all. | 0 | none |

### 3.7 Agent service conversation and claim (D14, D15)

**Where:**

- `agents/service.py:209-216`, `278-285`, `325-332`: three identical `TurnStore.open_conversation(...)` calls.
- `agents/service.py:225-235` and `348-358`: `RunStore.claim(...)` with the model name and prompt version.
- `agents/memory.py:110-117` and `159-166`.

```python
async def _conversation(self, s, turn: InboundTurn, language: str, deal_ref: UUID) -> UUID:
    return await TurnStore.open_conversation(s, thread_key=turn.thread_key, channel=turn.channel,
                                             deal_ref=deal_ref, contact_ref=turn.contact_ref, language=language)
```

- **Lines saved:** about 30.
- **Risk:** low.
- **Behaviour and speed:** preserved.

### 3.8 Settings classes

There are three settings classes: `CoreSettings` (`KX_`), `AutomationSettings` (`KX_AUTO_`) and `AppSettings` (`KX_APP_`). They are **not duplicated**; each owns its own prefix, as CONTRACTS.md §1 requires.

One concrete fix:

- **Current.** `arifaa_pack/container.py:156` and `:331` each call `AutomationSettings()`, which parses the environment again. Meanwhile `kx/automation/settings.py:179` `get_automation_settings()` (cached) is never called.
- **Proposed.** Use `get_automation_settings()` at both sites.
- **Result.** The dead function gets a caller, and two environment parses at start-up go away.
- **Lines saved:** 0. **Risk:** none. **Behaviour:** preserved.

  Note: `auto_s` at `container.py:364` must stay a fresh instance, because it takes init overrides.

### 3.9 HTTP client and retry code

This is already centralised:

- `HttpRetry` (tenacity, transient-only) in `kx/automation/channels/__init__.py:37`, used by WhatsApp, Telnyx and the scraper.
- Stalwart JMAP maps errors itself at `stalwart.py:86-92`.
- A single `httpx.AsyncClient` is created at `container.py:331`.

The app adapters in `arifaa_pack/adapters.py` make plain calls with no retry: `HttpEmbedder._embed:97`, `PayloadContentSource.fetch:135`, `OpenRatesFx.latest:172`, `PayloadProductWriter._find/_save:206/217` and `Turnstile:373`.

- **This is acceptable:**
  - Each caller is a job that the job store retries, or the embedder has a 2 s query cut-off where a retry would break the fallback.
  - Turnstile must not be retried.
- **One small merge:** `PayloadContentSource` and `PayloadProductWriter` build the same base URL and `users API-Key` header. A `_PayloadApi` base class saves about 6 lines. Low value.

### 3.10 try/except/log blocks

There are 32 `except Exception` sites. Every one logs a **different** event name with its own fallback (`pdf.engine_failed`, `kb.embedding_fallback`, `agents.stop_reading_failed` and so on). A decorator would hide the fallback values. **No action.**

### 3.11 StaffNotice emission (7 sites in the CRM) and AI pause (6 sites)

**StaffNotice.** The sites are `crm/services/phone.py:88-107` and `112-134`, `auth.py:280`, `company.py:198`, `consent.py:130`, `fx.py:118` and `scheduled_reports.py:93`. Each builds `record(..., emit=Emit(ev.STAFF_NOTICE, entity, id, StaffNotice(deal_ref=..., notify=(...), text=..., key=...)))`.

```python
# kx/crm/services/journal.py
def staff_notice(entity: str, entity_id: Any, *, deal_ref, notify, text: str, key: str) -> Emit:
    return Emit(ev.STAFF_NOTICE, entity, entity_id, StaffNotice(deal_ref=deal_ref, notify=notify, text=text, key=key))
```

- **Lines saved:** about 30.
- **Risk:** low.
- **Behaviour and speed:** preserved.

**AI pause.** The statement `deal.ai_enabled, deal.ai_paused_reason, deal.ai_paused_at = False, <reason>, <now>` appears 6 times: `assignment.py:312`, `consent.py:110`, `deals.py:525`, `deals.py:591`, `handover.py:56` and `negotiation.py:438`.

- **Proposed:** a model method `Deal.pause_ai(reason, at)`.
- **Lines saved:** 0. It is for one source of truth: a future column such as `ai_paused_by` would be set in one place.

### 3.12 Quote version prelude

**Where:** `crm/services/quotes.py:400-406` and `464-470`. The same 6 statements appear in both the v1 and v2 paths: contact, quote, supersede, has_versions, vat and total.

```python
async def _prelude(s, deal, reqs, drafts, settings, now) -> tuple[Contact, Quote, str | None, bool, Decimal, Decimal]: ...
```

- **Lines saved:** about 8.
- **Risk:** low.

### 3.13 Repeated SQL fragments

- **The Quote / QuoteVersion join.** `select(...).join(Quote, Quote.id == QuoteVersion.quote_id).where(Quote.deal_id == ...)` appears 10 times in 8 files. Add `versions_of_deal(deal_id) -> Select` in `kx/crm/services/quotes.py`, or a `Quote.deal_versions()` class method. Saves about 10 lines; low risk.
- **The open-stages list.** `[s.value for s in OPEN_STAGES]` is built 3 times per call, in `crm/jobs.py:328,346` and elsewhere. Make it a module constant `_OPEN_STAGE_VALUES: Final = tuple(...)`.

### 3.14 Splitting `workbook.plan` (CC 80)

**Where:** `crm/services/workbook.py:276-422`. Split it into three parts:

- `_plan_categories(p, sheets, now)`, covering lines 283-305;
- `_plan_products(p, sheets, now)`, covering 307-375, with an inner `_plan_product_row`;
- `_plan_tiers(p, sheets, now)`, covering 377-421.

Also move two things out of the per-row loop:

- `compare = tuple(k for k in (c.key for c in PRODUCT_COLUMNS) if k not in (...))` (line 350) is rebuilt for every row. Make it a module `Final`.
- `_diff(current, values, compare)` runs twice per row (lines 353 and 370). The second call runs after `values["unit"] = "pcs"`. Behaviour is the same only if the first result is reused for the lead / MOQ / money checks, as today, and the second is kept for `changes`. The proposal keeps both calls and only moves the constant.

- **Lines saved:** 0. Each part drops to CC ≤ 25 and the module MI rises from C toward B or A.
- **Risk:** medium. `plan` is pure and covered by the workbook tests.
- **Behaviour:** preserved.

---

## 4. Dead code, unused settings, unused env vars

### 4.1 vulture

- **Confidence ≥ 80: 6 hits, all false positives.** They are parameters required by an interface:
  - `agents/testing.py:74,90,96,101` (`run_manager`, `tool_choice`, `include_raw`): the LangChain `BaseChatModel` overrides;
  - `agents/tracing.py:50` `serialized`: the callback handler signature;
  - `agents/voice.py:178` `model_settings`: the LiveKit `llm_node` signature.

  Fix: rename each to `_name`, or add a vulture whitelist. Do not delete them.
- **Confidence 60, tests counted as callers: 87 hits.** About 70 are FastAPI route handlers, Celery signal handlers, Alembic `downgrade`, pydantic validators (`company.py` `_iban`, `_swift` and so on; `settings.py` `_ordered`, `_consistent`) or framework hooks. These were checked and are not dead.

### 4.2 Verified dead code

I checked each by grep over `packages/`, `apps/`, `tests/`, `scripts/`, `live-test/`, `docs/` and the Makefile. The only hits are the definitions, or a docstring that names the function.

| # | Location | What | Lines | Action |
|---|---|---|---|---|
| 1 | `agents/evals/harness.py:175` `record_eval_run` | writes `EvalRun`; never called | 16 | Delete it, or wire it into the evals CLI if the CI eval history is wanted (the `eval_runs` table exists). |
| 2 | `agents/registry.py:219` `AgentRegistry.with_specs` | "per-client overrides", unused | 3 | Delete it, or keep it as client-copy API and add a test. |
| 3 | `automation/channels/whatsapp.py:281` `wa_id_to_e164` | superseded by `try_wa_id_to_e164` | 4 | Delete it and fix the docstring at 287. |
| 4 | `automation/settings.py:179` `get_automation_settings` | never called | 4 | **Use it** (§3.8) rather than delete it. |
| 5 | `arifaa_pack/container.py:117` `EventBus.subscribers` | never called | 2 | Delete. |
| 6 | `core/events.py:176` `InMemoryBus.subscriptions` | never called | 3 | Delete. |
| 7 | `crm/services/assignment.py:403` `load_by_owner` | "dashboard / tests"; neither uses it | 8 | Delete. |
| 8 | `crm/services/auth.py:516` `active_sessions` | never called | 7 | Delete. |
| 9 | `crm/services/staff.py:35` `manager_for` | superseded by `manager_user` / `approver_for` | 8 | Delete. |

Total: **9 items, about 57 lines.** Risk is low. The public names are in package modules that the app does not import (checked).

**Unused ORM classes, kept on purpose:**

- `agents/orm.py:183` `NewsItem` and `:195` `LaneRiskAssessment` mirror v2 tables (`news_items`, `lane_risk_assessments`).
- `automation/models.py:208` `ScheduledJob` is documented as the schema mirror of the table that `kx.core.jobs` writes in SQL.
- The DB tests (`test_db.py:39`) check ORM ⊆ DDL, not the other way round, so deleting them would not fail tests. They are deliberate documentation. **Keep.**

**Dormant by design (not dead):** the v2 cost-based pricing path. It covers `engine.unit_cost_per_1000`, `price_line`, `concession_ladder`, `negotiation.load_state` and `evaluate_counter`'s non-ladder branch, `quotes.request_quote`'s cost branch, and `scraping.py`. CONTRACTS.md §3 keeps it behind `pricing.mode='cost'` / `features.scraping`. Not counted as removable.

### 4.3 Unused settings and env vars

- **Settings fields:** all 160 env names of `CoreSettings`, `AutomationSettings` and `AppSettings`, nested fields included, are read somewhere in non-test source. **0 unused.**
- **`.env.example`:** 155 `KX_*` keys. 9 match no settings class, and **every one is read elsewhere**:

  | Key | Read by |
  |---|---|
  | `KX_MIGRATE_{CRM,AUTOMATION,AGENTS}_DSN` | `os.environ` in `arifaa_pack/migrations/__init__.py:46` |
  | `KX_DDL_DIR` | `migrations/__init__.py`, Dockerfile |
  | `KX_PROCESS`, `KX_API_PORT`, `KX_HEALTH_PATH` | `docker/healthcheck.py`, compose |
  | `KX_CMS_WEBHOOK_HMAC`, `KX_API_HOOK_URL` | the CMS container (`pack-cms.env.example`) |

  Optional typing improvement: move `KX_MIGRATE_<DB>_DSN` into `CoreSettings` as `migrate_crm_dsn: str | None` and so on, so it is validated like the other DSNs. About 4 lines; low risk.
- **`pack.env.example`:** `KX_SES_SMTP_PASSWORD` and `KX_STALWART_RECOVERY_ADMIN` are consumed by `compose.mail.yml`. They are correct.

  43 tunables are not listed (they take their defaults), for example `KX_LOG_LEVEL`, `KX_DB_POOL_SIZE`, `KX_APP_WORKER_CONCURRENCY` and `KX_APP_PDF_RENDERER`. That is acceptable, but `KX_DB_POOL_SIZE` and `KX_APP_WORKER_CONCURRENCY` (bench-tuned, D48) deserve a commented line in the server example.
- **Undocumented in `.env.example`:**
  - `KX_APP_FX_RATES_AUTO` and `KX_APP_FX_RATES_URL` (added 18 Nov): **add them**.
  - The nested groups (`KX_AUTH__*`, `KX_PLATFORM__*`, `KX_AUTO_*__*` and so on) are listed field by field, so they are fine.

---

## 5. Efficiency

### 5.1 Indexes against the frequent filters

| # | Query (file:line) | How often | Current index | Proposal | Risk |
|---|---|---|---|---|---|
| **I1** | `automation/inbound.py:269,280` `advance_status`: `WHERE outbound_messages.provider_message_id = ?`; also `:327` `IN (...)` for bounce reports | **Every** WhatsApp and email status webhook: sent, delivered and read, so about 3 per outbound message | **none.** `outbound_messages` (DDL `02_automation.sql:72-104`) indexes only `status, not_before`, `deal_ref`, `contact_ref`, `recipient_hash`, `sending_at` | New automation migration `0010`: `CREATE INDEX CONCURRENTLY IF NOT EXISTS outbound_provider_msg_idx ON outbound_messages (provider_message_id) WHERE provider_message_id IS NOT NULL;`. This turns a sequential scan of a table that grows without limit into an index probe on the hottest webhook path. | low |
| **I2** | `agents.agent_runs (started_at)` | Write cost on every AI run | **Duplicate**: `agent_runs_day_idx` (DDL `03_agents.sql:80`) and `agent_runs_started_idx` (migration `agents/0004_v2_partitions.py:110`) index the same column. `agent_runs` is not partitioned. | `DROP INDEX CONCURRENTLY IF EXISTS agent_runs_started_idx;` | low |
| I3 | `crm/api/screens.py:207` `price_list_history` (`action IN (...) ORDER BY at DESC LIMIT`); `crm/services/dashboards.py:171` (`action IN ... AND at range`) | Screen and dashboard | Only `audit_entity_idx (entity_type, entity_id, at)`. `audit_events` is append-only and **never partitioned** (DATABASE.md), so it grows forever. | `CREATE INDEX CONCURRENTLY audit_action_at_idx ON audit_events (action, at DESC);` | low |
| I4 | `crm/api/screens.py:188-196` `product_history`: `entity_type='price_tier' AND (after->>'product_id' = ? OR before->>'product_id' = ?)` | Screen | none (JSONB expression) | Two partial expression indexes: `... ON audit_events ((after->>'product_id')) WHERE entity_type='price_tier'` and the same on `before`. Postgres combines them with a BitmapOr. | low |
| I5 | `crm/services/inbox.py:69` (sales view): `approvals WHERE status IN open AND assigned_user_id IN (...)`; `dashboards.py:72-75` `approver_user_id` | Every inbox load | Only partial indexes on `quote_version_id` and `deadline_at` | `CREATE INDEX approvals_assignee_open_idx ON approvals (assigned_user_id) WHERE status IN ('pending','overdue');`. The table is small today; this matters once approvals accumulate. | low |
| I6 | `crm/api/deals.py:162-172` `list_deals` default (no stage filter): keyset `ORDER BY updated_at DESC, id DESC` | The deals board | `deals_stage_idx (stage, updated_at DESC)` cannot serve an unfiltered order | `CREATE INDEX deals_updated_idx ON deals (updated_at DESC, id DESC);` | low |
| I7 | `crm/api/deals.py:192` timeline keyset: `WHERE deal_id = ? ORDER BY id DESC` | Deal page | `timeline_deal_idx (deal_id, at DESC)`. The order is by `id`, so Postgres sorts every row of the deal. | `(deal_id, id DESC)`. Changing the keyset to `(at, id)` would change the cursor format, so add the index rather than change the code. | low |
| I8 | `inbox.py:128-137` manager view: `timeline_events.kind='message_in'` joined to `deals.ai_paused_reason='handover'` | Every manager inbox load | none for `ai_paused_reason` | `CREATE INDEX deals_handover_idx ON deals (id) WHERE ai_paused_reason = 'handover';`. Check the plan with `EXPLAIN` first. | low |

The DDL baseline must not be edited, so every index goes in a new Alembic revision (`migrations/<db>/versions/00NN_*.py`) with `CONCURRENTLY` and `IF NOT EXISTS`.

### 5.2 N+1 query patterns (AST scan; 49 loop-await sites found, these are the real ones)

| # | Where | Current | Proposed | Queries | Risk |
|---|---|---|---|---|---|
| **N1** | `crm/services/workbook.py:490-495` `snapshot`; `price_lists.py:264` `export_xlsx`; `workbook.py:674` (`apply`) | `current_tiers(session, product.id, now)` per product: 1 + N queries, run on every workbook preview, apply and export | `current_tiers_many(session, product_ids, now)` in `crm/services/pricing.py:416`, using one correlated subquery or a window function (sketch below) | 1 + N → 2 | low (same rows, same order) |
| **N2** | `crm/api/users.py:73-89` `list_users` | `await s.get(StaffCredential, u.id)` per user | One `select(StaffCredential).where(StaffCredential.user_id.in_(ids))` into a dict, or an outer join | 1 + N → 2 | low |
| N3 | `crm/services/dashboards.py:143` `manager_board` | `salesperson_board` per salesperson, each about 7 queries | Group the per-user counts with `GROUP BY owner_user_id` / `approver_user_id` in one query per metric | 7N → about 7 | medium (dashboard maths; covered by the dashboard tests) |
| N4 | `crm/services/quotes.py:379, 456` | `list_pricing_inputs` per requirement line | Acceptable: deals have 1 to 3 lines | n/a | no action |
| N5 | `agents/knowledge.py:338` `reembed_stale` | `s.get(KbChunk)` per row inside a batch of 32 | `await s.execute(update(KbChunk), [{"id": r.id, "embedding": v, "embedding_model": m} ...])`, an executemany bulk update by primary key | 32 → 1 per batch | low (only runs during `kx reindex`) |
| N6 | `automation/inbound.py:269-295` `advance_status` | `UPDATE ... RETURNING id`, then a `SELECT` joined to `channel_accounts` | `RETURNING contact_ref, deal_ref, kind, channel_account_id` (the account's channel can come from a cached map) | 2 → 1 per webhook | low |
| N7 | `crm/services/fx.py:76-110` | `s.get` plus a "previous rate" query per currency | About 10 currencies once a day | n/a | no action |

**N1 sketch:**

```python
async def current_tiers_many(session, product_ids, now) -> dict[UUID, tuple[PriceTier, ...]]:
    t2 = aliased(ProductPriceTier)
    latest = (select(func.max(t2.valid_from))
              .where(t2.product_id == ProductPriceTier.product_id, t2.valid_from <= now)
              .scalar_subquery())
    rows = (await session.execute(
        select(ProductPriceTier)
        .where(ProductPriceTier.product_id.in_(product_ids), ProductPriceTier.valid_from == latest)
        .order_by(ProductPriceTier.product_id, ProductPriceTier.min_qty))).scalars()
    out: dict[UUID, list[PriceTier]] = defaultdict(list)
    for r in rows: out[r.product_id].append(_tier(r))
    return {k: tuple(v) for k, v in out.items()}
```

The existing index `product_price_tiers_lookup_idx (product_id, valid_from DESC)` serves it.

### 5.3 Sync and blocking work inside async code

There is no `time.sleep`, `requests`, `urllib` or sync boto3 anywhere. WeasyPrint already runs in `asyncio.to_thread` (`pdf.py:307`), Chromium runs as an async subprocess (`pdf.py:279`), and Jinja templates and CSS are `cached_property` (`pdf.py:333-347`). These three places remain:

| # | Where | What blocks | Proposed | Risk |
|---|---|---|---|---|
| **B1** | `crm/api/reports.py:258-300` `export` (an `async def` route) | It builds CSV or XLSX for up to **50,000 rows** on the event loop. openpyxl write-only takes about 1-3 s of CPU at that size, and the whole API process stalls meanwhile. | Fetch rows async, then `return await asyncio.to_thread(_render_export, entity, columns, rows, format)`. The rows are plain tuples, safe to use in a thread. | low |
| B2 | `crm/services/workbook.py:559, 578` (`read_sheets(data)` → openpyxl `load_workbook`, plus the pure `plan`); `price_lists.py:219` `import_xlsx`; `price_lists.py:259`, `workbook.py:757` (`Workbook().save`) | XLSX parse and serialise on the loop for every preview, apply, import and export | `sheets = await asyncio.to_thread(read_sheets, data)`; `wb_bytes = await asyncio.to_thread(_save, wb)`. `plan` is pure and can also run in the thread. | low |
| B3 | `crm/pdf.py:278, 294` (`write_text`, `read_bytes` in the temp dir) | Small (about 100 KB) local file I/O | Acceptable; no action | — |

### 5.4 Objects built per request that should be cached

- Already correct:
  - one `httpx.AsyncClient`, one Redis client and one aioboto3 session per process (`container.py`);
  - Jinja `Environment` and CSS are cached;
  - every regex is compiled at module level (`price_guard.py:39-46,227-231,342`). The 3 inline `re.sub` / `re.fullmatch` calls (`company.py:45`, `workbook.py:212`, `memory.py:273`) hit Python's internal regex cache.
  - `ZoneInfo(...)` caches its instances.
- `AutomationSettings()` is built 3 times at start-up (§3.8). Start-up only.
- `SettingsService(ttl_seconds=0)` at `container.py:362` is a one-off at start-up, so it is fine.

### 5.5 Large SELECT / missing LIMIT

- Pagination helpers clamp to `MAX_LIMIT` (`api/pagination.py:45,55`). Inbox queries use `limit` (`inbox.py:69,91,113,137`). Export caps at 50,000.
- Unbounded but small: `select(Product)` (`workbook.py:490`, `price_lists.py:220`) and `select(User)` (`users.py:88`, `dashboards.py:141`). These are catalogue and staff tables (hundreds of rows). No action.
- `reports.py:249-253` `takeover_queue` lists open taken-over deal IDs without a limit. It is bounded by the MD's capacity. No action.

---

## 6. Advanced improvements that keep exact behaviour (only where they pay off)

| # | Where | Current | Proposed | Pays off because | Lines | Risk |
|---|---|---|---|---|---|---|
| A1 | `arifaa_pack/texts_sync.py:83-93` `translate_text` | 5 LLM translations run **one after another** (ar, hi, ta, zh, si) | `async with asyncio.TaskGroup() as tg: tasks = {lang: tg.create_task(translate_one(...)) for lang in TRANSLATED}`, then save sequentially in one transaction | Job time drops from about 5× to about 1× model latency | +2 | low. One semantic difference: if one call fails, the others are cancelled and nothing is saved, where today the earlier languages are saved. The job store retries the whole job, so the final state is the same. |
| A2 | `arifaa_pack/adapters.py:224` `PayloadProductWriter.save_draft(self, p: Any)` | `Any` | `p: ProductContentChanged` (`kx.core.event_payloads`; the app may import it) | mypy checks the field names `p.code`, `p.category_code` and so on | 0 | none |
| A3 | `kx/automation/outbound.py:98` + `email_chain.py:20` | 2 Protocols | 1 Protocol (§3.6) | One contract for 3 implementations | −13 | none |
| A4 | `core/fallback.py:40` `CircuitBreaker`, `:91` `FallbackChain`; `core/jobs.py:349` `JobRunner`; `agents/models.py:174` `ModelRouter`; `agents/website.py:70` `WebsiteDeps` | `@dataclass` without slots (5 of 120) | `slots=True` | Consistency only. They are per-process singletons, so the memory and speed gain is negligible. Check that no test monkeypatches attributes first. | 0 | low; optional |
| A5 | `kx.core.db.get_or_404` (§3.2) | — | A generic function using PEP 695 `[M]` syntax | A typed return value replaces 25 hand-written `is None` checks | −45 | low |
| A6 | `crm/services/quotes.py`, `negotiation.py` | Branches on `s.pricing_mode == "price_list"` | Keep the `if`. A `PricingStrategy` Protocol is **not** recommended: v2 is off and a strategy object would add indirection for a single live path. | — | — | — |
| A7 | `crm/api/pagination.py` | `rows, nxt = page_of(list((await s.execute(stmt)).scalars()), limit, lambda ...)` repeated 7 times (`admin.py:65`, `deals.py:172,195`, `quotes.py:87`, `records.py:172,211,291`) | `async def fetch_page(s, stmt, limit, cursor_of) -> tuple[list[T], str \| None]` | Removes the `list(... scalars())` noise | −7 | none |

**Considered and not recommended:**

- `asyncio.gather` / `TaskGroup` over the same `AsyncSession` (`dashboards.py`, `reports.py`). An AsyncSession is not safe for concurrent use; it would need one session per task.
- Rewriting `price_guard` for readability. It is safety-critical and its speed is measured; it should stay as it is.

---

## 7. Prioritised plan (value against risk)

| Rank | ID | Change | Value | Risk | Lines |
|---|---|---|---|---|---|
| 1 | I1 | Index `outbound_messages(provider_message_id)` | High: the hottest webhook path, and a sequential scan on a growing table | low | +20 (migration) |
| 2 | B1 | Export CSV / XLSX via `asyncio.to_thread` | High: a 50k-row export stalls the whole API | low | +6 |
| 3 | N1 | `current_tiers_many` for workbook snapshot, apply and export | High: 1 + N → 2 queries | low | +15 / −6 |
| 4 | I2 | Drop the duplicate `agent_runs_started_idx` | Medium: write cost on every AI run | low | +10 (migration) |
| 5 | I3, I4 | `audit_events` indexes for price-list and product history plus dashboards | Medium: the table grows forever | low | +15 (migration) |
| 6 | B2 | Workbook and price-list XLSX parse and save in a thread | Medium | low | +6 |
| 7 | 4.2 | Delete 8 dead functions; use `get_automation_settings` | Medium (clarity) | low | −57 |
| 8 | 3.1 | Router role aliases and `ctx.Session` | Medium | low | −35 |
| 9 | 3.2 | `get_or_404` in `kx.core.db` | Medium | low | −45 |
| 10 | 3.3 | Negotiation `_rounds_of` / `_record_round` | Medium | low-med | −35 |
| 11 | 3.4 | Change-request event and version helpers | Medium | low | −35 |
| 12 | N2, N6, I5-I8 | `list_users` IN query; `advance_status` single `RETURNING`; inbox, deals and timeline indexes | Low-medium | low | ±0 |
| 13 | 3.5-3.7, 3.11-3.13 | Assignment, trivial merges, agent service and memory, StaffNotice, quote prelude, join helper | Low-medium | low | −120 |
| 14 | A1 | TaskGroup translation | Low-medium (job latency) | low | +2 |
| 15 | 3.14 | Split `workbook.plan` and `apply`; `container.build` into `_build_automation` / `_build_agents` | Medium (maintainability) | medium | ±0 |
| 16 | D17 | Outbox / inbox ORM mixin in `kx.core.events` | Low | medium | −25 |
| 17 | N3 | Grouped queries for `manager_board` | Low (a few salespeople) | medium | ±0 |

**Estimated removable lines:**

| Source | Lines |
|---|---|
| Merges (§2, §3, §6 A3 / A5 / A7) | ≈ 315 |
| Dead code (§4.2) | ≈ 57 |
| **Total** | **≈ 370 (≈ 1.1 % of 35k LOC)** |

The efficiency fixes add about 70 lines, almost all of them in migrations.

**Behaviour.** Every item keeps the current behaviour, with the one exception noted at A1 (how partial saves behave on a failed translation job).

**Package boundaries.** Every merge is either inside one package or moved down into `kx.core`. No new import from one package to another is proposed.
