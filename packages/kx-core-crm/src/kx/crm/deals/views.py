"""What the deal screens read, below the routes: the deal detail card and who may see which deal (§3, §9.3).

Moved unchanged out of `router.py` (19 Nov) because other slices use them (records, reports, change
requests, assignment) and slice code may not depend on a router (ADR-0001 rule 4).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Select, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from kx.core.auth import Principal, Role
from kx.crm._shared.schemas import ContactOut, DealDetail, DealSummary, RequirementOut
from kx.crm.deals import service as deal_svc
from kx.crm.models import Deal, User


async def deal_detail(s: AsyncSession, deal: Deal) -> DealDetail:
    contact = await deal_svc.contact_of(s, deal)
    reqs = await deal_svc.requirements_of(s, deal.id)
    return DealDetail.model_validate(
        {
            **DealSummary.model_validate(deal).model_dump(),
            "contact": ContactOut.model_validate(contact),
            "requirements": [RequirementOut.model_validate(x) for x in reqs],
            "missing_fields": list(await deal_svc.missing_for(s, deal)),
            "lost_reason": deal.lost_reason,
            "lost_note": deal.lost_note,
            "ai_summary": deal.ai_summary,
            "ai_summary_at": deal.ai_summary_at,
            "personal_contact_shared_at": deal.personal_contact_shared_at,
        }
    )


def visible_to[S: Select[Any]](stmt: S, p: Principal) -> S:
    """§3 "the salesperson sees their own; the manager and MD see all": a salesperson's lists show their
    own deals plus those of an away colleague they cover as backup (§9.3)."""
    if p.role is not Role.SALES:
        return stmt
    covered = select(User.id).where(User.is_away.is_(True), User.backup_user_id == p.user_id)
    return stmt.where(or_(Deal.owner_user_id == p.user_id, Deal.owner_user_id.in_(covered)))
