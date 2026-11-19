"""SRS §7 "draft -> preview -> publish": a short-lived token lets the website show drafts.

An editor asks for a token in the admin; the website opens in Next.js draft mode and passes `?preview=<token>`
to the content API, which then answers the open draft (or the live version where there is no draft), never
cached. The token is an HMAC-signed claim (`kx.core.signed`), valid 30 minutes, for content reading only.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Final

from kx.core.signed import sign, verify

PURPOSE: Final = "cms_preview"
TTL: Final = timedelta(minutes=30)


def issue(secret: str, actor: str, now: datetime) -> tuple[str, datetime]:
    expires = now + TTL
    return sign(secret, {"p": PURPOSE, "by": actor[:100]}, expires_at=expires), expires


def valid(secret: str, token: str | None, now: datetime) -> bool:
    if not token:
        return False
    claims = verify(secret, token, now=now)
    return claims is not None and claims.get("p") == PURPOSE
