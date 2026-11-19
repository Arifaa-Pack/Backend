"""18 Nov editable company details against the database: saved in settings, printed on new documents, and a change
of bank details alerts the MD and the manager."""

from __future__ import annotations

from typing import Any

import pytest
from conftest import MGR_ID, Seed
from sqlalchemy import select

from kx.core import events as ev
from kx.core.errors import RuleViolation
from kx.core.settings import LogicalDb
from kx.crm import models as m
from kx.crm._shared.journal import Actor
from kx.crm.company import service as company

pytestmark = pytest.mark.db
ACTOR = Actor("user", str(MGR_ID), MGR_ID)


async def _reset(databases: Any, deps: Any) -> None:
    async with databases.transaction(LogicalDb.CRM) as s:
        row = await s.get(m.Setting, company.KEY)
        assert row is not None, "migration crm_0015 seeds company.profile"
        row.value = {}
    deps.settings.invalidate()


async def test_details_are_printed_and_bank_changes_alert_the_md_and_manager(
    databases: Any, deps: Any, seed: Seed
) -> None:
    await _reset(databases, deps)
    async with databases.transaction(LogicalDb.CRM) as s:
        before = await company.letterhead(s, deps)
        await company.update(s, deps, {"trn": "100000000000003", "phone": "+971 6 748 5384"}, actor=ACTOR)
    deps.settings.invalidate()
    async with databases.transaction(LogicalDb.CRM) as s:
        now = await company.letterhead(s, deps)
        notices = list(
            (
                await s.execute(
                    select(m.OutboxEvent.payload).where(
                        m.OutboxEvent.event_type == ev.STAFF_NOTICE, m.OutboxEvent.aggregate_id == company.KEY
                    )
                )
            ).scalars()
        )
    assert (now.trn, now.phone, now.name) == ("100000000000003", "+971 6 748 5384", before.name)
    assert notices == []  # no bank change, no alert

    async with databases.transaction(LogicalDb.CRM) as s:
        await company.update(
            s, deps, {"bank": {"iban": "AE070331234567890123456", "bank_name": "ENBD"}}, actor=ACTOR
        )
    deps.settings.invalidate()
    async with databases.transaction(LogicalDb.CRM) as s:
        lh = await company.letterhead(s, deps)
        notices = list(
            (
                await s.execute(
                    select(m.OutboxEvent.payload).where(
                        m.OutboxEvent.event_type == ev.STAFF_NOTICE, m.OutboxEvent.aggregate_id == company.KEY
                    )
                )
            ).scalars()
        )
        history = list(
            (
                await s.execute(select(m.SettingHistory.key).where(m.SettingHistory.key == company.KEY))
            ).scalars()
        )
        audit = list(
            (
                await s.execute(
                    select(m.AuditEvent.after).where(m.AuditEvent.action == "company.bank_details_changed")
                )
            ).scalars()
        )
    assert lh.iban == "AE07 0331 2345 6789 0123 456" and lh.trn == "100000000000003"  # earlier fields kept
    assert len(notices) == 1 and "3456" in notices[0]["text"]
    assert {n["user_ref"] for n in notices[0]["notify"]} >= {str(seed.md)}
    assert len(history) >= 2 and audit[-1]["iban"] == "…3456"  # the audit masks the IBAN

    async with databases.transaction(LogicalDb.CRM) as s:
        with pytest.raises(RuleViolation):
            await company.update(s, deps, {"bank": {"iban": "AE08 0331 2345 6789 0123 456"}}, actor=ACTOR)
    await _reset(databases, deps)
