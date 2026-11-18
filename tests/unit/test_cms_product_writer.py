"""18 Nov product workbook -> CMS: product details are saved in Payload as DRAFTS (a manager publishes), specs are
merged (never replaced), only the fields the workbook set are sent, and no price ever reaches the CMS."""

from __future__ import annotations

import json
from typing import Any

import httpx

from arifaa_pack.adapters import PayloadProductWriter
from kx.core.event_payloads import ProductContentChanged


class FakePayload:
    def __init__(self, product: dict[str, Any] | None = None, category: dict[str, Any] | None = None) -> None:
        self.product, self.category = product, category
        self.calls: list[tuple[str, str, dict[str, str], dict[str, Any] | None]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, request.url.path, dict(request.url.params), body))
        assert request.headers["authorization"] == "users API-Key k"
        if request.method == "GET":
            doc = self.product if request.url.path.endswith("/products") else self.category
            return httpx.Response(200, json={"docs": [doc] if doc else []})
        if request.url.path.endswith("/product-categories"):
            return httpx.Response(201, json={"doc": {"id": "cat-new"}})
        return httpx.Response(200 if request.method == "PATCH" else 201, json={"doc": {"id": "prod-1"}})


async def save(fake: FakePayload, content: ProductContentChanged) -> tuple[str, bool]:
    async with httpx.AsyncClient(transport=httpx.MockTransport(fake)) as http:
        return await PayloadProductWriter(http, "http://cms:3000/", "k").save_draft(content)


async def test_a_new_product_and_category_are_created_as_drafts() -> None:
    fake = FakePayload()
    content = ProductContentChanged(
        code="BOX-1",
        name="Kraft box",
        category_code="BOXES",
        category_name="Kraft boxes",
        fields={
            "capacity": "1000 ml",
            "moq": 8000,
            "shortDescription": "Grease resistant",
            "custom_printing": True,
        },
    )
    assert await save(fake, content) == ("prod-1", True)
    writes = [(m, path, params, body) for m, path, params, body in fake.calls if m != "GET"]
    (_, cat_path, cat_params, cat_body), (_, path, params, body) = writes
    assert cat_path == "/api/product-categories" and cat_params == {"draft": "true"}
    assert cat_body == {"code": "BOXES", "name": "Kraft boxes", "_status": "draft"}
    assert path == "/api/products" and params == {"draft": "true"} and body is not None
    assert body["_status"] == "draft" and body["category"] == "cat-new" and body["title"] == "Kraft box"
    assert body["specs"] == {"capacity": "1000 ml"} and body["moq"] == 8000 and body["customPrinting"] is True
    assert "price" not in json.dumps(body).lower()


async def test_an_existing_product_gets_a_draft_update_that_merges_specs_and_touches_nothing_else() -> None:
    existing = {
        "id": "prod-9",
        "code": "CUP-8",
        "specs": {"material": "Paper", "colours": "White", "capacity": "240 ml"},
        "photos": ["p1"],
        "description": {"root": "rich text"},
    }
    fake = FakePayload(product=existing, category={"id": "cat-1"})
    content = ProductContentChanged(
        code="CUP-8",
        name="8 oz cup",
        category_code="CUPS",
        fields={"capacity": "250 ml"},
        cleared=["colours", "shortDescription"],
    )
    assert await save(fake, content) == ("prod-1", False)
    method, path, params, body = fake.calls[-1]
    assert (method, path, params) == ("PATCH", "/api/products/prod-9", {"draft": "true"}) and body is not None
    assert body["specs"] == {"material": "Paper", "colours": None, "capacity": "250 ml"}
    assert body["shortDescription"] is None and body["category"] == "cat-1"
    assert (
        "photos" not in body and "description" not in body
    )  # photos and rich text stay as edited in the CMS
