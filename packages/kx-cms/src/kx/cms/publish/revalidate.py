"""After a publish, tell the website which pages to re-render (SRS §7: "publishing updates the website within
about a minute"). Runs as a durable job on `content.published.v1` (retried by the job store).

    POST {website}/api/revalidate
    Content-Type: application/json
    X-Kx-Timestamp: <unix seconds>
    X-Kx-Signature: sha256=<hex HMAC-SHA256(secret, "<timestamp>." + raw body)>

    {"event_id": "...", "collection": "products", "key": "ac-8325", "operation": "publish",
     "tags": ["products:ac-8325", "products", "manifest", "family:containers", "sitemap"],
     "paths": ["/products", "/ar/products", "/quality", "/ar/quality"]}

The same signing scheme the platform already uses for webhooks. The website answers 2xx; a repeat of the same
`event_id` is harmless (re-rendering twice changes nothing). A 60-second ISR fallback on the site covers a
call that never arrives.
"""

from __future__ import annotations

import json
import time

import httpx

from kx.core.event_payloads import ContentPublished
from kx.core.events import Event
from kx.core.identity import hmac_sha256_hex
from kx.core.logging import get_logger

log = get_logger(__name__)


def signature(secret: str, timestamp: str, body: bytes) -> str:
    return "sha256=" + hmac_sha256_hex(secret.encode(), timestamp.encode() + b"." + body)


class WebsiteRevalidator:
    def __init__(self, http: httpx.AsyncClient, url: str | None, secret: str | None) -> None:
        self._http = http
        self._url = url.rstrip("/") + "/api/revalidate" if url else None
        self._secret = secret

    async def __call__(self, event: Event) -> None:
        if not self._url or not self._secret:
            log.warning("cms.revalidate_skipped", reason="KX_APP_WEBSITE_URL / _REVALIDATE_SECRET not set")
            return
        p = ContentPublished.model_validate(event.payload)
        body = json.dumps(
            {
                "event_id": str(event.event_id),
                "collection": p.collection,
                "key": p.key,
                "operation": p.operation,
                "tags": p.tags,
                "paths": p.paths,
            },
            separators=(",", ":"),
        ).encode()
        ts = str(int(time.time()))
        r = await self._http.post(
            self._url,
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Kx-Timestamp": ts,
                "X-Kx-Signature": signature(self._secret, ts, body),
            },
            timeout=10.0,
        )
        r.raise_for_status()  # the job store retries
        log.info("cms.revalidated", collection=p.collection, key=p.key, paths=len(p.paths))
