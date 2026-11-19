"""§16 follow-ups: the day 2 / 5 / 10 (or re-order) reminder (moved unchanged from
kx/automation/handlers.py, ADR-0001)."""

from __future__ import annotations

from sqlalchemy import select

from kx.automation._shared.sending import _send_or_skip
from kx.automation._shared.settings import AutomationSettings
from kx.automation.models import ChannelAccount, Thread
from kx.core.contracts import MessageSender, OutboundKind, OutboundRequest
from kx.core.db import Databases
from kx.core.event_payloads import DealNextActionSet
from kx.core.logging import get_logger
from kx.core.settings import LogicalDb

log = get_logger(__name__)


class FollowUps:
    """Sends the day 2/5/10 (or reorder) reminder when its job fires. The address comes from the
    buyer's own thread (the contact record's channel), never from an agent."""

    def __init__(self, databases: Databases, settings: AutomationSettings, sender: MessageSender) -> None:
        self._db = databases
        self._settings = settings
        self._sender = sender

    async def send(self, action: DealNextActionSet) -> None:
        address = await self._address(action)
        if address is None:
            log.info("followup.no_thread", deal_ref=str(action.deal_ref), channel=action.preferred_channel)
            return
        kind = OutboundKind.REORDER if action.reason == "reorder" else OutboundKind.FOLLOWUP
        await _send_or_skip(
            self._sender,
            OutboundRequest(
                channel=action.preferred_channel,
                to_address=address,
                kind=kind,
                template_name=self._settings.followup.templates[action.reason],
                deal_ref=action.deal_ref,
                contact_ref=action.contact_ref,
                buyer_timezone=action.buyer_timezone,
                requested_by="system:followup",
                idempotency_key=f"followup:{action.deal_ref}:{action.reason}:{action.due_at:%Y%m%d}",
            ),
        )

    async def _address(self, action: DealNextActionSet) -> str | None:
        async with self._db.transaction(LogicalDb.AUTOMATION) as s:
            return (
                await s.execute(
                    select(Thread.external_key)
                    .join(ChannelAccount, ChannelAccount.id == Thread.channel_account_id)
                    .where(
                        Thread.contact_ref == action.contact_ref,
                        ChannelAccount.channel == action.preferred_channel,
                        ChannelAccount.purpose == "customer",
                        Thread.external_key.not_like("anon:%"),
                    )
                    .order_by(Thread.last_inbound_at.desc().nulls_last())
                    .limit(1)
                )
            ).scalar_one_or_none()
