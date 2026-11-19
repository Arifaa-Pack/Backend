"""19 Nov live re-test (security probes): NUL bytes are refused before PostgreSQL sees them (a search with
`%00` answered 500), and every answer carries the security headers."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
from fastapi import FastAPI, Request, Response

from arifaa_pack.inputguard import API_CSP, DOCS_CSP, RejectNul, SecurityHeaders


def _app() -> FastAPI:
    app = FastAPI()

    @app.get("/v1/contacts")
    async def search(q: str = "") -> dict[str, str]:
        return {"q": q}

    @app.post("/v1/notes")
    async def note(request: Request) -> dict[str, int]:
        return {"n": len(await request.body())}

    @app.post("/v1/price-list/import")
    async def upload(request: Request) -> dict[str, int]:
        return {"n": len(await request.body())}

    @app.get("/v1/quote.pdf")
    async def pdf() -> Response:
        return Response(b"%PDF-1.7", media_type="application/pdf")

    @app.get("/v1/framed")
    async def framed() -> Response:
        return Response("{}", media_type="application/json", headers={"X-Frame-Options": "SAMEORIGIN"})

    return app


def _client(app: FastAPI) -> httpx.AsyncClient:
    guarded = SecurityHeaders(RejectNul(app))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=guarded), base_url="http://t")


async def test_nul_in_the_query_string_or_path_is_a_400_not_a_database_error() -> None:
    async with _client(_app()) as c:
        assert (await c.get("/v1/contacts", params={"q": "acme"})).json() == {"q": "acme"}
        for raw in ("/v1/contacts?q=%00", "/v1/contacts?q=a%00b", "/v1/contacts?q=%00%27", "/v1/con%00tacts"):
            r = await c.get(raw)
            assert r.status_code == 400, raw
            assert r.headers["content-type"] == "application/problem+json"
            assert r.json()["detail"] == "the request contains a NUL character"


async def test_nul_in_a_json_or_form_body_is_a_400_even_split_across_chunks() -> None:
    async with _client(_app()) as c:
        ok = await c.post("/v1/notes", json={"text": "fine"})
        assert ok.status_code == 200
        assert (await c.post("/v1/notes", json={"text": "a\u0000b"})).status_code == 400
        form = {"content-type": "application/x-www-form-urlencoded"}
        assert (await c.post("/v1/notes", content=b"reason=a%00", headers=form)).status_code == 400

        async def split() -> AsyncIterator[bytes]:  # the JSON escape arrives in two chunks
            yield b'{"text": "a\\u00'
            yield b'00b"}'

        r = await c.post("/v1/notes", content=split(), headers={"content-type": "application/json"})
        assert r.status_code == 400


async def test_binary_uploads_are_not_inspected() -> None:
    async with _client(_app()) as c:
        body = b"PK\x03\x04\x00\x00binary"
        files = {"file": ("prices.xlsx", body, "application/octet-stream")}
        assert (await c.post("/v1/price-list/import", files=files)).status_code == 200
        raw = await c.post(
            "/v1/price-list/import", content=body, headers={"content-type": "application/octet-stream"}
        )
        assert raw.json() == {"n": len(body)}


async def test_security_headers_on_every_answer_but_no_csp_on_a_pdf() -> None:
    async with _client(_app()) as c:
        r = await c.get("/v1/contacts")
        assert r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["x-frame-options"] == "DENY"
        assert r.headers["referrer-policy"] == "no-referrer"
        assert r.headers["content-security-policy"] == API_CSP.decode()
        refused = await c.get("/v1/contacts?q=%00")
        assert refused.headers["x-frame-options"] == "DENY"  # the guard's own 400 too
        pdf = await c.get("/v1/quote.pdf")
        assert (
            "content-security-policy" not in pdf.headers
            and pdf.headers["x-content-type-options"] == "nosniff"
        )
        assert (await c.get("/v1/framed")).headers["x-frame-options"] == "SAMEORIGIN"  # a route's own value


async def test_the_swagger_page_gets_a_csp_it_can_run_under() -> None:
    async with _client(_app()) as c:
        r = await c.get("/docs")
        assert r.status_code == 200 and r.headers["content-security-policy"] == DOCS_CSP.decode()
