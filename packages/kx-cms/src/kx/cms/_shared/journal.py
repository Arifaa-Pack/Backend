"""Every CMS change writes its audit row and its events in the same transaction (the platform rule that the
CRM's `journal.record()` follows: state, audit and outbox commit together, or not at all)."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from kx.cms.models import SCHEMA, AuditEntry
from kx.core.event_payloads import EventPayload, make_event
from kx.core.events import Outbox
from kx.core.identity import new_id


async def record(
    session: AsyncSession,
    *,
    actor: str,
    action: str,
    collection: str | None = None,
    key: str | None = None,
    version: int | None = None,
    detail: dict[str, Any] | None = None,
    events: Iterable[tuple[str, EventPayload]] = (),
) -> None:
    session.add(
        AuditEntry(
            id=new_id(),
            actor=actor,
            action=action,
            collection=collection,
            key=key,
            version=version,
            detail=detail or {},
        )
    )
    for event_type, payload in events:
        await Outbox.add(
            session,
            SCHEMA,
            make_event(event_type, "cms_document", f"{collection}:{key}", payload),
        )
