"""The whole app, end to end (WP-C0 … C9, 20 Nov): one visitor's journey through every package, on real
Postgres and Redis, through the real API (`create_app`), the real CLI and the real outbox / job runner.

1. Launch: `kx migrate`, staff (`kx create-user`), the current website into the CMS (`kx content
   import-site`), the outbox relay and the jobs (the CRM learns the website products), Arifa's ERP stock list
   (`kx catalogue import-erp`): the ERP items become the variants of the website products (D61).
2. The website reads the CMS: site, manifest, a product page; no price anywhere (§7).
3. A visitor: a session, "show me kitchen foil" moves the page at once (instant layer), the agent opens the
   8325 product with its own tool (agent layer); the streamed turn sends the action before the words; the
   contact card records the WhatsApp opt-in; the enquiry desk joins the same deal and gets a salesperson.
4. The CRM holds it all: one deal, the consent with its wording, the English enquiry note, the variants the
   AI may describe (availability words, no amounts).

The model is scripted (no vendor call); everything else is the production wiring. Needs `KX_TEST_PG_DSN`
and `KX_TEST_REDIS_URL` (a throwaway Redis: the website's rate limits and spend caps live there).
"""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from typing import Any

import psycopg
import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from kx.core.no_prices import find_price

from ..conftest import ADMIN_DSN

pytestmark = [
    pytest.mark.db,
    pytest.mark.skipif(not os.environ.get("KX_TEST_REDIS_URL"), reason="needs KX_TEST_REDIS_URL"),
]
NAMES = {"crm": "flow_crm", "automation": "flow_automation", "agents": "flow_agents", "cms": "flow_cms"}
ERP_SAMPLE = pathlib.Path(__file__).resolve().parents[2] / "packages/kx-core-crm/tests/erp_sample.tsv"


def _dsn(db: str, driver: str = "") -> str:
    assert ADMIN_DSN
    base, _, query = ADMIN_DSN.partition("?")
    head = base.rsplit("/", 1)[0].replace("postgresql://", f"postgresql{driver}://", 1)
    return f"{head}/{db}" + (f"?{query}" if query else "")


def _clear_caches() -> None:
    from arifaa_pack.config import app_settings
    from kx.core.settings import get_settings

    get_settings.cache_clear()
    app_settings.cache_clear()


@pytest.fixture(scope="module")
def launched() -> Iterator[dict[str, str]]:
    """Launch day on empty databases, with the real CLI."""
    from arifaa_pack.cli import main

    with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
        for name in NAMES.values():
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
            admin.execute(f'CREATE DATABASE "{name}"')
    mp = pytest.MonkeyPatch()
    for key, name in NAMES.items():
        mp.setenv(f"KX_{key.upper()}_DSN", _dsn(name, "+asyncpg"))
    mp.setenv("KX_REDIS_URL", os.environ["KX_TEST_REDIS_URL"])
    mp.setenv("KX_AWS_REGION", "me-central-1")
    mp.setenv("AWS_ACCESS_KEY_ID", "test")
    mp.setenv("AWS_SECRET_ACCESS_KEY", "test")
    _clear_caches()
    main(["migrate"])
    main(["create-user", "--email", "mona@example.com", "--name", "Mona", "--role", "manager"])
    main(["create-user", "--email", "sami@example.com", "--name", "Sami", "--role", "sales"])
    main(["content", "import-site"])
    yield {k: _dsn(v) for k, v in NAMES.items()}
    mp.undo()
    _clear_caches()


async def _drain(container: Any, automation_dsn: str, prefix: str = "crm.") -> dict[str, str]:
    """The outbox relay, then the package's jobs it scheduled, as Beat and the worker would (once each).
    Only one package's jobs: the knowledge re-index needs an embedding vendor, which a test has not."""
    results: dict[str, str] = {}
    for _ in range(50):  # the relay works in batches: Beat runs it again and again
        for name, job in container.periodic.items():
            if name.startswith("relay."):
                await job(datetime.now(UTC))
        with psycopg.connect(automation_dsn) as c:
            rows = c.execute(
                "select id, task from automation.scheduled_jobs where status = 'todo' and task like %s",
                (prefix + "%",),
            ).fetchall()
        fresh = [(i, t) for i, t in rows if f"{t}:{i}" not in results]
        if not fresh:
            return results
        for job_id, task in fresh:
            results[f"{task}:{job_id}"] = str(await container.runner.run(job_id))
    return results


def _scripted(container: Any, script: list[Any]) -> None:
    """The conversation model is scripted; the rest of the agent service stays the production one."""
    from kx.agents.llm.service import ModelRouter
    from kx.agents.runtime.service import GraphRunner
    from kx.agents.testing import ScriptedChatModel

    service = container.agent_service
    service.runner = GraphRunner(ModelRouter.static(ScriptedChatModel(script=script)), service.registry)
    service.price_check = None  # the M7 second opinion would spend a script line
    service.specialist_model = None
    service.deal_summaries = False


@pytest.fixture
async def app(launched: dict[str, str]) -> AsyncIterator[tuple[AsyncClient, Any]]:
    from arifaa_pack.api import create_app

    application = create_app()
    async with (
        LifespanManager(application),
        AsyncClient(transport=ASGITransport(app=application), base_url="http://t") as http,
    ):
        yield http, application.state.container


async def test_a_visitor_becomes_a_lead_across_every_package(
    app: tuple[AsyncClient, Any], launched: dict[str, str]
) -> None:
    from arifaa_pack.cli import main
    from kx.agents.testing import tool_call

    http, container = app

    # 1. the publish reaches the CRM; then the ERP list arrives and links to the website products (D61)
    await _drain(container, launched["automation"])
    await asyncio.to_thread(main, ["catalogue", "import-erp", str(ERP_SAMPLE)])  # the CLI runs its own loop
    with psycopg.connect(launched["crm"]) as c:
        web_products = c.execute("select count(*) from crm.website_products").fetchone()
        sellable = c.execute("select count(*) from crm.products where sellable and erp_category is not null")
        linked = c.execute(
            "select erp_description from crm.products where cms_product_id = 'ac-8325' order by 1"
        ).fetchall()
        assert web_products == (78,) and sellable.fetchone() == (13,)
    assert [r[0] for r in linked] == ["8325 ALUMINIUM CONTAINER", "8325 LID"]

    # 2. the website reads the CMS; nothing it serves carries a price
    site = await http.get("/v1/content/site")
    manifest = await http.get("/v1/content/manifest")
    product = await http.get("/v1/content/products/ac-8325")
    assert site.status_code == manifest.status_code == product.status_code == 200, product.text
    assert len(manifest.json()["families"]) == 8 and len(manifest.json()["products"]) == 78
    assert {p["key"] for p in manifest.json()["pages"]} >= {"home", "products", "contact", "quality"}
    for body in (site.json(), manifest.json(), product.json()):
        assert find_price(body) is None

    # 3. a visitor: the page moves at once (instant), then with the agent's own tool (agent)
    _scripted(
        container,
        [
            "Here is our kitchen foil range.",
            tool_call("show_product", {"product_id": "ac-8325"}),
            "This is our 8325 container with its lid, 1000 pieces a carton.",
        ],
    )
    s = (await http.post("/v1/public/sessions", json={"language": "en", "voice_consent": True})).json()
    sid, auth = s["session_id"], {"Authorization": f"Bearer {s['session_token']}"}
    first = await http.post(
        f"/v1/public/sessions/{sid}/messages",
        json={"text": "show me kitchen foil", "context": {"page": "home", "path": "/"}},
        headers=auth,
    )
    assert first.status_code == 200, first.text
    assert [(a["type"], a.get("family"), a["layer"]) for a in first.json()["actions"]] == [
        ("show_family", "foil", "instant")
    ]
    async with http.stream(
        "POST",
        f"/v1/public/sessions/{sid}/messages",
        json={
            "text": "I need containers with lids for biryani",
            "context": {"page": "products", "path": "/products"},
        },
        headers=auth | {"Accept": "text/event-stream"},
    ) as streamed:
        raw = "".join([chunk async for chunk in streamed.aiter_text()])
    events = [b.split("\n")[0].removeprefix("event: ") for b in raw.strip().split("\n\n")]
    assert events[0] == "action" and events[-1] == "done" and "delta" in events, raw
    assert '"product_id":"ac-8325"' in raw and '"layer":"agent"' in raw

    # the contact card (D59) and the enquiry desk (CT-01) on the same visit
    card = await http.post(
        f"/v1/public/sessions/{sid}/contact",
        json={
            "name": "Omar Haddad",
            "phone": "+971501112233",
            "whatsapp_opt_in": True,
            "consent_wording": "Yes, send my quotation on WhatsApp",
        },
        headers=auth,
    )
    desk = await http.post(
        "/v1/public/enquiries",
        json={
            "session_id": sid,
            "products": ["ac-8325"],
            "quantity": "20,000 a month",
            "city": "Dubai",
            "name": "Omar Haddad",
            "phone": "+971501112233",
            "channel": "whatsapp",
            "message": "Weekly delivery please",
        },
        headers=auth,
    )
    assert card.status_code == 200 and desk.status_code == 201, desk.text

    # 4. the CRM: one deal for the visit, its salesperson, the consent, the English enquiry note
    with psycopg.connect(launched["crm"]) as c:
        deals = c.execute(
            "select d.id, d.source, u.full_name from crm.deals d join crm.contacts k on k.id = d.primary_contact_id "
            "left join crm.users u on u.id = d.owner_user_id where k.visitor_ref = %s",
            (uuid.UUID(sid),),
        ).fetchall()
        assert len(deals) == 1 and deals[0][2] in {"Sami", "Mona"}
        deal_id = deals[0][0]
        consent = c.execute(
            "select channel, wording from crm.consents where status = 'granted' and contact_id = "
            "(select primary_contact_id from crm.deals where id = %s)",
            (deal_id,),
        ).fetchall()
        note = c.execute(
            "select summary from crm.timeline_events where deal_id = %s and kind = 'enquiry'", (deal_id,)
        ).fetchone()
        said = c.execute(
            "select count(*) from crm.timeline_events where deal_id = %s and kind like 'message%%'",
            (deal_id,),
        ).fetchone()
    assert consent == [("whatsapp", "Yes, send my quotation on WhatsApp")]
    assert note is not None and "Quantity: 20,000 a month" in note[0] and "Reply by: WhatsApp" in note[0]
    assert said is not None and said[0] >= 1  # the chat is on the deal's timeline for the salesperson

    # what the AI may say about the 8325's variants: words and quantities, never an amount
    from kx.crm.gateway import CrmGatewayImpl

    variants = await CrmGatewayImpl(container.databases, container.crm_deps).product_variants("ac-8325")
    assert {v.description for v in variants} == {"8325 ALUMINIUM CONTAINER", "8325 LID"}
    assert all(v.availability in {"in_stock", "limited", "made_to_order"} for v in variants)
    assert find_price(json.loads(json.dumps([v.model_dump(mode="json") for v in variants]))) is None
