"""The §16 staff-reminder ticket: what the approval ladder follows for one quote approval or change request.

It lives in `_shared/` because the TaskScheduler port (`_shared/ports.py`) carries it to the job table, and
shared code imports no slice; the ladder itself is `alerts/service.py`.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from kx.automation.models import CallPurpose
from kx.core.event_payloads import ChangeRequested, QuoteSubmitted, StaffContact
from kx.core.texts import TEXTS


class LadderTicket(BaseModel):
    """One thing waiting for a person (§16 "Staff reminders"): a quote approval or a change request.

    Built from QuoteSubmitted / ChangeRequested; the CRM computed every time in alert hours from
    `approval.reminder_minutes`, so automation only follows the schedule."""

    model_config = ConfigDict(frozen=True)

    ref: uuid.UUID  # approval_ref or change_request_ref (the CRM status reader knows both)
    kind: Literal["approval", "change_request"]
    deal_ref: uuid.UUID
    approver: StaffContact
    escalate_to: StaffContact | None
    remind_at: tuple[datetime, ...]  # [WhatsApp reminder (30 min), alert call (60 min)]
    escalate_at: datetime  # 120 min: the manager (WhatsApp + call)
    title: str  # what the message and the call say, e.g. "Quote Q-2026-00012 for AED 13,125.00"

    @classmethod
    def for_approval(cls, p: QuoteSubmitted) -> LadderTicket:
        key = "staff.special_price_title" if p.kind == "special_price" else "staff.approval_title"
        return cls(
            ref=p.approval_ref,
            kind="approval",
            deal_ref=p.deal_ref,
            approver=p.approver,
            escalate_to=p.escalate_to,
            remind_at=p.remind_at,
            escalate_at=p.due_at,
            title=TEXTS.get(key, "en", quote=p.quote_number, total=p.total_display),
        )

    @classmethod
    def for_change(cls, p: ChangeRequested) -> LadderTicket:
        return cls(
            ref=p.alert_ref or p.change_request_ref,
            kind="change_request",
            deal_ref=p.deal_ref,
            approver=p.assigned_to,
            escalate_to=p.escalate_to,
            remind_at=p.remind_at,
            escalate_at=p.escalate_at or p.sla_due_at,
            title=TEXTS.get("staff.change_request_title", "en", type=p.type, deal=p.deal_number).replace(
                "  ", " "
            ),
        )

    @property
    def purpose(self) -> CallPurpose:
        return "approval_alert" if self.kind == "approval" else "change_request_alert"
