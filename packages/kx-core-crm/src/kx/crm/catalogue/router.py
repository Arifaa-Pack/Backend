"""18 Nov: the product workbook (one Excel file for products, prices and categories). Thin: the rules are in
`kx.crm.catalogue.workbook`. Screen flow: Download -> edit in Excel -> Upload (preview) -> Apply."""

# No `from __future__ import annotations` here: FastAPI must evaluate the closure-local `Session`
# alias; PEP 649 (Python 3.14) lazy annotations resolve it, string annotations would not.

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from kx.core.auth import Principal, Role, require_human_mfa
from kx.core.errors import RuleViolation
from kx.crm._shared.api_context import ApiContext
from kx.crm._shared.journal import Actor
from kx.crm.catalogue import workbook

# client decision 18 Nov: manager, MD and admin (a person with MFA: the workbook changes prices)
Editor = Annotated[Principal, Depends(require_human_mfa(Role.MANAGER, Role.MD, Role.ADMIN))]
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


async def _file(request: Request) -> bytes:
    data = await request.body()
    if not data:
        raise RuleViolation("send the .xlsx file as the request body")
    if len(data) > MAX_BYTES:
        raise RuleViolation("the workbook is larger than 10 MB")
    return data


def router(ctx: ApiContext) -> APIRouter:
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

    return r
