"""D44: bounce and complaint reports that arrive as email (they used to come from SES through SNS).

Stalwart's own delivery failures and SES's forwarded feedback both reach the sending account's inbox:

* **DSN** (RFC 3464): `multipart/report; report-type=delivery-status`, a `message/delivery-status` part with
  per-recipient `Action:` / `Status:` / `Final-Recipient:` fields, and the original headers.
* **ARF** (RFC 5965): `multipart/report; report-type=feedback-report`, a `message/feedback-report` part with
  `Feedback-Type: abuse` (a complaint) and the original message.

`parse_feedback` returns None for ordinary mail, so the poller can route every inbox message through it first.
Only permanent failures (`Action: failed`, `Status: 5.x.x`) count as bounces: a temporary failure is retried
by
the mail server and says nothing about the address.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from email import message_from_bytes, policy
from email.message import EmailMessage, Message
from email.parser import HeaderParser
from email.utils import parsedate_to_datetime
from typing import Literal

from kx.core.identity import normalise_email

FeedbackKind = Literal["bounce", "complaint"]


@dataclass(frozen=True, slots=True)
class MailFeedback:
    kind: FeedbackKind
    original_message_id: str | None  # without <>; matches outbound_messages.provider_message_id
    recipients: tuple[str, ...]
    permanent: bool
    detail: str | None
    at: datetime


def parse_feedback(raw: bytes) -> MailFeedback | None:
    msg = message_from_bytes(raw, policy=policy.default)
    if msg.get_content_type() != "multipart/report":
        return None
    report_type = (msg.get_param("report-type") or "").lower()  # type: ignore[union-attr]
    at = _date(msg)
    if report_type == "delivery-status":
        return _dsn(msg, at)
    if report_type == "feedback-report":
        return _arf(msg, at)
    return None


def _dsn(msg: EmailMessage, at: datetime) -> MailFeedback | None:
    recipients: list[str] = []
    statuses: list[str] = []
    for part in msg.walk():
        if part.get_content_type() != "message/delivery-status":
            continue
        for block in _status_blocks(part):
            action = block.get("Action", "").strip().lower()
            status = block.get("Status", "").strip()
            addr = _address(block.get("Final-Recipient") or block.get("Original-Recipient"))
            if action == "failed" and status.startswith("5") and addr is not None:
                recipients.append(addr)
                statuses.append(status)
    if not recipients:
        return None  # delayed / relayed / temporary: nothing to suppress
    return MailFeedback(
        "bounce", _original_message_id(msg), tuple(recipients), True, ", ".join(sorted(set(statuses))), at
    )


def _arf(msg: EmailMessage, at: datetime) -> MailFeedback | None:
    for part in msg.walk():
        if part.get_content_type() != "message/feedback-report":
            continue
        fields = _fields(_payload_text(part))
        feedback_type = fields.get("Feedback-Type", "abuse").strip().lower()
        recipients = [a for a in (_address(fields.get("Original-Rcpt-To")),) if a is not None]
        return MailFeedback(
            "complaint", _original_message_id(msg), tuple(recipients), True, feedback_type, at
        )
    return None


def _status_blocks(part: Message) -> list[dict[str, str]]:
    """A delivery-status part is blocks of header fields separated by blank lines (first = per message)."""
    payload = part.get_payload()
    if isinstance(payload, list):  # the email package parses it into Message objects
        return [{k: str(v) for k, v in m.items()} for m in payload if isinstance(m, Message)]
    return [_fields(block) for block in _payload_text(part).split("\n\n") if block.strip()]


def _payload_text(part: Message) -> str:
    payload = part.get_payload(decode=True)
    if isinstance(payload, bytes):
        return payload.decode("utf-8", "replace").replace("\r\n", "\n")
    inner = part.get_payload()
    if isinstance(inner, list):
        return "\n".join(str(m) for m in inner)
    return str(inner).replace("\r\n", "\n")


def _fields(text: str) -> dict[str, str]:
    return {k: str(v) for k, v in HeaderParser().parsestr(text.strip() + "\n").items()}


def _address(value: str | None) -> str | None:
    """`rfc822; buyer@example.com` -> buyer@example.com"""
    if not value:
        return None
    try:
        return normalise_email(value.rpartition(";")[2].strip().strip("<>"))
    except ValueError:
        return None


def _original_message_id(msg: EmailMessage) -> str | None:
    for part in msg.walk():
        ctype = part.get_content_type()
        if ctype in ("message/rfc822", "message/rfc822-headers", "text/rfc822-headers"):
            inner = part.get_payload()
            source: Message | None = None
            if isinstance(inner, list) and inner and isinstance(inner[0], Message):
                source = inner[0]
            elif isinstance(inner, Message):
                source = inner
            else:
                source = HeaderParser().parsestr(_payload_text(part))
            if source is not None and (mid := source.get("Message-ID")):
                return str(mid).strip().strip("<>")
    return None


def _date(msg: EmailMessage) -> datetime:
    value = msg.get("Date")
    try:
        return parsedate_to_datetime(str(value)).astimezone(UTC) if value else datetime.now(UTC)
    except TypeError, ValueError:
        return datetime.now(UTC)


def candidate_ids(message_id: str) -> tuple[str, ...]:
    """Ids to match against `outbound_messages.provider_message_id`: the full Message-ID (Stalwart sends keep
    our own) and its local part (the SES API rewrites the header to `<ses-id@….amazonses.com>`)."""
    mid = message_id.strip().strip("<>")
    local = mid.partition("@")[0]
    return (mid,) if local == mid else (mid, local)
