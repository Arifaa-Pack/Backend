# The `docs/` folder: what each document is for

Every document in this folder, grouped by the question it answers, with who reads it and when. Superseded documents
are in `archive/` (kept for history, not for review). Updated 20 Nov 2026.

## The two documents people confuse: the SRS and the architecture guide

| | **SRS** (`Arifaa Pack — Detailed Business Flow v1 (SRS basis) (2).md`) | **Architecture and system design** (`Architecture-and-System-Design.docx`, short form `ARCHITECTURE.md`) |
|---|---|---|
| Answers | **What** the system must do for Arifa, and the business rules | **How** the system is built to do it |
| Written in | Business language: visitors, deals, quotes, approvals, salespeople, timers | Technical language: packages, databases, APIs, events, AI graphs, guards, servers |
| Owner | Arifa (the client) with Kairox; Arifa signs it off | Kairox (the engineering team) |
| Changes when | The business changes its mind (and Arifa approves) | The code changes |
| Example | "A price below the minimum needs the manager's approval" (§11) | "`require_human_mfa` + a `special_price` approval in `kx.crm.approvals`; the price guard fails closed" |
| In this repo | **Read-only**: never edited without Arifa's approval | Updated with every work package |
| Use it to | Check that a feature does what was agreed; write test scenarios | Understand, review or change the code; onboard a developer |

When the two disagree, the SRS wins on *what* and the architecture document says *how*. A change of *what* (for
example D54, the CMS package instead of Payload) is built, flagged in `OPEN_QUESTIONS.md` and waits for Arifa's
approval before the SRS text changes.

## Reading order for a review (before the code review)

1. `Architecture guide - pages changed since 18 Nov.pdf`: the 2-page summary of the latest changes.
2. `Architecture-and-System-Design.docx`: the full guide (each dated update chapter lists the sections it changed).
3. `ARCHITECTURE.md`: the same design in short, with the current diagrams.
4. `V1_CHANGE_PLAN.md` (the decisions, D-numbers) and `OPEN_QUESTIONS.md` (what is still to decide).
5. `REUSE_CATALOGUE.md` and `FEATURE_MAP.md`: where each feature lives in the code (open the code from here).

## 1. What the client wants (business, read-only)

| Document | Purpose | Read it when |
|---|---|---|
| `Arifaa Pack — Detailed Business Flow v1 (SRS basis) (2).md` | **The SRS.** The master business flow of v1: users and roles, website, CMS, CRM, lead capture, quotes, negotiation, approvals, follow-ups, notifications, AI agents, consent, retention, the §23 scenarios. Every rule is quoted by § in commits | You need to know what a feature must do, or why |
| `Arifaa Platform — Platforms, Pricing & Fallbacks/` | **The platform choice** agreed with Arifa IT. `… Accounts & Server Plan (for Arifa IT) v2.docx` wins (§2 platforms, §3 paid, §4 free, §5 server); `Recommendation.md` and `Fallback plan.md` follow it; `Options by field` (.md / .docx) and `Platform contest.md` are the comparison behind it; `Official sources.md` the price sources; the plan without "v2" is the earlier edition | You need to know which vendor or server is agreed, and its cost |
| `ARIFAA_AUTO_NOTES.md` | The one note kept for the second client project (Arifaa Auto), parked | Arifaa Auto restarts |
| `translations/Translation review 18 Nov.xlsx` | The message texts in each language for Arifa's reviewer | Before go-live (GO_LIVE_CHECKLIST E4) |

## 2. How it is built (design)

| Document | Purpose | Read it when |
|---|---|---|
| `Architecture-and-System-Design.docx` | **The full architecture and system design guide** (71 pages): components, code organisation, the end-to-end flows, the AI agent system, LangGraph, data and safety, technologies, assessment, glossary. Dated update chapters at the front (20 Nov, 16 Nov) | Reviewing the system as a whole; explaining it to the client |
| `Architecture guide - pages changed since 18 Nov.pdf` | The 20 Nov update chapter of the guide, as a 2-page PDF | A quick read of what changed |
| `ARCHITECTURE.md` | The design in short (one picture, layout, processes, a buyer turn, voice, the website assistant §5a, safety, observability, deployment, limits) | Day-to-day engineering; the first file a developer reads |
| `DATABASE.md` | The four logical databases, tiers, retention, partitions, and the tables added 19–20 Nov | Schema or data questions |
| `PLATFORM_MODES.md` | The paid / free switch per field (`KX_PLATFORM_PLAN`, `KX_PLATFORM__<FIELD>`) | Changing a vendor or the free version |
| `MULTI_PROJECT.md` | How the next client reuses the platform (one app per project) | Starting another client |
| `adr/` | Architecture decision records: `0001` modular monolith with feature slices, `0002` split contracts and publish packages (later), `0003` the CMS package; `0000` the template; `README.md` the index | Why a structural decision was taken |
| `arch/CRM_SLICES.md`, `arch/AUTOMATION_AGENTS_SLICES.md` | Where each feature folder is inside `kx.crm`, `kx.automation`, `kx.agents` and `kx.cms`, and the import rules | Finding the code of a feature; reviewing one feature |
| `arch/REPORT.md` | The final report of the 19 Nov restructuring (ADR-0001): what moved and how it was proved unchanged | Reviewing the restructure |

## 3. What to use in the code

| Document | Purpose | Read it when |
|---|---|---|
| `REUSE_CATALOGUE.md` | Each capability and the module to import (never rewrite one) | Before writing new code |
| `FEATURE_MAP.md` | **Generated** (`kx map`): every contract, event, route and job and where it is implemented | Tracing a route or an event to its code |
| (repo root) `CONTRACTS.md`, `CLAUDE.md` | Package rules, shared helpers, hard safety rules; the guide for the AI coding assistant | Before any change |

## 4. Plans, decisions and progress

| Document | Purpose | Read it when |
|---|---|---|
| `V1_CHANGE_PLAN.md` | The work lists and the decisions: 5 Nov (WP1–12), 11 Nov (WP13–29, D17–D40), 16 Nov (WP-V2, D41–D53), 19 Nov (WP-C0–C9, D54–D61), with each WP's state | What was decided and why; what is next |
| `V1_PROGRESS.md` | The work log, newest at the end: what each work package changed and how it was tested | Checking that something is done and tested |
| `OPEN_QUESTIONS.md` | Every open question with the choice made meanwhile and where in the code (#1–#96; newest at the top) | Before a client meeting; before changing a default |

## 5. Running and shipping

| Document | Purpose | Read it when |
|---|---|---|
| `LOCAL_DEV.md` | Laptop setup, tests, the free version, the website against the local stack | Setting up a machine; running the live test |
| `DEPLOYMENT.md` | Services, run order, settings, first deploy, the website / CMS settings | Deploying |
| `GO_LIVE_CHECKLIST.md` | Accounts, DNS, server, templates, data, the live tests, go-live day | The weeks before go-live |

## 6. For the other developers and the tester: `handoff/` (mostly generated by `make handoff`)

| Document | For | Purpose |
|---|---|---|
| `handoff/README.md` | everyone | Who reads what; how to regenerate |
| `handoff/FRONTEND_GUIDE.md` / `.docx` | the website developer | Eight steps to connect the website (each with a check) |
| `handoff/WEBSITE_API.md` | the website developer | The AI assistant API: site actions, streaming, voice topics, what the live test found on the site; then every endpoint |
| `handoff/CMS_API.md` | the website developer, CMS editors' screens | The content model, how a publish reaches the site, preview, media; every endpoint |
| `handoff/CRM_API.md` | the CRM screens developer | The staff API: sign-in, conventions, every endpoint |
| `handoff/TESTER_HELPER.docx` | the QA tester | Setup, roles, 28 end-to-end scenarios, negative tests, the bug template |
| `handoff/postman/`, `handoff/openapi/` | tester, developers | Postman collections (one end-to-end, one per package) and environments; OpenAPI files for mocks |
| `handoff/website-adapter/arifa-site-actions.patch` | the website developer | The change the live test ran on the website (`git apply`) |
| `handoff/vectors/site-nav.json` | the website developer | The 65 navigation cases (phrase → page action) |
| `handoff/endpoints.json`, `handoff/build/` | generators | Data and builders for the Word guides |

## 7. History: `archive/`

Superseded documents kept for reference only (`archive/README.md`): the 5–16 Nov delivery note and session
context, the competing-builds results, the restructure's recon and step log. Do not review them; the documents
above replace them. Anything deleted from `docs/` is still in git history.
