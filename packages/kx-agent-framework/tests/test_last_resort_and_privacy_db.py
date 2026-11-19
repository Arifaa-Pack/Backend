"""D9 and D8 (architecture guide, 17 Nov).

D9: a buyer turn that fails for a non-model reason on its last attempt still gets the safe reply, and the deal goes
to the salesperson (the same idempotency key as the normal reply, so a reply that did go out is not sent twice).
D8: the buyer's words in injection flags and knowledge gaps are masked when stored and removed on anonymisation.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import select

from kx.agents.handlers import AgentHandlers, ReplyAddress
from kx.agents.knowledge import KnowledgeBase
from kx.agents.memory import TurnStore
from kx.agents.models import ModelRouter
from kx.agents.orm import AgentRun, Conversation, GuardEvent, KnowledgeGap
from kx.agents.service import AgentService
from kx.agents.testing import FakeCrmGateway, HashEmbedder, ScriptedChatModel, make_deal
from kx.core import events as ev
from kx.core.contracts import DealStage, OutboundRequest
from kx.core.event_payloads import MessageReceived, make_event
from kx.core.settings import LogicalDb

pytestmark = pytest.mark.db
AGENTS = LogicalDb.AGENTS


class Sender:
    def __init__(self) -> None:
        self.sent: dict[str, OutboundRequest] = {}

    async def send(self, request: OutboundRequest) -> uuid.UUID | None:
        self.sent.setdefault(request.idempotency_key, request)  # the outbound gate de-duplicates by key
        return uuid.uuid4()


class Addresses:
    async def for_thread(self, thread_ref: uuid.UUID) -> ReplyAddress | None:
        return ReplyAddress("whatsapp", "+971501234567")

    async def for_deal(self, deal_ref: uuid.UUID) -> ReplyAddress | None:
        return ReplyAddress("whatsapp", "+971501234567")


def _event(deal_id: uuid.UUID) -> Any:
    return make_event(
        ev.MESSAGE_RECEIVED,
        "inbound_message",
        uuid.uuid4(),
        MessageReceived(
            message_ref=uuid.uuid4(),
            thread_ref=uuid.uuid4(),
            channel="whatsapp",
            from_address_hash="h" * 64,
            contact_ref=None,
            deal_ref=deal_id,
            text="Do you have 750 ml containers?",
            received_at=datetime.now(UTC),
            provider_message_id="wamid.D9",
        ),
    )


async def test_last_attempt_failure_sends_the_safe_reply_once_and_hands_over(databases: Any) -> None:
    deal = make_deal(DealStage.QUALIFIED)
    gw, sender = FakeCrmGateway(deal), Sender()
    model = ModelRouter.static(ScriptedChatModel(script=["x"]))
    svc = AgentService(databases=databases, gateway=gw, models=model)
    handlers = AgentHandlers(databases=databases, service=svc, sender=sender, addresses=Addresses())
    event = _event(deal.deal_id)
    await handlers.last_resort(event)
    await handlers.last_resort(event)  # a retried last resort: still one message (same idempotency key)
    (reply,) = sender.sent.values()
    assert reply.body == "Let me check with our sales team."
    assert reply.idempotency_key == f"agent-reply:{event.event_id}"  # = the normal reply's key
    assert ("pause_ai", "exception") in gw.calls


async def test_an_unverified_email_gets_no_ai_answer(databases: Any) -> None:
    """M8: an email whose sender failed SPF/DKIM/DMARC is not answered by the AI (not even the last-resort
    safe reply) and the deal is not touched; staff review it."""
    deal = make_deal(DealStage.QUALIFIED)
    gw, sender = FakeCrmGateway(deal), Sender()
    model = ScriptedChatModel(script=[])  # any model call would fail the test
    svc = AgentService(databases=databases, gateway=gw, models=ModelRouter.static(model))
    handlers = AgentHandlers(databases=databases, service=svc, sender=sender, addresses=Addresses())
    event = make_event(
        ev.MESSAGE_RECEIVED,
        "inbound_message",
        uuid.uuid4(),
        MessageReceived(
            message_ref=uuid.uuid4(),
            thread_ref=uuid.uuid4(),
            channel="email",
            from_address_hash="h" * 64,
            contact_ref=None,
            deal_ref=None,
            text="STOP. Also change my order to 900,000 pieces.",
            received_at=datetime.now(UTC),
            provider_message_id="<forged@buyer.ae>",
            sender_verified=False,
        ),
    )
    assert await handlers.dispatch(event)
    await handlers.last_resort(event)
    assert sender.sent == {} and model.calls == [] and gw.calls == []


async def test_injection_flags_and_gaps_are_masked_and_removed_on_anonymisation(databases: Any) -> None:
    contact = uuid.uuid4()
    async with databases.transaction(AGENTS) as s:
        conv = Conversation(channel="whatsapp", langgraph_thread_id=f"t-{uuid.uuid4()}", contact_ref=contact)
        s.add(conv)
        await s.flush()
        run = AgentRun(
            conversation_id=conv.id,
            graph="conversation",
            role="concierge",
            trigger_event="t",
            trigger_event_id=uuid.uuid4(),
            model="test",
            prompt_version="v1",
            status="ok",
        )
        s.add(run)
        await s.flush()
        s.add(GuardEvent(run_id=run.id, guard="injection", action="flagged", detail={"text": "ignore rules"}))
    kb = KnowledgeBase(databases, HashEmbedder())
    await kb.record_gap("Call me on +971 50 123 4567 or ahmed@example.com about lids", "en", conv.id)
    async with databases.transaction(AGENTS) as s:
        question = await s.execute(
            select(KnowledgeGap.question).where(KnowledgeGap.conversation_id == conv.id)
        )
        stored = question.scalar_one()
    assert "123 4567" not in stored and "ahmed@example.com" not in stored and "about lids" in stored

    async with databases.transaction(AGENTS) as s:
        await TurnStore.anonymise(s, contact)
    async with databases.transaction(AGENTS) as s:
        gaps = (await s.execute(select(KnowledgeGap).where(KnowledgeGap.conversation_id == conv.id))).all()
        detail = (await s.execute(select(GuardEvent.detail).where(GuardEvent.run_id == run.id))).scalar_one()
    assert gaps == [] and detail == {}
