"""The send step every automation reaction shares: permanent refusals are final, transient ones retry the
event (moved unchanged from kx/automation/handlers.py, ADR-0001)."""

from __future__ import annotations

import uuid

from kx.automation.outbound import TRANSIENT_REASONS
from kx.core.contracts import MessageSender, OutboundRequest
from kx.core.errors import RuleViolation
from kx.core.logging import get_logger

log = get_logger(__name__)


async def _send_or_skip(sender: MessageSender, request: OutboundRequest) -> uuid.UUID | None:
    """Permanent refusals are final (logged, CRM gets MESSAGE_STATUS); transient ones retry the event."""
    try:
        return await sender.send(request)
    except RuleViolation as exc:
        if exc.extra.get("reason") in TRANSIENT_REASONS:
            raise
        log.warning("handler.send_refused", reason=exc.extra.get("reason"), key=request.idempotency_key)
        return None
