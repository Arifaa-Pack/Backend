"""18 Nov: editable message texts (the AI's fixed phrases, buyer messages, staff alerts). Thin: rules in
`kx.crm.services.texts` and `kx.core.texts`. Screen flow: list -> edit the English text -> (AI translates)."""

# No `from __future__ import annotations` here: FastAPI must evaluate the closure-local `Session`
# alias; PEP 649 (Python 3.14) lazy annotations resolve it, string annotations would not.

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from kx.core.auth import Principal, Role, require_human_mfa, require_role
from kx.crm.api.context import ApiContext
from kx.crm.services import texts
from kx.crm.services.journal import Actor

# client decision 18 Nov: the manager edits, the MD may too and sees every change; the admin may look
Viewer = Annotated[Principal, Depends(require_role(Role.MANAGER, Role.MD, Role.ADMIN))]
Editor = Annotated[Principal, Depends(require_human_mfa(Role.MANAGER, Role.MD))]


class LanguageOut(BaseModel):
    language: str
    # built-in | edited | translated | outdated (the AI has not translated the new English yet) | missing
    status: str
    text: str | None  # None = English is shown in this language


class TextOut(BaseModel):
    key: str
    group: str  # ai | buyer | staff
    note: str
    placeholders: list[str]  # must stay in an edit, e.g. ["hours", "name"]
    builtin_en: str
    en: str
    en_version: int
    updated_by: uuid.UUID | None
    updated_at: datetime | None
    languages: list[LanguageOut]


class TextIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class ChangeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    at: datetime
    actor_type: str
    actor_id: str
    action: str  # message_text.updated | .reset | .translated | .translation_requested
    before: dict[str, Any] | None
    after: dict[str, Any] | None


class QueuedOut(BaseModel):
    queued: int


def router(ctx: ApiContext) -> APIRouter:
    r = APIRouter(prefix="/texts", tags=["message texts"])
    Session = Annotated[AsyncSession, Depends(ctx.session)]  # noqa: N806
    deps = ctx.deps

    @r.get("", response_model=list[TextOut])
    async def list_texts(s: Session, _: Viewer) -> list[TextOut]:
        """Every editable text: the English in use, the built-in English, its placeholders and the state of
        each language. Legal and opt-out wording is not here (not editable)."""
        return [TextOut.model_validate(texts.as_dict(v)) for v in await texts.list_texts(s)]

    @r.put("/{key}", response_model=TextOut)
    async def edit(key: str, body: TextIn, s: Session, p: Editor) -> TextOut:
        """Manager / MD (MFA): new English text, live within 30 seconds; buyer and AI texts are then
        translated by the AI. 422 `invalid_text` when a placeholder is missing or added, or an amount with a
        currency is in it."""
        view = await texts.update_english(s, key, body.text, actor=Actor.user(p), now=deps.now())
        return TextOut.model_validate(texts.as_dict(view))

    @r.post("/{key}/reset", response_model=TextOut)
    async def reset(key: str, s: Session, p: Editor) -> TextOut:
        """Manager / MD (MFA): back to the built-in wording in every language (the history keeps the
        edits)."""
        return TextOut.model_validate(texts.as_dict(await texts.reset(s, key, actor=Actor.user(p))))

    @r.get("/{key}/history", response_model=list[ChangeOut])
    async def history(
        key: str, s: Session, _: Viewer, limit: Annotated[int, Query(ge=1, le=500)] = 100
    ) -> list[ChangeOut]:
        """Every change of one text, newest first: who, when, old and new text (the MD's view)."""
        return [ChangeOut.model_validate(e) for e in await texts.history(s, key, limit)]

    @r.post("/translate-missing", response_model=QueuedOut)
    async def translate_missing(s: Session, p: Editor) -> QueuedOut:
        """Manager / MD (MFA): queue AI translations for every text without one (built-in texts included)."""
        return QueuedOut(queued=await texts.request_missing_translations(s, actor=Actor.user(p)))

    return r
