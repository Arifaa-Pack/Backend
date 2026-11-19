"""Webhook routes end to end (ASGI) against Postgres: signatures, dedupe, outbox events, statuses.

16 Nov: Telnyx TeXML callbacks (D43) replace Twilio; email has no webhook (D44, Stalwart is polled)."""

from __future__ import annotations

import base64
import json
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from automation_fakes import FakeScheduler
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi import FastAPI
from pydantic import SecretStr
from sqlalchemy import text
from test_auto_whatsapp import meta_payload, text_message

from kx.automation._shared.settings import AutomationSettings
from kx.automation.alerts.service import VoiceAlerts, callback_urls
from kx.automation.email.feedback import parse_feedback
from kx.automation.inbound.service import record_mail_event
from kx.automation.webhooks.router import WebhookDeps, build_router
from kx.core.errors import install_problem_handlers
from kx.core.identity import hmac_sha256_hex, recipient_hash
from kx.core.settings import LogicalDb

pytestmark = pytest.mark.db
SECRET = "app-secret"
BASE = "https://api.arifapack.com"
SETTINGS = AutomationSettings.model_validate(
    {
        "whatsapp": {"app_secret": SECRET, "verify_token": "vt"},
        "telnyx": {"callback_secret": "cb-secret"},
        "public_base_url": BASE,
    }
)


class FakeCaller:
    async def place_call(self, to_e164: str, texml: str, status_callback_url: str) -> str:
        return "CA1"


@dataclass
class Hook:
    http: httpx.AsyncClient
    db: Any
    scheduler: FakeScheduler

    async def sql(self, statement: str, **params: Any) -> list[Any]:
        async with self.db.transaction(LogicalDb.AUTOMATION) as s:
            return list((await s.execute(text(statement), params)).all())

    async def whatsapp(self, payload: dict[str, Any], *, secret: str = SECRET) -> httpx.Response:
        body = json.dumps(payload).encode()
        sig = "sha256=" + hmac_sha256_hex(secret.encode(), body)
        return await self.http.post("/webhooks/whatsapp", content=body, headers={"X-Hub-Signature-256": sig})


@pytest.fixture
async def hook(auto_db: Any) -> AsyncIterator[Hook]:
    scheduler = FakeScheduler()
    app = FastAPI()
    install_problem_handlers(app)
    voice = VoiceAlerts(auto_db, SETTINGS, FakeCaller())
    app.include_router(build_router(WebhookDeps(auto_db, SETTINGS, scheduler, voice)))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE) as http:
        yield Hook(http, auto_db, scheduler)


async def test_verify_challenge(hook: Hook) -> None:
    ok = await hook.http.get(
        "/webhooks/whatsapp",
        params={"hub.mode": "subscribe", "hub.verify_token": "vt", "hub.challenge": "42"},
    )
    assert (ok.status_code, ok.text) == (200, "42")
    bad = await hook.http.get(
        "/webhooks/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "x", "hub.challenge": "42"}
    )
    assert bad.status_code == 401


async def test_bad_signature_writes_nothing(hook: Hook) -> None:
    response = await hook.whatsapp(meta_payload([text_message()]), secret="wrong")
    assert response.status_code == 401
    assert await hook.sql("SELECT count(*) FROM webhook_receipts") == [(0,)]


async def test_inbound_text_stored_once_and_announced_with_hash_only(hook: Hook) -> None:
    payload = meta_payload(
        [text_message()], contacts=[{"profile": {"name": "Ahmed"}, "wa_id": "971501234567"}]
    )
    assert (await hook.whatsapp(payload)).status_code == 200
    assert (await hook.whatsapp(payload)).status_code == 200  # Meta redelivers
    rows = await hook.sql("SELECT kind, text FROM inbound_messages")
    assert rows == [("text", "Need 50,000 pouches")]
    (event,) = await hook.sql("SELECT event_type, payload FROM outbox_events")
    assert event[0] == "message.received.v1"
    assert event[1]["from_address_hash"] == recipient_hash("whatsapp", "+971501234567")
    assert "971501234567" not in json.dumps(event[1])
    (thread,) = await hook.sql("SELECT external_key, last_inbound_at IS NOT NULL FROM threads")
    assert thread == ("+971501234567", True)


async def test_media_message_waits_for_fetch(hook: Hook) -> None:
    msg = {
        "from": "971501234567",
        "id": "wamid.AUD",
        "timestamp": "1793600000",
        "type": "audio",
        "audio": {"id": "M3", "mime_type": "audio/ogg"},
    }
    await hook.whatsapp(meta_payload([msg]))
    assert len(hook.scheduler.named("media")) == 1
    assert await hook.sql("SELECT count(*) FROM outbox_events") == [(0,)]


async def test_stop_keyword_suppresses(hook: Hook) -> None:
    """§8.1 steps 1-2 and 9: the whole-message word stops every channel at once and is logged for good."""
    await hook.whatsapp(meta_payload([text_message(body=" STOP ")]))
    assert await hook.sql("SELECT channel, reason FROM suppressions") == [("all", "rule:all")]
    assert await hook.sql("SELECT scope, source, decided_by, exact_words FROM opt_out_log") == [
        ("all", "rule", "word:stop", " STOP ")
    ]
    events = await hook.sql("SELECT event_type, payload->>'opted_out' FROM outbox_events ORDER BY sequence")
    assert events == [("consent.opted_out.v1", None), ("message.received.v1", "true")]


async def test_channel_phrase_stops_one_channel_and_a_sentence_goes_to_the_ai(hook: Hook) -> None:
    """§8.1 step 6: "use email instead" stops WhatsApp only; "stop, let me check" is read by the AI."""
    await hook.whatsapp(meta_payload([text_message(body="Please use email instead")]))
    assert await hook.sql("SELECT channel FROM suppressions") == [("whatsapp",)]
    await hook.whatsapp(meta_payload([text_message(wamid="wamid.IN2", body="stop, let me check")]))
    rows = await hook.sql(
        "SELECT payload->>'stop_in_sentence' FROM outbox_events WHERE event_type = 'message.received.v1' "
        "ORDER BY sequence"
    )
    assert rows == [("false",), ("true",)]
    logged = await hook.sql(
        "SELECT scope, decided_by FROM opt_out_log WHERE exact_words IN ('Please use email instead', "
        "'stop, let me check')"
    )
    assert logged == [("whatsapp", "channel:use email instead")]  # the sentence stopped nothing by itself


async def test_statuses_move_forward_only(hook: Hook) -> None:
    await hook.sql(
        "INSERT INTO outbound_messages (channel_account_id, to_address, kind, requested_by, idempotency_key, status, "
        "provider_message_id) SELECT id, '+971501234567', 'reply', 'ai', 'k1', 'sent', 'wamid.OUT' "
        "FROM channel_accounts WHERE external_account_id = 'PNID1' RETURNING id"
    )
    status = {"id": "wamid.OUT", "timestamp": "1793600100", "recipient_id": "971501234567"}
    await hook.whatsapp(meta_payload(statuses=[status | {"status": "read"}]))
    await hook.whatsapp(meta_payload(statuses=[status | {"status": "delivered"}]))  # late, out of order
    assert await hook.sql("SELECT status, read_at IS NOT NULL FROM outbound_messages") == [("read", True)]
    events = await hook.sql("SELECT payload->>'status' FROM outbox_events ORDER BY sequence")
    assert events == [("read",)]


async def test_an_unparseable_sender_never_returns_500(hook: Hook) -> None:
    """Bench R1: a sender id that is not a phone number used to raise -> HTTP 500 -> Meta retried for days.
    Now that item is skipped and logged, the rest of the batch is stored, and Meta gets its 200."""
    bad = text_message(wamid="wamid.BAD") | {"from": "not-a-number"}
    good = text_message(wamid="wamid.OK")
    assert (await hook.whatsapp(meta_payload([bad, good]))).status_code == 200
    assert await hook.sql("SELECT provider_message_id FROM inbound_messages") == [("wamid.OK",)]
    garbage = b'{"object": "whatsapp_business_account", "entry": "nope"}'
    sig = "sha256=" + hmac_sha256_hex(SECRET.encode(), garbage)
    response = await hook.http.post(
        "/webhooks/whatsapp", content=garbage, headers={"X-Hub-Signature-256": sig}
    )
    assert response.status_code == 200


def signed(path_kind: str, attempt_id: uuid.UUID) -> str:
    urls = callback_urls(SETTINGS)
    url = urls.gather(attempt_id) if path_kind == "gather" else urls.status(attempt_id)
    return url.removeprefix(BASE)


async def test_telnyx_gather_press_2_pulls_ladder_forward(hook: Hook) -> None:
    approval = uuid.uuid4()
    (row,) = await hook.sql(
        "INSERT INTO call_attempts (purpose, approval_ref, to_e164, provider) "
        "VALUES ('approval_alert', :a, '+971501111111', 'telnyx') RETURNING id",
        a=approval,
    )
    form = {"CallSid": "v3:1", "Digits": "2"}
    unsigned = await hook.http.post(f"/webhooks/telnyx/gather/{row[0]}", data=form)
    assert unsigned.status_code == 401
    # a correctly signed URL for an attempt that does not exist: no "manager is being alerted" reply
    other = await hook.http.post(signed("gather", uuid.uuid4()), data=form)
    assert other.status_code == 404
    response = await hook.http.post(signed("gather", row[0]), data=form)
    assert response.status_code == 200 and "manager is being alerted" in response.text
    assert hook.scheduler.named("pull") == [{"approval_ref": approval}]
    assert await hook.sql("SELECT digits_pressed, status FROM call_attempts") == [("2", "answered")]


async def test_a_token_for_another_attempt_is_refused(hook: Hook) -> None:
    (row,) = await hook.sql(
        "INSERT INTO call_attempts (purpose, to_e164, provider) "
        "VALUES ('below_range_alert', '+971501111111', 'telnyx') RETURNING id"
    )
    token = signed("status", uuid.uuid4()).partition("?t=")[2]
    response = await hook.http.post(
        f"/webhooks/telnyx/status/{row[0]}?t={token}", data={"CallStatus": "busy"}
    )
    assert response.status_code == 401


async def test_telnyx_status_never_regresses(hook: Hook) -> None:
    (row,) = await hook.sql(
        "INSERT INTO call_attempts (purpose, to_e164, provider) "
        "VALUES ('below_range_alert', '+971501111111', 'telnyx') RETURNING id"
    )
    path = signed("status", row[0])
    for status in ("completed", "ringing"):
        form = {"CallSid": "v3:7", "CallStatus": status}
        assert (await hook.http.post(path, data=form)).status_code == 204
    assert await hook.sql("SELECT status, provider_call_id, ended_at IS NOT NULL FROM call_attempts") == [
        ("completed", "v3:7", True)
    ]


async def test_a_mailbox_bounce_report_suppresses_and_fails(hook: Hook) -> None:
    """D44: the DSN read from Stalwart's inbox does what the SES SNS event used to do."""
    from test_auto_email import DSN_BOUNCE

    await hook.sql(
        "INSERT INTO outbound_messages (channel_account_id, to_address, kind, requested_by, idempotency_key, status, "
        "provider_message_id) SELECT id, 'ahmed@buyer.ae', 'reply', 'ai', 'k1', 'sent', 'q-1045.v1@arifapack.com' "
        "FROM channel_accounts WHERE channel = 'email' RETURNING id"
    )
    feedback = parse_feedback(DSN_BOUNCE)
    assert feedback is not None
    async with hook.db.transaction(LogicalDb.AUTOMATION) as s:
        await record_mail_event(s, feedback)
    assert await hook.sql("SELECT status FROM outbound_messages") == [("failed",)]
    assert await hook.sql("SELECT channel, reason FROM suppressions") == [("email", "bounce")]
    assert await hook.sql("SELECT payload->>'status' FROM outbox_events") == [("bounced",)]


async def test_with_a_public_key_the_telnyx_signature_is_required(auto_db: Any) -> None:
    """L5 (security review 19 Nov): a valid URL token alone is not enough once the Ed25519 key is configured;
    leaving the signature header out no longer skips the check."""
    key = Ed25519PrivateKey.generate()
    public = base64.b64encode(key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
    settings = SETTINGS.model_copy(
        update={"telnyx": SETTINGS.telnyx.model_copy(update={"public_key": SecretStr(public)})}
    )
    app = FastAPI()
    install_problem_handlers(app)
    app.include_router(
        build_router(
            WebhookDeps(auto_db, settings, FakeScheduler(), VoiceAlerts(auto_db, settings, FakeCaller()))
        )
    )
    async with auto_db.transaction(LogicalDb.AUTOMATION) as s:
        (attempt,) = (
            await s.execute(
                text(
                    "INSERT INTO call_attempts (purpose, to_e164, provider) "
                    "VALUES ('below_range_alert', '+971501111111', 'telnyx') RETURNING id"
                )
            )
        ).one()
    path = callback_urls(settings).status(attempt).removeprefix(BASE)
    body = b"CallSid=v3%3A9&CallStatus=completed"
    form = {"content-type": "application/x-www-form-urlencoded"}
    stamp = str(int(time.time()))
    good = base64.b64encode(key.sign(stamp.encode() + b"|" + body)).decode()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE) as http:
        assert (await http.post(path, content=body, headers=form)).status_code == 401  # no signature header
        forged = {
            **form,
            "telnyx-timestamp": stamp,
            "telnyx-signature-ed25519": base64.b64encode(b"x" * 64).decode(),
        }
        assert (await http.post(path, content=body, headers=forged)).status_code == 401
        signed_ok = {**form, "telnyx-timestamp": stamp, "telnyx-signature-ed25519": good}
        assert (await http.post(path, content=body, headers=signed_ok)).status_code == 204
