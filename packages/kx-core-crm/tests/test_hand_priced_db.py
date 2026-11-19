"""D7 (architecture guide, decided 17 Nov): a catalogue product priced by hand has no minimum to check, so the
manager approves the price first (special_price) and the salesperson's approval follows."""

from __future__ import annotations

import uuid
from decimal import Decimal as D
from typing import Any

import pytest
from conftest import PRODUCT_ID
from test_approvals_db import sara_deal
from test_flow_db import MGR, SARA, open_approval

from kx.core.db import Databases
from kx.core.settings import LogicalDb
from kx.crm._shared.journal import Actor
from kx.crm.approvals import service as approvals
from kx.crm.deps import CrmDeps
from kx.crm.quotes import service as quotes

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("price_list")]
CRM = LogicalDb.CRM


async def _decide(dbs: Databases, deps: CrmDeps, approval_id: uuid.UUID, who: Any) -> None:
    async with dbs.transaction(CRM) as s:
        await approvals.decide(s, deps, approval_id, who, decision="approve")


async def test_a_manual_quote_for_a_catalogue_product_goes_to_the_manager_first(
    databases: Databases, deps: CrmDeps
) -> None:
    deal = await sara_deal(databases, deps)
    async with databases.transaction(CRM) as s:
        await quotes.manual_quote(
            s,
            deps,
            deal,
            [quotes.ManualLine("Aluminium container 750 ml", D(20_000), D("0.20"), product_id=PRODUCT_ID)],
            delivery_time_text="10 working days",
            actor=Actor.user(SARA),
        )
    first = await open_approval(databases, deal)
    assert (first.kind, first.required_role) == ("special_price", "manager")
    await _decide(databases, deps, first.id, MGR)
    then = await open_approval(databases, deal)
    assert (then.kind, then.required_role) == ("revision", "sales")  # the salesperson approves the quote


async def test_a_custom_item_without_a_catalogue_product_also_goes_to_the_manager_first(
    databases: Databases, deps: CrmDeps
) -> None:
    """D-5 (live test 18 Nov, client decision): a "custom item" line without a product link skipped the
    manager, so a catalogue product could be sold below its minimum on the salesperson's approval alone.
    Every hand price now goes to the manager first, then to the salesperson."""
    deal = await sara_deal(databases, deps)
    async with databases.transaction(CRM) as s:
        await quotes.manual_quote(
            s,
            deps,
            deal,
            [quotes.ManualLine("Container + 2-colour logo print", D(100_000), D("0.31"))],
            delivery_time_text="18-21 working days",
            actor=Actor.user(SARA),
        )
    a = await open_approval(databases, deal)
    assert (a.kind, a.required_role) == ("special_price", "manager")
    await _decide(databases, deps, a.id, MGR)
    then = await open_approval(databases, deal)
    assert (then.kind, then.required_role) == ("revision", "sales")


async def test_a_quantity_outside_every_tier_makes_the_revision_a_special_price(
    databases: Databases, deps: CrmDeps
) -> None:
    deal = await sara_deal(databases, deps)  # 50,000 pcs from the price list
    async with databases.transaction(CRM) as s:
        await quotes.revise_quote(
            s,
            deps,
            deal,
            quotes.QuoteEdits(lines=(quotes.LineEdit(1, quantity=D(1_000)),)),  # below the 5,000 MOQ
            Actor.user(SARA),
        )
    a = await open_approval(databases, deal)
    assert (a.kind, a.required_role) == ("special_price", "manager")
