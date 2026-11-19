"""Pure rules of the CMS (no database): languages, the no-price rule, schemas, cache tags and paths."""

from __future__ import annotations

import pytest

from kx.cms._shared.localized import deep_merge, locales_of, localize
from kx.cms._shared.paths import ar, product_path, tags_and_paths
from kx.cms.content.service import validate_content
from kx.core.errors import RuleViolation
from kx.core.no_prices import drop_price_keys, find_price

PRODUCT = {
    "family": "containers",
    "ref": "8325",
    "code": "AC-1",
    "name": "Aluminium Container & Lid",
    "qty": "1 x 1000 pcs",
}


def test_arabic_falls_back_to_english_field_by_field() -> None:
    data = {
        "en": {
            "title": "Home",
            "blocks": [
                {"section_id": "top", "heading": "Hello"},
                {"section_id": "floor", "heading": "Walk the floor"},
            ],
        },
        "ar": {"blocks": [{}, {"heading": "جولة في المصنع"}]},
    }
    page = localize(data, "ar")
    assert page["title"] == "Home"  # not translated: English
    assert page["blocks"][0]["heading"] == "Hello"
    assert page["blocks"][1] == {"section_id": "floor", "heading": "جولة في المصنع"}  # merged by position
    assert localize(data, "en") == data["en"]
    assert locales_of(data) == ["en", "ar"]


def test_empty_translation_keeps_english() -> None:
    assert deep_merge({"a": "x", "b": ["y"]}, {"a": "", "b": []}) == {"a": "x", "b": ["y"]}


@pytest.mark.parametrize(
    "value",
    [
        {"name": "Tray", "specs": {"price": "1.20"}},  # a price-like field name
        {"name": "Tray", "description": "Only AED 1.20 per piece"},  # money in a text
        {"faqs": [{"question": "Cost?", "answer": "from $2 a carton"}]},
        {"blurb": "about 3.5 dirhams each"},
    ],
)
def test_prices_are_found_anywhere(value: dict[str, object]) -> None:
    assert find_price(value) is not None


@pytest.mark.parametrize(
    "value",
    [
        PRODUCT,
        {"text": "80% recycled aluminium, founded 1997, open 08:00 to 20:00"},
        {"title": "Request a quote", "qty": "10 pcs x 100 pkt", "capacity": "750 ml"},
    ],
)
def test_product_facts_are_not_prices(value: dict[str, object]) -> None:
    assert find_price(value) is None


def test_webhook_spec_drops_price_keys() -> None:
    assert drop_price_keys({"material": "alu", "unit_price": 1, "AED": 2, "tier": "A"}) == {"material": "alu"}


def test_validate_refuses_a_price_and_names_where() -> None:
    with pytest.raises(RuleViolation, match=r"en\.description"):
        validate_content("products", {"en": {**PRODUCT, "description": "Special: AED 99 per carton"}})


def test_validate_needs_english_and_known_languages() -> None:
    with pytest.raises(RuleViolation, match="English"):
        validate_content("products", {"ar": {"name": "حاوية"}})
    with pytest.raises(RuleViolation, match="unknown language"):
        validate_content("products", {"en": PRODUCT, "fr": {"name": "Barquette"}})


def test_validate_checks_the_merged_arabic_document() -> None:
    clean = validate_content("products", {"en": PRODUCT, "ar": {"name": "حاوية ألمنيوم مع غطاء"}})
    assert clean["ar"] == {"name": "حاوية ألمنيوم مع غطاء"}
    with pytest.raises(RuleViolation, match=r"ar\.range"):
        validate_content("products", {"en": PRODUCT, "ar": {"range": "wholesale"}})


def test_section_ids_are_the_dom_contract() -> None:
    page = {"title": "Home", "path": "/", "blocks": [{"section_id": "floor"}, {"section_id": "floor"}]}
    with pytest.raises(RuleViolation, match="unique"):
        validate_content("pages", {"en": page})
    with pytest.raises(RuleViolation, match="section_id"):
        validate_content("pages", {"en": {**page, "blocks": [{"section_id": "Not A Dom Id"}]}})


def test_publish_tags_and_paths_for_a_product() -> None:
    tags, paths = tags_and_paths("products", "ac-8325", PRODUCT, product_pages=False)
    assert {"products:ac-8325", "products", "manifest", "family:containers", "sitemap"} <= set(tags)
    assert "/products" in paths and "/ar/products" in paths and "/quality" in paths
    _, live = tags_and_paths("products", "ac-8325", PRODUCT, product_pages=True)
    assert "/products/ac-8325" in live and "/ar/products/ac-8325" in live


def test_site_settings_touch_every_page() -> None:
    _, paths = tags_and_paths("site", "site", {}, product_pages=False)
    assert {"/", "/ar", "/contact", "/ar/contact"} <= set(paths)


def test_product_path_until_pg03_is_live() -> None:
    assert product_path("ac-8325", product_pages=False) == "/products?p=ac-8325"
    assert product_path("ac-8325", product_pages=True) == "/products/ac-8325"
    assert ar("/") == "/ar" and ar("/about") == "/ar/about"
