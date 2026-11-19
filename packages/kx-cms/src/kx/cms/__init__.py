"""kx.cms (D54): the website's content, edited by the client team (SRS §7).

Public surface for the app (ADR-0001 rule 3: the app imports only this module):

* `CmsDeps` + `build_public_router` (`/v1/content`, the website reads) + `build_admin_router` and
  `build_media_router` (`/v1/cms`, the editors);
* `ManifestCache`: `kx.core.contracts.SiteManifestSource`, the site map the AI drives the website with (D55);
* `plain_document`: published text for the AI's knowledge base (the app adapts it to kx.agents' source);
* `save_product_draft`: the CRM product workbook's details saved as a product draft (18 Nov);
* `WebsiteRevalidator`: the job that tells the website which pages to re-render after a publish;
* `MediaStorage`: what the app's R2 client must offer for uploads;
* `import_site`: load the current website's catalogue and content (`kx content import-site`).

Like every package, kx.cms imports only kx.core and owns its own database (`cms`).
"""

from __future__ import annotations

from kx.cms.content.admin_router import build_admin_router
from kx.cms.content.drafts import save_product_draft
from kx.cms.content.public_router import build_public_router
from kx.cms.content.reader import PlainDocument, plain_document
from kx.cms.content.schemas import COLLECTIONS, KB_COLLECTIONS
from kx.cms.content.service import ContentService
from kx.cms.deps import CmsDeps
from kx.cms.manifest.service import ManifestCache, build_manifest
from kx.cms.media.router import build_media_router
from kx.cms.media.service import MediaStorage
from kx.cms.models import SCHEMA
from kx.cms.publish.revalidate import WebsiteRevalidator
from kx.cms.site_import.service import ImportReport, import_site

__all__ = [
    "COLLECTIONS",
    "KB_COLLECTIONS",
    "SCHEMA",
    "CmsDeps",
    "ContentService",
    "ImportReport",
    "ManifestCache",
    "MediaStorage",
    "PlainDocument",
    "WebsiteRevalidator",
    "build_admin_router",
    "build_manifest",
    "build_media_router",
    "build_public_router",
    "import_site",
    "plain_document",
    "save_product_draft",
]
