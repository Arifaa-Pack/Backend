"""Editable company details (client decision 18 Nov): admin, MD and manager change the letterhead, logo and
bank details printed on quotes, without a developer.

Stored in the settings row `company.profile` (crm_0015), so every change has the settings history, an audit
row and the `settings.changed` event; every process sees it within 30 seconds. An empty field falls back to
the app's `company.yaml`. Quotes already sent keep the PDF they were sent with.

Bank details are the classic target of payment fraud (a changed IBAN on a real quote): a change to any bank
field also alerts the MD and the manager at once.
"""

from __future__ import annotations

import base64
from dataclasses import replace
from typing import TYPE_CHECKING, Any, Final

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from kx.core import events as ev
from kx.core.errors import RuleViolation
from kx.core.event_payloads import StaffNotice
from kx.crm._shared import staff
from kx.crm._shared.company_profile import BankDetails, CompanyProfile
from kx.crm._shared.journal import Actor, Emit, record
from kx.crm.quotes.pdf import Letterhead

if TYPE_CHECKING:
    from kx.crm.deps import CrmDeps

KEY: Final = "company.profile"


def effective(base: Letterhead, profile: CompanyProfile) -> Letterhead:
    """company.yaml overlaid with every non-empty profile field."""
    b = profile.bank
    pairs = {
        "name": profile.name,
        "address": profile.address,
        "trn": profile.trn,
        "phone": profile.phone,
        "email": profile.email,
        "logo": profile.logo,
        "bank_account_name": b.account_name,
        "bank_name": b.bank_name,
        "iban": b.iban,
        "swift": b.swift,
    }
    return replace(base, **{k: v for k, v in pairs.items() if v})


async def letterhead(session: AsyncSession, deps: CrmDeps) -> Letterhead:
    """The letterhead to print now (cached settings snapshot, 30 s)."""
    return effective(deps.letterhead, (await deps.settings.get(session)).company_profile)


def logo_data_uri(data: bytes, content_type: str) -> str:
    kind = {"image/png": "png", "image/jpeg": "jpeg", "image/jpg": "jpeg"}.get(
        content_type.split(";", maxsplit=1)[0].strip()
    )
    if kind is None:
        raise RuleViolation("upload a PNG or JPEG image", reason="invalid_logo")
    return f"data:image/{kind};base64,{base64.b64encode(data).decode()}"


async def update(
    session: AsyncSession, deps: CrmDeps, changes: dict[str, Any], *, actor: Actor
) -> CompanyProfile:
    """Merge `changes` (bank fields merge too), validate, save as the settings row; alert on a bank change."""
    current = (await deps.settings.get(session)).company_profile
    merged = current.model_dump()
    bank_changes = changes.pop("bank", None) or {}
    merged.update(changes)
    merged["bank"] = {**merged["bank"], **bank_changes}
    try:
        profile = CompanyProfile.model_validate(merged)
    except ValidationError as exc:
        raise RuleViolation(
            "invalid company details", reason="invalid_company", errors=exc.errors(include_url=False)
        ) from exc
    if profile == current:
        return profile
    await deps.settings.update(session, KEY, profile.model_dump(), actor)
    if profile.bank != current.bank:
        notify = {
            u.id: staff.staff_contact(u)
            for u in (await staff.md_user(session), await staff.manager_user(session))
        }
        who = actor.id if actor.type != "user" else str(actor.user_id)
        await record(
            session,
            actor,
            "company.bank_details_changed",
            "company",
            KEY,
            before=_masked(current.bank),
            after=_masked(profile.bank),
            emit=Emit(
                ev.STAFF_NOTICE,
                "company",
                KEY,
                StaffNotice(
                    deal_ref=None,
                    notify=tuple(notify.values()),
                    text="The bank details on quotes were changed "
                    f"(IBAN ending {_tail(profile.bank.iban) or '-'}). "
                    "If you did not expect this, check the CRM settings history now.",
                    key=f"bank-change:{who}:{_tail(profile.bank.iban)}:{profile.bank.account_name}",
                ),
            ),
        )
    return profile


def _tail(iban: str) -> str:
    """The last four characters of the IBAN itself (it is stored in groups of four with spaces)."""
    return iban.replace(" ", "")[-4:]


def _masked(b: BankDetails) -> dict[str, str]:
    return {**b.model_dump(), "iban": f"…{_tail(b.iban)}" if b.iban else ""}
