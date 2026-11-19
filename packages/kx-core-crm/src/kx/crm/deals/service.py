"""Deals: find-or-create from an inbound identity, requirements, contact capture, qualification and
the stage machine (allowed moves in kx.crm.rules.TRANSITIONS, optimistic locking via deals.version).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal, get_args

from sqlalchemy import ColumnElement, Text, cast, exists, func, literal, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.exc import StaleDataError

from kx.core import events as ev
from kx.core.contracts import Channel, ContactPatch, DealStage, QualifyResult, RequirementPatch
from kx.core.errors import Conflict, NotFound, RuleViolation
from kx.core.event_payloads import DealNextActionSet, DealQualified, DealStageChanged
from kx.core.identity import new_id, normalise_email, normalise_phone
from kx.crm._shared.journal import Actor, Emit, Timeline, record
from kx.crm.models import (
    Approval,
    ChangeRequest,
    Company,
    Contact,
    Deal,
    DealRequirement,
    Payment,
    Product,
    Quote,
    QuoteSendGrant,
    QuoteVersion,
    StageHistory,
)
from kx.crm.rules import OPEN_STAGES, QualificationFacts, check_transition, missing_fields

IdentityKind = Literal["phone", "email"]
NextActionReason = Literal["followup_d2", "followup_d5", "followup_d10", "reorder", "custom"]
_CONTACT_SOURCE: dict[str, str] = {
    "web_voice": "web_voice",
    "web_chat": "web_chat",
    "whatsapp": "whatsapp",
    "email": "email",
    "phone": "manual",
}  # DDL has no 'phone' source


# ---------------------------------------------------------------- loading
async def get_deal(session: AsyncSession, deal_id: uuid.UUID, *, lock: bool = False) -> Deal:
    """Load a deal; `lock=True` takes FOR UPDATE so concurrent AI turns on one deal serialise."""
    stmt = select(Deal).where(Deal.id == deal_id).execution_options(populate_existing=True)
    if lock:
        stmt = stmt.with_for_update()
    if (deal := (await session.execute(stmt)).scalar_one_or_none()) is None:
        raise NotFound(f"deal {deal_id} not found")
    return deal


async def contact_of(session: AsyncSession, deal: Deal) -> Contact:
    contact = await session.get(Contact, deal.primary_contact_id)
    assert contact is not None  # FK guarantees it
    return contact


async def requirements_of(session: AsyncSession, deal_id: uuid.UUID) -> list[DealRequirement]:
    stmt = (
        select(DealRequirement)
        .where(DealRequirement.deal_id == deal_id)
        .order_by(DealRequirement.created_at, DealRequirement.id)
    )
    return list((await session.execute(stmt)).scalars())


# ---------------------------------------------------------------- identity -> contact -> deal
def parse_identity(channel: Channel, address: str) -> tuple[IdentityKind, str]:
    """Normalise a buyer address the same way kx.core.identity.recipient_hash does."""
    try:
        if channel == "email" or "@" in address:
            return "email", normalise_email(address)
        return "phone", normalise_phone(address)
    except ValueError as exc:
        raise RuleViolation(str(exc)) from exc


def _hash_of(namespace: str, column: Any) -> ColumnElement[str]:
    """SQL twin of kx.core.identity.recipient_hash (sha256 of 'namespace:address', hex)."""
    return func.encode(func.sha256(func.convert_to(literal(f"{namespace}:") + column, "UTF8")), "hex")


async def contact_by_recipient_hash(session: AsyncSession, recipient_hash: str) -> Contact | None:
    """Automation only shares hashes; match them against phone, WhatsApp and email of live contacts."""
    stmt = (
        select(Contact)
        .where(
            Contact.anonymised_at.is_(None),
            or_(
                _hash_of("phone", Contact.phone_e164) == recipient_hash,
                _hash_of("phone", Contact.whatsapp_e164) == recipient_hash,
                _hash_of("email", cast(Contact.email, Text)) == recipient_hash,
            ),
        )
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def find_contact(
    session: AsyncSession, kind: IdentityKind | None, value: str | None, visitor_ref: uuid.UUID | None = None
) -> Contact | None:
    if kind == "phone" and value:
        cond = or_(Contact.phone_e164 == value, Contact.whatsapp_e164 == value)
    elif kind == "email" and value:
        cond = Contact.email == value
    elif visitor_ref is not None:
        cond = Contact.visitor_ref == visitor_ref
    else:
        return None
    stmt = select(Contact).where(cond, Contact.anonymised_at.is_(None)).order_by(Contact.created_at).limit(1)
    return (await session.execute(stmt)).scalar_one_or_none()


async def _insert_contact(
    session: AsyncSession,
    channel: Channel,
    kind: IdentityKind | None,
    value: str | None,
    visitor_ref: uuid.UUID | None,
) -> Contact:
    """Insert-or-get: the partial unique indexes on phone/email make concurrent first messages safe."""
    phone = value if kind == "phone" else None
    values = {
        "id": new_id(),
        "source": _CONTACT_SOURCE[channel],
        "visitor_ref": visitor_ref,
        "phone_e164": phone,
        "email": value if kind == "email" else None,
        "whatsapp_e164": phone if channel == "whatsapp" else None,
        "preferred_channel": "whatsapp" if channel == "whatsapp" else "email" if kind == "email" else None,
        "language": "en",
        "timezone": "Asia/Dubai",
        # §8.2: "The buyer replies from that number -> Verified": a WhatsApp first message is such a reply
        "phone_status": "verified" if channel == "whatsapp" and phone else "unknown",
        "phone_status_at": func.now() if channel == "whatsapp" and phone else None,
    }
    stmt = pg_insert(Contact).values(**values).on_conflict_do_nothing().returning(Contact.id)
    inserted = (await session.execute(stmt)).scalar_one_or_none()
    contact = await session.get(Contact, inserted) if inserted else await find_contact(session, kind, value)
    if contact is None:  # pragma: no cover - conflict row vanished (anonymised concurrently)
        raise Conflict("contact changed concurrently; retry")
    return contact


async def resolve_deal(
    session: AsyncSession, channel: Channel, address: str | None, visitor_ref: uuid.UUID | None, actor: Actor
) -> Deal:
    """The open deal for this buyer, creating contact and deal on first contact (one person = one record)."""
    kind, value = parse_identity(channel, address) if address else (None, None)
    contact = await find_contact(session, kind, value, visitor_ref)
    if contact is None and visitor_ref is not None and kind is not None:
        contact = await find_contact(session, None, None, visitor_ref)
    if contact is None:
        contact = await _insert_contact(session, channel, kind, value, visitor_ref)
    elif visitor_ref is not None and contact.visitor_ref is None:
        contact.visitor_ref = visitor_ref
    stmt = (
        select(Deal)
        .where(Deal.primary_contact_id == contact.id, Deal.stage.in_([s.value for s in OPEN_STAGES]))
        .order_by(Deal.created_at.desc())
        .limit(1)
    )
    if (deal := (await session.execute(stmt)).scalar_one_or_none()) is not None:
        return deal
    return await create_deal(session, contact, source=channel, actor=actor)


async def create_deal(session: AsyncSession, contact: Contact, *, source: str, actor: Actor) -> Deal:
    deal = Deal(
        id=new_id(),
        primary_contact_id=contact.id,
        company_id=contact.company_id,
        source=source,
        stage=DealStage.NEW.value,
    )
    session.add(deal)
    await session.flush()
    session.add(
        StageHistory(
            deal_id=deal.id,
            from_stage=None,
            to_stage=deal.stage,
            actor_type=actor.type,
            actor_id=actor.id,
            reason="created",
        )
    )
    await record(
        session,
        actor,
        "deal.created",
        "deal",
        deal.id,
        after={"stage": deal.stage, "source": source},
        timeline=Timeline(
            "deal_created", f"Deal {deal.number} opened via {source}", deal.id, contact.id, channel=source
        ),
        emit=Emit(
            ev.DEAL_STAGE_CHANGED,
            "deal",
            deal.id,
            DealStageChanged(deal_ref=deal.id, from_stage=None, to_stage=DealStage.NEW, reason="created"),
        ),
    )
    return deal


# ---------------------------------------------------------------- requirements & contact capture
async def patch_requirement(
    session: AsyncSession, deal: Deal, patch: RequirementPatch, actor: Actor
) -> DealRequirement:
    """Merge what the buyer said into the deal's primary requirement (created on first patch)."""
    reqs = await requirements_of(session, deal.id)
    req = reqs[0] if reqs else DealRequirement(id=new_id(), deal_id=deal.id, captured_by=actor.id, spec={})
    if not reqs:
        session.add(req)
    before = _req_snapshot(req)
    data = patch.model_dump(exclude_unset=True, exclude_none=True)
    if (sku := data.pop("product_sku", None)) is not None:
        product = (
            await session.execute(select(Product).where(Product.sku == sku, Product.is_active))
        ).scalar_one_or_none()
        if product is None:
            raise RuleViolation(f"unknown product sku {sku!r}")
        req.product_id = product.id
        req.unit = product.unit
    if (spec := data.pop("spec", None)) is not None:
        req.spec = {**req.spec, **spec}
    if (country := data.pop("delivery_country", None)) is not None:
        req.delivery_country = country.upper()
    for key, value in data.items():
        setattr(req, key, value)
    req.captured_by = actor.id
    await session.flush()
    await record(
        session,
        actor,
        "deal.requirement_updated",
        "deal_requirement",
        req.id,
        before=before,
        after=_req_snapshot(req),
        timeline=Timeline(
            "requirement",
            "Requirement updated: " + ", ".join(sorted(patch.model_fields_set)),
            deal.id,
            deal.primary_contact_id,
        ),
    )
    return req


def _req_snapshot(r: DealRequirement) -> dict[str, object]:
    return {
        "product_id": r.product_id,
        "product_text": r.product_text,
        "quantity": r.quantity,
        "delivery_country": r.delivery_country,
        "needed_by": r.needed_by,
    }


async def patch_contact(session: AsyncSession, deal: Deal, patch: ContactPatch, actor: Actor) -> Contact:
    """Capture buyer details. A phone or email the buyer types is a claim, not proof (D-4, live test 18 Nov:
    an anonymous website visitor typed a real buyer's phone and took over that buyer's name and email).

    Client rule (18 Nov): when a typed phone or email already belongs to another contact, the deal moves to
    that contact only if the phone AND the email both match it (the AI casually asks every visitor for the
    missing one). The existing contact is never overwritten: only its empty fields are filled. A partial
    match is not linked; the salesperson gets a note to check and merge by hand."""
    contact = await contact_of(session, deal)
    try:
        phone = normalise_phone(patch.phone) if patch.phone else None
        email = normalise_email(patch.email) if patch.email else None
    except ValueError as exc:
        raise RuleViolation(str(exc)) from exc
    taken: dict[str, Contact] = {}
    for kind, value in (("phone", phone), ("email", email)):
        other = await find_contact(session, kind, value) if value else None  # type: ignore[arg-type]
        if other is not None and other.id != contact.id:
            taken[kind] = other
    known_only = False  # merged into an existing buyer: fill blanks, never overwrite
    if taken:
        target = taken.get("phone") or taken["email"]
        claim_phone = phone or contact.phone_e164 or contact.whatsapp_e164
        claim_email = email or contact.email
        if claim_phone and claim_email and _has_identity(target, claim_phone, claim_email):
            contact, known_only = await _merge_into(session, deal, contact, target, actor), True
        else:
            await _note_possible_match(session, deal, stub=contact, existing=target, taken=taken, actor=actor)
            phone = None if "phone" in taken else phone  # never copied onto a second contact
            email = None if "email" in taken else email
    before = {"name": contact.full_name, "phone": contact.phone_e164, "email": contact.email}
    if phone and not (known_only and contact.phone_e164):
        contact.phone_e164 = phone
        if contact.source == "whatsapp" and contact.whatsapp_e164 is None:
            contact.whatsapp_e164 = phone
    if email and not (known_only and contact.email):
        contact.email = email
    if patch.name is not None and not (known_only and contact.full_name):
        contact.full_name = patch.name
    for attr, key in (
        ("preferred_channel", "preferred_channel"),
        ("language", "language"),
        ("timezone", "timezone"),
    ):
        if (value := getattr(patch, key)) is not None:
            setattr(contact, attr, value)
    if patch.company and not (known_only and contact.company_id):
        await _set_company(session, deal, contact, patch.company)
    elif known_only:
        deal.company_id = deal.company_id or contact.company_id
    await session.flush()
    await record(
        session,
        actor,
        "contact.updated",
        "contact",
        contact.id,
        before=before,
        after={"name": contact.full_name, "phone": contact.phone_e164, "email": contact.email},
        timeline=Timeline("contact", "Contact details captured", deal.id, contact.id),
    )
    return contact


def _has_identity(contact: Contact, phone: str, email: str) -> bool:
    """The contact's own phone (or WhatsApp number) AND email are the ones the buyer gave."""
    same_email = (contact.email or "").lower() == email.lower()
    return same_email and phone in (contact.phone_e164, contact.whatsapp_e164)


async def _note_possible_match(
    session: AsyncSession,
    deal: Deal,
    *,
    stub: Contact,
    existing: Contact,
    taken: dict[str, Contact],
    actor: Actor,
) -> None:
    """A partial match is never linked automatically; the salesperson decides (the buyer is not told)."""
    which = " and ".join(sorted(taken))
    await record(
        session,
        actor,
        "contact.possible_match",
        "contact",
        stub.id,
        after={"existing_contact_id": existing.id, "matched": sorted(taken)},
        timeline=Timeline(
            "contact_match",
            f"The buyer gave the {which} of an existing contact ({existing.full_name or 'no name'}), but not "
            "both their phone and email, so the deal was NOT linked to them. Check with the buyer and merge "
            "by hand if it is the same person.",
            deal.id,
            stub.id,
        ),
    )


async def _merge_into(
    session: AsyncSession, deal: Deal, stub: Contact, existing: Contact, actor: Actor
) -> Contact:
    existing.full_name = existing.full_name or stub.full_name
    existing.visitor_ref = existing.visitor_ref or stub.visitor_ref
    existing.company_id = existing.company_id or stub.company_id
    deal.primary_contact_id = existing.id
    deal.company_id = deal.company_id or existing.company_id
    await record(
        session,
        actor,
        "contact.merged",
        "contact",
        existing.id,
        before={"contact_id": stub.id},
        after={"contact_id": existing.id},
        timeline=Timeline("contact_merged", "Buyer matched an existing contact", deal.id, existing.id),
    )
    return existing


async def _set_company(session: AsyncSession, deal: Deal, contact: Contact, name: str) -> None:
    if contact.company_id is None:
        company = Company(id=new_id(), name=name)
        session.add(company)
        await session.flush()
        contact.company_id = company.id
    elif (company_row := await session.get(Company, contact.company_id)) is not None:
        company_row.name = name
    deal.company_id = contact.company_id


# ---------------------------------------------------------------- qualification
async def missing_for(session: AsyncSession, deal: Deal) -> tuple[str, ...]:
    contact = await contact_of(session, deal)
    reqs = await requirements_of(session, deal.id)
    req = reqs[0] if reqs else None
    return missing_fields(
        QualificationFacts(
            has_product=req is not None and (req.product_id is not None or bool(req.product_text)),
            has_quantity=req is not None and req.quantity is not None,
            has_delivery_country=req is not None and req.delivery_country is not None,
            has_contact_name=bool(contact.full_name and contact.full_name.strip()),
            has_phone_or_email=bool(contact.phone_e164 or contact.whatsapp_e164 or contact.email),
        )
    )


async def try_qualify(session: AsyncSession, deal: Deal, actor: Actor, now: datetime) -> QualifyResult:
    """NEW -> QUALIFIED once product, quantity, country, name and (phone or email) are known."""
    missing = await missing_for(session, deal)
    if missing:
        return QualifyResult(qualified=False, missing_fields=missing)
    if DealStage(deal.stage) is DealStage.NEW:
        await move_stage(session, deal, DealStage.QUALIFIED, actor, now=now, reason="requirements complete")
        await record(
            session,
            actor,
            "deal.qualified",
            "deal",
            deal.id,
            emit=Emit(
                ev.DEAL_QUALIFIED,
                "deal",
                deal.id,
                DealQualified(deal_ref=deal.id, contact_ref=deal.primary_contact_id),
            ),
        )
    return QualifyResult(qualified=True, missing_fields=())


# ---------------------------------------------------------------- stage machine
async def move_stage(
    session: AsyncSession,
    deal: Deal,
    target: DealStage,
    actor: Actor,
    *,
    now: datetime,
    reason: str | None = None,
    lost_reason: str | None = None,
    lost_note: str | None = None,
    expected_version: int | None = None,
) -> Deal:
    """The only way a deal changes stage: rule check, optimistic lock, history, timeline, event."""
    if expected_version is not None and deal.version != expected_version:
        raise Conflict("deal was changed by someone else; reload", current_version=deal.version)
    current = DealStage(deal.stage)
    has_payment = target is DealStage.WON and bool(
        await session.scalar(select(exists().where(Payment.deal_id == deal.id)))
    )
    check_transition(current, target, lost_reason=lost_reason, has_payment=has_payment)
    deal.stage = target.value
    closed: dict[str, int] | None = None
    match target:
        case DealStage.QUALIFIED:
            deal.qualified_at = deal.qualified_at or now
        case DealStage.WON:
            deal.won_at = now
        case DealStage.LOST:
            deal.lost_reason, deal.lost_note, deal.lost_at = lost_reason, lost_note, now
            closed = await _close_open_work(session, deal, now)
        case _:
            pass
    try:
        await session.flush()
    except StaleDataError as exc:
        raise Conflict("deal was changed concurrently; reload") from exc
    session.add(
        StageHistory(
            deal_id=deal.id,
            from_stage=current.value,
            to_stage=target.value,
            actor_type=actor.type,
            actor_id=actor.id,
            reason=reason or lost_reason,
        )
    )
    await record(
        session,
        actor,
        "deal.stage_changed",
        "deal",
        deal.id,
        before={"stage": current.value},
        after={"stage": target.value, "lost_reason": lost_reason, **({"closed": closed} if closed else {})},
        timeline=Timeline(
            "stage",
            f"{current.value} → {target.value}"
            + (f" ({reason or lost_reason})" if reason or lost_reason else ""),
            deal.id,
            deal.primary_contact_id,
        ),
        emit=Emit(
            ev.DEAL_STAGE_CHANGED,
            "deal",
            deal.id,
            DealStageChanged(
                deal_ref=deal.id, from_stage=current, to_stage=target, reason=reason or lost_reason or ""
            ),
        ),
    )
    return deal


async def _close_open_work(session: AsyncSession, deal: Deal, now: datetime) -> dict[str, int]:
    """D10 (architecture guide): closing a deal as LOST, in the same transaction, cancels its open approvals
    (no priced quote can be approved for a closed deal; the reminder ladder stops because nothing is pending),
    declines its open change requests and revokes every send grant not yet used (a quote can no longer leave).
    The AI is left as it is: on a LOST deal the router only lets the concierge re-engage, which states no
    price.
    Queued follow-ups are cancelled by automation on `deal.stage_changed` (LOST). A reopened deal starts the
    approval again."""
    versions = (
        select(QuoteVersion.id).join(Quote, Quote.id == QuoteVersion.quote_id).where(Quote.deal_id == deal.id)
    )
    no_sync = {"synchronize_session": False}
    approvals = (
        await session.execute(
            update(Approval)
            .where(Approval.quote_version_id.in_(versions), Approval.status.in_(("pending", "overdue")))
            .values(status="cancelled")
            .returning(Approval.id)
            .execution_options(**no_sync)
        )
    ).all()
    changes = (
        await session.execute(
            update(ChangeRequest)
            .where(ChangeRequest.deal_id == deal.id, ChangeRequest.status.in_(("open", "in_progress")))
            .values(status="declined", decline_reason="deal closed as LOST", closed_at=now)
            .returning(ChangeRequest.id)
            .execution_options(**no_sync)
        )
    ).all()
    grants = (
        await session.execute(
            update(QuoteSendGrant)
            .where(
                QuoteSendGrant.quote_version_id.in_(versions),
                QuoteSendGrant.consumed_at.is_(None),
                QuoteSendGrant.revoked_at.is_(None),
            )
            .values(revoked_at=now, revoked_reason="deal_lost")
            .returning(QuoteSendGrant.id)
            .execution_options(**no_sync)
        )
    ).all()
    return {"approvals": len(approvals), "change_requests": len(changes), "grants": len(grants)}


async def reopen(session: AsyncSession, deal: Deal, actor: Actor, *, now: datetime, reason: str) -> Deal:
    """§7A.1 "LOST: ... reopen (manager)" (D24): back to the stage the deal was lost from. The AI stays
    paused until a person resumes it (OPEN_QUESTIONS #47): a reopened deal needs a human first."""
    if DealStage(deal.stage) is not DealStage.LOST:
        raise RuleViolation("only a LOST deal can be reopened", stage=deal.stage)
    if not reason.strip():
        raise RuleViolation("give the reason for reopening the deal")
    back = await session.scalar(
        select(StageHistory.from_stage)
        .where(StageHistory.deal_id == deal.id, StageHistory.to_stage == DealStage.LOST.value)
        .order_by(StageHistory.id.desc())
        .limit(1)
    )
    target = DealStage(back) if back else DealStage.QUALIFIED
    lost = {"lost_reason": deal.lost_reason, "lost_note": deal.lost_note}
    deal.stage = target.value
    deal.lost_reason = deal.lost_note = deal.lost_at = None
    if deal.ai_enabled:
        deal.ai_enabled, deal.ai_paused_reason, deal.ai_paused_at = False, "human_takeover", now
    try:
        await session.flush()
    except StaleDataError as exc:
        raise Conflict("deal was changed concurrently; reload") from exc
    session.add(
        StageHistory(
            deal_id=deal.id,
            from_stage=DealStage.LOST.value,
            to_stage=target.value,
            actor_type=actor.type,
            actor_id=actor.id,
            reason=f"reopened: {reason}"[:500],
        )
    )
    await record(
        session,
        actor,
        "deal.reopened",
        "deal",
        deal.id,
        before={"stage": DealStage.LOST.value, **lost},
        after={"stage": target.value},
        timeline=Timeline(
            "stage", f"LOST → {target.value} (reopened: {reason})"[:2000], deal.id, deal.primary_contact_id
        ),
        emit=Emit(
            ev.DEAL_STAGE_CHANGED,
            "deal",
            deal.id,
            DealStageChanged(
                deal_ref=deal.id, from_stage=DealStage.LOST, to_stage=target, reason=f"reopened: {reason}"
            ),
        ),
    )
    return deal


async def personal_contact_shared(session: AsyncSession, deal: Deal, actor: Actor, now: datetime) -> Deal:
    """§14 practical notes: a salesperson may share a personal number when the buyer insists; the CRM
    records that it happened (and the deal must still be updated in the CRM)."""
    deal.personal_contact_shared_at, deal.personal_contact_shared_by = now, actor.user_id
    await session.flush()
    await record(
        session,
        actor,
        "deal.personal_contact_shared",
        "deal",
        deal.id,
        after={"personal_contact_shared_at": now},
        timeline=Timeline(
            "note",
            "The salesperson shared a personal contact with the buyer (keep the deal updated in the CRM)",
            deal.id,
            deal.primary_contact_id,
        ),
    )
    return deal


# ---------------------------------------------------------------- handover (D13, §14.6)
async def hand_over(session: AsyncSession, deal: Deal, actor: Actor, now: datetime) -> None:
    """The final quote is approved: the AI stops replying on this deal; buyer messages go to the
    salesperson's inbox; payment, PO and banking are discussed by people only (§14.6-7)."""
    if not deal.ai_enabled and deal.ai_paused_reason == "handover":
        return
    deal.ai_enabled, deal.ai_paused_reason, deal.ai_paused_at = False, "handover", now
    await session.flush()
    await record(
        session,
        actor,
        "deal.handed_over",
        "deal",
        deal.id,
        after={"ai_paused_reason": "handover"},
        timeline=Timeline(
            "handover",
            "Final quote approved: the AI stepped back, the salesperson takes over",
            deal.id,
            deal.primary_contact_id,
        ),
    )


# ---------------------------------------------------------------- next action (follow-ups)
async def set_next_action(
    session: AsyncSession, deal: Deal, due_at: datetime, reason: str, actor: Actor, *, primary: bool = True
) -> None:
    """Tell automation about a follow-up (it enforces 2/week and buyer contact hours). `primary` also
    records it as the deal's next action shown to the team."""
    contact = await contact_of(session, deal)
    kind: NextActionReason = reason if reason in get_args(NextActionReason) else "custom"  # type: ignore[assignment]
    if primary:
        deal.next_action_at, deal.next_action_kind, deal.next_action_reason = due_at, "followup", reason
    channel: Literal["whatsapp", "email"] = (
        "email"
        if contact.preferred_channel == "email" or not (contact.whatsapp_e164 or contact.phone_e164)
        else "whatsapp"
    )
    await session.flush()
    await record(
        session,
        actor,
        "deal.next_action_set",
        "deal",
        deal.id,
        after={"due_at": due_at, "reason": reason},
        emit=Emit(
            ev.DEAL_NEXT_ACTION_SET,
            "deal",
            deal.id,
            DealNextActionSet(
                deal_ref=deal.id,
                contact_ref=contact.id,
                due_at=due_at,
                reason=kind,
                buyer_timezone=contact.timezone,
                preferred_channel=channel,
            ),
        ),
    )


async def add_note(session: AsyncSession, deal: Deal, kind: str, summary: str, actor: Actor) -> None:
    await record(
        session,
        actor,
        f"timeline.{kind}",
        "deal",
        deal.id,
        timeline=Timeline(kind, summary[:2000], deal.id, deal.primary_contact_id),
    )


async def add_message(
    session: AsyncSession,
    deal: Deal,
    *,
    direction: Literal["in", "out"],
    channel: str,
    text: str,
    actor: Actor,
) -> None:
    """D-3 (live test 18 Nov): AI replies on every channel and website chat turns were missing from the
    timeline. `message_in` lines also feed the salesperson's inbox while the AI is paused."""
    kind = "message_in" if direction == "in" else "message_out"
    await record(
        session,
        actor,
        f"message.{'received' if direction == 'in' else 'sent'}",
        "deal",
        deal.id,
        timeline=Timeline(
            kind,
            text[:2000] or "(empty)",
            deal.id,
            deal.primary_contact_id,
            channel=channel,
            direction=direction,
            actor_type="buyer" if direction == "in" else "ai",
        ),
    )
