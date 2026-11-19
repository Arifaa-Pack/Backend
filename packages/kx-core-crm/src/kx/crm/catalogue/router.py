"""18 Nov: the product workbook (one Excel file for products, prices and categories). Thin: the rules are in
`kx.crm.catalogue.workbook`. Screen flow: Download -> edit in Excel -> Upload (preview) -> Apply."""

# No `from __future__ import annotations` here: FastAPI must evaluate the closure-local `Session`
# alias; PEP 649 (Python 3.14) lazy annotations resolve it, string annotations would not.

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from kx.core.auth import Principal, Role, require_human_mfa, require_role
from kx.core.errors import NotFound, RuleViolation
from kx.crm._shared.api_context import STAFF, ApiContext
from kx.crm._shared.journal import Actor, record
from kx.crm.catalogue import erp_import, variants, workbook
from kx.crm.models import Product

# client decision 18 Nov: manager, MD and admin (a person with MFA: the workbook changes prices)
Editor = Annotated[Principal, Depends(require_human_mfa(Role.MANAGER, Role.MD, Role.ADMIN))]
# D61: the ERP import and variant links touch no price: manager, MD or admin, signed in
Catalogue = Annotated[Principal, Depends(require_role(Role.MANAGER, Role.ADMIN))]
Staff = Annotated[Principal, Depends(require_role(*STAFF))]
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MAX_BYTES = 10 * 1024 * 1024


class IssueOut(BaseModel):
    sheet: str
    row: int
    column: str
    message: str


class FieldChangeOut(BaseModel):
    field: str
    before: Any
    after: Any


class ProductChangeOut(BaseModel):
    code: str
    row: int
    is_new: bool
    changes: list[FieldChangeOut]
    website_draft: bool  # this product's details will be saved in the CMS as a draft


class TierChangeOut(BaseModel):
    code: str
    before: list[list[str | None]]  # [from qty, to qty, list price, minimum price]
    after: list[list[str | None]]


class CategoryChangeOut(BaseModel):
    code: str
    is_new: bool
    changes: list[FieldChangeOut]


class SummaryOut(BaseModel):
    new_products: int
    updated_products: int
    unchanged_products: int
    price_lists_changed: int
    categories_changed: int
    website_drafts: int


class PreviewOut(BaseModel):
    token: str  # send it back with the same file to apply
    ok: bool
    summary: SummaryOut
    errors: list[IssueOut]
    warnings: list[IssueOut]
    products: list[ProductChangeOut]
    price_lists: list[TierChangeOut]
    categories: list[CategoryChangeOut]


class AppliedOut(BaseModel):
    products_created: int
    products_updated: int
    price_lists_changed: int
    categories_changed: int
    website_drafts: int


def _txt(v: Any) -> str | None:
    return None if v is None else format(v.normalize(), "f") if hasattr(v, "normalize") else str(v)


def _preview_out(pv: workbook.Preview) -> PreviewOut:
    p = pv.plan
    return PreviewOut(
        token=pv.token,
        ok=p.ok,
        summary=SummaryOut(
            new_products=sum(x.is_new for x in p.products),
            updated_products=sum(not x.is_new for x in p.products),
            unchanged_products=p.unchanged_products,
            price_lists_changed=len(p.tiers),
            categories_changed=len(p.categories),
            website_drafts=sum(x.push for x in p.products),
        ),
        errors=[IssueOut(sheet=e.sheet, row=e.row, column=e.column, message=e.message) for e in p.errors],
        warnings=[IssueOut(sheet=e.sheet, row=e.row, column=e.column, message=e.message) for e in p.warnings],
        products=[
            ProductChangeOut(
                code=x.code,
                row=x.row,
                is_new=x.is_new,
                changes=[
                    FieldChangeOut(field=c.field, before=_txt(c.before), after=_txt(c.after))
                    for c in x.changes
                ],
                website_draft=x.push,
            )
            for x in p.products
        ],
        price_lists=[
            TierChangeOut(
                code=t.code,
                before=[[_txt(v) for v in r] for r in t.before],
                after=[[_txt(v) for v in r] for r in t.after],
            )
            for t in p.tiers
        ],
        categories=[
            CategoryChangeOut(
                code=c.code,
                is_new=c.is_new,
                changes=[
                    FieldChangeOut(field=f.field, before=_txt(f.before), after=_txt(f.after))
                    for f in c.changes
                ],
            )
            for c in p.categories
        ],
    )


class ErpImportOut(BaseModel):
    rows: int
    created: int
    updated: int
    sellable: int
    not_sellable: int
    linked_to_website: int
    ambiguous_links: int
    errors: list[str]
    saved: bool


class WebsiteLinkIn(BaseModel):
    cms_product_id: str | None  # the website product (ac-8325); null = unlink


class VariantOut(BaseModel):
    sku: str
    description: str
    packing: str | None
    unit: str
    availability: str
    moq: int | None
    quantity_breaks: list[str]
    quotable: bool


async def _file(request: Request) -> bytes:
    data = await request.body()
    if not data:
        raise RuleViolation("send the .xlsx file as the request body")
    if len(data) > MAX_BYTES:
        raise RuleViolation("the workbook is larger than 10 MB")
    return data


def router(ctx: ApiContext) -> APIRouter:
    root = APIRouter()
    r = APIRouter(prefix="/catalogue/workbook", tags=["product workbook"])
    Session = Annotated[AsyncSession, Depends(ctx.session)]  # noqa: N806
    deps = ctx.deps

    @r.get("")
    async def download(s: Session, _: Editor) -> Response:
        """Download the current catalogue (products, price tiers, categories, how-to sheet). Staff edit this
        file and upload it back; it contains minimum prices, so only manager / MD / admin get it."""
        data = await workbook.download(s, deps.now())
        return Response(
            data,
            media_type=XLSX,
            headers={"Content-Disposition": 'attachment; filename="product-workbook.xlsx"'},
        )

    @r.post("/preview", response_model=PreviewOut)
    async def preview(request: Request, s: Session, _: Editor) -> PreviewOut:
        """Body: the .xlsx file. Saves nothing: returns every change and every error (sheet, row, column),
        plus the `token` that `apply` needs."""
        return _preview_out(await workbook.preview(s, await _file(request), deps.now()))

    @r.post("/apply", response_model=AppliedOut)
    async def apply(
        request: Request, s: Session, p: Editor, token: Annotated[str, Query(min_length=64, max_length=64)]
    ) -> AppliedOut:
        """Body: the SAME .xlsx file as the preview; `token` from the preview. Saves everything or nothing.
        409 when the catalogue changed since the preview (preview again); 422 when the file has errors."""
        done = await workbook.apply(s, await _file(request), token, actor=Actor.user(p), now=deps.now())
        return AppliedOut(
            products_created=done.products_created,
            products_updated=done.products_updated,
            price_lists_changed=done.price_lists_changed,
            categories_changed=done.categories_changed,
            website_drafts=done.website_drafts,
        )

    erp = APIRouter(prefix="/catalogue", tags=["product workbook"])

    @erp.post("/erp-import", response_model=ErpImportOut)
    async def import_erp(
        request: Request, s: Session, p: Catalogue, dry_run: Annotated[bool, Query()] = False
    ) -> ErpImportOut:
        """D61: body = the ERP stock list (CSV or .xlsx with the columns Category, Item Description, Brand,
        Unit, OpnStk, In, Out, ClosStk). Every row is kept (packing and raw material too, marked not
        sellable); codes are made from description + unit; closing stock is saved; new sellable items are
        linked to their website product where it is safe. Prices are never touched. `dry_run=true` shows
        what would change and saves nothing."""
        data = await _file(request)
        async with s.begin_nested() as nested:
            report = await erp_import.import_erp(s, data, actor=Actor.user(p), now=deps.now())
            if dry_run:
                await nested.rollback()
        return ErpImportOut(**erp_import.summary(report), saved=not dry_run)

    @erp.put("/products/{sku}/website-link", status_code=204)
    async def set_website_link(sku: str, body: WebsiteLinkIn, s: Session, p: Catalogue) -> Response:
        """Link an ERP item (a variant) to its website product, or unlink it (null): when the automatic
        match was not sure, or was wrong."""
        product = (
            await s.execute(select(Product).where(func.upper(Product.sku) == sku.upper()).with_for_update())
        ).scalar_one_or_none()
        if product is None:
            raise NotFound(f"product {sku} not found")
        before = product.cms_product_id
        product.cms_product_id = body.cms_product_id
        await record(
            s,
            Actor.user(p),
            "catalogue.variant_linked",
            "product",
            product.id,
            before={"cms_product_id": before},
            after={"cms_product_id": body.cms_product_id},
        )
        return Response(status_code=204)

    @erp.get("/website-products/{cms_product_id}/variants", response_model=list[VariantOut])
    async def website_variants(cms_product_id: str, s: Session, _: Staff) -> list[VariantOut]:
        """The sellable variants of a website product, as the AI describes them (availability word, the
        quantities where a better price starts; no amounts)."""
        settings = await deps.settings.get(s)
        found = await variants.variants_of(
            s, cms_product_id, limited_below=settings.stock_limited_below, now=deps.now()
        )
        return [
            VariantOut(
                sku=v.sku,
                description=v.description,
                packing=v.packing,
                unit=v.unit,
                availability=v.availability,
                moq=v.moq,
                quantity_breaks=[str(q) for q in v.quantity_breaks],
                quotable=v.quotable,
            )
            for v in found
        ]

    root.include_router(r)
    root.include_router(erp)
    return root
