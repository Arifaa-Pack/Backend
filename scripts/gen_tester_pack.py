# ruff: noqa: E501, S105, S106, PLR0915 - a build script: long JavaScript and description strings, dummy
# secrets for the offline build, and a few long table-like routines.
"""Generate the manual-testing pack from the code (no database, no network). Run:

    python scripts/gen_tester_pack.py            # writes docs/testing/postman/* and docs/testing/endpoints.json

Builds EVERY router the API process mounts (arifaa_pack.api: CRM under /v1, channel webhooks, the unsubscribe
page, the CMS webhook, the website chat API, channels admin, JWKS and the health checks) with dummy
dependencies, exports one OpenAPI document (hidden routes included) and turns it into:

* postman/Kairox.postman_collection.json: a Postman v2.1 collection, one request per route, in a test order,
  with example bodies, role notes and pm.test scripts that keep the ids a tester needs in the environment;
* postman/Kairox-local.postman_environment.json and Kairox-staging.postman_environment.json;
* endpoints.json: the same routes as data, read by docs/testing/build_guide.js for the guide's reference table.

Signed buyer-side requests (WhatsApp, CMS, Telnyx) sign themselves in a pre-request script from secrets held
only in the environment file. Who may call a route is read from the route's own auth dependency
(require_role / require_human_mfa closures), like scripts/gen_frontend_docs.py.
"""

import json
import pathlib
import re
import sys
from typing import Any

from fastapi import FastAPI, Request
from fastapi.routing import APIRoute

from kx.agents.website import WebsiteDeps
from kx.agents.website import build_router as website_router
from kx.automation.admin_api import build_router as channels_admin_router
from kx.automation.cms import build_router as cms_router
from kx.automation.inbound.webhooks import WebhookDeps
from kx.automation.inbound.webhooks import build_router as webhook_router
from kx.automation.optout.unsubscribe import UnsubscribeDeps
from kx.automation.optout.unsubscribe import build_router as unsubscribe_router
from kx.automation.settings import AutomationSettings
from kx.core.auth import Role
from kx.core.errors import install_problem_handlers
from kx.crm import CrmDeps, Letterhead
from kx.crm import build_router as crm_router

HERE = pathlib.Path(__file__).resolve().parent
OUT = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else HERE.parent / "docs" / "testing"
POSTMAN = OUT / "postman"
SCHEMA_V21 = "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"

# The live-test stack's own WhatsApp app secret (live-test/compose/live.yml + live-test/scripts/kxclient.py).
# TEST ONLY: it signs fake buyer messages for the developer's local stack and is worthless anywhere else.
LOCAL_WA_TEST_SECRET = "live-wa-secret"


# ---------------------------------------------------------------- offline app (dummy dependencies)
class _Dummy:
    def __getattr__(self, name: str) -> Any:
        raise RuntimeError("offline build")


class _Dbs(_Dummy):
    def dependency(self, db: Any) -> Any:
        async def session() -> Any:  # never called: the app is only built to export OpenAPI
            yield None

        return session


def full_app() -> FastAPI:
    """The same routers, in the same order, as arifaa_pack.api.create_app's lifespan."""
    app = FastAPI(title="Kairox platform API (all routes)", version="1.0.0")
    install_problem_handlers(app)
    auto = AutomationSettings.model_construct()  # defaults only: never reads the environment or a .env
    crm_deps = CrmDeps(uploader=_Dummy(), letterhead=Letterhead(name="Arifaa Pack", address=""))  # type: ignore[arg-type]
    app.include_router(crm_router(_Dbs(), crm_deps), prefix="/v1")  # type: ignore[arg-type]
    app.include_router(
        webhook_router(WebhookDeps(databases=_Dummy(), settings=auto, scheduler=_Dummy(), voice=_Dummy()))  # type: ignore[arg-type]
    )
    app.include_router(unsubscribe_router(UnsubscribeDeps(_Dummy(), auto, "x")))  # type: ignore[arg-type]
    app.include_router(cms_router(_Dummy(), "x"))  # type: ignore[arg-type]
    web = WebsiteDeps(
        databases=_Dummy(),  # type: ignore[arg-type]
        service=_Dummy(),  # type: ignore[arg-type]
        gateway=_Dummy(),  # type: ignore[arg-type]
        secret="x",
        human_check=None,
        limits=_Dummy(),  # type: ignore[arg-type]
        ai_budget_left=_Dummy(),  # type: ignore[arg-type]
    )
    app.include_router(website_router(web))
    app.include_router(channels_admin_router(_Dummy()))  # type: ignore[arg-type]

    @app.get("/healthz", tags=["health"])
    async def healthz() -> dict[str, str]:
        """Liveness: the API process answers."""
        return {"status": "ok"}

    @app.get("/healthz/beat", tags=["health"])
    async def healthz_beat(request: Request) -> dict[str, str]:
        """D46: UptimeRobot checks this from outside; 503 = Beat, the broker or the workers stopped (or the
        oldest due job is waiting too long)."""
        return {"status": "ok"}

    @app.get("/.well-known/jwks.json", tags=["auth"])
    async def jwks() -> dict[str, Any]:
        """The public key that verifies staff access tokens (RS256, D26)."""
        return {}

    return app


def flat_routes(items: Any, prefix: str = "") -> list[tuple[str, APIRoute]]:
    """FastAPI keeps included routers nested (`_IncludedRouter`): walk them, adding each prefix."""
    out: list[tuple[str, APIRoute]] = []
    for item in items:
        if isinstance(item, APIRoute):
            out.append((prefix + item.path, item))
        elif hasattr(item, "original_router"):
            out += flat_routes(item.original_router.routes, prefix + item.include_context.prefix)
    return out


def show_hidden(items: Any) -> None:
    """Webhooks and the unsubscribe page are `include_in_schema=False` in production; the pack covers them."""
    for item in items:
        if isinstance(item, APIRoute):
            item.include_in_schema = True
        elif hasattr(item, "original_router"):
            item.original_router.include_in_schema = True
            show_hidden(item.original_router.routes)


def roles_of(route: APIRoute) -> tuple[list[str], bool, bool]:
    """(roles, needs_mfa, signed_in) from the auth dependency closures (MD passes wherever MANAGER is listed)."""
    roles: list[str] = []
    mfa = False
    signed = False

    def visit(dep: Any) -> None:
        nonlocal mfa, signed
        call = getattr(dep, "call", None)
        qual = getattr(call, "__qualname__", "")
        if qual.startswith(("require_role.", "require_human_mfa.")):
            signed = True
            for cell in call.__closure__ or ():
                value = cell.cell_contents
                if isinstance(value, tuple) and all(isinstance(r, Role) for r in value):
                    roles.extend(r.value for r in value)
            if qual.startswith("require_human_mfa."):
                mfa = True
        elif qual == "current_principal":
            signed = True
        for sub in getattr(dep, "dependencies", []):
            visit(sub)

    visit(route.dependant)
    names = list(dict.fromkeys(roles))
    if "manager" in names and "md" not in names:
        names.append("md")
    return names, mfa, signed


# ---------------------------------------------------------------- folders, names, variables
FOLDERS: list[tuple[str, str]] = [
    ("00 Health and keys", "Is the server up? Run these first on every target."),
    (
        "01 Staff sign-in and MFA",
        "Sign in with email + password, then a second step. First sign-in: email code, change the "
        "one-time password, enrol YOUR OWN authenticator app, then sign in again with the app.",
    ),
    (
        "02 Users and admin settings",
        "Staff accounts, availability, settings, kill switch, company details, message texts.",
    ),
    (
        "03 Contacts, companies and deals",
        "The CRM records a buyer conversation creates. Run 'List deals' after each buyer test.",
    ),
    (
        "04 Products, stock and price list",
        "Catalogue, price tiers (list price + minimum per quantity), stock, FX rates.",
    ),
    (
        "05 Quotes, approvals and change requests",
        "The backend builds every quote; staff approve it before it is sent.",
    ),
    ("06 Payment, won and orders", "After the final quote: payment, WON, order tracking."),
    ("07 Dashboards and exports", "Dashboards per role and CSV / XLSX exports."),
    ("08 Channels admin", "Company WhatsApp numbers / mailboxes and message templates."),
    (
        "09 Buyer simulation - WhatsApp",
        "Pretend to be a buyer on WhatsApp. The request is signed automatically from wa_app_secret.",
    ),
    ("10 Buyer simulation - Website chat", "Pretend to be a website visitor (public API, no staff login)."),
    ("11 CMS publish webhook", "Pretend to be the website CMS publishing a product. Signed automatically."),
    ("12 Unsubscribe link", "The one-click unsubscribe link printed in every buyer email."),
    (
        "13 Telnyx call webhooks",
        "Call-status callbacks from the phone provider. Signed automatically (URL token).",
    ),
    (
        "14 Negative and security tests",
        "Requests that MUST be refused. A pass here means the server said no.",
    ),
]


def folder_of(method: str, path: str) -> str:
    f = [name for name, _ in FOLDERS]
    if path.startswith(("/healthz", "/.well-known")):
        return f[0]
    if path.startswith("/v1/auth"):
        return f[1]
    if path.startswith("/v1/public/unsubscribe"):
        return f[12]
    if path.startswith("/v1/public"):
        return f[10]
    if path.startswith("/webhooks/whatsapp"):
        return f[9]
    if path.startswith("/webhooks/cms"):
        return f[11]
    if path.startswith("/webhooks/telnyx"):
        return f[13]
    if path.startswith("/v1/admin/channel-accounts") or path.startswith("/v1/admin/message-templates"):
        return f[8]
    if path.startswith(
        ("/v1/users", "/v1/settings", "/v1/feature-flags", "/v1/admin", "/v1/company", "/v1/texts")
    ):
        return f[2]
    if path.startswith(("/v1/dashboard", "/v1/exports")):
        return f[7]
    if path.endswith(("/payments", "/won")) or "/orders" in path:
        return f[6]
    if (
        "/quotes" in path
        or path.startswith(("/v1/quote-versions", "/v1/approvals", "/v1/change-requests"))
        or path.endswith("/offer")
    ):
        return f[5]
    if path.startswith(
        (
            "/v1/products",
            "/v1/price-tiers",
            "/v1/price-list",
            "/v1/stock",
            "/v1/catalogue",
            "/v1/price-proposals",
            "/v1/materials",
            "/v1/fx-rates",
        )
    ):
        return f[4]
    return f[3]


# Path parameter -> environment variable (the same name, except where one name means different things).
PATH_VARS: dict[tuple[str, str], str] = {
    ("/v1/settings/{key}", "key"): "setting_key",
    ("/v1/feature-flags/{key}", "key"): "flag_key",
    ("/v1/exports/{entity}", "entity"): "export_entity",
}


def path_var(path: str, name: str) -> str:
    if name == "key" and path.startswith("/v1/texts"):
        return "text_key"
    return PATH_VARS.get((path, name), name)


NAMES: dict[tuple[str, str], str] = {
    ("POST", "/v1/auth/login"): "1. Login (email + password)",
    ("POST", "/v1/auth/mfa/email/send"): "2. Send the email sign-in code",
    ("POST", "/v1/auth/mfa/email/verify"): "3. Verify the email code (first sign-in / fallback)",
    ("POST", "/v1/auth/password"): "4. Change password (first sign-in: replace the one-time password)",
    ("POST", "/v1/auth/totp/enrol"): "5. Enrol your authenticator app (shows a QR code)",
    ("POST", "/v1/auth/totp/confirm"): "6. Confirm the authenticator code",
    ("POST", "/v1/auth/mfa/totp"): "7. Second step with the authenticator code (mfa=true)",
    ("GET", "/v1/auth/me"): "8. Who am I",
    ("POST", "/v1/auth/refresh"): "9. Refresh the access token",
    ("POST", "/v1/auth/logout"): "10. Logout",
    ("POST", "/v1/auth/password/reset/start"): "11. Forgot password - send reset code",
    ("POST", "/v1/auth/password/reset/finish"): "12. Forgot password - set new password",
    ("GET", "/webhooks/whatsapp"): "Meta webhook verification (hub.challenge)",
    ("POST", "/webhooks/whatsapp"): "Buyer sends a WhatsApp message (signed)",
    ("POST", "/webhooks/cms"): "CMS publishes a product (signed)",
    ("GET", "/v1/public/unsubscribe"): "Open the unsubscribe page (changes nothing)",
    ("POST", "/v1/public/unsubscribe"): "Press Unsubscribe (stops every channel)",
    ("POST", "/webhooks/telnyx/gather/{attempt_id}"): "Alert call - staff pressed a key (signed URL)",
    ("POST", "/webhooks/telnyx/status/{attempt_id}"): "Alert call - status update (signed URL)",
    ("POST", "/v1/public/sessions"): "1. Start a website chat session",
    ("POST", "/v1/public/sessions/{session_id}/messages"): "2. Visitor sends a chat message",
    ("POST", "/v1/public/sessions/{session_id}/identify"): "3. Visitor types phone / email (identify)",
    ("POST", "/v1/public/sessions/{session_id}/voice"): "4. Ask for a live-voice room token",
    ("GET", "/v1/deals"): "List deals (newest first; saves deal_id)",
    ("GET", "/v1/inbox"): "My inbox (approvals, change requests, tasks; saves approval_id / cr_id)",
    ("POST", "/v1/deals/{deal_id}/quotes"): "Draft a quote (the backend prices it)",
    ("POST", "/v1/deals/{deal_id}/quotes/revise"): "Revise the quote (prices, quantity, currency)",
    ("POST", "/v1/deals/{deal_id}/quotes/manual"): "Hand-priced quote (custom / unpriced item)",
    ("POST", "/v1/approvals/{approval_id}/decision"): "Approve / reject a quote or special price (MFA)",
    ("POST", "/v1/deals/{deal_id}/offer"): "Manager makes a price offer (MFA)",
    ("GET", "/healthz"): "Health check (API up)",
    ("GET", "/healthz/beat"): "Health check (scheduler and workers)",
    ("GET", "/.well-known/jwks.json"): "Public key for staff tokens (JWKS)",
    ("GET", "/v1/users"): "List staff users (saves user_id / sales_user_id)",
    ("POST", "/v1/users"): "Create a staff user (MFA)",
    ("PATCH", "/v1/users/{user_id}"): "Edit a staff user (MFA)",
    ("POST", "/v1/users/{user_id}/reset-mfa"): "Reset a user's authenticator (lost phone, MFA)",
    ("POST", "/v1/users/{user_id}/unlock"): "Unlock a locked-out user (MFA)",
    ("GET", "/v1/settings"): "List business settings",
    ("PUT", "/v1/settings/{key}"): "Change a business setting (MFA)",
    ("PUT", "/v1/feature-flags/{key}"): "Switch a feature flag (MFA)",
    ("PUT", "/v1/users/{user_id}/availability"): "Set away / backup colleague",
    ("GET", "/v1/company"): "Company details for quotes",
    ("PATCH", "/v1/company"): "Edit company details (MFA)",
    ("PUT", "/v1/company/logo"): "Upload the quote logo (MFA)",
    ("DELETE", "/v1/company/logo"): "Remove the quote logo (MFA)",
    ("GET", "/v1/admin/kill-switch"): "Kill switch - read level",
    ("PUT", "/v1/admin/kill-switch"): "Kill switch - set level (MFA)",
    ("GET", "/v1/texts"): "List message texts (saves text_key)",
    ("PUT", "/v1/texts/{key}"): "Edit a message text (MFA)",
    ("POST", "/v1/texts/{key}/reset"): "Reset a message text to default (MFA)",
    ("GET", "/v1/texts/{key}/history"): "Message text history",
    ("POST", "/v1/texts/translate-missing"): "Translate missing texts (MFA)",
    ("GET", "/v1/deals/{deal_id}"): "Get deal (saves deal_version, contact_id)",
    ("GET", "/v1/deals/{deal_id}/timeline"): "Deal timeline (messages, AI replies, stage changes)",
    ("POST", "/v1/deals/{deal_id}/stage"): "Move deal stage (example: LOST)",
    ("POST", "/v1/deals/{deal_id}/ai"): "Switch the AI on / off for a deal",
    ("POST", "/v1/deals/{deal_id}/reopen"): "Reopen a closed deal (MFA)",
    ("POST", "/v1/deals/{deal_id}/personal-contact-shared"): "Record: salesperson shared a personal contact",
    ("POST", "/v1/deals/{deal_id}/messages"): "Staff sends a message to the buyer",
    ("POST", "/v1/deals/{deal_id}/call"): "Click-to-call the buyer (saves attempt_id)",
    ("POST", "/v1/deals/{deal_id}/opt-out"): "Record a buyer opt-out by hand",
    ("POST", "/v1/deals/{deal_id}/reassign"): "Reassign the deal to a salesperson",
    ("POST", "/v1/deals/{deal_id}/takeover"): "MD takes over the deal",
    ("POST", "/v1/deals/{deal_id}/handback"): "MD hands the deal back",
    ("GET", "/v1/contacts"): "Search contacts (by buyer_phone; saves contact_id)",
    ("GET", "/v1/contacts/{contact_id}"): "Get contact",
    ("PATCH", "/v1/contacts/{contact_id}"): "Edit contact",
    ("GET", "/v1/companies"): "List buyer companies",
    ("POST", "/v1/companies"): "Create a buyer company (saves company_id)",
    ("PATCH", "/v1/companies/{company_id}"): "Edit a buyer company",
    ("GET", "/v1/products"): "List products (saves product_id)",
    ("GET", "/v1/products/{product_id}"): "Get product",
    ("PATCH", "/v1/products/{product_id}"): "Edit product (MFA)",
    ("GET", "/v1/products/{product_id}/price-tiers"): "Price tiers of a product (saves tier_id)",
    ("POST", "/v1/products/{product_id}/price-tiers"): "Add a price tier (MFA)",
    ("PATCH", "/v1/price-tiers/{tier_id}"): "Edit a price tier (MFA)",
    ("DELETE", "/v1/price-tiers/{tier_id}"): "Delete a price tier (MFA)",
    ("POST", "/v1/price-list/import"): "Import the price list .xlsx (MFA)",
    ("GET", "/v1/price-list/export"): "Export the price list .xlsx",
    ("GET", "/v1/products/{product_id}/history"): "Price history of a product",
    ("GET", "/v1/price-list/history"): "Price list change log",
    ("GET", "/v1/stock"): "Stock table",
    ("PUT", "/v1/stock"): "Update stock",
    ("GET", "/v1/catalogue/workbook"): "Download the product workbook .xlsx (MFA)",
    (
        "POST",
        "/v1/catalogue/workbook/preview",
    ): "Preview a product workbook upload (MFA, saves workbook_token)",
    ("POST", "/v1/catalogue/workbook/apply"): "Apply the previewed workbook (MFA)",
    ("GET", "/v1/price-proposals"): "Material rate proposals (saves rate_id)",
    ("POST", "/v1/price-proposals/{rate_id}/approve"): "Approve a rate proposal (MFA)",
    ("POST", "/v1/price-proposals/{rate_id}/reject"): "Reject a rate proposal (MFA)",
    ("GET", "/v1/materials/freshness"): "How fresh the material rates are",
    ("GET", "/v1/fx-rates"): "Currency rates to AED",
    ("POST", "/v1/fx-rates"): "Set a currency rate (MFA)",
    ("GET", "/v1/deals/{deal_id}/quotes"): "Quote versions of the deal (saves version_id)",
    ("GET", "/v1/approvals"): "Open approvals (manager / MD; saves approval_id)",
    ("GET", "/v1/quote-versions/{version_id}"): "Get one quote version",
    ("GET", "/v1/quote-versions/{version_id}/costs"): "Cost view of a quote version (MD only)",
    ("GET", "/v1/change-requests"): "List change requests (saves cr_id)",
    ("GET", "/v1/change-requests/{cr_id}"): "Get a change request",
    ("POST", "/v1/change-requests/{cr_id}/start"): "Start work on a change request",
    ("POST", "/v1/change-requests/{cr_id}/revise"): "Revise the quote for a change request",
    ("POST", "/v1/change-requests/{cr_id}/resolve"): "Resolve a change request with a version",
    ("POST", "/v1/change-requests/{cr_id}/decline"): "Decline a change request",
    ("POST", "/v1/deals/{deal_id}/payments"): "Record a payment (MFA)",
    ("POST", "/v1/deals/{deal_id}/won"): "Mark the deal WON (MFA)",
    ("PATCH", "/v1/deals/orders/{order_id}"): "Update order status",
    ("GET", "/v1/orders"): "List orders (saves order_id)",
    ("GET", "/v1/dashboard/summary"): "Dashboard summary (manager / MD)",
    ("GET", "/v1/dashboards/salesperson"): "Salesperson dashboard",
    ("GET", "/v1/dashboards/manager"): "Manager dashboard",
    ("GET", "/v1/dashboards/md"): "MD dashboard",
    ("GET", "/v1/exports/{entity}"): "Export a list as CSV / XLSX",
    ("GET", "/v1/admin/channel-accounts"): "Company channel accounts (saves account_id)",
    ("PATCH", "/v1/admin/channel-accounts/{account_id}"): "Edit a channel account (admin, MFA)",
    ("GET", "/v1/admin/message-templates"): "Message templates (saves template_id)",
    ("PUT", "/v1/admin/message-templates"): "Create / update a message template (MFA)",
    ("POST", "/v1/admin/message-templates/{template_id}/status"): "Set a template's provider status (MFA)",
}
ROLE_ORDER = ("sales", "manager", "md", "admin")

# Order inside a folder (lower first); routes not listed keep the OpenAPI order after these.
PRIORITY: dict[tuple[str, str], int] = {
    ("GET", "/healthz"): 1,
    ("GET", "/healthz/beat"): 2,
    ("POST", "/v1/auth/login"): 1,
    ("POST", "/v1/auth/mfa/email/send"): 2,
    ("POST", "/v1/auth/mfa/email/verify"): 3,
    ("POST", "/v1/auth/password"): 4,
    ("POST", "/v1/auth/totp/enrol"): 5,
    ("POST", "/v1/auth/totp/confirm"): 6,
    ("POST", "/v1/auth/mfa/totp"): 7,
    ("GET", "/v1/auth/me"): 8,
    ("POST", "/v1/auth/refresh"): 9,
    ("POST", "/v1/auth/logout"): 10,
    ("POST", "/v1/auth/password/reset/start"): 11,
    ("POST", "/v1/auth/password/reset/finish"): 12,
    ("GET", "/v1/deals"): 1,
    ("GET", "/v1/inbox"): 2,
    ("GET", "/v1/deals/{deal_id}"): 3,
    ("GET", "/v1/deals/{deal_id}/timeline"): 4,
    ("GET", "/v1/products"): 1,
    ("GET", "/v1/deals/{deal_id}/quotes"): 1,
    ("POST", "/v1/deals/{deal_id}/quotes"): 2,
    ("GET", "/v1/approvals"): 3,
    ("POST", "/v1/approvals/{approval_id}/decision"): 4,
    ("GET", "/webhooks/whatsapp"): 2,
    ("POST", "/webhooks/whatsapp"): 1,
    ("GET", "/v1/public/unsubscribe"): 1,
    ("POST", "/v1/public/sessions"): 1,
    ("POST", "/v1/public/sessions/{session_id}/messages"): 2,
    ("POST", "/v1/public/sessions/{session_id}/identify"): 3,
    ("POST", "/v1/public/sessions/{session_id}/voice"): 4,
}

WHO_OVERRIDE = {
    "/webhooks/whatsapp": "Meta (WhatsApp Cloud API): header X-Hub-Signature-256 = HMAC-SHA256 of the raw body with the app secret",
    "/webhooks/cms": "The website CMS: headers X-Kx-Timestamp + X-Kx-Signature (HMAC-SHA256 of '<timestamp>.<body>'), max 5 minutes old",
    "/webhooks/telnyx": "Telnyx: signed per-call URL token ?t=... (plus an Ed25519 signature header when the Telnyx public key is configured)",
    "/v1/public/unsubscribe": "The buyer (anyone holding the signed link from an email)",
    "/v1/public/sessions": "A website visitor (Turnstile human check on staging/production)",
    "/v1/public/sessions/": "A website visitor: header Authorization: Bearer <session_token>",
    "/healthz": "public (monitoring)",
    "/.well-known": "public",
}

# ---------------------------------------------------------------- example request bodies
BODY: dict[tuple[str, str], Any] = {
    ("POST", "/v1/auth/login"): {"email": "{{staff_email}}", "password": "{{staff_password}}"},
    ("POST", "/v1/auth/mfa/totp"): {"challenge_token": "{{challenge_token}}", "code": "{{totp_code}}"},
    ("POST", "/v1/auth/mfa/email/send"): {"challenge_token": "{{challenge_token}}"},
    ("POST", "/v1/auth/mfa/email/verify"): {
        "challenge_token": "{{challenge_token}}",
        "code": "{{email_code}}",
    },
    ("POST", "/v1/auth/refresh"): {},
    ("POST", "/v1/auth/totp/confirm"): {"code": "{{totp_code}}"},
    ("POST", "/v1/auth/password"): {
        "current_password": "{{staff_password}}",
        "new_password": "{{new_password}}",
    },
    ("POST", "/v1/auth/password/reset/start"): {"email": "{{staff_email}}"},
    ("POST", "/v1/auth/password/reset/finish"): {
        "email": "{{staff_email}}",
        "code": "{{email_code}}",
        "new_password": "{{new_password}}",
    },
    ("POST", "/v1/users"): {
        "email": "{{new_user_email}}",
        "full_name": "QA Test Salesperson",
        "role": "sales",
        "phone_e164": "+971500000101",
        "break_glass": False,
    },
    ("PATCH", "/v1/users/{user_id}"): {
        "languages": ["en", "ar"],
        "countries": ["AE", "SA"],
        "alert_channels": ["email", "in_app"],
    },
    ("POST", "/v1/deals/{deal_id}/stage"): {
        "to": "LOST",
        "expected_version": "{{deal_version}}",
        "lost_reason": "duplicate",
        "lost_note": "QA test deal - closed by the tester",
    },
    ("POST", "/v1/deals/{deal_id}/quotes/revise"): {
        "lines": [{"line_no": 1, "quantity": "60000"}],
        "currency": "SAR",
        "delivery_time_text": "15 working days after artwork approval",
        "incoterm": "EXW",
        "cover_note": "Revised for 60,000 pcs as discussed.",
    },
    ("POST", "/v1/deals/{deal_id}/ai"): {"enabled": False, "reason": "human_takeover"},
    ("POST", "/v1/deals/{deal_id}/offer"): {"unit_price": "0.40", "resume_ai": True},
    ("POST", "/v1/deals/{deal_id}/reopen"): {"reason": "Buyer came back after the LOST decision (QA test)"},
    ("POST", "/v1/deals/{deal_id}/messages"): {
        "text": "Hello, this is your salesperson from Arifaa Pack. Your quote is on its way.",
        "client_key": "{{$guid}}",
        "channel": "whatsapp",
        "reopen_template": False,
    },
    ("POST", "/v1/deals/{deal_id}/call"): {"client_key": "{{$guid}}"},
    ("POST", "/v1/deals/{deal_id}/opt-out"): {
        "scope": "all",
        "words": "Please do not contact me again",
        "reason": "QA test: buyer asked by phone",
    },
    ("POST", "/v1/deals/{deal_id}/payments"): {
        "amount": "5000.00",
        "currency": "AED",
        "method": "bank transfer",
        "reference": "QA-TT-0001",
        "paid_on": "2026-11-19",
        "note": "Advance received (QA test)",
    },
    ("POST", "/v1/deals/{deal_id}/won"): {"expected_reorder_date": "2027-02-15"},
    ("PATCH", "/v1/deals/orders/{order_id}"): {
        "status": "shipped",
        "on_date": "2026-11-25",
        "tracking_ref": "QA-TRACK-001",
    },
    ("POST", "/v1/deals/{deal_id}/reassign"): {"user_id": "{{sales_user_id}}"},
    ("POST", "/v1/deals/{deal_id}/quotes/manual"): {
        "lines": [
            {
                "description": "Printed aluminium container 750 ml, 2-colour logo on lid",
                "quantity": "20000",
                "unit_price": "0.65",
                "unit": "pcs",
            }
        ],
        "delivery_time_text": "20 working days after artwork approval",
        "buyer_note": "Price includes printing plates.",
    },
    ("POST", "/v1/approvals/{approval_id}/decision"): {"decision": "approve", "note": "Checked by QA"},
    ("POST", "/v1/change-requests/{cr_id}/revise"): {
        "lines": [],
        "delivery_time_text": "10 working days (express, as the buyer asked)",
    },
    ("POST", "/v1/change-requests/{cr_id}/resolve"): {"version_id": "{{version_id}}"},
    ("POST", "/v1/change-requests/{cr_id}/decline"): {
        "reason": "Express production is not possible this month."
    },
    ("POST", "/v1/products/{product_id}/price-tiers"): {
        "min_qty": "100000",
        "max_qty": None,
        "list_price": "0.38",
        "min_price": "0.33",
    },
    ("PATCH", "/v1/price-tiers/{tier_id}"): {"list_price": "0.39", "min_price": "0.34"},
    ("PUT", "/v1/stock"): {"product_id": "{{product_id}}", "quantity": "120000", "unit": "pcs"},
    ("POST", "/v1/price-proposals/{rate_id}/approve"): {"note": "Approved by QA"},
    ("POST", "/v1/price-proposals/{rate_id}/reject"): {"note": "Rejected by QA"},
    ("PUT", "/v1/settings/{key}"): {"value": "{{setting_value}}"},
    ("PUT", "/v1/feature-flags/{key}"): {"enabled": True},
    ("PUT", "/v1/users/{user_id}/availability"): {"is_away": False, "backup_user_id": None},
    ("PATCH", "/v1/company"): {
        "phone": "+97167400000",
        "bank": {
            "account_name": "Arifa Packing & Packaging LLC",
            "bank_name": "Test Bank PJSC",
            "iban": "AE070331234567890123456",
            "swift": "TESTAEADXXX",
        },
    },
    ("PUT", "/v1/admin/kill-switch"): {"level": "off"},
    ("PATCH", "/v1/contacts/{contact_id}"): {
        "designation": "Purchasing Manager",
        "preferred_channel": "whatsapp",
        "language": "en",
        "timezone": "Asia/Dubai",
    },
    ("POST", "/v1/companies"): {
        "name": "QA Test Restaurants LLC",
        "country_code": "AE",
        "city": "Dubai",
        "customer_type": "restaurant_chain",
        "website": "https://qa-restaurants.example",
    },
    ("PATCH", "/v1/companies/{company_id}"): {"name": "QA Test Restaurants LLC", "city": "Abu Dhabi"},
    ("PATCH", "/v1/products/{product_id}"): {
        "moq": 5000,
        "carton_qty": 500,
        "default_lead_days_min": 7,
        "default_lead_days_max": 14,
    },
    ("POST", "/v1/fx-rates"): {"currency": "SAR", "rate_to_aed": "0.979", "as_of": "2026-11-19"},
    ("PUT", "/v1/texts/{key}"): {
        "text": "Thank you for your enquiry. A salesperson will contact you shortly."
    },
    ("PATCH", "/v1/admin/channel-accounts/{account_id}"): {
        "display_name": "Arifaa Pack UAE",
        "region": "UAE",
        "countries": ["AE"],
        "is_default": True,
    },
    ("PUT", "/v1/admin/message-templates"): {
        "channel": "whatsapp",
        "name": "qa_quote_followup",
        "language": "en",
        "category": "utility",
        "subject": None,
        "body": "Hello {{1}}, your quote {{2}} is ready. Reply here with any question.",
        "variables": ["buyer_name", "quote_number"],
    },
    ("POST", "/v1/admin/message-templates/{template_id}/status"): {"provider_status": "submitted"},
    ("POST", "/v1/public/sessions"): {
        "turnstile_token": "{{turnstile_token}}",
        "language": "en",
        "voice_consent": False,
        "cookie_consent": True,
        "country_code": "AE",
    },
    ("POST", "/v1/public/sessions/{session_id}/messages"): {"text": "{{web_text}}", "language": "en"},
    ("POST", "/v1/public/sessions/{session_id}/identify"): {
        "phone": "{{web_phone}}",
        "email": "{{web_email}}",
        "name": "QA Website Visitor",
    },
}

UPLOADS = {  # raw-file bodies (Postman: Body > binary > choose the file)
    ("POST", "/v1/price-list/import"): (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "the price-list .xlsx (columns sku, min_qty, max_qty, list_price, min_price, currency)",
    ),
    ("POST", "/v1/catalogue/workbook/preview"): (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "the product workbook .xlsx (download it first with 'GET /v1/catalogue/workbook', edit, send back)",
    ),
    ("PUT", "/v1/company/logo"): ("image/png", "a PNG or JPEG logo, max 200 KB"),
}

QUERY_EXAMPLES: dict[str, str] = {
    "limit": "20",
    "q": "{{buyer_phone}}",
    "stage": "NEW",
    "status": "open",
    "format": "csv",
    "token": "{{workbook_token}}",
    "period_from": "2026-11-01",
    "period_to": "2026-11-30",
    "current_only": "true",
    "user_id": "{{me_user_id}}",
    "deal_id": "{{deal_id}}",
    "hub.mode": "subscribe",
    "hub.verify_token": "{{wa_verify_token}}",
    "hub.challenge": "1158201444",
    "t": "{{unsubscribe_token}}",
}
QUERY_ON = {
    "limit",
    "format",
    "token",
    "status",
    "current_only",
    "hub.mode",
    "hub.verify_token",
    "hub.challenge",
    "t",
}


def example(schema: dict[str, Any], comps: dict[str, Any], name: str = "", depth: int = 0) -> Any:
    """A realistic example value for a JSON schema (field names drive the choice)."""
    if depth > 6:
        return None
    if "$ref" in schema:
        return example(comps[schema["$ref"].rsplit("/", 1)[-1]], comps, name, depth + 1)
    if "anyOf" in schema:
        options = [s for s in schema["anyOf"] if s.get("type") != "null"]
        return example(options[0], comps, name, depth + 1) if options else None
    if "enum" in schema:
        return schema["enum"][0]
    if "default" in schema and schema["default"] is not None:
        return schema["default"]
    t = schema.get("type")
    n = name.lower()
    if t == "object" or "properties" in schema:
        return {k: example(v, comps, k, depth + 1) for k, v in schema.get("properties", {}).items()}
    if t == "array":
        return [example(schema.get("items", {}), comps, name, depth + 1)]
    if t == "boolean":
        return False
    if t in ("integer", "number"):
        return 1 if "line" in n else 50000 if "qty" in n or "quantity" in n else 1
    if schema.get("format") == "uuid" or n.endswith("_id"):
        return "{{" + n + "}}"
    if schema.get("format") == "date":
        return "2026-12-15"
    if schema.get("format") == "date-time":
        return "2026-12-15T09:00:00+04:00"
    if "email" in n:
        return "qa.buyer@example.com"
    if "phone" in n:
        return "+971500000001"
    if "currency" in n:
        return "AED"
    if "country" in n:
        return "AE"
    if "language" in n:
        return "en"
    return "QA test"


# ---------------------------------------------------------------- Postman scripts
def js(*lines: str) -> list[str]:
    return list(lines)


STATUS_TEST = 'pm.test("Status is {code}", function () {{ pm.response.to.have.status({code}); }});'

SAVE_TOKEN = js(
    "if (pm.response.code === 200) {",
    "  const d = pm.response.json();",
    '  pm.environment.set("access_token", d.access_token);',
    '  pm.environment.set("token_" + d.user.role + (d.mfa ? "" : "_emailcode"), d.access_token);',
    '  pm.environment.set("me_user_id", d.user.id);',
    '  pm.test("Signed in as " + d.user.role + " (mfa=" + d.mfa + ")", function () { pm.expect(d.access_token).to.be.a("string"); });',
    "  if (d.must_change_password) { console.warn(\"must_change_password=true: run '4. Change password' next\"); }",
    "  if (!d.totp_enrolled) { console.warn(\"No authenticator yet: run '5. Enrol your authenticator app' next\"); }",
    "}",
)

SCRIPTS: dict[tuple[str, str], list[str]] = {
    ("POST", "/v1/auth/login"): js(
        "if (pm.response.code === 200) {",
        "  const d = pm.response.json();",
        '  pm.environment.set("challenge_token", d.challenge_token);',
        '  pm.test("Second step offered: " + d.methods.join(", "), function () { pm.expect(d.methods.length).to.be.above(0); });',
        '  console.log(d.totp_enrolled ? "Next: 7. Second step with the authenticator code" : "Next: 2. Send the email sign-in code");',
        "}",
    ),
    ("POST", "/v1/auth/mfa/totp"): SAVE_TOKEN,
    ("POST", "/v1/auth/mfa/email/verify"): SAVE_TOKEN,
    ("POST", "/v1/auth/refresh"): SAVE_TOKEN,
    ("POST", "/v1/auth/password"): js(
        "if (pm.response.code === 204) {",
        '  console.log("Password changed. Copy new_password into staff_password in your environment, then run 1. Login again.");',
        "}",
    ),
    ("POST", "/v1/auth/totp/enrol"): js(
        "if (pm.response.code === 200) {",
        "  const d = pm.response.json();",
        '  const tpl = `<div style="font-family:sans-serif;padding:8px"><p>Scan this QR code with your authenticator app',
        "  (Google Authenticator, Microsoft Authenticator, 1Password ...), or type the key by hand.</p>",
        '  <div id="qr"></div><p>Key: <code>{{secret}}</code></p>',
        '  <script src="https://cdnjs.cloudflare.com/ajax/libs/qrcodejs/1.0.0/qrcode.min.js"></script>',
        '  <script>pm.getData(function (err, data) { new QRCode(document.getElementById("qr"), data.uri); });</script></div>`;',
        "  pm.visualizer.set(tpl, { uri: d.otpauth_uri, secret: d.secret });",
        '  console.log("Open the Visualize tab of this response to scan the QR code. Then put the 6-digit code in totp_code and run 6.");',
        "}",
    ),
    ("GET", "/v1/users"): js(
        "if (pm.response.code === 200) {",
        "  const users = pm.response.json();",
        '  const sales = users.find(function (u) { return u.role === "sales" && u.is_active; });',
        '  if (sales) { pm.environment.set("sales_user_id", sales.id); }',
        '  if (users.length) { pm.environment.set("user_id", users[0].id); }',
        "}",
    ),
    ("POST", "/v1/users"): js(
        "if (pm.response.code === 201) {",
        "  const d = pm.response.json();",
        '  pm.environment.set("user_id", d.user.id);',
        '  console.log("Give the new user their one-time password in person (shown once in the response).");',
        "}",
    ),
    ("GET", "/v1/deals"): js(
        "if (pm.response.code === 200) {",
        "  const items = pm.response.json().items || [];",
        "  if (items.length) {",
        '    pm.environment.set("deal_id", items[0].id);',
        '    pm.environment.set("deal_version", items[0].version);',
        '    console.log("Newest deal: " + items[0].number + " stage " + items[0].stage + " -> saved as deal_id");',
        "  }",
        "}",
    ),
    ("GET", "/v1/deals/{deal_id}"): js(
        "if (pm.response.code === 200) {",
        "  const d = pm.response.json();",
        '  pm.environment.set("deal_version", d.version);',
        '  if (d.contact && d.contact.id) { pm.environment.set("contact_id", d.contact.id); }',
        '  console.log("Deal " + d.number + " stage " + d.stage + ", AI " + (d.ai_enabled ? "on" : "off") + ", missing: " + JSON.stringify(d.missing_fields));',
        "}",
    ),
    ("GET", "/v1/deals/{deal_id}/timeline"): js(
        "if (pm.response.code === 200) {",
        "  (pm.response.json().items || []).slice(0, 10).forEach(function (t) {",
        '    console.log(t.at + " " + (t.direction || "") + " " + (t.channel || "") + " " + t.kind + ": " + t.summary);',
        "  });",
        "}",
    ),
    ("GET", "/v1/inbox"): js(
        "if (pm.response.code === 200) {",
        "  const items = pm.response.json();",
        '  const ap = items.find(function (i) { return i.kind === "approval"; });',
        '  const sp = items.find(function (i) { return i.kind === "special_price"; });',
        '  const cr = items.find(function (i) { return i.kind === "change_request"; });',
        '  if (ap) { pm.environment.set("approval_id", ap.ref_id); }',
        '  if (sp) { pm.environment.set("special_approval_id", sp.ref_id); }',
        '  if (cr) { pm.environment.set("cr_id", cr.ref_id); }',
        '  items.forEach(function (i) { console.log(i.kind + " " + i.deal_number + ": " + i.title + (i.overdue ? " (OVERDUE)" : "")); });',
        "}",
    ),
    ("GET", "/v1/approvals"): js(
        "if (pm.response.code === 200) {",
        "  const items = pm.response.json().items || [];",
        '  if (items.length) { pm.environment.set("approval_id", items[0].id); }',
        "}",
    ),
    ("GET", "/v1/deals/{deal_id}/quotes"): js(
        "if (pm.response.code === 200) {",
        "  const v = pm.response.json();",
        '  if (v.length) { pm.environment.set("version_id", v[0].id); console.log("Newest version " + v[0].version_no + " " + v[0].status + " total " + v[0].total + " " + v[0].currency); }',
        "}",
    ),
    ("POST", "/v1/deals/{deal_id}/quotes"): js(
        "if (pm.response.code === 201) {",
        "  const v = pm.response.json();",
        '  pm.environment.set("version_id", v.id);',
        '  pm.test("Quote priced by the backend", function () { pm.expect(Number(v.total)).to.be.above(0); });',
        "}",
    ),
    ("GET", "/v1/change-requests"): js(
        "if (pm.response.code === 200) {",
        "  const items = pm.response.json();",
        "  const list = Array.isArray(items) ? items : (items.items || []);",
        '  if (list.length) { pm.environment.set("cr_id", list[0].id); }',
        "}",
    ),
    ("GET", "/v1/products"): js(
        "if (pm.response.code === 200) {",
        "  const p = pm.response.json();",
        "  const q = p.find(function (x) { return x.is_quotable && x.is_active; }) || p[0];",
        '  if (q) { pm.environment.set("product_id", q.id); console.log("product_id = " + q.sku + " " + q.name); }',
        "}",
    ),
    ("GET", "/v1/products/{product_id}/price-tiers"): js(
        "if (pm.response.code === 200) {",
        "  const t = pm.response.json();",
        '  if (t.length) { pm.environment.set("tier_id", t[0].id); }',
        "}",
    ),
    ("POST", "/v1/products/{product_id}/price-tiers"): js(
        'if (pm.response.code === 201) { pm.environment.set("tier_id", pm.response.json().id); }',
    ),
    ("POST", "/v1/catalogue/workbook/preview"): js(
        'if (pm.response.code === 200) { pm.environment.set("workbook_token", pm.response.json().token); }',
    ),
    ("GET", "/v1/price-proposals"): js(
        "if (pm.response.code === 200) {",
        "  const items = pm.response.json().items || [];",
        '  if (items.length) { pm.environment.set("rate_id", items[0].id); }',
        "}",
    ),
    ("GET", "/v1/contacts"): js(
        "if (pm.response.code === 200) {",
        "  const items = pm.response.json().items || [];",
        '  if (items.length) { pm.environment.set("contact_id", items[0].id); }',
        "}",
    ),
    ("POST", "/v1/companies"): js(
        'if (pm.response.code === 201) { pm.environment.set("company_id", pm.response.json().id); }',
    ),
    ("GET", "/v1/orders"): js(
        "if (pm.response.code === 200) {",
        "  const items = pm.response.json().items || [];",
        '  if (items.length) { pm.environment.set("order_id", items[0].id); }',
        "}",
    ),
    ("POST", "/v1/deals/{deal_id}/call"): js(
        'if (pm.response.code === 202) { pm.environment.set("attempt_id", pm.response.json().call_id); }',
    ),
    ("GET", "/v1/admin/channel-accounts"): js(
        "if (pm.response.code === 200) {",
        "  const a = pm.response.json();",
        '  if (a.length) { pm.environment.set("account_id", a[0].id); }',
        "}",
    ),
    ("GET", "/v1/admin/message-templates"): js(
        "if (pm.response.code === 200) {",
        "  const t = pm.response.json();",
        '  if (t.length) { pm.environment.set("template_id", t[0].id); }',
        "}",
    ),
    ("PUT", "/v1/admin/message-templates"): js(
        'if (pm.response.code === 200) { pm.environment.set("template_id", pm.response.json().id); }',
    ),
    ("GET", "/v1/texts"): js(
        "if (pm.response.code === 200) {",
        "  const t = pm.response.json();",
        "  const list = Array.isArray(t) ? t : (t.items || []);",
        '  if (list.length && list[0].key) { pm.environment.set("text_key", list[0].key); }',
        "}",
    ),
    ("POST", "/v1/public/sessions"): js(
        "if (pm.response.code === 201) {",
        "  const d = pm.response.json();",
        '  pm.environment.set("session_id", d.session_id);',
        '  pm.environment.set("session_token", d.session_token);',
        '  console.log("Website session started, language " + d.language + ", voice available: " + d.voice_available);',
        "}",
    ),
    ("POST", "/v1/public/sessions/{session_id}/messages"): js(
        "if (pm.response.code === 200) {",
        "  const d = pm.response.json();",
        '  console.log("AI reply: " + d.reply);',
        '  pm.test("The reply never shows a price the backend did not build (check by eye)", function () { pm.expect(d).to.have.property("reply"); });',
        "}",
    ),
}

WA_PRE = js(
    "// Builds a fresh WhatsApp Cloud API webhook (new message id + timestamp) and signs it the way Meta does:",
    "// X-Hub-Signature-256: sha256=<HMAC-SHA256(app secret, raw body)>.",
    'const secret = pm.environment.get("wa_app_secret");',
    'if (!secret) { throw new Error("wa_app_secret is empty in the environment: ask the admin (staging) or use the local environment file."); }',
    'const reuse = pm.variables.get("wa_reuse_last_id") === "yes" && pm.environment.get("last_wa_message_id");',
    'const msgId = reuse ? pm.environment.get("last_wa_message_id") : "wamid.QA" + Date.now() + Math.floor(Math.random() * 1e6);',
    "const body = {",
    '  object: "whatsapp_business_account",',
    '  entry: [{ id: "WABA", changes: [{ field: "messages", value: {',
    '    messaging_product: "whatsapp",',
    '    metadata: { display_phone_number: pm.variables.get("wa_display_number"), phone_number_id: pm.variables.get("wa_phone_number_id") },',
    '    contacts: [{ wa_id: pm.variables.get("buyer_phone"), profile: { name: pm.variables.get("buyer_name") } }],',
    '    messages: [{ from: pm.variables.get("buyer_phone"), id: msgId, timestamp: String(Math.floor(Date.now() / 1000)),',
    '                 type: "text", text: { body: pm.variables.get("buyer_text") } }]',
    "  } }] }]",
    "};",
    "const raw = JSON.stringify(body);",
    'pm.variables.set("wa_body", raw);',
    'pm.variables.set("wa_signature", "sha256=" + CryptoJS.HmacSHA256(raw, secret).toString(CryptoJS.enc.Hex));',
    'pm.environment.set("last_wa_message_id", msgId);',
)

WA_BAD_PRE = js(
    "// Same message shape, signed with a WRONG secret: the server must answer 401 and store nothing.",
    'const raw = JSON.stringify({ object: "whatsapp_business_account", entry: [{ id: "WABA", changes: [{ field: "messages", value: {',
    '  messaging_product: "whatsapp", metadata: { display_phone_number: "971400000001", phone_number_id: pm.variables.get("wa_phone_number_id") },',
    '  contacts: [{ wa_id: pm.variables.get("buyer_phone"), profile: { name: "Forged" } }],',
    '  messages: [{ from: pm.variables.get("buyer_phone"), id: "wamid.FORGED" + Date.now(), timestamp: String(Math.floor(Date.now() / 1000)), type: "text", text: { body: "forged message" } }] } }] }] });',
    'pm.variables.set("wa_body", raw);',
    'pm.variables.set("wa_signature", "sha256=" + CryptoJS.HmacSHA256(raw, "not-the-real-secret").toString(CryptoJS.enc.Hex));',
)

CMS_PRE = js(
    "// Signs like the CMS: X-Kx-Timestamp = unix seconds, X-Kx-Signature = sha256=<HMAC-SHA256(secret, timestamp + '.' + body)>.",
    'const secret = pm.environment.get("cms_webhook_secret");',
    'if (!secret) { throw new Error("cms_webhook_secret is empty in the environment: ask the admin / developer."); }',
    'const ts = String(Math.floor(Date.now() / 1000) - Number(pm.variables.get("cms_age_seconds") || 0));',
    "const body = JSON.stringify({",
    '  event_id: pm.variables.replaceIn("{{$guid}}"),',
    '  operation: pm.variables.get("cms_operation") || "publish",',
    '  collection: "products", doc_id: "qa-doc-001", locales: ["en", "ar"],',
    '  url: "https://www.example.invalid/products/qa-test",',
    '  product: { code: pm.variables.get("cms_product_code"), name: "Aluminium container 750 ml (QA test)",',
    '             category_code: "ALU", category_name: "Aluminium containers",',
    '             spec: { material: "aluminium", capacity_ml: 750, price_aed: 1.23 }, custom_printing: false }',
    "});",
    'pm.variables.set("cms_body", body);',
    'pm.variables.set("cms_ts", ts);',
    'pm.variables.set("cms_sig", "sha256=" + CryptoJS.HmacSHA256(ts + "." + body, secret).toString(CryptoJS.enc.Hex));',
)

TELNYX_PRE = js(
    "// The per-call URL token the platform puts in Telnyx callback URLs: base64url(JSON claims) + '.' + hex HMAC-SHA256.",
    'const secret = pm.environment.get("telnyx_callback_secret");',
    'if (!secret) { throw new Error("telnyx_callback_secret is empty in the environment: ask the admin / developer."); }',
    'const claims = JSON.stringify({ a: pm.environment.get("attempt_id"), exp: Math.floor(Date.now() / 1000) + 600 });',
    'const b64 = CryptoJS.enc.Base64.stringify(CryptoJS.enc.Utf8.parse(claims)).replace(/\\+/g, "-").replace(/\\//g, "_").replace(/=+$/, "");',
    'pm.variables.set("telnyx_token", b64 + "." + CryptoJS.HmacSHA256(b64, secret).toString(CryptoJS.enc.Hex));',
)


# ---------------------------------------------------------------- building the collection
def first_sentence(doc: str) -> str:
    text = " ".join((doc or "").split())
    m = re.match(r"(.+?[.!?])(\s|$)", text)
    return (m.group(1) if m else text)[:220]


def _enum_of(schema: dict[str, Any], comps: dict[str, Any]) -> list[Any]:
    """The allowed values of a (possibly nullable / referenced) enum parameter, else []."""
    if "$ref" in schema:
        return _enum_of(comps[schema["$ref"].rsplit("/", 1)[-1]], comps)
    if "anyOf" in schema:
        for s in schema["anyOf"]:
            if found := _enum_of(s, comps):
                return found
    return list(schema.get("enum", []))


def who_text(path: str, roles: list[str], mfa: bool, signed: bool) -> str:
    for prefix, text in WHO_OVERRIDE.items():
        if path.startswith(prefix) and not signed:
            if prefix == "/v1/public/sessions" and path != "/v1/public/sessions":
                continue
            return text
    if not signed:
        return "public (no sign-in)"
    ordered = sorted(roles, key=lambda r: ROLE_ORDER.index(r) if r in ROLE_ORDER else 99)
    who = ", ".join(ordered) if ordered else "any signed-in staff"
    return who + (" + signed in with the authenticator app (mfa=true)" if mfa else "")


def auth_kind(path: str, method: str, signed: bool) -> str:
    if signed:
        return "staff"
    if path.startswith("/v1/public/sessions/"):
        return "website_session"
    return "none"


def url_obj(path: str, query: list[dict[str, Any]]) -> dict[str, Any]:
    p = re.sub(r"\{(\w+)\}", lambda m: "{{" + path_var(path, m.group(1)) + "}}", path)
    raw = "{{base_url}}" + p
    enabled = [q for q in query if not q.get("disabled")]
    if enabled:
        raw += "?" + "&".join(f"{q['key']}={q['value']}" for q in enabled)
    return {
        "raw": raw,
        "host": ["{{base_url}}"],
        "path": [s for s in p.split("/") if s],
        **({"query": query} if query else {}),
    }


def body_raw(value: Any) -> str:
    text = json.dumps(value, indent=2, ensure_ascii=False)
    return text.replace('"{{deal_version}}"', "{{deal_version}}")


def describe(doc: str, who: str, expected: int, notes: str = "") -> str:
    parts = [
        " ".join((doc or "").split()) or "(no description in the code)",
        "",
        f"**Who may call it:** {who}",
        "",
        f"**Expected on success:** HTTP {expected}",
    ]
    if notes:
        parts += ["", notes]
    return "\n".join(parts)


def request_item(
    name: str,
    method: str,
    path: str,
    *,
    description: str,
    auth: str,
    headers: list[dict[str, str]] | None = None,
    body: dict[str, Any] | None = None,
    query: list[dict[str, Any]] | None = None,
    tests: list[str] | None = None,
    prerequest: list[str] | None = None,
    url_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    req: dict[str, Any] = {
        "method": method,
        "header": headers or [],
        "url": url_override or url_obj(path, query or []),
        "description": description,
    }
    if body is not None:
        req["body"] = body
    if auth == "none":
        req["auth"] = {"type": "noauth"}
    elif auth == "website_session":
        req["auth"] = {
            "type": "bearer",
            "bearer": [{"key": "token", "value": "{{session_token}}", "type": "string"}],
        }
    elif auth.startswith("{{"):
        req["auth"] = {"type": "bearer", "bearer": [{"key": "token", "value": auth, "type": "string"}]}
    item: dict[str, Any] = {"name": name, "request": req}
    events = []
    if prerequest:
        events.append({"listen": "prerequest", "script": {"type": "text/javascript", "exec": prerequest}})
    if tests:
        events.append({"listen": "test", "script": {"type": "text/javascript", "exec": tests}})
    if events:
        item["event"] = events
    return item


def json_body(value: Any) -> dict[str, Any]:
    return {"mode": "raw", "raw": body_raw(value), "options": {"raw": {"language": "json"}}}


def build() -> tuple[dict[str, Any], list[dict[str, Any]], int]:
    app = full_app()
    show_hidden(app.routes)
    spec = app.openapi()
    comps = spec.get("components", {}).get("schemas", {})
    routes = {(m, path): r for path, r in flat_routes(app.routes) for m in r.methods}
    openapi_ops = [(m.upper(), p) for p, ops in spec["paths"].items() for m in ops]

    folders: dict[str, list[tuple[int, int, dict[str, Any]]]] = {name: [] for name, _ in FOLDERS}
    reference: list[dict[str, Any]] = []
    for order, (method, path) in enumerate(openapi_ops):
        op = spec["paths"][path][method.lower()]
        route = routes[(method, path)]
        roles, mfa, signed = roles_of(route)
        who = who_text(path, roles, mfa, signed)
        # the Telnyx status handler answers a bare Response(204) that the route object does not declare
        expected = 204 if path == "/webhooks/telnyx/status/{attempt_id}" else route.status_code or 200
        doc = route.description or op.get("description", "") or op.get("summary", "")
        name = NAMES.get((method, path)) or (route.name or "").replace("_", " ").capitalize()
        folder = folder_of(method, path)
        kind = auth_kind(path, method, signed)

        query = []
        for prm in op.get("parameters", []):
            if prm["in"] != "query":
                continue
            key = prm["name"]
            enum = _enum_of(prm.get("schema", {}), comps)
            value = QUERY_EXAMPLES.get(key, "")
            if enum and value not in enum:
                value = str(enum[0])
            on = (key in QUERY_ON and not (path == "/v1/orders" and key == "status")) or prm.get("required")
            hint = ("one of: " + ", ".join(map(str, enum)) + "; ") if enum else ""
            query.append(
                {
                    "key": key,
                    "value": value,
                    "description": hint + ("required" if prm.get("required") else "optional"),
                    **({} if on else {"disabled": True}),
                }
            )
        headers: list[dict[str, str]] = []
        body: dict[str, Any] | None = None
        notes = ""
        pre: list[str] | None = None
        url_override = None
        if (method, path) in UPLOADS:
            ctype, what = UPLOADS[(method, path)]
            headers.append({"key": "Content-Type", "value": ctype})
            body = {"mode": "file", "file": {}}
            notes = f"**Body:** choose {what} under Body > binary."
        elif path == "/webhooks/whatsapp" and method == "POST":
            headers += [
                {"key": "Content-Type", "value": "application/json"},
                {"key": "X-Hub-Signature-256", "value": "{{wa_signature}}"},
            ]
            body = {"mode": "raw", "raw": "{{wa_body}}", "options": {"raw": {"language": "json"}}}
            pre = WA_PRE
            notes = (
                "**How to use:** set `buyer_phone` (digits only, e.g. 971501234567), `buyer_name` and `buyer_text` "
                "(the buyer's words), then Send. The pre-request script builds a fresh message id and timestamp and signs "
                "the body. The answer is always an empty 200 (Meta needs it fast); the AI reply arrives a few seconds "
                "later: run 'List deals' then 'Deal timeline'. To test duplicate delivery, set `wa_reuse_last_id` = yes."
            )
        elif path == "/webhooks/cms":
            headers += [
                {"key": "Content-Type", "value": "application/json"},
                {"key": "X-Kx-Timestamp", "value": "{{cms_ts}}"},
                {"key": "X-Kx-Signature", "value": "{{cms_sig}}"},
            ]
            body = {"mode": "raw", "raw": "{{cms_body}}", "options": {"raw": {"language": "json"}}}
            pre = CMS_PRE
            notes = "**How to use:** set `cms_product_code` to a product code (SKU) that exists in the CRM and `cms_operation` to publish or unpublish. The spec deliberately contains `price_aed`: the platform must drop it (prices never come from the CMS)."
        elif path.startswith("/webhooks/telnyx"):
            headers.append({"key": "Content-Type", "value": "application/x-www-form-urlencoded"})
            form = (
                [{"key": "Digits", "value": "1"}, {"key": "CallSid", "value": "v3:qa-call-0001"}]
                if "gather" in path
                else [
                    {"key": "CallSid", "value": "v3:qa-call-0001"},
                    {"key": "CallStatus", "value": "completed"},
                ]
            )
            body = {"mode": "urlencoded", "urlencoded": form}
            pre = TELNYX_PRE
            query = [{"key": "t", "value": "{{telnyx_token}}"}]
            notes = "**How to use:** `attempt_id` is saved by 'Click-to-call' (folder 03) or given by the developer. The URL token is computed from `telnyx_callback_secret`."
        elif path == "/v1/public/unsubscribe" and method == "POST":
            headers.append({"key": "Content-Type", "value": "application/x-www-form-urlencoded"})
            body = {
                "mode": "urlencoded",
                "urlencoded": [{"key": "reason", "value": "QA test: too many emails"}],
            }
            notes = "**How to use:** copy the `t=` value from the unsubscribe link in a buyer email into `unsubscribe_token`."
        elif path == "/v1/public/unsubscribe":
            notes = "**How to use:** copy the `t=` value from the unsubscribe link in a buyer email into `unsubscribe_token`. A GET must NEVER unsubscribe (mail scanners open links)."
        elif (method, path) in BODY:
            headers.append({"key": "Content-Type", "value": "application/json"})
            body = json_body(BODY[(method, path)])
        elif "requestBody" in op:
            media = next(iter(op["requestBody"].get("content", {}).values()), {})
            value = example(media.get("schema", {}), comps)
            headers.append({"key": "Content-Type", "value": "application/json"})
            body = json_body(value if value is not None else {})

        tests = [STATUS_TEST.format(code=expected), *SCRIPTS.get((method, path), [])]
        item = request_item(
            name,
            method,
            path,
            description=describe(doc, who, expected, notes),
            auth=kind,
            headers=headers,
            body=body,
            query=query,
            tests=tests,
            prerequest=pre,
            url_override=url_override,
        )
        folders[folder].append((PRIORITY.get((method, path), 100), order, item))
        reference.append(
            {
                "folder": folder,
                "name": name,
                "method": method,
                "path": path,
                "who": who,
                "roles": roles,
                "mfa_required": mfa,
                "auth": kind,
                "purpose": first_sentence(doc),
                "expected_status": expected,
            }
        )

    folders[FOLDERS[14][0]] = [(i, i, it) for i, it in enumerate(negative_items())]
    items = []
    for name, desc in FOLDERS:
        entries = [it for _, _, it in sorted(folders[name], key=lambda t: (t[0], t[1]))]
        items.append({"name": name, "description": desc, "item": entries})
    collection = {
        "info": {
            "name": "Kairox platform - manual tests",
            "description": (
                "Generated by scripts/gen_tester_pack.py from the code. Read docs/testing/TESTER_HELPER.docx "
                "first. Pick the environment (local or staging) in the top-right corner of Postman. Folders are in test "
                "order; requests save the ids the next ones need (deal_id, version_id, approval_id ...) into the "
                "environment. No passwords or authenticator secrets are stored here."
            ),
            "schema": SCHEMA_V21,
        },
        "auth": {
            "type": "bearer",
            "bearer": [{"key": "token", "value": "{{access_token}}", "type": "string"}],
        },
        "variable": [
            {
                "key": "buyer_text",
                "value": "Hi, I need 50,000 aluminium containers 750 ml with lids, delivery to Dubai.",
            },
            {"key": "web_text", "value": "Hello, do you make aluminium food containers with lids?"},
            {"key": "wa_reuse_last_id", "value": "no"},
            {"key": "cms_operation", "value": "publish"},
            {"key": "cms_age_seconds", "value": "0"},
            {"key": "export_entity", "value": "deals"},
            {"key": "setting_value", "value": ""},
        ],
        "item": items,
    }
    return collection, reference, len(openapi_ops)


def negative_items() -> list[dict[str, Any]]:
    """Requests that must be refused (they reuse real routes, so the route count is unchanged)."""
    j = {"key": "Content-Type", "value": "application/json"}

    def neg(name: str, method: str, path: str, expect: list[int], why: str, **kw: Any) -> dict[str, Any]:
        codes = ", ".join(str(c) for c in expect)
        tests = [
            f'pm.test("Refused with {codes}", function () {{ pm.expect(pm.response.code).to.be.oneOf({json.dumps(expect)}); }});',
            *kw.pop("tests", []),
        ]
        return request_item(
            name, method, path, description=f"{why}\n\n**Expected:** HTTP {codes}", tests=tests, **kw
        )

    cors_test = [
        'pm.test("No CORS permission for this origin", function () { pm.expect(pm.response.headers.has("Access-Control-Allow-Origin")).to.be.false; });',
    ]
    return [
        neg(
            "N01 No token -> 401",
            "GET",
            "/v1/deals",
            [401],
            "A CRM route without a Bearer token.",
            auth="none",
        ),
        neg(
            "N02 Broken token -> 401",
            "GET",
            "/v1/deals",
            [401],
            "A CRM route with a made-up token.",
            auth="{{bad_token}}",
        ),
        neg(
            "N03 Salesperson creates a user -> 403",
            "POST",
            "/v1/users",
            [403],
            "Only admin / MD (with the authenticator app) may create users. Sign in as a salesperson first (saves token_sales).",
            auth="{{token_sales}}",
            headers=[j],
            body=json_body(BODY[("POST", "/v1/users")]),
        ),
        neg(
            "N04 Salesperson opens the manager dashboard -> 403",
            "GET",
            "/v1/dashboards/manager",
            [403],
            "Manager / MD only.",
            auth="{{token_sales}}",
        ),
        neg(
            "N05 Salesperson reads quote costs -> 403",
            "GET",
            "/v1/quote-versions/{version_id}/costs",
            [403],
            "Cost data is MD only; the AI and salespeople never see it.",
            auth="{{token_sales}}",
        ),
        neg(
            "N06 Email-code login cannot approve -> 403",
            "PUT",
            "/v1/admin/kill-switch",
            [403],
            "A session from the EMAIL code has mfa=false. Approvals, price edits and settings need the authenticator app. Sign in as admin with the email code (3.) to get token_admin_emailcode.",
            auth="{{token_admin_emailcode}}",
            headers=[j],
            body=json_body({"level": "off"}),
        ),
        neg(
            "N07 Email-code login cannot approve a quote -> 403",
            "POST",
            "/v1/approvals/{approval_id}/decision",
            [403],
            "Same rule for a quote approval (salesperson signed in with the email code).",
            auth="{{token_sales_emailcode}}",
            headers=[j],
            body=json_body({"decision": "approve", "note": "should be refused"}),
        ),
        neg(
            "N08 WhatsApp webhook with a wrong signature -> 401",
            "POST",
            "/webhooks/whatsapp",
            [401],
            "Signed with the wrong secret. Nothing may be stored (check: no new deal / timeline entry).",
            auth="none",
            headers=[j, {"key": "X-Hub-Signature-256", "value": "{{wa_signature}}"}],
            body={"mode": "raw", "raw": "{{wa_body}}", "options": {"raw": {"language": "json"}}},
            prerequest=WA_BAD_PRE,
        ),
        neg(
            "N09 WhatsApp webhook without a signature -> 401",
            "POST",
            "/webhooks/whatsapp",
            [401],
            "No X-Hub-Signature-256 header at all.",
            auth="none",
            headers=[j],
            body=json_body({"object": "whatsapp_business_account", "entry": []}),
        ),
        neg(
            "N10 WhatsApp verify with a wrong verify token -> 401",
            "GET",
            "/webhooks/whatsapp",
            [401],
            "Meta's subscription check with the wrong hub.verify_token.",
            auth="none",
            query=[
                {"key": "hub.mode", "value": "subscribe"},
                {"key": "hub.verify_token", "value": "wrong-token"},
                {"key": "hub.challenge", "value": "123"},
            ],
        ),
        neg(
            "N11 CMS webhook with a wrong signature -> 401",
            "POST",
            "/webhooks/cms",
            [401],
            "A fresh timestamp but a made-up signature.",
            auth="none",
            headers=[
                j,
                {"key": "X-Kx-Timestamp", "value": "{{$timestamp}}"},
                {
                    "key": "X-Kx-Signature",
                    "value": "sha256=0000000000000000000000000000000000000000000000000000000000000000",
                },
            ],
            body=json_body(
                {
                    "event_id": "00000000-0000-0000-0000-000000000001",
                    "operation": "publish",
                    "collection": "pages",
                    "doc_id": "x",
                }
            ),
        ),
        neg(
            "N12 CMS webhook older than 5 minutes -> 401",
            "POST",
            "/webhooks/cms",
            [401],
            "Correctly signed, but the timestamp is 10 minutes old (replay protection). Uses cms_age_seconds = 600 for this request only.",
            auth="none",
            headers=[
                j,
                {"key": "X-Kx-Timestamp", "value": "{{cms_ts}}"},
                {"key": "X-Kx-Signature", "value": "{{cms_sig}}"},
            ],
            body={"mode": "raw", "raw": "{{cms_body}}", "options": {"raw": {"language": "json"}}},
            prerequest=['pm.variables.set("cms_age_seconds", "600");', *CMS_PRE],
        ),
        neg(
            "N13 Telnyx callback without the URL token -> 401",
            "POST",
            "/webhooks/telnyx/status/{attempt_id}",
            [401, 404, 422],
            "No ?t= token (or a random attempt id). Postman cannot make Telnyx's Ed25519 signature, so this negative test is the check for the signature path.",
            auth="none",
            headers=[{"key": "Content-Type", "value": "application/x-www-form-urlencoded"}],
            body={"mode": "urlencoded", "urlencoded": [{"key": "CallStatus", "value": "completed"}]},
            url_override=url_obj("/webhooks/telnyx/status/{attempt_id}", []),
        ),
        neg(
            "N14 Website message without the session token -> 401",
            "POST",
            "/v1/public/sessions/{session_id}/messages",
            [401],
            "The chat API needs the session token from 'Start a website chat session'.",
            auth="none",
            headers=[j],
            body=json_body({"text": "hello"}),
        ),
        neg(
            "N15 Website message with another session's token -> 401/403",
            "POST",
            "/v1/public/sessions/{session_id}/messages",
            [401, 403],
            "A token belongs to one session only. Uses a random session id with your current token.",
            auth="website_session",
            headers=[j],
            body=json_body({"text": "hello"}),
            url_override=url_obj("/v1/public/sessions/{other_session_id}/messages", []),
        ),
        neg(
            "N16 Unsubscribe with a forged link -> 400",
            "POST",
            "/v1/public/unsubscribe",
            [400],
            "A made-up token must not unsubscribe anybody.",
            auth="none",
            headers=[{"key": "Content-Type", "value": "application/x-www-form-urlencoded"}],
            query=[
                {
                    "key": "t",
                    "value": "0000000000000000000000000000000000000000000000000000000000000000.forged",
                }
            ],
            body={"mode": "urlencoded", "urlencoded": [{"key": "reason", "value": "x"}]},
        ),
        neg(
            "N17 Staff sign-in from a foreign browser origin -> 403",
            "POST",
            "/v1/auth/login",
            [403],
            "A browser request whose Origin is not the CRM is refused before the password is checked (M2). Uses Origin = {{foreign_origin}}.",
            auth="none",
            headers=[j, {"key": "Origin", "value": "{{foreign_origin}}"}],
            body=json_body({"email": "{{staff_email}}", "password": "wrong-password-on-purpose"}),
        ),
        neg(
            "N18 CORS: the website origin may not read CRM data",
            "GET",
            "/v1/deals",
            [200, 401],
            "Sent with Origin = {{website_origin}}. Whatever the status, the answer must NOT carry Access-Control-Allow-Origin (a browser would hide it from the website).",
            auth="none",
            headers=[{"key": "Origin", "value": "{{website_origin}}"}],
            tests=cors_test,
        ),
        neg(
            "N19 Wrong password -> 401 (repeat 6 times: then 429)",
            "POST",
            "/v1/auth/login",
            [401, 429],
            "Run this request 6 times in a row (Collection Runner, 6 iterations). Attempts 1-5: 401. Then 429 'too many failed attempts' for 15 minutes from this IP. Use a TEST account: it will be locked out from your IP.",
            auth="none",
            headers=[j],
            body=json_body({"email": "{{staff_email}}", "password": "wrong-password-on-purpose"}),
        ),
        neg(
            "N20 Stage move with an old version -> 409",
            "POST",
            "/v1/deals/{deal_id}/stage",
            [409, 422],
            "expected_version = 0 never matches: the server must refuse instead of overwriting someone else's change.",
            auth="staff",
            headers=[j],
            body=json_body(
                {"to": "LOST", "expected_version": 0, "lost_reason": "other", "lost_note": "must be refused"}
            ),
        ),
    ]


def environment(name: str, values: list[tuple[str, str, str, str]]) -> dict[str, Any]:
    return {
        "name": name,
        "values": [
            {"key": k, "value": v, "type": t, "enabled": True, "description": d} for k, v, t, d in values
        ],
        "_postman_variable_scope": "environment",
    }


RUNTIME = [  # filled by the collection's scripts while the tester works
    ("access_token", "", "secret", "Set by the sign-in requests. Do not type."),
    ("challenge_token", "", "secret", "Set by '1. Login'."),
    ("token_sales", "", "secret", "Saved when a salesperson signs in with the authenticator app."),
    ("token_manager", "", "secret", "Saved when a manager signs in with the authenticator app."),
    ("token_md", "", "secret", "Saved when the MD signs in with the authenticator app."),
    ("token_admin", "", "secret", "Saved when an admin signs in with the authenticator app."),
    (
        "token_sales_emailcode",
        "",
        "secret",
        "Saved when a salesperson signs in with the EMAIL code (mfa=false).",
    ),
    ("token_admin_emailcode", "", "secret", "Saved when an admin signs in with the EMAIL code (mfa=false)."),
    (
        "bad_token",
        "eyJhbGciOiJSUzI1NiJ9.e30.not-a-real-signature",
        "default",
        "A broken token for negative tests.",
    ),
    ("me_user_id", "", "default", ""),
    ("user_id", "", "default", ""),
    ("sales_user_id", "", "default", ""),
    ("deal_id", "", "default", ""),
    ("deal_version", "", "default", ""),
    ("contact_id", "", "default", ""),
    ("company_id", "", "default", ""),
    ("version_id", "", "default", ""),
    ("approval_id", "", "default", ""),
    ("special_approval_id", "", "default", ""),
    ("cr_id", "", "default", ""),
    ("product_id", "", "default", ""),
    ("tier_id", "", "default", ""),
    ("rate_id", "", "default", ""),
    ("order_id", "", "default", ""),
    ("attempt_id", "", "default", ""),
    ("account_id", "", "default", ""),
    ("template_id", "", "default", ""),
    ("workbook_token", "", "default", ""),
    ("text_key", "", "default", ""),
    ("setting_key", "", "default", "A key from 'GET /v1/settings'."),
    ("flag_key", "", "default", "A feature flag name (ask the developer)."),
    ("session_id", "", "default", ""),
    ("session_token", "", "secret", "Set by '1. Start a website chat session'."),
    (
        "other_session_id",
        "00000000-0000-7000-8000-000000000000",
        "default",
        "A session id that is not yours.",
    ),
    ("last_wa_message_id", "", "default", ""),
    ("unsubscribe_token", "", "default", "The t= value from the unsubscribe link in a buyer email."),
]

TYPED = [  # typed by the tester
    ("staff_email", "", "default", "Your own test account email (from the admin)."),
    ("staff_password", "", "secret", "Your password. Type it yourself; never share or commit it."),
    ("new_password", "", "secret", "The new password for '4. Change password' (min 12 characters)."),
    ("email_code", "", "default", "The 6-digit code from the sign-in / reset email."),
    ("totp_code", "", "default", "The 6-digit code your authenticator app shows right now."),
    ("new_user_email", "", "default", "An email for a user the admin creates in a test."),
    (
        "buyer_phone",
        "971500000123",
        "default",
        "The fake buyer's WhatsApp number, digits only. Change it for a new buyer.",
    ),
    ("buyer_name", "QA Buyer", "default", "The fake buyer's WhatsApp profile name."),
    ("web_phone", "+971500000124", "default", "Phone typed by the website visitor."),
    ("web_email", "qa.visitor@example.com", "default", "Email typed by the website visitor."),
    ("cms_product_code", "", "default", "A product code (SKU) that exists in the CRM."),
]


def environments() -> tuple[dict[str, Any], dict[str, Any]]:
    local = environment(
        "Kairox - local live-test stack",
        [
            (
                "base_url",
                "http://localhost:38000",
                "default",
                "The developer's live-test stack (ask the developer to start it).",
            ),
            (
                "wa_app_secret",
                LOCAL_WA_TEST_SECRET,
                "secret",
                "TEST ONLY: the live-test stack's WhatsApp app secret. Worthless anywhere else.",
            ),
            ("wa_verify_token", "", "secret", "Ask the developer (local test value)."),
            (
                "wa_phone_number_id",
                "100000000000001",
                "default",
                "The live-test stack's fake WhatsApp business number id.",
            ),
            ("wa_display_number", "971400000001", "default", ""),
            ("cms_webhook_secret", "", "secret", "Ask the developer (local test value)."),
            ("telnyx_callback_secret", "", "secret", "Ask the developer (local test value)."),
            ("turnstile_token", "", "default", "Not needed locally (no human check)."),
            (
                "website_origin",
                "https://www.example.invalid",
                "default",
                "A website origin for the CORS test.",
            ),
            (
                "foreign_origin",
                "https://evil.example",
                "default",
                "An origin that is neither the CRM nor the website.",
            ),
            *TYPED,
            *RUNTIME,
        ],
    )
    staging = environment(
        "Kairox - staging",
        [
            (
                "base_url",
                "https://staging.example.invalid",
                "default",
                "Ask the admin for the staging API address.",
            ),
            (
                "wa_app_secret",
                "",
                "secret",
                "Ask the admin (staging WhatsApp TEST app secret). Never a production secret.",
            ),
            ("wa_verify_token", "", "secret", "Ask the admin."),
            ("wa_phone_number_id", "", "default", "Ask the admin (the staging WhatsApp phone number id)."),
            ("wa_display_number", "", "default", "Ask the admin."),
            ("cms_webhook_secret", "", "secret", "Ask the admin."),
            ("telnyx_callback_secret", "", "secret", "Ask the admin."),
            (
                "turnstile_token",
                "XXXX.DUMMY.TOKEN.XXXX",
                "default",
                "Works only if staging uses Cloudflare's always-pass TEST secret key; ask the admin.",
            ),
            (
                "website_origin",
                "",
                "default",
                "Ask the admin (the staging website address, e.g. https://www.<domain>).",
            ),
            (
                "foreign_origin",
                "https://evil.example",
                "default",
                "An origin that is neither the CRM nor the website.",
            ),
            *TYPED,
            *RUNTIME,
        ],
    )
    return local, staging


def validate(collection: dict[str, Any], openapi_count: int) -> None:
    text = json.dumps(collection)
    json.loads(text)
    seen: set[tuple[str, str]] = set()
    total = 0
    for folder in collection["item"]:
        for it in folder["item"]:
            total += 1
            url = it["request"]["url"]["raw"]
            assert url.startswith("{{base_url}}/"), url
            if not folder["name"].startswith("14"):
                path = "/" + "/".join(it["request"]["url"]["path"])
                seen.add((it["request"]["method"], path))
    main = sum(len(f["item"]) for f in collection["item"] if not f["name"].startswith("14"))
    assert main == len(seen), "a route appears twice outside the negative folder"
    print(
        f"OpenAPI operations: {openapi_count}; collection routes (folders 00-13): {main}; negative tests: {total - main}"
    )
    assert main == openapi_count, "route count mismatch"


def main() -> None:
    collection, reference, openapi_count = build()
    validate(collection, openapi_count)
    local, staging = environments()
    POSTMAN.mkdir(parents=True, exist_ok=True)

    def dump(path: pathlib.Path, data: Any) -> None:
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    dump(POSTMAN / "Kairox.postman_collection.json", collection)
    dump(POSTMAN / "Kairox-local.postman_environment.json", local)
    dump(POSTMAN / "Kairox-staging.postman_environment.json", staging)
    dump(
        OUT / "endpoints.json",
        {"folders": [{"name": n, "description": d} for n, d in FOLDERS], "routes": reference},
    )
    print("wrote", POSTMAN, "and", OUT / "endpoints.json")


if __name__ == "__main__":
    main()
