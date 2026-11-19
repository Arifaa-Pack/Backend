"""Recording what providers tell us: inbound messages, delivery statuses, bounces and complaints.

Every function runs inside the caller's transaction so the receipt, the rows and the outbox event
commit together (a crash can never leave a stored message without its MESSAGE_RECEIVED event).
Raw addresses stay in this database; events carry only `recipient_hash`.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final, Literal

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from kx.automation import optout
from kx.automation.channels.email import InboundEmail
from kx.automation.channels.feedback import MailFeedback, candidate_ids
from kx.automation.models import ChannelAccount, InboundMessage, OutboundMessage, OutboundStatus, Thread
from kx.automation.outbox import emit
from kx.automation.repo import (
    ThreadRef,
    account_by_address,
    account_by_external_id,
    channel_account,
    first_receipt,
    suppress,
    upsert_thread,
)
from kx.automation.whatsapp import channel as wa
from kx.core.event_payloads import MessageReceived, MessageStatus
from kx.core.identity import recipient_hash
from kx.core.logging import get_logger
from kx.core.optout import StopMatch, match

log = get_logger(__name__)

# A status may only move forward; e.g. a late "delivered" must not overwrite "read".
_FORWARD: Final[dict[OutboundStatus, tuple[OutboundStatus, ...]]] = {
    "sent": ("sending",),
    "delivered": ("sending", "sent"),
    "read": ("sending", "sent", "delivered"),
    "failed": ("sending", "sent", "delivered"),
}
_STAMP: Final[dict[str, str]] = {"sent": "sent_at", "delivered": "delivered_at", "read": "read_at"}
EventStatus = Literal["sent", "delivered", "read", "failed", "bounced", "complained"]
_ROW_STATUS: Final[dict[EventStatus, OutboundStatus | None]] = {
    "sent": "sent",
    "delivered": "delivered",
    "read": "read",
    "failed": "failed",
    "bounced": "failed",
    "complained": None,
}


@dataclass(frozen=True, slots=True)
class _Stored:
    id: uuid.UUID
    thread: ThreadRef


async def _store(
    s: AsyncSession,
    *,
    thread: ThreadRef,
    provider: str,
    provider_message_id: str,
    kind: str,
    text: str | None,
    media_s3_key: str | None,
    raw: dict[str, Any],
    received_at: datetime,
) -> _Stored | None:
    """Insert once per (provider, provider_message_id); None when it was already stored."""
    stmt = (
        pg_insert(InboundMessage)
        .values(
            thread_id=thread.id,
            provider=provider,
            provider_message_id=provider_message_id,
            kind=kind,
            text=text,
            media_s3_key=media_s3_key,
            raw=raw,
            received_at=received_at,
        )
        .on_conflict_do_nothing(index_elements=["provider", "provider_message_id"])
        .returning(InboundMessage.id)
    )
    inserted = (await s.execute(stmt)).scalar_one_or_none()
    return _Stored(inserted, thread) if inserted is not None else None


async def _received(
    s: AsyncSession,
    stored: _Stored,
    *,
    channel: Literal["whatsapp", "email"],
    address: str,
    text: str,
    media: tuple[str, ...],
    at: datetime,
    provider_message_id: str,
    stop: StopMatch | None = None,
    profile_name: str | None = None,
    sender_verified: bool = True,
) -> None:
    stop = stop or StopMatch("none")
    await emit(
        s,
        MessageReceived(
            message_ref=stored.id,
            thread_ref=stored.thread.id,
            channel=channel,
            from_address_hash=recipient_hash(channel, address),
            # M8: an unverified sender is never linked to the buyer by the address alone
            contact_ref=stored.thread.contact_ref if sender_verified else None,
            deal_ref=stored.thread.deal_ref if sender_verified else None,
            sender_verified=sender_verified,
            text=text,
            media_s3_keys=media,
            received_at=at,
            provider_message_id=provider_message_id,
            opted_out=stop.kind == "opt_out" and stop.scope == "all",
            stop_in_sentence=stop.kind == "context",
            stop_language=stop.language if stop.kind != "none" else None,
            profile_name=profile_name,
        ),
    )


async def _apply_rule_stop(
    s: AsyncSession,
    stop: StopMatch,
    *,
    channel: str,
    address_hash: str,
    thread: ThreadRef,
    words: str,
    at: datetime,
) -> None:
    """§8.1 steps 1-2: a rule matched, so the stop happens now, inside the webhook's transaction."""
    if stop.stops:
        await optout.apply_stop(
            s,
            optout.StopRecord(
                scope=stop.scope,
                source="rule",
                channel=channel,
                address_hashes=(address_hash,),
                contact_ref=thread.contact_ref,
                deal_ref=thread.deal_ref,
                thread_ref=thread.id,
                words=words,
                reason=stop.reason,
                decided_by=stop.rule,
                language=stop.language,
                at=at,
            ),
        )


# ---------------------------------------------------------------- WhatsApp
async def record_whatsapp(s: AsyncSession, hook: wa.WaWebhook) -> list[uuid.UUID]:
    """Store messages + statuses. Returns inbound ids whose media must be fetched before they are
    announced (the AI needs the voice note / image, not just "a file arrived")."""
    media_pending: list[uuid.UUID] = []
    for m in hook.messages:
        if not await first_receipt(s, wa.PROVIDER, m.message_id):
            continue
        account = await account_by_external_id(s, wa.PROVIDER, m.phone_number_id)
        if account is None:
            log.warning("whatsapp.unknown_phone_number_id", phone_number_id=m.phone_number_id)
            continue
        thread = await upsert_thread(s, account, m.from_e164, inbound_at=m.received_at)
        stored = await _store(
            s,
            thread=thread,
            provider=wa.PROVIDER,
            provider_message_id=m.message_id,
            kind=m.kind,
            text=m.text or None,
            media_s3_key=None,
            raw=m.raw,
            received_at=m.received_at,
        )
        if stored is None:
            continue
        stop = match(m.text) if m.kind in {"text", "button", "interactive"} else StopMatch("none")
        await _apply_rule_stop(
            s,
            stop,
            channel="whatsapp",
            address_hash=recipient_hash("whatsapp", m.from_e164),
            thread=thread,
            words=m.text,
            at=m.received_at,
        )
        if m.media is not None:
            media_pending.append(stored.id)
        elif m.kind in {"text", "button", "interactive"}:
            await _received(
                s,
                stored,
                channel="whatsapp",
                address=m.from_e164,
                text=m.text,
                media=(),
                at=m.received_at,
                provider_message_id=m.message_id,
                stop=stop,
                profile_name=m.profile_name,
            )
    for st in hook.statuses:
        if st.status != "deleted" and await first_receipt(s, wa.PROVIDER, f"{st.message_id}:{st.status}"):
            await advance_status(s, st.message_id, st.status, at=st.at, error=st.error)
    return media_pending


async def attach_media(s: AsyncSession, inbound_id: uuid.UUID, key: str) -> None:
    """Media stored in S3: link it and announce the message (once: only while media_s3_key is NULL)."""
    row = (
        await s.execute(
            update(InboundMessage)
            .where(InboundMessage.id == inbound_id, InboundMessage.media_s3_key.is_(None))
            .values(media_s3_key=key)
            .returning(InboundMessage)
        )
    ).scalar_one_or_none()
    if row is None:
        return
    thread = await s.get(Thread, row.thread_id)
    assert thread is not None  # FK
    ref = ThreadRef(thread.id, thread.contact_ref, thread.deal_ref, thread.last_inbound_at)
    await _received(
        s,
        _Stored(row.id, ref),
        channel="whatsapp",
        address=thread.external_key,
        text=row.text or "",
        media=(key,),
        at=row.received_at,
        provider_message_id=row.provider_message_id,
    )


async def media_id_of(s: AsyncSession, inbound_id: uuid.UUID) -> tuple[str, str | None] | None:
    raw = (
        await s.execute(select(InboundMessage.raw).where(InboundMessage.id == inbound_id))
    ).scalar_one_or_none()
    if raw is None:
        return None
    media = next((raw[k] for k in ("image", "audio", "document", "video", "sticker") if k in raw), None)
    return (str(media["id"]), media.get("mime_type")) if isinstance(media, dict) else None


# ---------------------------------------------------------------- statuses (WhatsApp + email reports)
async def advance_status(
    s: AsyncSession, provider_message_id: str, status: EventStatus, *, at: datetime, error: str | None = None
) -> None:
    """Move an outbound row forward and announce it. Bounce/complaint are announced as such even
    though the row only records failed / unchanged (the DDL has no such statuses)."""
    row_status = _ROW_STATUS[status]
    if row_status is None:
        message_id = (
            await s.execute(
                select(OutboundMessage.id).where(OutboundMessage.provider_message_id == provider_message_id)
            )
        ).scalar_one_or_none()
    else:
        values: dict[str, Any] = {"status": row_status, "error": error}
        if (stamp := _STAMP.get(row_status)) is not None:
            values[stamp] = at
        message_id = (
            await s.execute(
                update(OutboundMessage)
                .where(
                    OutboundMessage.provider_message_id == provider_message_id,
                    OutboundMessage.status.in_(_FORWARD[row_status]),
                )
                .values(**values)
                .returning(OutboundMessage.id)
            )
        ).scalar_one_or_none()
    if message_id is not None:
        who = (
            await s.execute(
                select(
                    OutboundMessage.contact_ref,
                    OutboundMessage.deal_ref,
                    OutboundMessage.kind,
                    ChannelAccount.channel,
                )
                .join(ChannelAccount, ChannelAccount.id == OutboundMessage.channel_account_id)
                .where(OutboundMessage.id == message_id)
            )
        ).one_or_none()
        await emit(
            s,
            MessageStatus(
                message_ref=message_id,
                status=status,
                provider_message_id=provider_message_id,
                error=error,
                at=at,
                contact_ref=who.contact_ref if who else None,
                deal_ref=who.deal_ref if who else None,
                kind=who.kind if who else None,
                channel=who.channel if who else None,
            ),
        )


async def record_mail_event(s: AsyncSession, fb: MailFeedback) -> None:
    """D44: a bounce or complaint report read from the mailbox. A permanent bounce or any complaint
    suppresses the address on the email channel (§8.1 list) and marks the outbound row it refers to."""
    reason = "bounce" if fb.kind == "bounce" else "complaint"
    if fb.permanent or fb.kind == "complaint":
        await _suppress_emails(s, fb.recipients, reason)
    if fb.original_message_id is None:
        return
    provider_id = (
        await s.execute(
            select(OutboundMessage.provider_message_id)
            .where(OutboundMessage.provider_message_id.in_(candidate_ids(fb.original_message_id)))
            .limit(1)
        )
    ).scalar_one_or_none()
    if provider_id is not None:
        status: EventStatus = "bounced" if fb.kind == "bounce" else "complained"
        await advance_status(s, provider_id, status, at=fb.at, error=fb.detail)


async def _suppress_emails(s: AsyncSession, addresses: Iterable[str], reason: str) -> None:
    for address in addresses:
        await suppress(s, "email", recipient_hash("email", address), reason)


# ---------------------------------------------------------------- email
async def record_email(
    s: AsyncSession,
    mail: InboundEmail,
    *,
    s3_key: str | None,
    attachment_keys: tuple[str, ...],
    provider: str = "stalwart",
) -> None:
    if not await first_receipt(s, provider, mail.message_id):
        return
    account = await account_by_address(s, "email", mail.to_addresses) or await channel_account(
        s, "email", "customer"
    )
    # M8: an unverified sender does not move the buyer's thread ("last heard from" picks the reply channel)
    inbound_at = mail.received_at if mail.sender_verified else None
    thread = await upsert_thread(s, account, mail.from_address, inbound_at=inbound_at)
    raw = {
        "s3_key": s3_key,
        "subject": mail.subject,
        "in_reply_to": mail.in_reply_to,
        "attachments": list(attachment_keys),
        "sender_verified": mail.sender_verified,  # M8
    }
    stored = await _store(
        s,
        thread=thread,
        provider=provider,
        provider_message_id=mail.message_id,
        kind="email",
        text=mail.text,
        media_s3_key=attachment_keys[0] if attachment_keys else None,
        raw=raw,
        received_at=mail.received_at,
    )
    if stored is not None:
        text = f"{mail.subject}\n\n{mail.text}".strip() if mail.subject else mail.text
        if mail.sender_verified:
            stop = match(mail.text or mail.subject or "")
            await _apply_rule_stop(
                s,
                stop,
                channel="email",
                address_hash=recipient_hash("email", mail.from_address),
                thread=thread,
                words=mail.text or mail.subject or "",
                at=mail.received_at,
            )
        else:  # M8: anyone can write "STOP" from a forged address; staff review it instead
            stop = StopMatch("none")
            log.warning("inbound.email_sender_not_verified", message_ref=str(stored.id))
        await _received(
            s,
            stored,
            channel="email",
            address=mail.from_address,
            text=text,
            media=attachment_keys,
            at=mail.received_at,
            provider_message_id=mail.message_id,
            stop=stop,
            sender_verified=mail.sender_verified,
        )
