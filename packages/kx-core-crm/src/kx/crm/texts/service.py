"""Editable message texts in the CRM (client decisions 18 Nov; catalogue and rules in `kx.core.texts`).

* The **manager or the MD** edits the English text of a text (MFA). It goes live at once (all processes load
  edits every 30 seconds); the old and new text are in the audit trail, which the MD reads (history), and
  "reset" brings back the built-in wording.
* Buyer-facing texts are then **translated by the AI** into the other languages (event
  `texts.text_changed.v1` -> the app's translation job -> `save_translation`). A translation is only stored if
  it keeps the placeholders and adds no amount, and only for the English version it was made from; until it is
  stored, the previous text of that language is used.
* Staff alerts are English only.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from kx.core import events as ev
from kx.core.errors import NotFound, RuleViolation
from kx.core.event_payloads import MessageTextChanged
from kx.core.texts import CATALOG, LANGUAGES, TRANSLATED, TextDef, check_edit
from kx.crm.models import AuditEvent, MessageText
from kx.crm.services.journal import Actor, Emit, record

Status = Literal["built-in", "edited", "translated", "outdated", "missing"]


@dataclass(frozen=True, slots=True)
class LanguageText:
    language: str
    status: Status
    text: str | None


@dataclass(frozen=True, slots=True)
class TextView:
    key: str
    group: str
    note: str
    placeholders: tuple[str, ...]
    builtin_en: str
    en: str
    en_version: int  # 0 = never edited
    updated_by: uuid.UUID | None
    updated_at: datetime | None
    languages: tuple[LanguageText, ...]


def _view(d: TextDef, rows: Mapping[str, MessageText]) -> TextView:
    en_row = rows.get("en")
    version = en_row.en_version if en_row else 0
    langs: list[LanguageText] = [
        LanguageText("en", "edited" if en_row else "built-in", en_row.body if en_row else d.en)
    ]
    if d.translated:
        for lang in TRANSLATED:
            row = rows.get(lang)
            if row is not None:
                status: Status = "translated" if row.en_version >= version else "outdated"
                langs.append(LanguageText(lang, status, row.body))
            elif lang in d.builtin:
                langs.append(LanguageText(lang, "outdated" if en_row else "built-in", d.builtin[lang]))
            else:
                langs.append(LanguageText(lang, "missing", None))  # the English text is used
    return TextView(
        key=d.key,
        group=d.group,
        note=d.note,
        placeholders=tuple(sorted(d.placeholders)),
        builtin_en=d.en,
        en=en_row.body if en_row else d.en,
        en_version=version,
        updated_by=en_row.updated_by if en_row else None,
        updated_at=en_row.updated_at if en_row else None,
        languages=tuple(langs),
    )


async def _rows(session: AsyncSession, key: str | None = None) -> dict[str, dict[str, MessageText]]:
    stmt = select(MessageText)
    if key is not None:
        stmt = stmt.where(MessageText.key == key)
    out: dict[str, dict[str, MessageText]] = {}
    for row in (await session.execute(stmt)).scalars():
        out.setdefault(row.key, {})[row.language] = row
    return out


def _def(key: str) -> TextDef:
    d = CATALOG.get(key)
    if d is None:
        raise NotFound(f"text {key} not found")
    return d


async def list_texts(session: AsyncSession) -> list[TextView]:
    rows = await _rows(session)
    return [
        _view(d, rows.get(k, {})) for k, d in sorted(CATALOG.items(), key=lambda kv: (kv[1].group, kv[0]))
    ]


async def get_text(session: AsyncSession, key: str) -> TextView:
    d = _def(key)
    return _view(d, (await _rows(session, key)).get(key, {}))


async def update_english(
    session: AsyncSession, key: str, text: str, *, actor: Actor, now: datetime
) -> TextView:
    d = _def(key)
    body = text.strip()
    problems = check_edit(key, body)
    if problems:
        raise RuleViolation("; ".join(problems), reason="invalid_text")
    row = await session.get(MessageText, (key, "en"), with_for_update=True)
    before = row.body if row else d.en
    if body == before:
        return await get_text(session, key)
    version = (row.en_version if row else 0) + 1
    if row is None:
        row = MessageText(key=key, language="en", body=body, source="staff", en_version=version)
        session.add(row)
    else:
        row.body, row.en_version = body, version
    row.updated_by, row.updated_at = actor.user_id, now
    await session.flush()
    emit = (
        (
            Emit(
                ev.MESSAGE_TEXT_CHANGED,
                "message_text",
                key,
                MessageTextChanged(key=key, english=body, en_version=version),
            ),
        )
        if d.translated
        else ()
    )
    await record(
        session,
        actor,
        "message_text.updated",
        "message_text",
        key,
        before={"en": before},
        after={"en": body, "en_version": version},
        emit=emit,
    )
    return await get_text(session, key)


async def reset(session: AsyncSession, key: str, *, actor: Actor) -> TextView:
    """Back to the built-in wording in every language (the edits stay in the history)."""
    d = _def(key)
    rows = (await _rows(session, key)).get(key, {})
    if rows:
        await session.execute(delete(MessageText).where(MessageText.key == key))
        await record(
            session,
            actor,
            "message_text.reset",
            "message_text",
            key,
            before={lang: r.body for lang, r in rows.items()},
            after={"en": d.en},
        )
    return await get_text(session, key)


async def save_translation(
    session: AsyncSession, key: str, language: str, text: str, *, en_version: int, now: datetime
) -> bool:
    """Store an AI translation, only for the current English version and only if it passes the checks."""
    d = _def(key)
    if language not in TRANSLATED or not d.translated or check_edit(key, text):
        return False
    en_row = await session.get(MessageText, (key, "en"))
    current = en_row.en_version if en_row else 0
    if en_version != current:  # a newer edit is being translated already
        return False
    row = await session.get(MessageText, (key, language), with_for_update=True)
    before = row.body if row else d.builtin.get(language)
    if row is None:
        row = MessageText(key=key, language=language, body=text.strip(), source="ai", en_version=current)
        session.add(row)
    else:
        row.body, row.source, row.en_version = text.strip(), "ai", current
    row.updated_by, row.updated_at = None, now
    await session.flush()
    await record(
        session,
        Actor.system("ai_translation"),
        "message_text.translated",
        "message_text",
        key,
        before={language: before},
        after={language: row.body, "en_version": current},
    )
    return True


def needs_translation(view: TextView) -> list[str]:
    """Languages without a translation of the current English text."""
    return [
        lt.language for lt in view.languages if lt.language != "en" and lt.status in ("missing", "outdated")
    ]


async def history(session: AsyncSession, key: str, limit: int = 100) -> list[AuditEvent]:
    _def(key)
    stmt = (
        select(AuditEvent)
        .where(AuditEvent.entity_type == "message_text", AuditEvent.entity_id == key)
        .order_by(AuditEvent.at.desc(), AuditEvent.id.desc())
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars())


async def load_edits(session: AsyncSession) -> dict[tuple[str, str], str]:
    """Every stored text, for `kx.core.texts.TEXTS.replace` in each process."""
    rows = (await session.execute(select(MessageText.key, MessageText.language, MessageText.body))).all()
    return {(k, lang): body for k, lang, body in rows if lang in LANGUAGES}


def translation_requests(views: list[TextView]) -> list[tuple[str, MessageTextChanged]]:
    """(key, payload) for every buyer / AI text with a missing or outdated translation."""
    return [
        (v.key, MessageTextChanged(key=v.key, english=v.en, en_version=v.en_version))
        for v in views
        if CATALOG[v.key].translated and needs_translation(v)
    ]


async def request_missing_translations(session: AsyncSession, *, actor: Actor) -> int:
    """Queue AI translations for every text that lacks one (the built-in texts included)."""
    requests = translation_requests(await list_texts(session))
    for key, payload in requests:
        await record(
            session,
            actor,
            "message_text.translation_requested",
            "message_text",
            key,
            after={"en_version": payload.en_version},
            emit=(Emit(ev.MESSAGE_TEXT_CHANGED, "message_text", key, payload),),
        )
    return len(requests)


def as_dict(view: TextView) -> dict[str, Any]:
    return {
        "key": view.key,
        "group": view.group,
        "note": view.note,
        "placeholders": list(view.placeholders),
        "builtin_en": view.builtin_en,
        "en": view.en,
        "en_version": view.en_version,
        "updated_by": view.updated_by,
        "updated_at": view.updated_at,
        "languages": [
            {"language": lt.language, "status": lt.status, "text": lt.text} for lt in view.languages
        ],
    }
