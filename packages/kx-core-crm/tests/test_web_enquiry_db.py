"""CT-01 / WP-C3: the website's enquiry desk becomes a `web_form` deal with its salesperson, in one step."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest
from conftest import Seed, new_phone
from sqlalchemy import select

from kx.core.contracts import ContactPatch, RequirementPatch, WebEnquiry
from kx.core.settings import LogicalDb
from kx.crm.deps import CrmDeps
from kx.crm.gateway import CrmGatewayImpl
from kx.crm.models import Consent, Contact, Deal, TimelineEvent

pytestmark = pytest.mark.db


def _enquiry(phone: str) -> WebEnquiry:
    return WebEnquiry(
        contact=ContactPatch(
            name="Omar",
            company="Omar Catering",
            phone=phone,
            whatsapp_opt_in=True,
            consent_via="web_form",
            consent_wording="Yes, send my quotation on WhatsApp",
        ),
        requirement=RequirementPatch(
            product_text="Aluminium Container & Lid (8325)", quantity=Decimal(20_000), unit="pcs"
        ),
        summary="Website enquiry (Contact page)\nProducts: Aluminium Container & Lid (8325)\nQuantity: 20,000",
        website_products=("ac-8325",),
    )


async def test_an_enquiry_is_a_web_form_deal_with_its_salesperson(
    databases: Any, seed: Seed, deps: CrmDeps
) -> None:
    gw = CrmGatewayImpl(databases, deps)
    visitor = uuid.uuid4()
    deal_id = await gw.website_enquiry(visitor, _enquiry(new_phone()))
    async with databases.transaction(LogicalDb.CRM) as s:
        deal = await s.get(Deal, deal_id)
        assert deal is not None and deal.source == "web_form" and deal.owner_user_id is not None
        contact = await s.get(Contact, deal.primary_contact_id)
        assert contact is not None and contact.source == "form" and contact.full_name == "Omar"
        notes = (
            (await s.execute(select(TimelineEvent).where(TimelineEvent.deal_id == deal_id))).scalars().all()
        )
        assert any(n.kind == "enquiry" and "Quantity: 20,000" in n.summary for n in notes)
        consent = (await s.execute(select(Consent).where(Consent.contact_id == contact.id))).scalars().all()
        assert [(c.channel, c.captured_via) for c in consent if c.status == "granted"] == [
            ("whatsapp", "web_form")
        ]
    assert await gw.website_enquiry(visitor, _enquiry(new_phone())) == deal_id  # the same visit, one deal


async def test_an_enquiry_after_a_chat_joins_the_chats_deal(
    databases: Any, seed: Seed, deps: CrmDeps
) -> None:
    gw = CrmGatewayImpl(databases, deps)
    visitor = uuid.uuid4()
    chat_deal = await gw.resolve_deal("web_chat", None, visitor)
    assert await gw.website_enquiry(visitor, _enquiry(new_phone())) == chat_deal
