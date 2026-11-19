"""Infra review M7 (19 Nov): the Chromium PDF engine prints our quote but runs no page script and fetches
nothing. Found on the way: `--blink-settings=scriptEnabled=false` makes headless Chromium write no PDF at all,
so scripts are blocked by a Content-Security-Policy in the page instead. The Chromium tests run where the
production image's Chromium is (the Linux test run sets KX_APP_CHROMIUM_PATH)."""

from __future__ import annotations

import os
import shutil
import threading
from collections.abc import Iterator
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from test_quote_document import DOC

from kx.crm.pdf import PDF_CSP, ChromiumEngine, QuoteRenderer, _locked

CHROMIUM = os.environ.get("KX_APP_CHROMIUM_PATH") or shutil.which("chromium") or ""
needs_chromium = pytest.mark.skipif(not os.path.exists(CHROMIUM), reason="Chromium not installed here")


def test_the_policy_is_the_first_thing_in_head() -> None:
    page = '<!doctype html><html><HEAD lang="en"><meta charset="utf-8"><title>T</title></HEAD><body>x</body></html>'
    assert _locked(page) == page.replace('<HEAD lang="en">', f'<HEAD lang="en">{PDF_CSP}')
    assert "default-src 'none'" in PDF_CSP and "img-src data:" in PDF_CSP and "script-src" not in PDF_CSP
    with pytest.raises(ValueError, match="no <head>"):
        _locked("<html><body>no head</body></html>")


@needs_chromium
@pytest.mark.parametrize("language", ["en", "ar"])
async def test_chromium_prints_the_real_quote(language: str) -> None:
    html = QuoteRenderer().html(replace(DOC, language=language))
    assert (await ChromiumEngine(CHROMIUM).render(html)).startswith(b"%PDF")


@pytest.fixture
def server() -> Iterator[tuple[int, list[str]]]:
    hits: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            hits.append(self.path)
            self.send_response(404)
            self.end_headers()

        def log_message(self, *args: object) -> None:
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1], hits
    srv.shutdown()


def _title_is(pdf: bytes, word: str) -> bool:
    """Chromium writes the page <title> into the PDF's metadata (plain or UTF-16BE hex)."""
    utf16 = ("﻿" + word).encode("utf-16-be")
    return word.encode() in pdf or utf16 in pdf or utf16.hex().upper().encode() in pdf.upper()


@needs_chromium
async def test_chromium_runs_no_script_and_fetches_nothing(server: tuple[int, list[str]]) -> None:
    port, hits = server
    page = (
        '<!doctype html><html><head><meta charset="utf-8"><title>SCRIPTOFF</title>'
        f"<style>@font-face {{font-family: X; src: url(http://127.0.0.1:{port}/font.woff2)}}"
        " body {font-family: X, sans-serif}</style></head><body><h1>Quote</h1>"
        f'<img src="http://127.0.0.1:{port}/tracker.png">'
        '<script>document.title = "SCRIPTRAN"</script></body></html>'
    )
    pdf = await ChromiumEngine(CHROMIUM).render(page)
    assert pdf.startswith(b"%PDF")
    assert _title_is(pdf, "SCRIPTOFF") and not _title_is(pdf, "SCRIPTRAN")
    assert hits == []
