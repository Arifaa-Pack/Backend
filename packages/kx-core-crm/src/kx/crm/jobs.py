"""Background work for the CRM: periodic job bodies and event handlers (no job-queue import: the app runs
them on Celery, D25).

Wire in the app:
    rt = CrmRuntime(databases, deps, publish=<publisher that hands crm outbox events to the bus>)
    periodic_jobs(rt)          # name -> coroutine for Beat (schedules in arifaa_pack.schedule)
    HANDLERS / dispatch(rt, e) # events the CRM consumes, run as `crm.handle_event` jobs

Each job runs in one CRM transaction and is safe to run twice: the sweep and the relay use
SKIP LOCKED, handlers are de-duplicated with kx.core.events.inbox_once in the same transaction.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import wraps
from typing import Any, Final

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from kx.core import events as ev
from kx.core.db import Databases
from kx.core.event_payloads import (
    ConversationSummaryUpdated,
    DealQualified,
    MessageReceived,
    MessageStatus,
    OptedOut,
    PriceProposed,
    ProductPublished,
    QuoteNotDelivered,
    StaffNotice,
    parse_payload,
)
from kx.core.events import Event, Publisher, inbox_once, relay_once
from kx.core.jobs import RetryLater
from kx.core.logging import get_logger
from kx.core.settings import LogicalDb
from kx.crm.catalogue import service as catalogue
from kx.crm.change_requests import service as change_requests
from kx.crm.contacts import consent, phone
from kx.crm.deps import CrmDeps
from kx.crm.models import Contact, Deal, FxRate
from kx.crm.models.base import SCHEMA
from kx.crm.pricing import fx
from kx.crm.pricing import proposals as price_proposals
from kx.crm.reports import scheduled as scheduled_reports
from kx.crm.retention import service as retention
from kx.crm.rules import OPEN_STAGES
from kx.crm.services import approvals, issuing, lost_proposals, quotes, staff, summary
from kx.crm.services import deals as deal_svc
from kx.crm.services.journal import Actor, Emit, Timeline, record

log = get_logger(__name__)
_STAFF_KINDS: Final = frozenset({"approval_alert", "md_notice"})
_SIGNAL: Final[dict[str, phone.Signal]] = {"delivered": "delivered", "read": "read", "failed": "failed"}
EventHandler = Callable[[AsyncSession, CrmDeps, Event], Awaitable[None]]
SUMMARY_CHARS: Final = 500
AI_SUMMARY_CHARS: Final = 1200
UNVERIFIED_KIND: Final = "message_in_unverified"  # M8 timeline kind: shown flagged, never a buyer reply


@dataclass(frozen=True, slots=True)
class CrmRuntime:
    databases: Databases
    deps: CrmDeps
    publish: Publisher


# ---------------------------------------------------------------- job bodies (plain async, testable)
async def run_fx_update(rt: CrmRuntime) -> dict[str, int]:
    """18 Nov: daily automatic FX rates (kx.crm.services.fx). Without a source configured, nothing happens."""
    source = rt.deps.fx_source
    if source is None:
        return {"skipped": 1}
    async with rt.databases.transaction(LogicalDb.CRM) as s:
        settings = await rt.deps.settings.get(s)
        stored = (await s.execute(select(FxRate.currency).distinct())).scalars().all()
        run = await fx.update_rates(
            s, source, fx.wanted(settings.currency_by_country.values(), stored, rt.deps.base_currency)
        )
    log.info(
        "fx.updated",
        as_of=str(run.as_of),
        saved=len(run.saved),
        refused=sorted(run.refused),
        missing=run.missing,
        kept_manual=run.kept_manual,
    )
    return {"saved": len(run.saved), "refused": len(run.refused), "missing": len(run.missing)}


async def run_approval_sweep(rt: CrmRuntime) -> int:
    async with rt.databases.transaction(LogicalDb.CRM) as s:
        return await approvals.sweep_overdue(s, rt.deps) + await change_requests.sweep_escalations(s, rt.deps)


async def run_lost_proposals(rt: CrmRuntime) -> int:
    async with rt.databases.transaction(LogicalDb.CRM) as s:
        return await lost_proposals.propose_lost(s, rt.deps)


async def run_price_staleness(rt: CrmRuntime) -> int:
    """Daily: expire quotes past their validity (§24); PRICE_STALE at warn/block level in cost mode (v2)."""
    async with rt.databases.transaction(LogicalDb.CRM) as s:
        settings = await rt.deps.settings.get(s)
        stale = 0
        if settings.pricing_mode == "cost":  # D14: no staleness warning or block in price-list mode (v1)
            stale = await price_proposals.emit_stale(
                s, settings, rt.deps.now(), Actor.system("price_staleness")
            )
        expired = await quotes.expire_quotes(s, rt.deps)
    log.info("crm.price_staleness", stale=stale, expired_quotes=expired)
    return stale


async def run_retention(rt: CrmRuntime) -> dict[str, int]:
    async with rt.databases.transaction(LogicalDb.CRM) as s:
        counts = await retention.run_retention(s, rt.deps)
    log.info("crm.retention", **counts)
    return counts


async def run_scheduled_reports(rt: CrmRuntime) -> int:
    """§21: the daily summary and the weekly report, once each when their time (a setting) has passed."""
    async with rt.databases.transaction(LogicalDb.CRM) as s:
        sent = await scheduled_reports.send_due(s, rt.deps)
    if sent:
        log.info("crm.scheduled_reports", sent=sent)
    return sent


async def run_outbox_relay(rt: CrmRuntime, limit: int = 100) -> int:
    async with rt.databases.transaction(LogicalDb.CRM) as s:
        return await relay_once(s, SCHEMA, rt.publish, limit)


# ---------------------------------------------------------------- event handlers
def once(name: str) -> Callable[[EventHandler], EventHandler]:
    """Run the handler at most once per event (inbox row written in the handler's own transaction)."""

    def wrap(fn: EventHandler) -> EventHandler:
        @wraps(fn)
        async def handler(session: AsyncSession, deps: CrmDeps, event: Event) -> None:
            if await inbox_once(session, SCHEMA, event.event_id, name):
                await fn(session, deps, event)

        return handler

    return wrap


@once("crm.price_proposed")
async def on_price_proposed(session: AsyncSession, deps: CrmDeps, event: Event) -> None:
    """A scraper observation becomes a *proposed* material rate awaiting manager approval."""
    p = parse_payload(event, PriceProposed)
    if await price_proposals.propose_rate(session, p, Actor.system("price_scraper")) is None:
        log.warning("crm.price_proposed.unknown_material", material=p.material_code)


UNMATCHED_RETRY_WINDOW: Final = timedelta(seconds=60)


@once("crm.message_received")
async def on_message_received(session: AsyncSession, deps: CrmDeps, event: Event) -> None:
    """Inbound buyer message -> deal timeline (text truncated; full chat stays in automation/agents)."""
    m = parse_payload(event, MessageReceived)
    if not m.sender_verified:
        await _unverified_message(session, m)
        return
    deal = await _deal_for_message(session, m)
    if deal is None:
        # D-3 (live test 18 Nov): the first message of a new buyer arrives while the agents are still creating
        # the deal (8 ms later in the test), so it was dropped. A fresh message is retried (6, 18, 42 s);
        # rolling back also removes the inbox row, so the retry runs the handler again.
        if deps.now() - m.received_at < UNMATCHED_RETRY_WINDOW:
            raise RetryLater(f"no deal yet for message {m.message_ref}")
        log.info("crm.message_received.unmatched", message_ref=str(m.message_ref))
        return
    # §8.1: a stop arrives as its own event (consent.opted_out.v1 -> on_opted_out); here only the timeline
    if m.channel == "whatsapp":  # §8.2: the buyer replied from that number -> Verified (+ profile name)
        contact = await session.get(Contact, deal.primary_contact_id)
        if contact is not None:
            await phone.apply_signal(session, contact, "reply", m.received_at, profile_name=m.profile_name)
    await record(
        session,
        Actor.system("inbound"),
        "message.received",
        "deal",
        deal.id,
        timeline=Timeline(
            "message_in",
            m.text[:SUMMARY_CHARS] or "(attachment)",
            deal.id,
            deal.primary_contact_id,
            channel=m.channel,
            direction="in",
            ref_type="inbound_message",
            ref_id=str(m.message_ref),
            actor_type="buyer",
            at=m.received_at,  # in order on the timeline even when recorded after a retry (D-3)
        ),
    )


@once("crm.opted_out")
async def on_opted_out(session: AsyncSession, deps: CrmDeps, event: Event) -> None:
    """§8.1 step 8: a stop automation applied (a rule, the unsubscribe link, the 24-hour timer). A stop the
    CRM decided itself (origin crm) is already applied."""
    p = parse_payload(event, OptedOut)
    if p.origin == "crm":
        return
    contact = await session.get(Contact, p.contact_ref) if p.contact_ref else None
    if contact is None and p.address_hashes:
        contact = await deal_svc.contact_by_recipient_hash(session, p.address_hashes[0])
    if contact is None:
        log.info("crm.opted_out.unknown_contact", log_ref=str(p.log_ref))
        return
    deal = await session.get(Deal, p.deal_ref) if p.deal_ref else None
    deal = deal or await _latest_deal(session, contact.id)
    await consent.apply_opt_out(
        session,
        deps,
        deal=deal,
        contact=contact,
        scope=p.scope,
        source=p.source,
        words=p.exact_words,
        reason=p.reason,
        decided_by=p.decided_by,
        channel=p.channel,
        actor=Actor.system(f"opt_out:{p.source}"),
    )
    missing = set(contact.recipient_hashes or ()) - set(p.address_hashes)
    if p.scope == "all" and (missing or p.contact_ref is None):
        # step 2 "stop at once, everywhere": automation knew one address (e.g. the email link); hand it the
        # person and every address the CRM holds
        await record(
            session,
            Actor.system(f"opt_out:{p.source}"),
            "consent.opt_out_extended",
            "contact",
            contact.id,
            emit=Emit(
                ev.CONSENT_OPTED_OUT,
                "contact",
                contact.id,
                p.model_copy(
                    update={
                        "contact_ref": contact.id,
                        "deal_ref": deal.id if deal else p.deal_ref,
                        "address_hashes": tuple(sorted(missing)),
                        "origin": "crm",
                        "log_ref": None,
                    }
                ),
            ),
        )


@once("crm.phone_status")
async def on_message_status(session: AsyncSession, deps: CrmDeps, event: Event) -> None:
    """§8.2 step 3: read WhatsApp's signals silently (Delivered, Read, failed) for messages to the buyer. A
    message our own gate refused (blocked, cancelled) says nothing about the number."""
    p = parse_payload(event, MessageStatus)
    if p.error and p.error.startswith("unknown:"):  # D6: delivery unknown (any channel): tell, never a signal
        if p.deal_ref is not None and p.kind not in _STAFF_KINDS:
            deal = await session.get(Deal, p.deal_ref)
            if deal is not None:
                await phone.delivery_unknown(session, deal, p.kind or "message", p.channel or "")
        return
    if p.channel != "whatsapp" or p.contact_ref is None or p.kind in _STAFF_KINDS:
        return
    if p.error and p.error.startswith(("blocked:", "cancelled:")):
        return
    signal = _SIGNAL.get(p.status)
    contact = await session.get(Contact, p.contact_ref)
    if signal is None or contact is None:
        return
    await phone.apply_signal(session, contact, signal, p.at)
    if signal == "failed" and p.kind == "summary_pdf" and p.deal_ref is not None:
        deal = await session.get(Deal, p.deal_ref)
        if deal is not None:
            await summary.summary_not_delivered(session, deps, deal)


@once("crm.requirement_summary")
async def on_deal_qualified(session: AsyncSession, deps: CrmDeps, event: Event) -> None:
    """§5 step 3 / §6.5 / §17 row 1: the qualified deal's Requirement Summary goes to the buyer."""
    p = parse_payload(event, DealQualified)
    deal = await session.get(Deal, p.deal_ref)
    if deal is not None:
        await summary.send_requirement_summary(session, deps, deal)


@once("crm.cms_product")
async def on_product_published(session: AsyncSession, deps: CrmDeps, event: Event) -> None:
    """§7 / D22: a CMS publish or unpublish mirrors the descriptive product record by product code."""
    await catalogue.mirror_product(session, parse_payload(event, ProductPublished), deps.now())


@once("crm.quote_not_delivered")
async def on_quote_not_delivered(session: AsyncSession, deps: CrmDeps, event: Event) -> None:
    """D32: the same approved quote by email (its own grant), else the salesperson is told to call."""
    await issuing.resend_by_email(session, deps, parse_payload(event, QuoteNotDelivered))


@once("crm.ai_summary")
async def on_summary_updated(session: AsyncSession, deps: CrmDeps, event: Event) -> None:
    """§7A.2 (D23): the short AI summary at the top of the deal page. A display field, not a business state:
    written without bumping the deal version (staff edits are not refused because the AI re-summarised),
    audited but kept off the timeline (it changes every few turns). An older summary never replaces a newer
    one (events can arrive out of order)."""
    p = parse_payload(event, ConversationSummaryUpdated)
    result = await session.execute(
        update(Deal)
        .where(
            Deal.id == p.deal_ref,
            (Deal.ai_summary_at.is_(None)) | (Deal.ai_summary_at < p.at),
        )
        .values(ai_summary=p.summary[:AI_SUMMARY_CHARS], ai_summary_at=p.at)
        .execution_options(synchronize_session=False)
    )
    if getattr(result, "rowcount", 0):
        await record(session, Actor.system("ai_summary"), "deal.ai_summary_updated", "deal", p.deal_ref)


async def _latest_deal(session: AsyncSession, contact_id: uuid.UUID) -> Deal | None:
    stmt = (
        select(Deal)
        .where(Deal.primary_contact_id == contact_id)
        .order_by(Deal.stage.in_([s.value for s in OPEN_STAGES]).desc(), Deal.created_at.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def _unverified_message(session: AsyncSession, m: MessageReceived) -> None:
    """M8: an email whose SPF/DKIM/DMARC did not pass. It goes on the timeline of the deal its address
    claims, flagged and not attributed to the contact (no contact update, no buyer-reply signal), and the
    manager and that deal's salesperson are alerted. Automation already skipped the stop rule; the agents
    do not answer it."""
    claimed = await _deal_for_message(session, m)  # by the address only: to show it, never to act on it
    manager = await staff.manager_user(session)
    owner = await staff.owner_of(session, claimed) if claimed is not None else None
    notify = tuple({u.id: staff.staff_contact(u) for u in (owner, manager) if u is not None}.values())
    where = f"Deal {claimed.number}" if claimed is not None else "Sales inbox"
    entity = ("deal", claimed.id) if claimed is not None else ("inbound_message", m.message_ref)
    await record(
        session,
        Actor.system("inbound"),
        "message.received_unverified",
        entity[0],
        entity[1],
        after={"sender_verified": False, "message_ref": str(m.message_ref)},
        timeline=(
            Timeline(
                UNVERIFIED_KIND,
                f"Sender not verified: {m.text}"[:SUMMARY_CHARS],
                claimed.id,
                None,  # not linked to the buyer's contact by the address alone
                channel=m.channel,
                direction="in",
                ref_type="inbound_message",
                ref_id=str(m.message_ref),
                actor_type="system",
                at=m.received_at,
            )
            if claimed is not None
            else None
        ),
        emit=Emit(
            ev.STAFF_NOTICE,
            entity[0],
            entity[1],
            StaffNotice(
                deal_ref=claimed.id if claimed is not None else None,
                notify=notify,
                text=f"{where}: an email failed the sender checks (SPF/DKIM/DMARC) and may be forged. The AI "
                "did not answer it and nothing was changed automatically; please check it.",
                key=f"unverified-email:{m.message_ref}",
            ),
        ),
    )


async def _deal_for_message(session: AsyncSession, m: MessageReceived) -> Deal | None:
    if m.deal_ref is not None and (deal := await session.get(Deal, m.deal_ref)) is not None:
        return deal
    contact_id = m.contact_ref
    if contact_id is None and (c := await deal_svc.contact_by_recipient_hash(session, m.from_address_hash)):
        contact_id = c.id
    if contact_id is None:
        return None
    return await _latest_deal(session, contact_id)


HANDLERS: Final[dict[str, EventHandler]] = {
    ev.PRICE_PROPOSED: on_price_proposed,
    ev.MESSAGE_RECEIVED: on_message_received,
    ev.CONSENT_OPTED_OUT: on_opted_out,
    ev.MESSAGE_STATUS: on_message_status,
    ev.DEAL_QUALIFIED: on_deal_qualified,
    ev.CONVERSATION_SUMMARY_UPDATED: on_summary_updated,
    ev.CATALOGUE_PRODUCT_PUBLISHED: on_product_published,
    ev.QUOTE_NOT_DELIVERED: on_quote_not_delivered,
}


async def dispatch(rt: CrmRuntime, event: Event, handler: EventHandler | None = None) -> bool:
    """Run the CRM handler for `event` in a CRM transaction. False when the CRM does not handle it."""
    fn = handler or HANDLERS.get(event.event_type)
    if fn is None:
        return False
    async with rt.databases.transaction(LogicalDb.CRM) as s:
        await fn(s, rt.deps, event)
    return True


# ---------------------------------------------------------------- periodic jobs (Beat, by name)
Periodic = Callable[[datetime], Awaitable[Any]]


def periodic_jobs(rt: CrmRuntime) -> dict[str, Periodic]:
    """CRM work Beat runs on a timer. Times are UTC in `arifaa_pack.schedule` (Dubai = UTC+4)."""

    async def relay(_: datetime) -> int:
        total = 0
        while (n := await run_outbox_relay(rt)) == 100:  # drain bursts without waiting for the next tick
            total += n
        return total + n

    async def sweep(_: datetime) -> int:
        return await run_approval_sweep(rt)

    async def staleness(_: datetime) -> int:
        return await run_price_staleness(rt)

    async def lost(_: datetime) -> int:
        return await run_lost_proposals(rt)

    async def retention_(_: datetime) -> dict[str, int]:
        return await run_retention(rt)

    async def reports(_: datetime) -> int:
        return await run_scheduled_reports(rt)

    async def fx_rates(_: datetime) -> dict[str, int]:
        return await run_fx_update(rt)

    return {
        "relay.crm": relay,
        "crm.approval_sweep": sweep,
        "crm.price_staleness": staleness,
        "crm.lost_proposals": lost,
        "crm.retention": retention_,
        "crm.scheduled_reports": reports,
        "crm.fx_rates": fx_rates,
    }
