"""19 Nov (client): quick buyer messages get one AI answer. ThreadBurst reads automation.inbound_messages: the
turn of an older message steps aside while a newer one exists; the newest gets every unanswered text, oldest
first, without forged (unverified) mail and without what came before our last message to the thread."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import select, text

from arifaa_pack.adapters import ThreadBurst
from kx.automation.email.channel import parse_inbound
from kx.automation.inbound.service import record_email
from kx.automation.models import InboundMessage, Thread
from kx.core.settings import LogicalDb

AUTO = LogicalDb.AUTOMATION


@pytest.fixture
async def email_account(databases: Any) -> None:
    """The company mailbox inbound mail lands in (the automation package's own fixture seeds it too)."""
    async with databases.transaction(AUTO) as s:
        await s.execute(
            text(
                "INSERT INTO channel_accounts (channel, provider, external_account_id, display_name, address, "
                "purpose, secret_ref) SELECT 'email', 'ses', 'sales@arifapack.com', 'Arifaa Pack', "
                "'sales@arifapack.com', 'customer', 'arn:z' WHERE NOT EXISTS "
                "(SELECT 1 FROM channel_accounts WHERE channel = 'email' AND purpose = 'customer')"
            )
        )


pytestmark = [pytest.mark.db, pytest.mark.usefixtures("email_account")]


def _mail(sender: str, second: int, body: str, *, verified: bool = True) -> bytes:
    auth = "dmarc=pass" if verified else "dmarc=fail"
    return (
        f"From: Buyer <{sender}>\r\nTo: sales@arifapack.com\r\nSubject: Packaging enquiry\r\n"
        f"Message-ID: <{uuid.uuid4().hex}@example.com>\r\nDate: Wed, 18 Nov 2026 10:00:{second:02d} +0000\r\n"
        f"Authentication-Results: mail.arifapack.com; {auth}\r\n\r\n{body}\r\n"
    ).encode()


async def _store(databases: Any, raw: bytes) -> None:
    async with databases.transaction(AUTO) as s:
        await record_email(
            s, parse_inbound(raw, fallback_id=uuid.uuid4().hex), s3_key=None, attachment_keys=()
        )


async def _rows(databases: Any, sender_like: str) -> list[Any]:
    async with databases.transaction(AUTO) as s:
        stmt = (
            select(InboundMessage.id, InboundMessage.thread_id)
            .join(Thread, Thread.id == InboundMessage.thread_id)
            .where(Thread.external_key.like(sender_like))
            .order_by(InboundMessage.received_at)
        )
        return list(await s.execute(stmt))


def _last_lines(texts: tuple[str, ...] | None) -> list[str]:
    """An email's text is "<subject>\\n\\n<body>": compare the bodies."""
    assert texts is not None
    return [t.splitlines()[-1] for t in texts]


async def test_the_newest_quick_message_answers_all_of_them(databases: Any) -> None:
    for second, body in ((1, "Hi"), (2, "750 ml containers"), (3, "20,000 pcs to Dubai")):
        await _store(databases, _mail("quick@example.com", second, body))
    await _store(databases, _mail("quick@example.com", 4, "STOP", verified=False))  # forged: never joins
    first, _, third, forged = await _rows(databases, "%quick@example.com%")
    burst = ThreadBurst(databases)
    assert await burst.unanswered(first.thread_id, first.id) is None  # a newer message exists: step aside
    assert _last_lines(await burst.unanswered(third.thread_id, third.id)) == [
        "Hi",
        "750 ml containers",
        "20,000 pcs to Dubai",
    ]
    # the forged mail is the newest row, but it never counts as "a newer message" for the third one
    assert forged.id != third.id


async def test_messages_before_our_last_message_are_not_answered_again(databases: Any) -> None:
    await _store(databases, _mail("replied@example.com", 1, "Hi"))
    await _store(databases, _mail("replied@example.com", 5, "Price for 750 ml?"))
    first, second = await _rows(databases, "%replied@example.com%")
    async with databases.transaction(AUTO) as s:  # our reply went out between the two buyer messages
        await s.execute(
            text(
                "INSERT INTO outbound_messages (thread_id, channel_account_id, to_address, kind, requested_by, "
                "idempotency_key, status, created_at) SELECT id, channel_account_id, 'replied@example.com', "
                "'reply', 'test', :k, 'sent', '2026-11-18 10:00:03+00' FROM threads WHERE id = :t"
            ),
            {"k": uuid.uuid4().hex, "t": first.thread_id},
        )
    assert _last_lines(await ThreadBurst(databases).unanswered(second.thread_id, second.id)) == [
        "Price for 750 ml?"
    ]
