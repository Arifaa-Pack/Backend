"""D43: scripted staff alert calls and §14 click-to-call over Telnyx TeXML (TwiML-compatible). No AI on calls.

Business rule: a call is only an ALERT. Approving needs MFA in the CRM, so the keypad can only acknowledge
("1": stop calling me) or hand the approval to the manager ("2"). Nothing a caller presses can approve,
change or send a quote.

* **Placing a call:** one REST request, `POST /v2/texml/Accounts/{account_sid}/Calls`, with the TeXML inline
  (`Texml`) and a status callback, over the shared httpx client (`HttpRetry`, respx-testable, no SDK).
* **Callbacks** (Gather action, status) are form posts to `/webhooks/telnyx/{gather,status}/{attempt}`. Each
  URL carries a short-lived HMAC token bound to the attempt (`CallbackUrls`), always checked; when the Telnyx
  public key is configured the Ed25519 signature headers are checked as well (`verify_signature`).
* TeXML is built with the standard library (`xml.etree`), so no telephony SDK is needed.

Later (server plan v2 §2): an Asterisk + UAE operator SIP adapter can implement the same `Caller` protocol.
"""

from __future__ import annotations

import base64
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Final
from xml.etree import ElementTree as ET

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from kx.automation._shared.channels import ChannelError, HttpRetry
from kx.automation._shared.settings import TelnyxSettings
from kx.automation.models import CallStatus
from kx.core import signed

PROVIDER: Final = "telnyx"
SIGNATURE_HEADER: Final = "telnyx-signature-ed25519"
TIMESTAMP_HEADER: Final = "telnyx-timestamp"
SIGNATURE_TOLERANCE_SECONDS: Final = 300


class Digit(StrEnum):
    ACKNOWLEDGE = "1"
    ESCALATE = "2"  # §16: send it to the manager now


# TeXML CallStatus (same values as TwiML) -> call_attempts.status
CALL_STATUS: Final[dict[str, CallStatus]] = {
    "queued": "queued",
    "initiated": "queued",
    "ringing": "ringing",
    "in-progress": "answered",
    "answered": "answered",
    "completed": "completed",
    "busy": "busy",
    "no-answer": "no_answer",
    "failed": "failed",
    "canceled": "failed",
}
TERMINAL_STATUSES: Final[frozenset[CallStatus]] = frozenset({"completed", "busy", "no_answer", "failed"})


@dataclass(frozen=True, slots=True)
class AlertScript:
    """What the call says. `prompt` is spoken inside the Gather so a key press interrupts it."""

    intro: str
    prompt: str


def approval_alert_script(title: str) -> AlertScript:
    """§16 60-minute alert call (scripted, not AI): "A quotation is waiting for your approval in the CRM".
    It only reminds: approval always happens in the CRM with two-step login."""
    return AlertScript(
        intro="This is the Arifaa Pack sales system.",
        prompt=(
            f"{title} is waiting for your approval in the CRM. "
            f"Press {Digit.ACKNOWLEDGE.value} to acknowledge. "
            f"Press {Digit.ESCALATE.value} to send it to the manager."
        ),
    )


def change_request_script(title: str) -> AlertScript:
    """§13.5 / §16: the same reminder call for a buyer change request."""
    return AlertScript(
        intro="This is the Arifaa Pack sales system.",
        prompt=(
            f"{title} is waiting for you in the CRM. The buyer was promised an updated quotation. "
            f"Press {Digit.ACKNOWLEDGE.value} to acknowledge. "
            f"Press {Digit.ESCALATE.value} to send it to the manager."
        ),
    )


def escalation_script(title: str, approver_name: str) -> AlertScript:
    """§16 120-minute escalation to the manager, who may approve or reassign."""
    return AlertScript(
        intro="This is the Arifaa Pack sales system.",
        prompt=(
            f"{title} is still waiting. {approver_name} did not respond. "
            "Please review it in the CRM: you can approve it or reassign the deal. "
            f"Press {Digit.ACKNOWLEDGE.value} to acknowledge."
        ),
    )


def negotiation_paused_script(respond_by_local: str) -> AlertScript:
    return AlertScript(
        intro="This is the Arifaa Pack sales system.",
        prompt=(
            "A buyer asked for a price below the approved range. "
            f"The assistant has paused and promised a reply by {respond_by_local}, Dubai time. "
            f"Please open the paused deal in the CRM. Press {Digit.ACKNOWLEDGE.value} to acknowledge."
        ),
    )


# ---------------------------------------------------------------- TeXML
def _say(parent: ET.Element, text: str, settings: TelnyxSettings, loop: int | None = None) -> None:
    attrs = {"voice": settings.voice, "language": settings.language}
    if loop is not None:
        attrs["loop"] = str(loop)
    ET.SubElement(parent, "Say", attrs).text = text


def _xml(root: ET.Element) -> str:
    return '<?xml version="1.0" encoding="UTF-8"?>' + ET.tostring(root, encoding="unicode")


def alert_texml(script: AlertScript, action_url: str, settings: TelnyxSettings) -> str:
    response = ET.Element("Response")
    _say(response, script.intro, settings)
    gather = ET.SubElement(
        response, "Gather", {"numDigits": "1", "action": action_url, "method": "POST", "timeout": "8"}
    )
    _say(gather, script.prompt, settings, loop=2)
    _say(response, "No choice received. Goodbye.", settings)
    return _xml(response)


def gather_reply_texml(digit: str | None, settings: TelnyxSettings) -> str:
    text = {
        Digit.ACKNOWLEDGE.value: "Thank you. Acknowledged. Please approve in the CRM.",
        Digit.ESCALATE.value: "Thank you. The manager is being alerted now.",
    }.get(digit or "", "That is not a valid option. Please check the CRM. Goodbye.")
    response = ET.Element("Response")
    _say(response, text, settings)
    ET.SubElement(response, "Hangup")
    return _xml(response)


def bridge_texml(intro: str, buyer_e164: str, settings: TelnyxSettings) -> str:
    """§14 click-to-call, leg 2: the salesperson answered; dial the buyer from the company number."""
    response = ET.Element("Response")
    _say(response, intro, settings)
    dial = ET.SubElement(
        response, "Dial", {"callerId": settings.from_number, "timeout": str(settings.ring_timeout_seconds)}
    )
    ET.SubElement(dial, "Number").text = buyer_e164
    return _xml(response)


# ---------------------------------------------------------------- callback URLs + signatures
class CallbackUrls:
    """Public callback URLs for one call attempt, each with an HMAC token bound to that attempt (the token is
    the authentication that never depends on a vendor's signature format)."""

    def __init__(self, public_base_url: str, secret: str, ttl_seconds: int) -> None:
        self._base = public_base_url.rstrip("/")
        self._secret = secret
        self._ttl = ttl_seconds

    def _token(self, attempt_id: uuid.UUID, now: datetime | None = None) -> str:
        expires = (now or datetime.now(UTC)) + timedelta(seconds=self._ttl)
        return signed.sign(self._secret, {"a": str(attempt_id)}, expires_at=expires)

    def gather(self, attempt_id: uuid.UUID) -> str:
        return f"{self._base}/webhooks/telnyx/gather/{attempt_id}?t={self._token(attempt_id)}"

    def status(self, attempt_id: uuid.UUID) -> str:
        return f"{self._base}/webhooks/telnyx/status/{attempt_id}?t={self._token(attempt_id)}"

    def verify(self, attempt_id: uuid.UUID, token: str, now: datetime | None = None) -> bool:
        claims = signed.verify(self._secret, token, now=now or datetime.now(UTC))
        return claims is not None and claims.get("a") == str(attempt_id)


def verify_signature(
    public_key_b64: str,
    body: bytes,
    timestamp: str | None,
    signature_b64: str | None,
    now: float | None = None,
) -> bool:
    """Telnyx webhook signature: Ed25519 over `"{timestamp}|{body}"`, base64 in `telnyx-signature-ed25519`;
    a timestamp older than 5 minutes is refused (replays)."""
    if not public_key_b64 or not timestamp or not signature_b64:
        return False
    try:
        if abs((now if now is not None else time.time()) - int(timestamp)) > SIGNATURE_TOLERANCE_SECONDS:
            return False
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key_b64))
        key.verify(base64.b64decode(signature_b64), timestamp.encode() + b"|" + body)
    except ValueError, InvalidSignature:
        return False
    return True


# ---------------------------------------------------------------- placing calls
class TelnyxCaller:
    """`Caller` over the Telnyx TeXML Calls API."""

    provider = PROVIDER

    def __init__(
        self, http: httpx.AsyncClient, settings: TelnyxSettings, retry: HttpRetry | None = None
    ) -> None:
        self._http = http
        self._settings = settings
        self._retry = retry or HttpRetry()

    async def place_call(self, to_e164: str, texml: str, status_callback_url: str) -> str:
        """Start an outbound call with inline TeXML; returns the call id Telnyx reports."""
        s = self._settings
        url = f"{s.api_base_url.rstrip('/')}/v2/texml/Accounts/{s.account_sid}/Calls"
        body = {
            "To": to_e164,
            "From": s.from_number,
            "Texml": texml,
            "Timeout": s.ring_timeout_seconds,
            "StatusCallback": status_callback_url,
            "StatusCallbackMethod": "POST",
            "StatusCallbackEvent": "initiated ringing answered completed",
        }
        response = await self._retry.request(
            self._http,
            PROVIDER,
            "POST",
            url,
            json=body,
            headers={"Authorization": f"Bearer {s.api_key.get_secret_value()}"},
        )
        call_id = _call_id(response.json())
        if not call_id:
            raise ChannelError(PROVIDER, "response without call id", retryable=False)
        return call_id


def _call_id(payload: object) -> str | None:
    """The TeXML API mirrors TwiML's response (`sid`); accept the other names Telnyx uses too (OPEN #55)."""
    if not isinstance(payload, dict):
        return None
    for key in ("sid", "call_sid", "call_control_id", "id"):
        if isinstance(value := payload.get(key), str) and value:
            return value
    return _call_id(payload.get("data"))
