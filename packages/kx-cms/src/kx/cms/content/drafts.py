"""18 Nov client decision, kept with the new CMS (D54): the CRM's product workbook is the master for product
details. When a manager applies an upload, each product's website details arrive here (event
`catalogue.product_content_changed.v1`) and are saved **as a draft**; a manager previews and publishes it in
the CMS. Photos, videos, FAQs, SEO and Arabic text are never touched by the workbook. No price exists in the
payload (`kx.core.event_payloads.ProductContentChanged`).

The website product is the one the CRM variant is linked to (`cms_product_id`, D61); a variant linked to no
website product is skipped (an ERP packing variant never becomes a website product of its own). A link to a
product the CMS does not have yet creates that product as a draft; its family is the workbook's category code
when that names a family, else the editor picks one before publishing.
"""

from __future__ import annotations

import re
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kx.cms._shared.localized import BASE_LOCALE
from kx.cms.content.service import ContentService
from kx.cms.models import Document, Version
from kx.core.event_payloads import ProductContentChanged

ACTOR: Final = "system:product-workbook"
_SPEC_FIELDS: Final = {
    "material": "material",
    "capacity": "capacity",
    "dimensions": "dimensions",
    "thickness": "thickness",
    "colours": "colours",
    "lidOptions": "lid_options",
}


def slug(code: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", code.lower()).strip("-")[:120] or "product"


async def _find(s: AsyncSession, key: str) -> tuple[Document, dict[str, Any]] | None:
    """The website product with this id (its open draft if there is one, else its live version)."""
    rows = await s.execute(
        select(Document, Version)
        .join(Version, Version.document_id == Document.id)
        .where(Document.collection == "products", Document.key == key.strip().lower())
        .where(Version.status != "archived")
        .order_by(Version.number.desc())
        .limit(1)
    )
    row = rows.first()
    return (row[0], row[1].data) if row else None


async def save_product_draft(
    s: AsyncSession, content: ProductContentChanged, service: ContentService
) -> tuple[str, bool] | None:
    """(document key, created?) after saving the workbook's details into the website product's draft; None
    when the CRM variant is linked to no website product (an ERP packing variant never becomes a product
    page)."""
    if not content.cms_product_id:
        return None
    found = await _find(s, content.cms_product_id)
    created = found is None
    key = found[0].key if found else slug(content.cms_product_id)
    data: dict[str, Any] = dict(found[1]) if found else {}
    en: dict[str, Any] = dict(data.get(BASE_LOCALE, {}))
    if created:
        en.update({"ref": content.code, "family": (content.category_code or "").lower()})
    en["name"] = content.name
    specs = dict(en.get("specs") or {})
    for field, value in content.fields.items():
        if field == "shortDescription":
            en["short_description"] = str(value)
        elif field == "descriptionText":
            en["description"] = str(value)
        elif field in {"customPrinting", "custom_printing"}:
            en["custom_printing"] = bool(value)
        elif field in _SPEC_FIELDS:
            specs[_SPEC_FIELDS[field]] = str(value)
    for field in content.cleared:
        if field == "shortDescription":
            en.pop("short_description", None)
        elif field == "descriptionText":
            en.pop("description", None)
        elif field in _SPEC_FIELDS:
            specs.pop(_SPEC_FIELDS[field], None)
    en["specs"] = specs
    data[BASE_LOCALE] = en
    doc = (await service.get(s, "products", key)) if not created else None
    revision = doc.draft.revision if doc and doc.draft else None
    await service.save_draft(s, "products", key, data, actor=ACTOR, revision=revision)
    return key, created
