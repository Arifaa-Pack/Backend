# `kx.crm` feature slices: the mapping (ADR-0001 Phase 1)

Every module under `kx/crm/services`, `kx/crm/api` and the loose top-level modules, with the slice it moves to.
Each move is a `git mv` (history kept) and leaves an **alias shim** at the old path: the old module name becomes
the same module object as the new one (`sys.modules[old] = new`), so `from kx.crm.services.quotes import X`,
`from kx.crm.services import quotes`, private helpers and `monkeypatch` on the old path all keep working for the
Arifaa Auto team and for the tests (which are left on the old paths on purpose: they exercise the shims).
Imports inside `src/` (the package and the app) are rewritten to the new paths in the same commit.

## What does not move

| Module | Why it stays |
| --- | --- |
| `models/` (9 modules + `__init__`) | The ORM is the package's shared data layer: `models.quotes` is imported by 18 module groups, `models.deals` by 28, `models.catalogue` by 18 (`CURRENT_STATE.md` §3). Splitting it per slice would turn most of those imports into rule-5 violations without making any feature easier to read. It stays one layer under every slice; per-slice repositories (and then per-slice models) are follow-up work once a slice has a repository. |
| `pricing/engine.py`, `pricing/price_list.py`, `pricing/types.py` | Already a slice-shaped package of pure maths; not touched (contest-verified). |
| `gateway.py` | The package's implementation of the `kx.core.contracts` Protocols for other packages (brief §2.2 keeps it at the root). |
| `jobs.py`, `deps.py`, `rules.py` | Package-wide: the job table, the long-lived collaborators every slice gets, the pure stage / rule tables. |
| `api/__init__.py` (`build_router`) | The router assembly the app mounts; it now imports each slice's `router`. |
| `templates/` | Loaded by package name (`PackageLoader("kx.crm", "templates")`), so it stays where it is. |

## Mapping

Order = the order of the moves (least depended-on first, `_shared/` last). One commit and one tag
(`arch-slice/crm-<slice>`) per row group.

| # | Slice | Current module | Target |
| --- | --- | --- | --- |
| 1 | `texts` | `services/texts.py` | `texts/service.py` |
| | | `api/texts.py` | `texts/router.py` |
| 2 | `company` | `services/company.py` | `company/service.py` |
| | | `api/company.py` | `company/router.py` |
| 3 | `operations` | `api/operations.py` | `operations/router.py` |
| 4 | `retention` | `services/retention.py` | `retention/service.py` |
| 5 | `reports` | `services/dashboards.py` | `reports/dashboards.py` |
| | | `services/scheduled_reports.py` | `reports/scheduled.py` |
| | | `api/reports.py` | `reports/router.py` |
| 6 | `catalogue` | `services/catalogue.py` | `catalogue/service.py` |
| | | `services/workbook.py` | `catalogue/workbook.py` |
| | | `api/catalogue_workbook.py` | `catalogue/router.py` |
| 7 | `pricing` (existing package) | `services/pricing.py` | `pricing/service.py` |
| | | `services/price_lists.py` | `pricing/price_list_service.py` |
| | | `services/fx.py` | `pricing/fx.py` |
| | | `services/price_proposals.py` | `pricing/proposals.py` |
| 8 | `contacts` | `services/consent.py` | `contacts/consent.py` |
| | | `services/phone.py` | `contacts/phone.py` |
| 9 | `records` | `api/records.py` | `records/router.py` |
| 10 | `auth` | `services/auth.py` | `auth/service.py` |
| | | `api/auth.py` | `auth/router.py` |
| | | `api/session_guard.py` | `auth/session_guard.py` |
| | | `api/users.py` | `auth/users_router.py` |
| | | `throttle.py` | `auth/throttle.py` |
| 11 | `handover` | `services/handover.py` | `handover/service.py` |
| 12 | `assignment` | `services/assignment.py` | `assignment/service.py` |
| | | `api/assignment.py` | `assignment/router.py` |
| 13 | `change_requests` | `services/change_requests.py` | `change_requests/service.py` |
| | | `api/change_requests.py` | `change_requests/router.py` |
| 14 | `orders` | `services/orders.py` | `orders/service.py` |
| 15 | `approvals` | `services/approvals.py` | `approvals/service.py` |
| 16 | `negotiation` | `services/negotiation.py` | `negotiation/service.py` |
| 17 | `inbox` | `services/inbox.py` | `inbox/service.py` |
| 18 | `screens` | `api/screens.py` | `screens/router.py` |
| 19 | `admin` | `api/admin.py` | `admin/router.py` |
| 20 | `deals` | `services/deals.py` | `deals/service.py` |
| | | `services/summary.py` | `deals/summary.py` |
| | | `services/lost_proposals.py` | `deals/lost_proposals.py` |
| | | `api/deals.py` | `deals/router.py` |
| | | `api/deal_actions.py` | `deals/actions_router.py` |
| 21 | `quotes` | `services/quotes.py` | `quotes/service.py` |
| | | `services/issuing.py` | `quotes/issuing.py` |
| | | `services/grants.py` | `quotes/grants.py` |
| | | `pdf.py` | `quotes/pdf.py` |
| | | `api/quotes.py` | `quotes/router.py` |
| 22 | `_shared` | `services/journal.py` | `_shared/journal.py` |
| | | `services/access.py` | `_shared/access.py` |
| | | `services/staff.py` | `_shared/staff.py` |
| | | `services/settings.py` | `_shared/settings.py` |
| | | `api/context.py` | `_shared/api_context.py` |
| | | `api/pagination.py` | `_shared/pagination.py` |
| | | `api/schemas.py` | `_shared/schemas.py` |

## Choices worth a second look

- **`_shared/`** holds what several slices import today: the journal (audit + timeline + outbox: almost
  every service), the access checks, the staff lookups, the settings snapshot (6 slices), and the API plumbing
  (`ApiContext`, keyset pagination, the request / response schemas). `api/schemas.py` (413 lines) is moved whole;
  splitting it per slice is follow-up work (a pure move of classes, but it touches most routers at once).
- **`pdf.py` → `quotes/`** (as the brief's target shape shows). The Requirement Summary in `deals/summary.py` also
  renders through it: a service-level call between slices, which rule 5 allows.
- **`grants.py` → `quotes/`**: grants are issued by `quotes/issuing.py` (4 imports) when an approval releases a
  quote; `approvals/` calls into `quotes/` for that.
- **`records` and `screens`** are screen routers that span several features (contacts, companies, products,
  orders, FX; inbox, price list, stock). They are moved whole into their own slice rather than split: splitting
  a router module is a bigger change than a move.
- **`throttle.py` → `auth/`**: only sign-in and recovery use it (the app's Redis version implements it).

## Cross-slice imports that remain (the rule-5 baseline)

Rule 5 forbids importing another slice's `repository` or `models`. With the ORM kept as the shared data layer
(above) and no repository modules yet, the baseline for rule 5 is **empty**: no slice has a `models.py` or
`repository.py` of its own. What remains between slices are service-level calls (allowed): the table
"service modules imported by other service / api modules" in `CURRENT_STATE.md` §3, with each name read through
this mapping (for example `services.quotes` → `quotes.service`). Phase 2 records them as the `independence`
baseline that may only shrink.
