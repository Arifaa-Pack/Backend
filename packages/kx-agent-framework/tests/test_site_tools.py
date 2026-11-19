"""WP-C4 (D55 / D56 / D61): the agent moves the page with checked actions, finds products in the site map,
explains variants without amounts, and asks its specialists; the site tools exist only on the website."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from kx.agents.llm.service import ModelSet
from kx.agents.roles.registry import AgentRegistry, DefaultPromptStore
from kx.agents.router.service import route
from kx.agents.runtime.context import AgentContext
from kx.agents.runtime.middleware import available_tools, deal_facts
from kx.agents.site.models import ShowFamily, ShowProduct, SiteAction, SiteContext
from kx.agents.testing import FakeCrmGateway, ScriptedChatModel, make_deal
from kx.agents.tools.site import _ask, _show, describe_variants, find_in_manifest
from kx.core.contracts import (
    AiRole,
    Channel,
    ManifestFamily,
    ManifestPage,
    ManifestProduct,
    SiteManifest,
    VariantView,
)

MANIFEST = SiteManifest(
    revision=1,
    built_at=datetime(2026, 11, 20, tzinfo=UTC),
    pages=(ManifestPage(key="home", title="Home", path_en="/", path_ar="/ar"),),
    families=(
        ManifestFamily(
            id="containers",
            name="Aluminium Containers",
            synonyms={"en": ("takeaway boxes",)},
            path_en="/products?family=containers",
            path_ar="/ar/products?family=containers",
        ),
        ManifestFamily(id="foil", name="Aluminium Foil", path_en="/products?family=foil"),
    ),
    products=(
        ManifestProduct(
            id="ac-8325",
            ref="8325",
            family="containers",
            name="Aluminium Container & Lid",
            packing="1 x 1000 pcs",
            path_en="/products?p=ac-8325",
        ),
        ManifestProduct(
            id="sr-con-8325",
            ref="8325",
            family="containers",
            name="Container retail pack",
            range="self",
            path_en="/products?p=sr-con-8325",
        ),
        ManifestProduct(
            id="foil-30",
            ref="30 cm",
            family="foil",
            name="Aluminium Foil Roll",
            path_en="/products?p=foil-30",
        ),
    ),
)


def ctx(
    channel: Channel = "web_chat",
    *,
    manifest: SiteManifest | None = MANIFEST,
    fast: bool = False,
    sent: list[SiteAction] | None = None,
) -> AgentContext:
    async def collect(action: SiteAction) -> None:
        if sent is not None:
            sent.append(action)

    model = ModelSet(ScriptedChatModel(script=["The 8325 holds 1000 ml."]))
    return AgentContext(
        deal=make_deal(),
        spec=AgentRegistry().get(AiRole.CONCIERGE),
        channel=channel,
        language="en",
        gateway=FakeCrmGateway(make_deal()),
        prompts=DefaultPromptStore(),
        site=SiteContext(page="products", path="/products") if channel.startswith("web") else None,
        manifest=manifest,
        on_action=collect,
        specialist_model=model if fast else None,
    )


async def test_an_action_is_checked_sent_once_and_never_invented() -> None:
    sent: list[SiteAction] = []
    c = ctx(sent=sent)
    assert (await _show(c, ShowProduct(product_id="ac-8325"))).startswith("Shown")
    assert "Already" in await _show(c, ShowProduct(product_id="ac-8325"))  # the same intent: once
    assert "no such" in await _show(c, ShowProduct(product_id="ac-9999"))
    assert [a.type for a in sent] == ["show_product"] and sent[0].layer == "agent"
    assert [a.id for a in c.log.site_actions] == [sent[0].id]


async def test_the_instant_layer_and_the_agent_do_not_both_move_the_page() -> None:
    sent: list[SiteAction] = []
    c = ctx(sent=sent)
    c.log.site_actions.append(ShowFamily(family="containers", layer="instant", id="x"))
    first = c.log.site_actions[0]
    from kx.agents.site.actions import resolve_action

    resolved = resolve_action(first, MANIFEST)
    assert resolved is not None
    c.log.site_actions[0] = resolved
    assert "Already" in await _show(c, ShowFamily(family="containers"))
    assert sent == []


def test_site_tools_only_on_the_website_with_a_map() -> None:
    web = available_tools(ctx("web_chat"))
    assert {"show_product", "ask_for_contact", "find_products"} <= web
    assert "ask_product_specialist" not in web  # no fast model wired
    whatsapp = available_tools(ctx("whatsapp", fast=True))
    assert (
        "show_product" not in whatsapp
        and "find_products" in whatsapp
        and "ask_product_specialist" in whatsapp
    )
    assert "find_products" not in available_tools(ctx("web_chat", manifest=None))


def test_the_prompt_knows_the_page_and_how_to_move_it() -> None:
    facts = deal_facts(ctx("web_chat"))
    assert "The visitor is on the website: page products" in facts and "find_products" in facts
    assert "find_products" not in deal_facts(ctx("whatsapp"))


def test_find_products_by_reference_family_words_and_family() -> None:
    first = find_in_manifest(MANIFEST, "do you have 8325?")
    assert [p.id for p, _ in first][:2] == ["ac-8325", "sr-con-8325"]  # trade first, then the retail pack
    assert find_in_manifest(MANIFEST, "takeaway boxes")[0][0].family == "containers"
    assert [p.id for p, _ in find_in_manifest(MANIFEST, "anything", family="foil")] == ["foil-30"]
    assert find_in_manifest(MANIFEST, "bottles") == []


def test_variants_are_described_without_any_amount() -> None:
    text = describe_variants(
        (
            VariantView(
                sku="A",
                description="8325 ALUMINIUM CONTAINER",
                packing="1*1000 PCS",
                unit="pcs",
                availability="limited",
                moq=5000,
                quantity_breaks=(Decimal(10_000),),
            ),
            VariantView(
                sku="B",
                description="8325 LID",
                packing="1*1000 PCS",
                unit="pcs",
                availability="made_to_order",
                quotable=False,
            ),
        )
    )
    assert "limited stock" in text and "better pricing from 10,000" in text and "minimum order 5,000" in text
    assert "made to order" in text and "the sales team prices it" in text and "AED" not in text


async def test_a_specialist_answers_the_assistant_from_approved_facts_only() -> None:
    c = ctx(fast=True)
    answer = await _ask(c, "product", "what does the 8325 hold?")
    assert answer.startswith("product specialist (for you, not the buyer") and "1000 ml" in answer
    assert c.log.input_tokens > 0  # metered on the same run (D34 spend cap)
    nothing = await _ask(ctx(fast=True, manifest=None), "manufacturing", "how many machines?")
    assert nothing.startswith("Not in our approved content")  # no facts: no model call, no guess


@pytest.mark.parametrize(
    ("text", "role"),
    [
        ("what is the capacity of the 8325 container?", AiRole.PRODUCT),
        ("is your foil safe in the oven?", AiRole.PRODUCT),
        ("هل عندكم حاويات ألمنيوم مع غطاء؟", AiRole.PRODUCT),
        ("what is your monthly production capacity?", AiRole.MANUFACTURING),
        ("do you have ISO or halal certificates?", AiRole.MANUFACTURING),
    ],
)
def test_arifas_words_pick_the_role(text: str, role: AiRole) -> None:
    from arifaa_pack.topics import ARIFA_TOPICS

    deal = make_deal(missing=["quantity"])
    assert route(text, deal, topics=ARIFA_TOPICS).role is role


@pytest.mark.parametrize(
    ("text", "price"),
    [
        ("هل عندكم حاويات ألمنيوم؟", False),  # "do you have": the كم inside عندكم is no price ask
        ("شكرا لكم", False),
        ("كم السعر؟", True),
        ("بكم الكرتون؟", True),
        ("how much for 10,000?", True),
    ],
)
def test_arabic_how_much_is_a_word_of_its_own(text: str, price: bool) -> None:
    from kx.agents.router.service import asks_price

    assert asks_price(text) is price
