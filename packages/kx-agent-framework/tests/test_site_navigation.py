"""D55 / D57: the site actions and the instant resolver (no model, no database).

Precision over recall: a wrong move is worse than no move, so most of these check that the resolver keeps
quiet when the visitor is not asking to see something."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

import pytest

from kx.agents.site.actions import resolve_action
from kx.agents.site.models import (
    AddToEnquiry,
    EnquiryFill,
    FactoryTour,
    FillEnquiry,
    Navigate,
    ScrollTo,
    ShowFamily,
    ShowProduct,
    SiteContext,
    intent_id,
)
from kx.agents.site.resolver import instant_actions
from kx.core.contracts import (
    ManifestChapter,
    ManifestFamily,
    ManifestPage,
    ManifestProduct,
    ManifestSection,
    SiteManifest,
)


def _product(
    pid: str, ref: str, family: str, rng: Literal["trade", "self"] = "trade", code: str | None = None
) -> ManifestProduct:
    return ManifestProduct(
        id=pid,
        ref=ref,
        catalogue_code=code,
        family=family,
        name=pid,
        range=rng,
        path_en=f"/products?p={pid}",
        path_ar=f"/ar/products?p={pid}",
    )


MANIFEST = SiteManifest(
    revision=7,
    built_at=datetime(2026, 11, 19, tzinfo=UTC),
    pages=(
        ManifestPage(
            key="home",
            title="Home",
            path_en="/",
            path_ar="/ar",
            sections=(
                ManifestSection(id="factory", keywords=("how it is made", "production line")),
                ManifestSection(id="floor", keywords=("factory film",)),
            ),
        ),
        ManifestPage(
            key="products",
            title="Products",
            path_en="/products",
            path_ar="/ar/products",
            sections=(ManifestSection(id="catalogue"),),
        ),
        ManifestPage(
            key="quality",
            title="Quality",
            path_en="/quality",
            path_ar="/ar/quality",
            sections=(ManifestSection(id="documents", keywords=("certificates", "iso")),),
        ),
        ManifestPage(
            key="contact",
            title="Contact",
            path_en="/contact",
            path_ar="/ar/contact",
            sections=(
                ManifestSection(id="enquiry", keywords=("enquiry",)),
                ManifestSection(id="where", keywords=("address", "directions")),
            ),
        ),
    ),
    families=(
        ManifestFamily(
            id="foil",
            name="Aluminium Foil",
            names={"en": "Aluminium Foil", "ar": "ورق الألمنيوم"},
            synonyms={"en": ("kitchen foil", "foil roll"), "ar": ("ورق قصدير",)},
            path_en="/products?family=foil",
            path_ar="/ar/products?family=foil",
        ),
        ManifestFamily(
            id="containers",
            name="Aluminium Containers",
            names={"en": "Aluminium Containers"},
            synonyms={"en": ("foil containers", "takeaway containers")},
            path_en="/products?family=containers",
            path_ar="/ar/products?family=containers",
        ),
        ManifestFamily(
            id="cling-film",
            name="Cling Film",
            names={"zh": "保鲜膜"},
            path_en="/products?family=cling-film",
            path_ar="/ar/products?family=cling-film",
        ),
    ),
    products=(
        _product("ac-8325", "8325", "containers", code="AC-1"),
        _product("ac-8342", "8342", "containers", code="AC-2"),
        _product("sr-con-8342", "8342", "containers", "self", code="ARF-8342"),
    ),
    chapters=(ManifestChapter(id="foil", title="Foil", seconds=82.8, family="foil"),),
)


def one(text: str, context: SiteContext | None = None) -> object:
    found = instant_actions(text, MANIFEST, context)
    assert len(found) == 1, (text, found)
    return found[0].action


@pytest.mark.parametrize(
    ("text", "family"),
    [
        ("show me foil containers", "containers"),
        ("kitchen foil", "foil"),
        ("Do you have takeaway containers?", "containers"),
        ("أرني ورق الألمنيوم", "foil"),
        ("ورق قصدير", "foil"),
        ("给我看保鲜膜", "cling-film"),
    ],
)
def test_family_words_in_six_languages(text: str, family: str) -> None:
    action = one(text)
    assert isinstance(action, ShowFamily) and action.family == family
    assert action.layer == "instant" and action.resolved is not None
    assert action.resolved.path_en == f"/products?family={family}"


def test_a_product_code_opens_the_product() -> None:
    action = one("I want product 8325")
    assert isinstance(action, ShowProduct) and action.product_id == "ac-8325" and action.view == "quick"
    assert one("AC-1 please").product_id == "ac-8325"  # type: ignore[attr-defined]


def test_a_shared_reference_is_narrowed_or_left_to_the_family() -> None:
    assert one("show me 8342 retail pack").product_id == "sr-con-8342"  # type: ignore[attr-defined]
    ambiguous = one("8342")  # the trade carton and the self-range pack share it: the agent asks which
    assert isinstance(ambiguous, ShowFamily) and ambiguous.family == "containers"


@pytest.mark.parametrize(
    "text",
    [
        "what is your production capacity per month and do you hold ISO certificates for food contact items",
        "my email is buyer@example.com",
        "call me on +971 50 123 4567",
        "we need 20,000 pieces delivered to Dubai every month for our restaurants please",
        "hello",
        "",
    ],
)
def test_no_move_without_a_clear_request(text: str) -> None:
    assert instant_actions(text, MANIFEST) == []


def test_pages_sections_and_the_film() -> None:
    nav = one("where are your certificates?")
    assert isinstance(nav, Navigate) and (nav.page, nav.section) == ("quality", "documents")
    assert nav.resolved is not None and nav.resolved.path_ar == "/ar/quality#documents"
    tour = one("show me the factory film")
    assert isinstance(tour, FactoryTour) and tour.resolved is not None and tour.resolved.anchor == "floor"


def test_already_on_that_family_does_not_move() -> None:
    here = SiteContext(page="products", family="foil", path="/products?family=foil")
    assert instant_actions("kitchen foil", MANIFEST, here) == []


def test_unknown_targets_are_dropped() -> None:
    assert resolve_action(ShowProduct(product_id="ac-9999"), MANIFEST) is None
    assert resolve_action(ShowFamily(family="bottles"), MANIFEST) is None
    assert resolve_action(Navigate(page="careers"), MANIFEST) is None
    assert resolve_action(Navigate(page="quality", section="nope"), MANIFEST) is None
    assert resolve_action(FactoryTour(chapter="painting"), MANIFEST) is None


def test_scroll_to_another_pages_section_becomes_navigate() -> None:
    action = resolve_action(ScrollTo(section="documents"), MANIFEST, SiteContext(page="home"))
    assert isinstance(action, Navigate) and action.page == "quality"
    here = resolve_action(ScrollTo(section="floor"), MANIFEST, SiteContext(page="home"))
    assert isinstance(here, ScrollTo) and here.resolved is not None and here.resolved.path_en == "/#floor"


def test_product_page_view_falls_back_to_quick_view_until_pg03_is_live() -> None:
    action = resolve_action(ShowProduct(product_id="ac-8325", view="page"), MANIFEST)
    assert isinstance(action, ShowProduct) and action.view == "quick"


def test_enquiry_fill_never_carries_a_price_and_keeps_known_families() -> None:
    priced = FillEnquiry(fill=EnquiryFill(message="they quoted AED 0.30 each", family=["foil"]))
    assert resolve_action(priced, MANIFEST) is None
    clean = resolve_action(
        FillEnquiry(fill=EnquiryFill(quantity="20,000 a month", family=["foil", "x"])), MANIFEST
    )
    assert isinstance(clean, FillEnquiry) and clean.fill.family == ["foil"]
    assert clean.resolved is not None and clean.resolved.anchor == "enquiry"
    add = resolve_action(AddToEnquiry(product_ids=["ac-8325", "nope", "ac-8325"]), MANIFEST)
    assert isinstance(add, AddToEnquiry) and add.product_ids == ["ac-8325"]


def test_the_same_intent_has_the_same_id_in_both_layers() -> None:
    fast = one("kitchen foil")
    slow = resolve_action(ShowFamily(family="foil", layer="agent"), MANIFEST)
    assert slow is not None and fast.id == slow.id == intent_id(ShowFamily(family="foil"))  # type: ignore[attr-defined]
