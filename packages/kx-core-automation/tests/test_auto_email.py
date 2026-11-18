"""SES email: MIME build + raw send (fake client), inbound parsing, SES event and S3 event parsing."""

from __future__ import annotations

from email import message_from_bytes, policy
from email.message import EmailMessage
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from kx.automation.channels import ChannelError
from kx.automation.channels.email import (
    Attachment,
    EmailSender,
    build_mime,
    html_to_text,
    parse_inbound,
    strip_quoted,
)
from kx.automation.channels.feedback import candidate_ids, parse_feedback
from kx.automation.channels.stalwart import JmapClient
from kx.automation.settings import SesSettings, StalwartSettings

PDF = Attachment("Q-1045.pdf", "application/pdf", b"%PDF-1.7 quote")


class FakeSes:
    def __init__(self, error: Exception | None = None) -> None:
        self.requests: list[dict[str, Any]] = []
        self.error = error

    async def send_email(self, **kwargs: Any) -> dict[str, Any]:
        if self.error:
            raise self.error
        self.requests.append(kwargs)
        return {"MessageId": "0102-abc"}


class ClientError(Exception):  # shaped like botocore.exceptions.ClientError
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.response = {"Error": {"Code": code, "Message": "x"}}


def test_build_mime_with_pdf_and_threading() -> None:
    msg = build_mime(
        from_address="sales@arifapack.com",
        to_address="buyer@example.com",
        subject="Your quote",
        body="Please find attached.",
        attachment=PDF,
        in_reply_to="<abc@mail.example.com>",
    )
    parsed = message_from_bytes(msg.as_bytes(), policy=policy.default)
    assert isinstance(parsed, EmailMessage)
    assert parsed["In-Reply-To"] == "<abc@mail.example.com>" and parsed["Message-ID"].endswith(
        "@arifapack.com>"
    )
    (att,) = list(parsed.iter_attachments())
    assert att.get_filename() == "Q-1045.pdf" and att.get_payload(decode=True) == PDF.data
    body = parsed.get_body(("plain",))
    assert body is not None and body.get_content().strip() == "Please find attached."


async def test_sender_sends_raw_with_configuration_set() -> None:
    ses = FakeSes()
    sender = EmailSender(ses, SesSettings(from_address="sales@arifapack.com", configuration_set="kx-events"))
    assert (
        await sender.send(to_address="buyer@example.com", subject="Q", body="b", attachment=PDF) == "0102-abc"
    )
    (req,) = ses.requests
    assert req["ConfigurationSetName"] == "kx-events" and req["Destination"] == {
        "ToAddresses": ["buyer@example.com"]
    }
    assert b"Q-1045.pdf" in req["Content"]["Raw"]["Data"]


@pytest.mark.parametrize(
    ("error", "retryable"),
    [
        (ClientError("TooManyRequestsException"), True),
        (ClientError("MessageRejected"), False),
        (ConnectionError("reset"), True),
    ],
)
async def test_sender_classifies_errors(error: Exception, retryable: bool) -> None:
    with pytest.raises(ChannelError) as info:
        await EmailSender(FakeSes(error), SesSettings()).send(to_address="a@b.com", subject="s", body="b")
    assert info.value.retryable is retryable


RAW_REPLY = b"""From: Ahmed Khan <Ahmed@Buyer.AE>
To: Sales <sales@arifapack.com>
Subject: Re: Your quote Q-1045
Message-ID: <reply-1@buyer.ae>
In-Reply-To: <out-1@arifapack.com>
Date: Tue, 03 Nov 2026 10:15:00 +0400
MIME-Version: 1.0
Content-Type: multipart/mixed; boundary="b1"

--b1
Content-Type: multipart/alternative; boundary="b2"

--b2
Content-Type: text/plain; charset=utf-8

Can you do AED 0.21 per piece for 80,000?

On Mon, 2 Nov 2026 at 09:00, Arifaa Pack <sales@arifapack.com> wrote:
> Your quote Q-1045 is attached.
--b2
Content-Type: text/html; charset=utf-8

<p>Can you do AED 0.21 per piece for 80,000?</p><blockquote>old</blockquote>
--b2--
--b1
Content-Type: application/pdf; name="po.pdf"
Content-Disposition: attachment; filename="po.pdf"
Content-Transfer-Encoding: base64

JVBERi0xLjQ=
--b1--
"""


def test_parse_inbound_reply() -> None:
    mail = parse_inbound(RAW_REPLY, fallback_id="s3-key")
    assert mail.from_address == "ahmed@buyer.ae" and mail.from_name == "Ahmed Khan"
    assert mail.to_addresses == ("sales@arifapack.com",)
    assert mail.text == "Can you do AED 0.21 per piece for 80,000?"
    assert mail.message_id == "<reply-1@buyer.ae>" and mail.in_reply_to == "<out-1@arifapack.com>"
    assert mail.received_at.isoformat() == "2026-11-03T06:15:00+00:00"
    assert [(a.filename, a.data) for a in mail.attachments] == [("po.pdf", b"%PDF-1.4")]


def test_parse_inbound_html_only() -> None:
    raw = (
        b"From: x@y.com\nTo: sales@arifapack.com\nSubject: Hi\nContent-Type: text/html\n\n"
        b"<html><head><style>p{}</style></head><body><p>Need <b>samples</b></p>"
        b"<div class='gmail_quote'>older</div><script>x()</script></body></html>"
    )
    mail = parse_inbound(raw, fallback_id="k1")
    assert mail.text == "Need\nsamples" and mail.message_id == "k1"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Yes please\n\n-----Original Message-----\nFrom: us", "Yes please"),
        ("OK\n> quoted\nmore", "OK\nmore"),
        ("Thanks\nSent from my iPhone", "Thanks"),
        ("موافق\nفي الاثنين ٢ نوفمبر كتب Arifaa:\n> نص", "موافق"),
    ],
)
def test_strip_quoted(text: str, expected: str) -> None:
    assert strip_quoted(text) == expected


def test_html_to_text_drops_quotes() -> None:
    assert html_to_text("<div>new</div><blockquote>old</blockquote>") == "new"


DSN_BOUNCE = b"""From: MAILER-DAEMON@mail.arifapack.com
To: sales@arifapack.com
Subject: Undelivered Mail Returned to Sender
Date: Mon, 16 Nov 2026 06:01:00 +0000
MIME-Version: 1.0
Content-Type: multipart/report; report-type=delivery-status; boundary="b1"

--b1
Content-Type: text/plain

Your message could not be delivered.

--b1
Content-Type: message/delivery-status

Reporting-MTA: dns; mail.arifapack.com

Final-Recipient: rfc822; Ahmed@Buyer.AE
Action: failed
Status: 5.1.1
Diagnostic-Code: smtp; 550 5.1.1 user unknown

--b1
Content-Type: text/rfc822-headers

Message-ID: <q-1045.v1@arifapack.com>
To: ahmed@buyer.ae
Subject: Quotation Q-1045

--b1--
"""

DSN_DELAYED = DSN_BOUNCE.replace(b"Action: failed", b"Action: delayed").replace(b"5.1.1", b"4.4.1")

ARF_COMPLAINT = b"""From: complaints@email-abuse.amazonses.com
To: sales@arifapack.com
Subject: Complaint
MIME-Version: 1.0
Content-Type: multipart/report; report-type=feedback-report; boundary="b2"

--b2
Content-Type: text/plain

This is an email abuse report.

--b2
Content-Type: message/feedback-report

Feedback-Type: abuse
User-Agent: SomeISP
Version: 1
Original-Rcpt-To: buyer@example.com

--b2
Content-Type: message/rfc822

Message-ID: <0102abc@eu-west-1.amazonses.com>
Subject: Quotation Q-2001

hello
--b2--
"""


def test_dsn_permanent_bounce_names_the_address_and_the_original_message() -> None:
    """D44: a permanent bounce suppresses the address and marks the outbound row it refers to."""
    fb = parse_feedback(DSN_BOUNCE)
    assert fb is not None and fb.kind == "bounce" and fb.permanent
    assert fb.recipients == ("ahmed@buyer.ae",)
    assert fb.original_message_id == "q-1045.v1@arifapack.com"
    assert fb.detail == "5.1.1"


def test_a_delayed_delivery_report_is_not_a_bounce() -> None:
    assert parse_feedback(DSN_DELAYED) is None


def test_arf_complaint() -> None:
    fb = parse_feedback(ARF_COMPLAINT)
    assert fb is not None and fb.kind == "complaint" and fb.recipients == ("buyer@example.com",)
    assert fb.original_message_id == "0102abc@eu-west-1.amazonses.com"


def test_ordinary_mail_is_not_feedback() -> None:
    assert parse_feedback(RAW_REPLY) is None


def test_candidate_ids_match_stalwart_and_ses_ids() -> None:
    """Stalwart keeps our Message-ID; the SES API rewrites it to <ses-id@….amazonses.com> and stores ses-id."""
    assert candidate_ids("<q-1@arifapack.com>") == ("q-1@arifapack.com", "q-1")
    assert candidate_ids("0102abc") == ("0102abc",)


def test_from_display_name_keeps_the_company_address() -> None:
    """§14: the salesperson's name as sender, always on the company address."""
    msg = build_mime(
        from_address="sales@arifapack.com",
        to_address="buyer@example.com",
        subject="D-1",
        body="Hello",
        from_name="Sara, Arifa Pack",
    )
    parsed = message_from_bytes(msg.as_bytes(), policy=policy.default)
    assert parsed["From"].addresses[0].display_name == "Sara, Arifa Pack"
    assert parsed["From"].addresses[0].addr_spec == "sales@arifapack.com"


async def test_jmap_session_follows_the_well_known_redirect() -> None:
    """D44, both found on the local Stalwart 16 Nov: (1) /.well-known/jmap answers 307 -> /jmap/session (RFC 8620
    §2.2 allows it); not following it failed every send and mail poll on a non-JSON body. (2) The session's
    upload / download URLs are absolute templates on the container's own hostname; rewriting the host must keep
    the {accountId} / {blobId} placeholders (they were percent-encoded, so every upload hit a 404)."""
    seen: list[tuple[str, str | None]] = []

    def stalwart(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, request.headers.get("Authorization")))
        if request.url.path == "/.well-known/jmap":
            location = "http://sales%40example.com:pw@stalwart:8080/jmap/session"  # what Stalwart sends
            return httpx.Response(307, headers={"Location": location})
        return httpx.Response(
            200,
            json={
                # Stalwart v0.14 advertises its container hostname: rewritten to the internal URL we were given
                "apiUrl": "http://5f047c78428d:8080/jmap/",
                "uploadUrl": "http://5f047c78428d:8080/jmap/upload/{accountId}/",
                "downloadUrl": "http://5f047c78428d:8080/jmap/download/{accountId}/{blobId}/{name}?accept={type}",
                "primaryAccounts": {"urn:ietf:params:jmap:mail": "a1"},
            },
        )

    settings = StalwartSettings(
        url="http://stalwart:8080", username="sales@example.com", password=SecretStr("pw")
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(stalwart)) as http:
        session = await JmapClient(http, settings).session()
    assert session.account_id == "a1"
    assert session.api_url == "http://stalwart:8080/jmap/"
    assert session.upload_url == "http://stalwart:8080/jmap/upload/{accountId}/"
    assert (
        session.download_url == "http://stalwart:8080/jmap/download/{accountId}/{blobId}/{name}?accept={type}"
    )
    assert [path for path, _ in seen] == ["/.well-known/jmap", "/jmap/session"]
    assert all(auth and auth.startswith("Basic ") for _, auth in seen)  # same origin: auth kept


async def test_stalwart_v1_submission_cap_is_pruned_and_the_same_email_resubmitted() -> None:
    """Measured 17 Nov on Stalwart v1.1.1: every JMAP send leaves an EmailSubmission record and the account refuses
    new ones at `maxSubmissions` (500): all email stopped with `overQuota`. The sender deletes the finished records
    and submits the Email already in Sent again (never a second copy). Deleting does not cancel delivery."""
    import json

    from kx.automation.channels.stalwart import StalwartSender

    calls: list[str] = []
    state = {"full": True}

    def jmap(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/.well-known/jmap":
            return httpx.Response(
                200,
                json={
                    "apiUrl": "/jmap/",
                    "uploadUrl": "/jmap/upload/{accountId}/",
                    "downloadUrl": "/jmap/download/{accountId}/{blobId}/{name}",
                    "primaryAccounts": {"urn:ietf:params:jmap:mail": "c"},
                },
            )
        if request.url.path.startswith("/jmap/upload"):
            return httpx.Response(200, json={"blobId": "b1"})
        out = []
        for name, args, tag in json.loads(request.content)["methodCalls"]:
            calls.append(
                name if name != "EmailSubmission/set" else f"{name}:{sorted(set(args) - {'accountId'})}"
            )
            if name == "Mailbox/get":
                res: dict[str, Any] = {"list": [{"id": "s1", "role": "sent"}]}
            elif name == "Identity/get":
                res = {"list": [{"id": "i1", "email": "sales@example.com"}]}
            elif name == "Email/import":
                res = {"created": {"m": {"id": "e9"}}}
            elif name == "EmailSubmission/query":
                assert args["filter"] == {"undoStatus": "final"}
                res = {"ids": ["x1", "x2"]}
            elif name == "EmailSubmission/set" and "destroy" in args:
                state["full"] = False
                res = {"destroyed": args["destroy"]}
            else:  # create
                assert args["create"]["s"]["emailId"] in ("#m", "e9")
                res = (
                    {"notCreated": {"s": {"type": "overQuota"}}}
                    if state["full"]
                    else {"created": {"s": {"id": "new"}}}
                )
            out.append([name, res, tag])
        return httpx.Response(200, json={"methodResponses": out})

    settings = StalwartSettings(
        url="http://stalwart:8080", username="sales@example.com", password=SecretStr("pw")
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(jmap)) as http:
        sender = StalwartSender(JmapClient(http, settings), SesSettings(from_address="sales@example.com"))
        message_id = await sender.send(to_address="buyer@x.ae", subject="Quote", body="hi")
    assert message_id.endswith("@example.com")
    assert calls.count("Email/import") == 1  # the email is never imported twice
    assert calls[-3:] == [
        "EmailSubmission/query",
        "EmailSubmission/set:['destroy']",
        "EmailSubmission/set:['create']",
    ]
