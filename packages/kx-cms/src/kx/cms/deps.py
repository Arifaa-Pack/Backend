"""The long-lived collaborators every CMS router gets (built once by the app's composition root)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from kx.cms.content.service import ContentService
from kx.cms.media.service import MediaStorage
from kx.core.db import Databases


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class CmsDeps:
    databases: Databases
    preview_secret: str  # signs preview tokens (the platform's service token secret)
    media_base_url: str = ""  # public URL of the media bucket / CDN, e.g. https://media.arifapack.com
    storage: MediaStorage | None = None  # R2 presigned uploads; None = uploads unavailable (local dev)
    website_url: str | None = None  # the site, for preview links
    clock: Callable[[], datetime] = _now
    service: ContentService = field(default_factory=ContentService)
