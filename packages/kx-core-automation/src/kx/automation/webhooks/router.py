"""Provider webhooks. Thin: verify the request, record in one transaction, enqueue slow work (media
download) after commit, answer fast.

Mount with `app.include_router(build_router(...))` -> /webhooks/whatsapp (Meta, HMAC signature) and
/webhooks/telnyx/{gather,status}/{attempt} (D43: signed per-attempt URL token, plus the Ed25519 signature when
the Telnyx public key is configured). Email has no webhook any more (D44: Stalwart's inbox is polled). Badly
authenticated requests get 401 (kx.core problem handlers) and write nothing.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from urllib.parse import parse_qsl

import orjson
from fastapi import APIRouter, Request, Response

from kx.automation._shared.ports import MediaStore, TaskScheduler
from kx.automation._shared.settings import AutomationSettings
from kx.automation.alerts.service import VoiceAlerts, callback_urls
from kx.automation.calls import channel as calls
from kx.automation.inbound import service as inbound
from kx.automation.whatsapp import channel as whatsapp
from kx.core.db import Databases
from kx.core.errors import Unauthorized
from kx.core.logging import get_logger
from kx.core.settings import LogicalDb

log = get_logger(__name__)
_XML = "application/xml"


@dataclass(frozen=True, slots=True)
class WebhookDeps:
    databases: Databases
    settings: AutomationSettings
    scheduler: TaskScheduler
    voice: VoiceAlerts
    media: MediaStore | None = None


def build_router(deps: WebhookDeps) -> APIRouter:
    router = APIRouter(prefix="/webhooks", tags=["webhooks"], include_in_schema=False)
    _whatsapp_routes(router, deps)
    _telnyx_routes(router, deps)
    return router


def _whatsapp_routes(router: APIRouter, deps: WebhookDeps) -> None:
    wa = deps.settings.whatsapp

    @router.get("/whatsapp")
    async def whatsapp_verify(request: Request) -> Response:
        q = request.query_params
        challenge = whatsapp.verify_challenge(
            wa.verify_token.get_secret_value(),
            q.get("hub.mode"),
            q.get("hub.verify_token"),
            q.get("hub.challenge"),
        )
        if challenge is None:
            raise Unauthorized("verify token mismatch")
        return Response(challenge, media_type="text/plain")

    @router.post("/whatsapp")
    async def whatsapp_events(request: Request) -> Response:
        raw = await request.body()
        signature = request.headers.get(whatsapp.SIGNATURE_HEADER)
        if not whatsapp.verify_signature(wa.app_secret.get_secret_value(), raw, signature):
            raise Unauthorized("bad signature")
        try:
            hook = whatsapp.parse_webhook(orjson.loads(raw))
        except (
            ValueError
        ):  # bad JSON or a shape Meta never documented: log, never 500 (Meta retries for days)
            log.warning("whatsapp.webhook_unparseable", size=len(raw))
            return Response(status_code=200)
        async with deps.databases.transaction(LogicalDb.AUTOMATION) as s:
            media_pending = await inbound.record_whatsapp(s, hook)
        for inbound_id in media_pending:
            await deps.scheduler.enqueue_media_ingest(inbound_id)
        return Response(status_code=200)


def _telnyx_routes(router: APIRouter, deps: WebhookDeps) -> None:
    tx = deps.settings.telnyx
    urls = callback_urls(deps.settings)

    async def checked_form(request: Request, attempt_id: uuid.UUID) -> dict[str, str]:
        raw = await request.body()
        if not urls.verify(attempt_id, request.query_params.get("t", "")):
            raise Unauthorized("bad callback token")
        public_key = tx.public_key.get_secret_value()
        # L5 (security review 19 Nov): with the public key configured the Ed25519 signature is required; a
        # missing header no longer skips the check (TeXML callbacks carry telnyx-signature-ed25519)
        if public_key and not calls.verify_signature(
            public_key,
            raw,
            request.headers.get(calls.TIMESTAMP_HEADER),
            request.headers.get(calls.SIGNATURE_HEADER),
        ):
            raise Unauthorized("bad signature")
        # TeXML callbacks are application/x-www-form-urlencoded; stdlib parsing avoids a multipart dependency
        return dict(parse_qsl(raw.decode(errors="replace"), keep_blank_values=True))

    @router.post("/telnyx/gather/{attempt_id}")
    async def telnyx_gather(attempt_id: uuid.UUID, request: Request) -> Response:
        digits = (await checked_form(request, attempt_id)).get("Digits", "")[:1]
        async with deps.databases.transaction(LogicalDb.AUTOMATION) as s:
            escalate = await deps.voice.record_digits(s, attempt_id, digits) if digits else None
        if escalate is not None:
            await deps.scheduler.pull_ladder_forward(escalate)
        return Response(calls.gather_reply_texml(digits or None, tx), media_type=_XML)

    @router.post("/telnyx/status/{attempt_id}")
    async def telnyx_status(attempt_id: uuid.UUID, request: Request) -> Response:
        form = await checked_form(request, attempt_id)
        call_id = form.get("CallSid") or form.get("CallSidLegacy") or ""
        async with deps.databases.transaction(LogicalDb.AUTOMATION) as s:
            await deps.voice.record_status(s, attempt_id, call_id, form.get("CallStatus", ""))
        return Response(status_code=204)
