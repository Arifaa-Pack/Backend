"""§7 / D22 / D61: what the CMS publishes about a product reaches the CRM.

"Which system is the master copy: CMS for descriptive content; CRM for prices, MOQ and the default delivery
time." Since 19 Nov (D61) a CMS product is the website's marketing record (`ac-8325`); what Arifa sells,
prices and stocks are its **variants**: the ERP items in the CRM (`catalogue.erp_import`), linked to it by
`products.cms_product_id`. A publish therefore:

* remembers the website product (`website_products`) so ERP items can be matched to it, now and on later
  imports;
* links the unlinked sellable ERP items that fit it (`variants.link_unlinked`);
* writes the descriptive fields (name of the website product, specification, custom-printing flag, published
  flag) onto its variants, never prices, tiers, MOQ, lead days or `is_quotable`.

A website product with no ERP item yet creates nothing in the CRM: the AI records the buyer's words as the
requirement and the salesperson picks the item. Unpublish keeps the variants (open deals and quotes refer to
them) and only clears their published flag.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from kx.core.event_payloads import ProductPublished
from kx.core.no_prices import drop_price_keys
from kx.crm._shared.journal import Actor, record
from kx.crm.catalogue.variants import link_unlinked, remember_website_product
from kx.crm.models import Product

_ACTOR = Actor.system("cms_sync")


async def mirror_product(session: AsyncSession, p: ProductPublished, now: datetime) -> list[Product]:
    """The variants of the published website product, updated (empty when no ERP item is linked yet)."""
    await remember_website_product(session, p)
    linked, ambiguous = await link_unlinked(session) if p.published else (0, 0)
    linked_here = func.lower(Product.cms_product_id) == p.cms_id.lower()
    variants = list((await session.execute(select(Product).where(linked_here).with_for_update())).scalars())
    descriptive = drop_price_keys(p.spec)
    for product in variants:
        product.spec = {**(product.spec or {}), **descriptive}  # the workbook's columns show the same text
        if p.custom_printing is not None:
            product.custom_printing = p.custom_printing
        product.cms_published, product.cms_synced_at = p.published, now
    await session.flush()
    await record(
        session,
        _ACTOR,
        "catalogue.product_published" if p.published else "catalogue.product_unpublished",
        "website_product",
        p.cms_id,
        after={
            "name": p.name,
            "variants": len(variants),
            "newly_linked": linked,
            "ambiguous": ambiguous,
            "cms_published": p.published,
        },
    )
    return variants
