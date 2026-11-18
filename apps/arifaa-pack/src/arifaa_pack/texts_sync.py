"""Editable message texts at run time (18 Nov): load the edits into `kx.core.texts.TEXTS` in every
process, and translate an edited English text into the other languages with the AI.

* `refresh_loop`: loads every stored text at start and every 30 seconds (an edit is live within 30 s).
* `translate_text` (job `app.translate_text`, on `texts.text_changed.v1`): the FAST model translates the
  English into Arabic, Hindi, Tamil, Chinese and Sinhala; a translation is stored only if it keeps the
  placeholders and adds no amount (`kx.crm.services.texts.save_translation`). A failing model call fails
  the job, which the job store retries; until then each language keeps its previous text.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Final

from langchain_core.messages import HumanMessage, SystemMessage

from kx.agents.models import ModelRouter
from kx.agents.registry import ModelTier
from kx.core.db import Databases
from kx.core.event_payloads import MessageTextChanged
from kx.core.events import Event
from kx.core.jobs import Job
from kx.core.logging import get_logger
from kx.core.settings import LogicalDb
from kx.core.texts import CATALOG, TEXTS, TRANSLATED, check_edit
from kx.crm.services import texts as crm_texts

log = get_logger(__name__)
REFRESH_SECONDS: Final = 30.0
LANGUAGE_NAMES: Final = {
    "ar": "Arabic (Modern Standard, as used in the UAE)",
    "hi": "Hindi",
    "ta": "Tamil",
    "zh": "Simplified Chinese",
    "si": "Sinhala",
}
_SYSTEM: Final = (
    "You translate short business messages of ARIFA Packing, a packaging manufacturer in Ajman, UAE, for its "
    "B2B buyers. Translate the user's English text into {language}. Rules: keep every placeholder in curly "
    "braces exactly as written, untranslated (for example {{name}} stays {{name}}); keep line breaks; do not "
    "add, remove or change any fact, number, amount, promise or time; use a polite, natural, professional "
    "tone; "
    "keep product terms (MOQ, PDF, WhatsApp) as they are. Answer with the translation only."
)


async def refresh(dbs: Databases) -> int:
    async with dbs.transaction(LogicalDb.CRM) as s:
        edits = await crm_texts.load_edits(s)
    TEXTS.replace(edits)
    return len(edits)


async def refresh_loop(dbs: Databases, every: float = REFRESH_SECONDS) -> None:
    while True:
        await asyncio.sleep(every)
        try:
            await refresh(dbs)
        except Exception:  # the texts in memory stay; the next round retries
            log.warning("texts.refresh_failed", exc_info=True)


async def translate_one(models: ModelRouter, key: str, english: str, language: str) -> str | None:
    model = models.for_tier(ModelTier.FAST).runnable()
    msg = await model.ainvoke(
        [SystemMessage(_SYSTEM.format(language=LANGUAGE_NAMES[language])), HumanMessage(english)]
    )
    text = str(getattr(msg, "text", "") or "").strip()
    problems = check_edit(key, text)
    if problems:
        log.warning("texts.translation_refused", key=key, language=language, problems=problems)
        return None
    return text


async def translate_text(models: ModelRouter, dbs: Databases, job: Job) -> None:
    p = MessageTextChanged.model_validate(Event.model_validate(job.args["event"]).payload)
    d = CATALOG.get(p.key)
    if d is None or not d.translated:
        return
    english = p.english or d.en
    saved: list[str] = []
    for language in TRANSLATED:
        text = await translate_one(models, p.key, english, language)
        if text is None:
            continue
        async with dbs.transaction(LogicalDb.CRM) as s:
            if await crm_texts.save_translation(
                s, p.key, language, text, en_version=p.en_version, now=datetime.now(UTC)
            ):
                saved.append(language)
    await refresh(dbs)
    log.info("texts.translated", key=p.key, en_version=p.en_version, languages=saved)
