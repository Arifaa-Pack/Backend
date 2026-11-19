"""§3 / §5 step 14 / §15.3 over HTTP: the deal's salesperson updates the order status (the route was manager-only;
found by the tester-pack review of 19 Nov). Another salesperson and Admin / IT may not; cancelling stays with the
manager / MD."""

from __future__ import annotations

import uuid

import httpx
import pytest
from conftest import MGR_ID, SALES2_ID, SALES_ID
from test_api_db import Auth, api  # noqa: F401 - the HTTP client fixture
from test_flow_db import SARA
from test_won_lost_db import agreed_deal

from kx.core.auth import Role
from kx.core.db import Databases
from kx.core.settings import LogicalDb
from kx.crm._shared.journal import Actor
from kx.crm.deps import CrmDeps
from kx.crm.orders import service as orders

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("price_list")]


async def test_the_deals_salesperson_tracks_the_order(
    api: tuple[httpx.AsyncClient, Auth],  # noqa: F811 - the imported fixture
    databases: Databases,
    deps: CrmDeps,
) -> None:
    client, auth = api
    deal_id = await agreed_deal(databases, deps)
    async with databases.transaction(LogicalDb.CRM) as s:
        await orders.record_payment(s, deps, deal_id, Actor.user(SARA))
    async with databases.transaction(LogicalDb.CRM) as s:
        order = await orders.mark_won(s, deps, deal_id, Actor.user(SARA))
    url = f"/v1/deals/orders/{order.id}"

    auth.as_(Role.SALES, SALES2_ID)  # a salesperson who does not own the deal
    assert (await client.patch(url, json={"status": "ready"})).status_code == 403
    auth.as_(Role.ADMIN, uuid.uuid4())  # IT runs the system, not orders
    assert (await client.patch(url, json={"status": "ready"})).status_code == 403
    auth.as_(Role.SALES, SALES_ID)
    r = await client.patch(url, json={"status": "ready"})
    assert r.status_code == 200 and r.json()["status"] == "ready"
    r = await client.patch(url, json={"status": "shipped", "tracking_ref": "BL-1"})
    assert r.status_code == 200 and r.json()["status"] == "shipped"
    assert (await client.patch(url, json={"status": "cancelled"})).status_code == 403
    assert (
        await client.patch(f"/v1/deals/orders/{uuid.uuid4()}", json={"status": "ready"})
    ).status_code == 404
    auth.as_(Role.MANAGER, MGR_ID)
    r = await client.patch(url, json={"status": "delivered"})
    assert r.status_code == 200 and r.json()["status"] == "delivered"
