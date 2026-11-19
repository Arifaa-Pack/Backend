"""Security review 19 Nov over HTTP. L3: a salesperson lists and edits only the companies of their own deals'
contacts (and companies nobody is linked to yet). L4: Admin / IT may view a deal but not work on it, and
exports only the product list."""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
from conftest import MGR_ID, SALES2_ID, SALES_ID, assign, new_phone
from test_api_db import Auth, api  # noqa: F401 - the HTTP client fixture

from kx.core.auth import Role
from kx.core.contracts import AiRole, ContactPatch
from kx.crm.deps import CrmDeps
from kx.crm.gateway import CrmGatewayImpl

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("cost_mode")]


async def _sales_deal_with_company(
    client: httpx.AsyncClient, auth: Auth, databases: Any, deps: CrmDeps
) -> Any:
    gw = CrmGatewayImpl(databases, deps)
    deal_id = await gw.resolve_deal("whatsapp", new_phone(), None)
    await gw.set_contact(deal_id, ContactPatch(name="Omar"), AiRole.CONTACT_CAPTURE)
    await assign(databases, deal_id, SALES_ID)
    auth.as_(Role.MANAGER, MGR_ID)
    company = (
        await client.post("/v1/companies", json={"name": f"Gulf Catering {uuid.uuid4().hex[:6]}"})
    ).json()
    contact_id = (await client.get(f"/v1/deals/{deal_id}")).json()["contact"]["id"]
    r = await client.patch(f"/v1/contacts/{contact_id}", json={"company_id": company["id"]})
    assert r.status_code == 200
    return deal_id, company["id"]


async def test_l3_a_salesperson_sees_only_their_customers_companies(
    api: tuple[httpx.AsyncClient, Auth],  # noqa: F811 - the imported fixture
    databases: Any,
    deps: CrmDeps,
) -> None:
    client, auth = api
    _, theirs = await _sales_deal_with_company(client, auth, databases, deps)
    fresh = (await client.post("/v1/companies", json={"name": f"New Co {uuid.uuid4().hex[:6]}"})).json()["id"]

    auth.as_(Role.SALES, SALES2_ID)  # not their customer
    listed = {c["id"] for c in (await client.get("/v1/companies", params={"limit": 100})).json()["items"]}
    assert theirs not in listed and fresh in listed  # a company nobody is linked to yet stays visible
    assert (await client.patch(f"/v1/companies/{theirs}", json={"name": "Hijacked"})).status_code == 404
    gw = CrmGatewayImpl(databases, deps)
    own_deal = await gw.resolve_deal("whatsapp", new_phone(), None)
    await gw.set_contact(own_deal, ContactPatch(name="Fatima"), AiRole.CONTACT_CAPTURE)
    await assign(databases, own_deal, SALES2_ID)
    own_contact = (await client.get(f"/v1/deals/{own_deal}")).json()["contact"]["id"]
    r = await client.patch(f"/v1/contacts/{own_contact}", json={"company_id": theirs})
    assert r.status_code == 404  # cannot attach their contact to another rep's customer
    assert (await client.patch(f"/v1/contacts/{own_contact}", json={"company_id": fresh})).status_code == 200

    auth.as_(Role.SALES, SALES_ID)  # the owner
    listed = {c["id"] for c in (await client.get("/v1/companies", params={"limit": 100})).json()["items"]}
    assert theirs in listed
    edit = {"name": "Gulf Catering", "city": "Ajman"}  # CompanyIn needs the name on an edit too
    assert (await client.patch(f"/v1/companies/{theirs}", json=edit)).status_code == 200


async def test_l4_admin_views_deals_but_never_works_them(
    api: tuple[httpx.AsyncClient, Auth],  # noqa: F811 - the imported fixture
    databases: Any,
    deps: CrmDeps,
) -> None:
    client, auth = api
    deal_id, _ = await _sales_deal_with_company(client, auth, databases, deps)
    version = (await client.get(f"/v1/deals/{deal_id}")).json()["version"]
    auth.as_(Role.ADMIN, uuid.uuid4())
    assert (await client.get(f"/v1/deals/{deal_id}")).status_code == 200
    r = await client.post(f"/v1/deals/{deal_id}/stage", json={"to": "QUALIFIED", "expected_version": version})
    assert r.status_code == 403
    assert (await client.post(f"/v1/deals/{deal_id}/quotes")).status_code == 403
    for entity in ("contacts", "companies", "deals", "orders"):
        assert (await client.get(f"/v1/exports/{entity}")).status_code == 403, entity
    assert (await client.get("/v1/exports/products")).status_code == 200
