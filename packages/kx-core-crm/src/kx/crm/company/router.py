"""18 Nov: editable company details (letterhead, logo, bank details on quotes). Thin: rules in
`kx.crm.company.service`."""

# No `from __future__ import annotations` here: FastAPI must evaluate the closure-local `Session`
# alias; PEP 649 (Python 3.14) lazy annotations resolve it, string annotations would not.

from dataclasses import asdict
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from kx.core.auth import Principal, Role, require_human_mfa, require_role
from kx.core.errors import RuleViolation
from kx.crm._shared.api_context import ApiContext
from kx.crm._shared.journal import Actor
from kx.crm.company import service as company

# client decision 18 Nov: admin, MD and manager
Viewer = Annotated[Principal, Depends(require_role(Role.ADMIN, Role.MD, Role.MANAGER))]
Editor = Annotated[Principal, Depends(require_human_mfa(Role.ADMIN, Role.MD, Role.MANAGER))]


class BankIn(BaseModel):
    account_name: str | None = Field(default=None, max_length=200)
    bank_name: str | None = Field(default=None, max_length=200)
    iban: str | None = Field(default=None, max_length=42)
    swift: str | None = Field(default=None, max_length=11)


class CompanyIn(BaseModel):
    """Only the fields to change; "" clears a field (company.yaml's value is printed again)."""

    # kx.crm.records.router has a model of the same name, so the OpenAPI component name carries the module
    # path: pinned to the pre-ADR-0001 module path to keep the published name (the module itself is gone).
    __module__ = "kx.crm.api.company"

    name: str | None = Field(default=None, max_length=200)
    address: str | None = Field(default=None, max_length=400)
    trn: str | None = Field(default=None, max_length=20)
    phone: str | None = Field(default=None, max_length=30)
    email: str | None = Field(default=None, max_length=200)
    bank: BankIn | None = None


class CompanyOut(BaseModel):
    saved: dict[str, Any]  # what staff set (empty fields = company.yaml)
    printed: dict[str, Any]  # what quotes print now (company.yaml overlaid with `saved`)
    has_logo: bool


async def _out(s: AsyncSession, ctx: ApiContext) -> CompanyOut:
    profile = (await ctx.deps.settings.get(s)).company_profile
    printed = asdict(await company.letterhead(s, ctx.deps))
    printed.pop("logo", None)
    saved = profile.model_dump()
    saved.pop("logo", None)
    return CompanyOut(saved=saved, printed=printed, has_logo=bool(profile.logo))


def router(ctx: ApiContext) -> APIRouter:
    r = APIRouter(prefix="/company", tags=["company details"])
    Session = Annotated[AsyncSession, Depends(ctx.session)]  # noqa: N806

    @r.get("", response_model=CompanyOut)
    async def show(s: Session, _: Viewer) -> CompanyOut:
        """The company details printed on quotes and summaries, and what staff have set."""
        return await _out(s, ctx)

    @r.patch("", response_model=CompanyOut)
    async def edit(body: CompanyIn, s: Session, p: Editor) -> CompanyOut:
        """Admin / MD / manager (MFA): change details; live on new quotes within 30 seconds. A change of bank
        details alerts the MD and the manager. 422 `invalid_company` lists the fields that are wrong (TRN 15
        digits, IBAN checksum, SWIFT, email, phone)."""
        changes = body.model_dump(exclude_unset=True)
        if "bank" in changes:
            changes["bank"] = {k: v for k, v in (changes["bank"] or {}).items() if v is not None}
        await company.update(s, ctx.deps, changes, actor=Actor.user(p))
        ctx.deps.settings.invalidate()
        return await _out(s, ctx)

    @r.put("/logo", response_model=CompanyOut)
    async def upload_logo(request: Request, s: Session, p: Editor) -> CompanyOut:
        """Body: the PNG or JPEG file itself (`Content-Type: image/png` or `image/jpeg`), max 200 KB."""
        data = await request.body()
        if not data:
            raise RuleViolation("send the image as the request body", reason="invalid_logo")
        uri = company.logo_data_uri(data, request.headers.get("content-type", ""))
        await company.update(s, ctx.deps, {"logo": uri}, actor=Actor.user(p))
        ctx.deps.settings.invalidate()
        return await _out(s, ctx)

    @r.delete("/logo", response_model=CompanyOut)
    async def remove_logo(s: Session, p: Editor) -> CompanyOut:
        """Remove the logo from quotes."""
        await company.update(s, ctx.deps, {"logo": ""}, actor=Actor.user(p))
        ctx.deps.settings.invalidate()
        return await _out(s, ctx)

    return r
