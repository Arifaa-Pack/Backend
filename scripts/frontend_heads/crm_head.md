# CRM API: frontend hand-off

For the developer building the **CRM screens** (staff web app, desktop and mobile) against a mock first, then
the real API. Everything below is taken from the code (`packages/kx-core-crm/src/kx/crm/api/`,
`kx/automation/admin_api.py`) and the business flow (SRS §3, §7A, §14, §15, §20, §21). The endpoint reference
in the second half is **generated from the code** (FastAPI OpenAPI), so names, types and limits are exact.

Companion files in this folder:

* `openapi-crm.json`: the OpenAPI 3.1 document of every CRM endpoint (import into Postman, or mock it, below).
* `WEBSITE_API.md`: the public website API. `CMS.md`: the content side.

---

## 1. Working against a mock

```bash
# any OpenAPI mock server works; for example Prism:
npx @stoplight/prism-cli mock docs/frontend/openapi-crm.json --port 4010
# then point the app at http://localhost:4010 (the paths below start with /v1)
```

The mock returns example data shaped exactly like the real responses. Two things a mock cannot reproduce:
the login cookie (section 3) and the business refusals (section 6); build those screens from this document.

Real API locally: `make up && kx migrate && kx api` (see `README.md`), base URL `http://localhost:8000`.
Create the first user with `kx create-user --role admin --email you@example.com --name "You"`.

---

## 2. What the CRM is (core design)

The CRM is where Arifa's team works the deals the AI starts. The AI talks to buyers on WhatsApp, email and the
website; **the backend builds every quote** (§11.1) and **a person approves it** before anything priced goes
out. The screens never compute prices: they show what the API returns and send decisions back.

| Role (`role` in the token) | What they do (SRS §3) |
|---|---|
| `sales` | Owns assigned deals. Approves 1st, revised and final quotes on their deals. Handles change requests, payment, PO, order updates. Records WON / LOST. Sees **only their own deals** (and those of an away colleague they cover). |
| `manager` | Price list and catalogue, assigns / reassigns, below-minimum decisions, sees everything. |
| `md` | Sees everything, dashboards, can take over any deal. **The MD passes every check that allows `manager`.** |
| `admin` | Users, roles, settings, channels (WhatsApp numbers, templates), kill switch. |

Screens (SRS §20) and the endpoints behind them:

| Screen | Endpoints |
|---|---|
| 1 Login (two-step) | `POST /v1/auth/login`, `/v1/auth/mfa/totp`, `/v1/auth/mfa/email/send`, `/v1/auth/mfa/email/verify`, `/v1/auth/refresh`, `/v1/auth/logout`, `GET /v1/auth/me`, TOTP enrolment `/v1/auth/totp/*`, password `/v1/auth/password*` |
| 2 My inbox (SLA timers) | `GET /v1/inbox` (everyone's own items); managers also `GET /v1/approvals` |
| 3 Deals board | `GET /v1/deals` (filters: stage, owner, country, product, sku, value range, opted_out; cursor pages) |
| 4 Deal page | `GET /v1/deals/{id}` (with `ai_summary` at the top, §7A.2), `/timeline`, `/quotes`, `POST /stage`, `/reopen`, `/ai`, `/reassign`, `/takeover`, `/handback`, `/messages` (reply to the buyer), `/call` (click-to-call), `/payments`, `/won`, `/personal-contact-shared`, `PATCH /v1/deals/orders/{id}` |
| 5 Quote approval (§11.3) | `GET /v1/quote-versions/{id}`, `POST /v1/approvals/{id}/decision`, `POST /v1/deals/{id}/quotes`, `/quotes/revise`, `/quotes/manual` |
| Change requests (§13) | `GET /v1/change-requests`, `/{id}`, `POST /{id}/start`, `/revise`, `/resolve`, `/decline` |
| 6 Contacts and companies | `GET/PATCH /v1/contacts…`, `GET/POST/PATCH /v1/companies…` |
| 7 Catalogue and price list | **Product workbook** (main way, §5.1): `GET /v1/catalogue/workbook`, `POST /v1/catalogue/workbook/preview`, `POST /v1/catalogue/workbook/apply`. Single edits: `GET /v1/products`, `PATCH /v1/products/{id}`, price tiers `/v1/products/{id}/price-tiers`, `/v1/price-tiers/{id}`, history `/v1/products/{id}/history`, `/v1/price-list/history`. Older price-only Excel: `POST /v1/price-list/import`, `GET /v1/price-list/export` |
| 8 Stock | `GET/PUT /v1/stock` |
| 9 Orders | `GET /v1/orders`, `PATCH /v1/deals/orders/{id}` |
| 10 Dashboards (§21) | `GET /v1/dashboards/salesperson`, `/manager`, `/md`, `GET /v1/dashboard/summary` |
| 11 Settings | `GET/PUT /v1/settings…`, `PUT /v1/feature-flags/{key}`, FX `GET/POST /v1/fx-rates`, users `/v1/users…`, availability `PUT /v1/users/{id}/availability`, WhatsApp numbers `/v1/admin/channel-accounts`, templates `/v1/admin/message-templates`, kill switch `/v1/admin/kill-switch`, material prices `/v1/price-proposals…`, `/v1/materials/freshness` |
| 11b Message texts (§5.2) | `GET /v1/texts`, `PUT /v1/texts/{key}`, `POST /v1/texts/{key}/reset`, `GET /v1/texts/{key}/history`, `POST /v1/texts/translate-missing`; WhatsApp / email templates with versions `GET/PUT /v1/admin/message-templates`, `POST /v1/admin/message-templates/{id}/status` |
| 11c Company details (§5.3) | `GET /v1/company`, `PATCH /v1/company`, `PUT /v1/company/logo`, `DELETE /v1/company/logo` |
| 12 Exports | `GET /v1/exports/{deals|contacts|companies|orders|products}?format=csv|xlsx` |

Deal stages (`stage`): `NEW`, `QUALIFIED`, `QUOTATION`, `NEGOTIATION`, `READY_TO_CLOSE`, `PAYMENT`, `WON`, `LOST`.
LOST reasons (`reason` when moving to LOST): `price_too_high`, `delivery_not_suitable`, `bought_competitor`,
`product_unavailable`, `no_response_postponed`, `budget`, `duplicate`, `other`, `opted_out`.

Rules the screens must respect:

* **Approvals, price-list edits, settings, payments, WON, reopen** need a session signed in with the
  authenticator app (`mfa=true` in the login answer). An email-code session gets `403` there: show "sign in
  with your authenticator app to approve".
* **No cost or margin on the v1 screens** (§3: "See cost and margin data: not in v1"). The endpoint
  `GET /v1/quote-versions/{id}/costs` exists for the MD only; leave it out of v1 screens unless asked.
* **Opted out** (`opted_out_at` set on the deal): show the badge; the API refuses messages to that buyer.
* **Optimistic concurrency**: stage moves send the `expected_version` they displayed; `409` = someone changed
  the deal, reload it.
* **After hand-over (§14)** the salesperson replies from the deal page on the same company number:
  `POST /v1/deals/{id}/messages` with a `client_key` (a UUID you generate per message, so a double tap sends
  once). A `422` with `reason: "window_closed"` means WhatsApp's 24-hour window has closed: offer the button
  "Send the re-open message" which resends with `reopen_template: true`. A `422` with
  `reason: "unapproved_price"` means the text names a price that is not in an approved quote (`amounts` lists
  them): tell the salesperson to revise the quote instead.
* **Click-to-call** rings the salesperson's own phone first, then the buyer, from the company number. Needs
  the salesperson's phone in their profile (`422 no_staff_phone`); refused if the buyer said "don't call me"
  (`422 opt_out`).

---

## 3. Sign-in, tokens, sessions (D26)

1. `POST /v1/auth/login {email, password}` -> `{challenge_token, methods, totp_enrolled, expires_at}`.
   `methods` is `["totp","email"]` once the authenticator is set up, `["email"]` before.
2. Second step, either:
   * `POST /v1/auth/mfa/totp {challenge_token, code}` -> tokens with `mfa: true`; or
   * `POST /v1/auth/mfa/email/send {challenge_token}` (204; a 6-digit code goes to the work email, valid
     10 minutes), then `POST /v1/auth/mfa/email/verify {challenge_token, code}` -> tokens with `mfa: false`.
3. Answer (`TokenOut`): `access_token` (RS256 JWT, 15 minutes), `expires_in`, `mfa`, `amr`,
   `must_change_password`, `totp_enrolled`, `user`. Send `Authorization: Bearer <access_token>` on every call.
4. The refresh token is set as an **HttpOnly cookie `kx_refresh`** (path `/v1/auth`, SameSite=Strict, Secure
   in production). Call `POST /v1/auth/refresh` (with `credentials: "include"`) before the access token expires;
   each refresh **rotates** the cookie. A reused old refresh token ends every session of that user: on `401`
   from refresh, send the user to the login screen. Non-browser clients may send `{"refresh_token": ...}`.
   Security review M2 (19 Nov): every `/v1/auth/*` call from a browser must come from a CRM origin
   (`KX_APP_CORS_ORIGINS`, or `KX_AUTH__CRM_ORIGINS` when set); any other `Origin` gets `403`. CORS with
   credentials is given only to these CRM origins. The public website's origin belongs in
   `KX_APP_CORS_PUBLIC_ORIGINS`: it may call `/v1/public/*` without credentials and nothing else, so never
   list the website in `KX_APP_CORS_ORIGINS`.
5. `must_change_password: true` (a new user's one-time password): force `POST /v1/auth/password` first.
   Until then every route outside `/v1/auth/*` answers `403` "change your one-time password first" (security
   review L7, 19 Nov). Changing the password ends every other session; the one in use stays signed in.
   Security review L1: an access token works only while its sign-in lives. After sign-out, a password reset,
   an MFA reset, a deactivation or a role change, calls answer `401` "your session has ended; sign in again"
   within about 10 seconds: send the user to the login screen. A session ends 12 hours after sign-in
   however often it is refreshed.
   `totp_enrolled: false`: offer `POST /v1/auth/totp/enrol` (returns `secret` + `otpauth_uri` to show as a QR
   code), then `POST /v1/auth/totp/confirm {code}`; the next sign-in can use the authenticator.
6. 5 wrong passwords or codes from one IP lock that IP out of the account for 15 minutes (`429`); 50 from all
   IPs together lock the account (security review M3, 19 Nov). Sign-in, the email code and password recovery are
   rate limited per IP and per typed address (`429`, the same for unknown addresses; show "too many attempts,
   try again in a few minutes"). Forgotten password: `POST /v1/auth/password/reset/start {email}` (`202`;
   at most 3 an hour per address), then `/reset/finish {email, code, new_password}`. Only the newest code works;
   5 wrong codes lock recovery for 15 minutes. Limits: `KX_AUTH__MAX_FAILED_ATTEMPTS`,
   `KX_AUTH__ACCOUNT_LOCK_FAILURES`, `KX_AUTH__RESET_MAX_WRONG_CODES`, `KX_AUTH__RATE_<ROUTE>__BURST` /
   `__PER_MINUTE` (`kx.core.settings.AuthSettings`).

Public key for verifying tokens elsewhere: `GET /.well-known/jwks.json`.

---

## 4. Conventions

* Base path `/v1`; JSON in and out; UTF-8. IDs are UUIDs. Times are ISO 8601 in UTC (`...Z` / `+00:00`);
  show them in the user's time zone (Arifa: Asia/Dubai). Dates are `YYYY-MM-DD`.
* **Money is a decimal string** (`"12500.00"`), never a float; keep it as a string or use a decimal library.
  Currency codes are ISO 4217 (`AED`, `USD`, `SAR` ...).
* Lists that can grow are **cursor pages**: `{items: [...], next_cursor: "..." | null}`; pass `cursor` and
  `limit` (1-100) to get the next page. Smaller lists are plain arrays.
* There is no push channel to the CRM in v1: refresh the inbox and the open deal by polling (for example every
  30 seconds while visible). Staff also get WhatsApp / email alerts (§16).
* **Unverified emails** (security review M8, 19 Nov): an inbound email whose sender failed SPF / DKIM / DMARC
  is shown on the deal its address claims as timeline kind `message_in_unverified` (summary starts
  "Sender not verified:", no contact attached) and in `GET /v1/inbox` as kind `unverified_email` for 7 days.
  Show it with a clear "sender not verified" warning. The AI did not answer it and nothing changed
  automatically (no opt-out, no contact update); the manager and the deal's salesperson got an alert.

---

## 5. Exports and files

* `GET /v1/exports/{list}?format=csv|xlsx` returns the file (`Content-Disposition: attachment`).
* `GET /v1/price-list/export` returns the price list as `.xlsx`. `POST /v1/price-list/import` takes the
  `.xlsx` file itself as the request body (columns `sku, min_qty, max_qty, list_price, min_price, currency`).
  All rows are checked first: any error saves nothing and the answer lists the row errors (§10).
* Quote PDFs are produced and sent by the backend; the deal page shows them through the quote versions.

### 5.1 Product workbook: one Excel file for products, prices and website details (18 Nov)

Client decision: staff keep the whole catalogue in **one workbook**. One upload updates the CRM (prices, tiers,
MOQ, delivery days) **and** the CMS (product details as **drafts**; a manager previews and publishes them in the
CMS, then the website changes). Prices never go to the CMS. Who: **manager, MD, admin**, signed in with the
authenticator app (`403` otherwise).

Screen "Product workbook" (under Catalogue), three buttons and one result panel:

1. **Download current catalogue**: `GET /v1/catalogue/workbook` returns `product-workbook.xlsx` with the sheets
   *How to fill*, *Products*, *Price tiers*, *Categories*, already filled with today's data. Staff change
   cells; they never start from an empty sheet.
2. **Upload**: `POST /v1/catalogue/workbook/preview` with the `.xlsx` file itself as the request body
   (`Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`, max 10 MB). **Saves
   nothing.** Show the answer:
   * `summary`: new / updated / unchanged products, price lists changed, categories changed, website drafts.
   * `errors[]` (`sheet`, `row`, `column`, `message`): a table "Sheet / Row / Column / Problem". If `ok` is
     false, the Apply button stays disabled: staff fix the file and upload again.
   * `warnings[]`: show, but Apply is allowed (e.g. "BOX-1 has no price tiers: it cannot be quoted yet").
   * `products[]`: per product `code`, `is_new`, `changes[]` (`field`, `before`, `after`) and `website_draft`
     (this product's details will be saved in the CMS as a draft). Show old -> new side by side.
   * `price_lists[]`: per product the tiers `before` and `after` as rows `[from qty, to qty, list price,
     minimum price]` (strings, exact; `null` = no upper limit / no minimum).
   * `categories[]`.
3. **Apply**: `POST /v1/catalogue/workbook/apply?token=<token from the preview>` with **the same file** as the
   body. Saves everything or nothing and answers the counts. `409` = the catalogue changed since the preview
   (someone else saved, or another file was sent): upload again to get a new preview. `422` = the file has
   errors.

After Apply show: "Saved. N product(s) were sent to the website as drafts: open the CMS, preview and publish
them." Keep the uploaded file in browser memory between preview and apply (do not ask the user to pick it
twice).

Rules the screen can repeat to staff (they are also in the *How to fill* sheet): a blank cell keeps the current
value and `-` clears it; never change a code; a product missing from the file is kept (Active = No stops
selling it); in *Price tiers* list all tiers of a product, a product not in that sheet keeps its prices; no
prices in text columns; photos and videos are added in the CMS.

History: every workbook upload is in `GET /v1/price-list/history` (action `catalogue.workbook_applied`) and each
product's changes in `GET /v1/products/{id}/history` (actions `product.created`, `product.updated`,
`price_tier.created`). The MD reads these on the price-list history screen.


### 5.2 Message texts and templates: editable by the manager and the MD (18 Nov)

Client decisions: the **manager** edits the wording of the AI's fixed phrases, the buyer messages that carry
quotes and summaries, and the staff alerts; the **MD** may edit too and sees every change; staff write
**English only** and the AI translates the buyer and AI texts into Arabic, Hindi, Tamil, Chinese and Sinhala.
Legal and opt-out wording is not editable and not listed.

Screen "Message texts" (Settings), grouped by `group`: `ai` (what the AI says in fixed situations), `buyer`
(quote and summary messages), `staff` (alerts, English only).

* `GET /v1/texts` (manager, MD, admin): per text `key`, `note` (where it is used: show it), `en` (in use),
  `builtin_en`, `placeholders` and `languages[]` with `status`: `built-in`, `edited`, `translated`,
  `outdated` (the AI has not translated the new English yet: the previous text is still sent) or `missing`
  (English is sent in that language).
* **Edit** (manager / MD, authenticator sign-in): `PUT /v1/texts/{key}` `{"text": "..."}`. Live within 30
  seconds. Show the placeholders as chips the user must keep, e.g. `{name}`, `{hours}`; a `422` with
  `reason: "invalid_text"` lists the problem in `detail` (a placeholder missing or unknown, or an amount with a
  currency: prices only ever come from the approved quote). After saving buyer / AI texts show "Translating
  into 5 languages..." and refresh the list a minute later.
* **Undo**: `POST /v1/texts/{key}/reset` brings back the built-in wording; or open the history and save an
  older text again.
* **History** (the MD's view): `GET /v1/texts/{key}/history`: who, when, old and new text, translations.
* "Translate all missing languages" button: `POST /v1/texts/translate-missing` (manager / MD).

**WhatsApp / email templates** (`/v1/admin/message-templates`, manager, MD, admin with the authenticator app):
every edit is a version. Editing the **live** WhatsApp template creates a new version (`provider_status:
submitted`, `provider_name` e.g. `quote_ready_v2`, the name to submit to Meta); the live version (`live: true`)
keeps being sent until Meta approves the new one, then record Meta's decision with `POST
/v1/admin/message-templates/{id}/status` `{"provider_status": "approved"}`: it becomes live and the previous
one `superseded`. A version not yet approved is changed in place. Email templates go live at once. List with
`?current_only=true` for the main table and without it for the version history.


### 5.3 Company details: letterhead, logo and bank details on quotes (18 Nov)

Client decision: **admin, MD and manager** change the company details printed on quotes and Requirement
Summaries (authenticator sign-in to save). New documents use them within 30 seconds; quotes already sent keep
their PDF.

* `GET /v1/company`: `saved` (what staff set; an empty field means "use the default from company.yaml"),
  `printed` (what quotes print now) and `has_logo`. Show `printed` as a live preview next to the form.
* `PATCH /v1/company` with only the changed fields: `name`, `address`, `trn`, `phone`, `email` and `bank`
  {`account_name`, `bank_name`, `iban`, `swift`}. `""` clears a field. `422` with `reason: "invalid_company"`
  and `errors[]` (field + message): TRN must have 15 digits, the IBAN is checked (checksum), SWIFT 8 or 11
  characters, email and phone format. Show each message under its field.
* Logo: `PUT /v1/company/logo` with the PNG or JPEG file itself as the body (`Content-Type: image/png` or
  `image/jpeg`, max 200 KB); `DELETE /v1/company/logo` removes it.
* **Bank details:** changing any bank field sends an alert to the MD and the manager (payment-fraud
  protection). Before saving, show a confirmation: "Buyers will pay to this account. The MD and manager will be
  notified." Every change is in the settings history.


### 5.4 FX rates are automatic (18 Nov)

A daily job (06:05 Dubai) stores the rate of each quote currency (`source` = `auto:open-er-api`). The FX
screen (`GET /v1/fx-rates`) shows `source` and `as_of`; label automatic rates "automatic" and manual ones
"entered by manager". `POST /v1/fx-rates` still sets the manager's rate for a day, and an automatic update never
replaces it. If a rate moves more than 10 % in a day it is not stored and the manager gets an alert; quotes
then use the last rate (its date is printed on the quote).

---

## 6. Errors

Business errors are RFC 9457 `application/problem+json`:

```json
{
  "type": "https://errors.kairox.ai/rule_violation",
  "title": "Business rule violated",
  "status": 422,
  "detail": "a price that is not in an approved quote cannot be sent; revise the quote instead",
  "instance": "/v1/deals/0190.../messages",
  "reason": "unapproved_price",
  "amounts": ["AED 9.50"]
}
```

| Status | `type` ends with | Meaning for the screen |
|---|---|---|
| 401 | `unauthorized` | Not signed in / token expired: refresh, else go to login |
| 403 | `forbidden` | Wrong role, someone else's deal, MFA needed, or the MD has taken the deal over |
| 404 | `not_found` | Gone or not visible to this user |
| 409 | `conflict` | Changed by someone else: reload |
| 422 | `rule_violation` | A business rule refused it: show `detail`; switch on `reason` where listed above |
| 429 | `too_many_requests` | Locked out / rate-limited: wait |
| 503 | `unavailable` | A dependency (email, calls) is down: try again later |

Request-shape errors (a missing or wrong field) come from the framework as `422` with
`{"detail": [{"loc": [...], "msg": "...", "type": "..."}]}`: show them next to the fields.

---
