"""D43 Telnyx TeXML calls: TeXML content, signed callback URLs, Ed25519 signatures, REST placement over respx."""

from __future__ import annotations

import base64
import time
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import orjson
import pytest
import respx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from tenacity import wait_none

from kx.automation.channels import ChannelError, HttpRetry
from kx.automation.channels.calls import (
    CallbackUrls,
    TelnyxCaller,
    alert_texml,
    approval_alert_script,
    bridge_texml,
    gather_reply_texml,
    verify_signature,
)
from kx.automation.settings import TelnyxSettings

SETTINGS = TelnyxSettings(api_key="KEY-1", account_sid="TX123", from_number="+97140000000")  # type: ignore[arg-type]


def test_alert_texml_is_an_alert_not_an_approval() -> None:
    xml = alert_texml(
        approval_alert_script("Quote Q-1045 for AED 11,390.20"),
        "https://api.x/webhooks/telnyx/gather/1?t=abc",
        SETTINGS,
    )
    assert xml.startswith('<?xml version="1.0" encoding="UTF-8"?><Response>')
    assert 'action="https://api.x/webhooks/telnyx/gather/1?t=abc"' in xml and 'numDigits="1"' in xml
    assert "Quote Q-1045 for AED 11,390.20 is waiting for your approval" in xml
    assert "Press 1 to acknowledge" in xml and "Press 2 to send it to the manager" in xml
    assert "approve" not in gather_reply_texml("9", SETTINGS).lower().split("check")[0]
    assert "<Hangup />" in gather_reply_texml("1", SETTINGS)


def test_bridge_dials_the_buyer_from_the_company_number() -> None:
    xml = bridge_texml("Connecting you", "+971501234567", SETTINGS)
    assert 'callerId="+97140000000"' in xml and "<Number>+971501234567</Number>" in xml


def test_texml_escapes_text() -> None:
    xml = alert_texml(approval_alert_script("A <b> & C"), "https://x/?a=1&b=2", SETTINGS)
    assert "A &lt;b&gt; &amp; C" in xml and "a=1&amp;b=2" in xml


def test_callback_tokens_are_bound_to_one_attempt_and_expire() -> None:
    urls = CallbackUrls("https://api.arifapack.com/", "secret", ttl_seconds=60)
    a, b = uuid.uuid4(), uuid.uuid4()
    gather = urls.gather(a)
    assert gather.startswith(f"https://api.arifapack.com/webhooks/telnyx/gather/{a}?t=")
    token = gather.partition("?t=")[2]
    assert urls.verify(a, token)
    assert not urls.verify(b, token)  # another attempt's URL
    assert not urls.verify(a, token, now=datetime.now(UTC) + timedelta(seconds=120))  # expired
    assert not CallbackUrls("https://x", "other", 60).verify(a, token)  # another secret


def test_ed25519_signature() -> None:
    key = Ed25519PrivateKey.generate()
    public = base64.b64encode(key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
    body, ts = b"CallStatus=completed", str(int(time.time()))
    sig = base64.b64encode(key.sign(ts.encode() + b"|" + body)).decode()
    assert verify_signature(public, body, ts, sig)
    assert not verify_signature(public, body + b"x", ts, sig)  # body changed
    assert not verify_signature(public, body, str(int(time.time()) - 3600), sig)  # replay window
    assert not verify_signature(public, body, ts, None)
    assert not verify_signature("", body, ts, sig)


@respx.mock
async def test_place_call_posts_inline_texml_with_status_callback() -> None:
    route = respx.post("https://api.telnyx.com/v2/texml/Accounts/TX123/Calls").mock(
        return_value=httpx.Response(200, json={"sid": "v3:call-1", "status": "queued"})
    )
    async with httpx.AsyncClient() as http:
        call_id = await TelnyxCaller(http, SETTINGS, HttpRetry(attempts=2, wait=wait_none())).place_call(
            "+971501234567", "<Response/>", "https://api.x/webhooks/telnyx/status/1?t=abc"
        )
    assert call_id == "v3:call-1"
    body = orjson.loads(route.calls[0].request.content)
    assert body["To"] == "+971501234567" and body["Texml"] == "<Response/>"
    assert body["From"] == "+97140000000" and body["StatusCallback"].endswith("?t=abc")
    assert route.calls[0].request.headers["Authorization"] == "Bearer KEY-1"


@respx.mock
async def test_call_id_under_data_and_missing_id() -> None:
    route = respx.post("https://api.telnyx.com/v2/texml/Accounts/TX123/Calls")
    route.mock(return_value=httpx.Response(200, json={"data": {"call_sid": "c-2"}}))
    async with httpx.AsyncClient() as http:
        caller = TelnyxCaller(http, SETTINGS, HttpRetry(attempts=1, wait=wait_none()))
        assert await caller.place_call("+971501234567", "<Response/>", "https://x") == "c-2"
        route.mock(return_value=httpx.Response(200, json={"status": "queued"}))
        with pytest.raises(ChannelError):
            await caller.place_call("+971501234567", "<Response/>", "https://x")
