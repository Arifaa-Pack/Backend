"""§20.11 settings that live in automation: "WhatsApp numbers per region" and "message templates" (admin).

* Channel accounts: which company number serves which countries (§11.4 / D11 regional numbers). Secrets are
  never shown or set here: `secret_ref` names the Secrets Manager entry an operator filled.
* Templates: WhatsApp templates must be approved by Meta before they can open a chat (§14 practical notes,
  §17), so a new or edited WhatsApp template is `submitted` until the admin records Meta's approval; email
  templates are usable at once. Every change is logged.
"""

# No `from __future__ import annotations` here: FastAPI resolves the closure-local annotations.

import uuid
from datetime import datetime
from typing import Annotated, Any, Final, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from kx.automation.models import ChannelAccount, MessageTemplate
from kx.core.auth import Principal, Role, require_human_mfa, require_role
from kx.core.db import Databases, get_or_404
from kx.core.logging import get_logger
from kx.core.settings import LogicalDb

log = get_logger(__name__)
Reader = Annotated[Principal, Depends(require_role(Role.ADMIN, Role.MANAGER, Role.MD))]
Admin = Annotated[Principal, Depends(require_human_mfa(Role.ADMIN))]
# client decision 18 Nov: manager, MD and admin edit message templates (a person with MFA)
TemplateEditor = Annotated[Principal, Depends(require_human_mfa(Role.MANAGER, Role.MD, Role.ADMIN))]
LIVE: Final = "approved"
NOT_YET_LIVE: Final = frozenset({"draft", "submitted", "rejected"})


class ChannelAccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    channel: str
    provider: str
    display_name: str
    address: str  # the company number / address buyers see
    purpose: str  # customer | internal_alerts
    status: str
    region: str | None
    countries: list[str]  # ISO-2 countries this number serves (§11.4)
    is_default: bool
    created_at: datetime


class ChannelAccountPatch(BaseModel):
    display_name: str | None = Field(default=None, max_length=200)
    region: str | None = Field(default=None, max_length=64)
    countries: list[Annotated[str, Field(pattern=r"^[A-Z]{2}$")]] | None = None
    is_default: bool | None = None
    status: Literal["active", "disabled"] | None = None


class TemplateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    channel: str
    name: str
    language: str
    category: str
    provider_status: (
        str  # draft | submitted | approved | rejected | paused | superseded (Meta's decision for WhatsApp)
    )
    subject: str | None
    body: str  # placeholders {{1}}, {{2}} ... in Meta's style
    variables: list[str]
    version: int
    provider_name: str | None  # the name submitted to Meta for this version (quote_ready_v2)
    live: bool = False  # this version is the one being sent now
    created_by: str | None
    created_at: datetime
    updated_at: datetime


class TemplateIn(BaseModel):
    channel: Literal["whatsapp", "email"]
    name: str = Field(pattern=r"^[a-z0-9_]{1,64}$")
    language: str = Field(min_length=2, max_length=16)
    category: Literal["utility", "marketing", "authentication", "internal"] = "utility"
    subject: str | None = Field(default=None, max_length=300)
    body: str = Field(min_length=1, max_length=4000)
    variables: list[str] = Field(default_factory=list, max_length=20)


class TemplateStatusIn(BaseModel):
    provider_status: Literal["draft", "submitted", "approved", "rejected", "paused", "superseded"]


def _out(t: MessageTemplate) -> TemplateOut:
    return TemplateOut.model_validate(t).model_copy(update={"live": t.provider_status == LIVE})


async def _supersede(s: Any, channel: str, name: str, language: str) -> None:
    """The live version steps down (kept as history) before another one becomes live."""
    stmt = select(MessageTemplate).where(
        MessageTemplate.channel == channel,
        MessageTemplate.name == name,
        MessageTemplate.language == language,
        MessageTemplate.provider_status == LIVE,
    )
    for t in (await s.execute(stmt.with_for_update())).scalars().all():
        t.provider_status = "superseded"
    await s.flush()


def build_router(databases: Databases) -> APIRouter:  # noqa: PLR0915 - one route table
    r = APIRouter(prefix="/v1/admin", tags=["admin: channels"])

    @r.get("/channel-accounts", response_model=list[ChannelAccountOut])
    async def accounts(_: Reader) -> list[ChannelAccountOut]:
        """§20.11 company WhatsApp numbers and email addresses, with the countries each number serves."""
        async with databases.transaction(LogicalDb.AUTOMATION) as s:
            rows = (
                await s.execute(
                    select(ChannelAccount).order_by(ChannelAccount.channel, ChannelAccount.region)
                )
            ).scalars()
            return [ChannelAccountOut.model_validate(a) for a in rows]

    @r.patch("/channel-accounts/{account_id}", response_model=ChannelAccountOut)
    async def edit_account(account_id: uuid.UUID, body: ChannelAccountPatch, p: Admin) -> ChannelAccountOut:
        """Regional routing for WhatsApp numbers: which countries a number serves, the default number."""
        async with databases.transaction(LogicalDb.AUTOMATION) as s:
            account = await get_or_404(s, ChannelAccount, account_id, "channel account not found", lock=True)
            changes = body.model_dump(exclude_unset=True)
            for k, v in changes.items():
                setattr(account, k, v)
            await s.flush()
            log.info("admin.channel_account", account=str(account_id), by=p.subject, changes=changes)
            return ChannelAccountOut.model_validate(account)

    @r.get("/message-templates", response_model=list[TemplateOut])
    async def templates(_: Reader, current_only: bool = False) -> list[TemplateOut]:
        """§20.11 message templates and Meta's approval state, every version (the history), newest first;
        `live` marks the version being sent. `current_only=true`: the newest version of each template."""
        async with databases.transaction(LogicalDb.AUTOMATION) as s:
            stmt = select(MessageTemplate).order_by(
                MessageTemplate.name, MessageTemplate.language, MessageTemplate.version.desc()
            )
            rows = list((await s.execute(stmt)).scalars())
        if current_only:
            newest: dict[tuple[str, str, str], MessageTemplate] = {}
            for t in rows:
                newest.setdefault((t.channel, t.name, t.language), t)
            rows = list(newest.values())
        return [_out(t) for t in rows]

    @r.put("/message-templates", response_model=TemplateOut)
    async def upsert_template(body: TemplateIn, p: TemplateEditor) -> TemplateOut:
        """Add or change a template. Editing the live (approved) WhatsApp version creates a NEW version that
        waits for Meta under its own name; the approved one keeps sending until Meta approves the new one.
        A version not yet approved (draft, submitted, rejected) is changed in place. Email templates go live
        at once (no provider approval); the previous email version is kept as history."""
        status = "submitted" if body.channel == "whatsapp" else LIVE
        async with databases.transaction(LogicalDb.AUTOMATION) as s:
            latest = (
                (
                    await s.execute(
                        select(MessageTemplate)
                        .where(
                            MessageTemplate.channel == body.channel,
                            MessageTemplate.name == body.name,
                            MessageTemplate.language == body.language,
                        )
                        .order_by(MessageTemplate.version.desc())
                        .with_for_update()
                    )
                )
                .scalars()
                .first()
            )
            fields = body.model_dump(exclude={"channel", "name", "language"})
            if latest is not None and latest.provider_status in NOT_YET_LIVE:
                row = latest
                for k, v in fields.items():
                    setattr(row, k, v)
                row.provider_status, row.created_by = status, p.subject
            else:
                version = latest.version + 1 if latest is not None else 1
                if status == LIVE:
                    await _supersede(s, body.channel, body.name, body.language)
                whatsapp_rename = body.channel == "whatsapp" and version > 1
                row = MessageTemplate(
                    channel=body.channel,
                    name=body.name,
                    language=body.language,
                    version=version,
                    provider_name=f"{body.name}_v{version}" if whatsapp_rename else None,
                    provider_status=status,
                    created_by=p.subject,
                    **fields,
                )
                s.add(row)
            await s.flush()
            await s.refresh(row)
            log.info(
                "admin.template",
                name=body.name,
                language=body.language,
                version=row.version,
                by=p.subject,
                status=status,
            )
            return _out(row)

    @r.post("/message-templates/{template_id}/status", response_model=TemplateOut)
    async def template_status(
        template_id: uuid.UUID, body: TemplateStatusIn, p: TemplateEditor
    ) -> TemplateOut:
        """Record Meta's decision on a WhatsApp template version. Approving it makes it the one that is sent;
        the version sent before becomes `superseded` (kept as history)."""
        async with databases.transaction(LogicalDb.AUTOMATION) as s:
            row = await get_or_404(s, MessageTemplate, template_id, "template not found", lock=True)
            if body.provider_status == LIVE and row.provider_status != LIVE:
                await _supersede(s, row.channel, row.name, row.language)
            row.provider_status = body.provider_status
            await s.flush()
            log.info(
                "admin.template_status",
                template=str(template_id),
                version=row.version,
                by=p.subject,
                status=body.provider_status,
            )
            return _out(row)

    return r
