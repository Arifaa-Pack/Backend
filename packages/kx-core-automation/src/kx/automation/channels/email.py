"""Email building blocks: MIME building, the Amazon SES v2 API sender (D44: the app's backup route when
Stalwart is down) and inbound MIME parsing (buyer replies read from Stalwart's inbox by `mail_poll`)."""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from email import message_from_bytes, policy
from email.message import EmailMessage
from email.utils import (
    format_datetime,
    formataddr,
    getaddresses,
    make_msgid,
    parseaddr,
    parsedate_to_datetime,
)
from typing import Any, Final, Protocol

from selectolax.lexbor import LexborHTMLParser

from kx.automation.channels import ChannelError
from kx.automation.settings import SesSettings
from kx.core.identity import normalise_email

PROVIDER: Final = "ses"
_RETRYABLE_AWS: Final = frozenset(
    {
        "Throttling",
        "ThrottlingException",
        "TooManyRequestsException",
        "ServiceUnavailable",
        "InternalFailure",
        "RequestTimeout",
    }
)


class SesV2Client(Protocol):
    """The slice of an aioboto3 `sesv2` client we use (create it once in the app lifespan)."""

    async def send_email(self, **kwargs: Any) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class Attachment:
    filename: str
    content_type: str
    data: bytes


# ---------------------------------------------------------------- outbound
def build_mime(
    *,
    from_address: str,
    to_address: str,
    subject: str,
    body: str,
    attachment: Attachment | None = None,
    reply_to: str | None = None,
    in_reply_to: str | None = None,
    list_unsubscribe: str | None = None,
    from_name: str | None = None,
) -> EmailMessage:
    msg = EmailMessage(policy=policy.SMTP)
    # §14: the salesperson's name as sender, always on the company address (replies land in the deal)
    msg["From"] = formataddr((from_name, from_address)) if from_name else from_address
    msg["To"] = to_address
    msg["Subject"] = subject
    msg["Date"] = format_datetime(datetime.now(UTC))
    msg["Message-ID"] = make_msgid(domain=from_address.rpartition("@")[2] or None)
    if reply_to:
        msg["Reply-To"] = reply_to
    if in_reply_to:  # keeps the buyer's mail client threading our reply under theirs
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = in_reply_to
    if list_unsubscribe:  # §8.1 step 10 / RFC 8058 one-click: outside the body, so grant hashes are untouched
        msg["List-Unsubscribe"] = f"<{list_unsubscribe}>"
        msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    msg.set_content(body)
    if attachment is not None:
        maintype, _, subtype = attachment.content_type.partition("/")
        msg.add_attachment(
            attachment.data,
            maintype=maintype,
            subtype=subtype or "octet-stream",
            filename=attachment.filename,
        )
    return msg


class EmailSender:
    def __init__(self, ses: SesV2Client, settings: SesSettings) -> None:
        self._ses = ses
        self._settings = settings

    async def send(
        self,
        *,
        to_address: str,
        subject: str,
        body: str,
        attachment: Attachment | None = None,
        in_reply_to: str | None = None,
        list_unsubscribe: str | None = None,
        from_name: str | None = None,
    ) -> str:
        """Send a raw MIME message; returns the SES MessageId. SES rewrites the Message-ID header to
        `<MessageId@….amazonses.com>`, so a bounce report quotes it back (`feedback.candidate_ids`)."""
        msg = build_mime(
            from_address=self._settings.from_address,
            to_address=to_address,
            subject=subject,
            body=body,
            attachment=attachment,
            in_reply_to=in_reply_to,
            list_unsubscribe=list_unsubscribe,
            from_name=from_name,
        )
        request: dict[str, Any] = {
            "FromEmailAddress": self._settings.from_address,
            "Destination": {"ToAddresses": [to_address]},
            "Content": {"Raw": {"Data": msg.as_bytes()}},
        }
        if self._settings.configuration_set:
            request["ConfigurationSetName"] = self._settings.configuration_set
        try:
            response = await self._ses.send_email(**request)
        except Exception as exc:
            raise _channel_error(exc) from exc
        return str(response["MessageId"])


def _channel_error(exc: Exception) -> ChannelError:
    """botocore ClientError carries `response['Error']['Code']`; anything else is a transport failure."""
    error = getattr(exc, "response", {}).get("Error", {})
    code = str(error.get("Code", type(exc).__name__))
    retryable = code in _RETRYABLE_AWS or not error
    return ChannelError(PROVIDER, code, retryable=retryable)


# ---------------------------------------------------------------- inbound
@dataclass(frozen=True, slots=True)
class InboundEmail:
    message_id: str
    from_address: str
    from_name: str | None
    to_addresses: tuple[str, ...]
    subject: str
    text: str  # reply only: quoted history stripped
    in_reply_to: str | None
    received_at: datetime
    attachments: tuple[Attachment, ...]
    sender_verified: bool = True  # M8: False = DMARC (or SPF and DKIM) did not pass; see sender_verified()


_AR_COMMENT: Final = re.compile(r"\([^()]*\)")
_AR_RESULT: Final = re.compile(r"^\s*([a-z0-9_-]+)(?:/[0-9]+)?\s*=\s*([a-z]+)")


def _authserv_id(header: str) -> str:
    return (_AR_COMMENT.sub(" ", header).split(";", 1)[0].split() or [""])[0].lower()


def sender_verified(msg: EmailMessage, authserv_id: str = "") -> bool:
    """M8 (security review 19 Nov): may we trust the From address? Read from the `Authentication-Results`
    header our own mail server (Stalwart) adds on receipt (RFC 8601). With `authserv_id` (Stalwart's host
    name) only a header with that id counts, so a header the sender wrote into the message is ignored;
    without it only the topmost header counts (the receiving server prepends its own). Trusted when DMARC
    is `pass`; with no DMARC result, when SPF or DKIM passes. No header at all = not verified."""
    headers = [str(h) for h in msg.get_all("Authentication-Results", [])]
    if authserv_id:
        headers = [h for h in headers if _authserv_id(h) == authserv_id.lower()]
    if not headers:
        return False
    results: dict[str, set[str]] = {}
    for part in _AR_COMMENT.sub(" ", headers[0]).split(";")[1:]:
        if (m := _AR_RESULT.match(part.lower())) is not None:
            results.setdefault(m.group(1), set()).add(m.group(2))
    if "dmarc" in results:
        return "pass" in results["dmarc"]
    return "pass" in results.get("spf", set()) or "pass" in results.get("dkim", set())


def parse_inbound(raw: bytes, *, fallback_id: str, authserv_id: str = "") -> InboundEmail:
    msg = message_from_bytes(raw, policy=policy.default)
    name, address = parseaddr(str(msg.get("From", "")))
    received = msg.get("Date")
    return InboundEmail(
        message_id=str(msg.get("Message-ID") or fallback_id).strip(),
        from_address=normalise_email(address),
        from_name=name or None,
        to_addresses=tuple(_addresses(msg, "To", "Cc")),
        subject=str(msg.get("Subject", "")),
        text=strip_quoted(_body_text(msg)),
        in_reply_to=str(v).strip() if (v := msg.get("In-Reply-To")) else None,
        received_at=parsedate_to_datetime(str(received)).astimezone(UTC) if received else datetime.now(UTC),
        attachments=tuple(_attachments(msg)),
        sender_verified=sender_verified(msg, authserv_id),
    )


def _addresses(msg: EmailMessage, *headers: str) -> Iterator[str]:
    for _, addr in getaddresses([str(v) for h in headers for v in msg.get_all(h, [])]):
        try:
            yield normalise_email(addr)
        except ValueError:
            continue


def _body_text(msg: EmailMessage) -> str:
    part = msg.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    content = str(part.get_content())
    return html_to_text(content) if part.get_content_subtype() == "html" else content


def _attachments(msg: EmailMessage) -> Iterator[Attachment]:
    for part in msg.iter_attachments():
        if not isinstance(part, EmailMessage):
            continue
        payload = part.get_payload(decode=True)
        if isinstance(payload, bytes):
            yield Attachment(part.get_filename() or "attachment", part.get_content_type(), payload)


def html_to_text(html: str) -> str:
    tree = LexborHTMLParser(html)
    tree.strip_tags(["script", "style", "head"])
    for quote in tree.css("blockquote, div.gmail_quote, div#appendonsend, div.OutlookMessageHeader"):
        quote.decompose()
    root = tree.body or tree.root
    return root.text(separator="\n", strip=True) if root is not None else ""


# Lines that start the quoted history in common clients (Gmail, Outlook, Apple Mail; en + ar).
_QUOTE_HEADER: Final = re.compile(
    r"^(On .{4,200} wrote:|-{2,}\s*Original Message\s*-{2,}|From:\s.+|Sent from my \w+|"
    r"في .{4,200} كتب.*:|_{5,})\s*$",
    re.IGNORECASE,
)


def strip_quoted(text: str) -> str:
    """Keep only the new reply: stop at the first quote header; drop '>' quoted lines."""
    kept: list[str] = []
    for line in text.replace("\r\n", "\n").split("\n"):
        stripped = line.strip()
        if _QUOTE_HEADER.match(stripped):
            break
        if not stripped.startswith(">"):
            kept.append(line.rstrip())
    return "\n".join(kept).strip()
