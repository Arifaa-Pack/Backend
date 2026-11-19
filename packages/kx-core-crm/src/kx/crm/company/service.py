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
import binascii
import re
from dataclasses import replace
from typing import TYPE_CHECKING, Any, Final

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from kx.core import events as ev
from kx.core.errors import RuleViolation
from kx.core.event_payloads import StaffNotice
from kx.crm._shared import staff
from kx.crm._shared.journal import Actor, Emit, record
from kx.crm.quotes.pdf import Letterhead

if TYPE_CHECKING:
    from kx.crm.deps import CrmDeps

KEY: Final = "company.profile"
MAX_LOGO_BYTES: Final = 200 * 1024
_TRN = re.compile(r"^\d{15}$")
_SWIFT = re.compile(r"^[A-Z]{6}[A-Z0-9]{2}([A-Z0-9]{3})?$")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
_PHONE = re.compile(r"^[+\d][\d\s()-]{5,24}$")
_LOGO = re.compile(r"^data:image/(png|jpeg);base64,([A-Za-z0-9+/=]+)$")
_MAGIC: Final = {"png": b"\x89PNG\r\n\x1a\n", "jpeg": b"\xff\xd8\xff"}


def iban_ok(iban: str) -> bool:
    s = iban.replace(" ", "").upper()
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{10,30}", s):
        return False
    digits = "".join(str(int(ch, 36)) for ch in s[4:] + s[:4])
    return int(digits) % 97 == 1


class BankDetails(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    account_name: str = Field("", max_length=200)
    bank_name: str = Field("", max_length=200)
    iban: str = Field("", max_length=42)
    swift: str = Field("", max_length=11)

    @field_validator("iban")
    @classmethod
    def _iban(cls, v: str) -> str:
        v = " ".join(v.replace(" ", "").upper()[i : i + 4] for i in range(0, len(v.replace(" ", "")), 4))
        if v and not iban_ok(v):
            raise ValueError("not a valid IBAN (check the digits)")
        return v

    @field_validator("swift")
    @classmethod
    def _swift(cls, v: str) -> str:
        v = v.strip().upper()
        if v and not _SWIFT.match(v):
            raise ValueError("a SWIFT / BIC code has 8 or 11 letters and digits")
        return v


class CompanyProfile(BaseModel):
    """What staff can change. Empty = use company.yaml."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field("", max_length=200)
    address: str = Field("", max_length=400)
    trn: str = Field("", max_length=20)  # spaces allowed when typed; 15 digits after removing them
    phone: str = Field("", max_length=30)
    email: str = Field("", max_length=200)
    logo: str = ""  # data:image/png|jpeg;base64,... (max 200 KB)
    bank: BankDetails = BankDetails()

    @field_validator("trn")
    @classmethod
    def _trn(cls, v: str) -> str:
        v = v.replace(" ", "")
        if v and not _TRN.match(v):
            raise ValueError("the UAE VAT TRN has 15 digits")
        return v

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        if v and not _EMAIL.match(v.strip()):
            raise ValueError("not an email address")
        return v.strip()

    @field_validator("phone")
    @classmethod
    def _phone(cls, v: str) -> str:
        if v and not _PHONE.match(v.strip()):
            raise ValueError("digits, spaces, + ( ) - only")
        return v.strip()

    @field_validator("logo")
    @classmethod
    def _logo(cls, v: str) -> str:
        if not v:
            return v
        m = _LOGO.match(v)
        if m is None:
            raise ValueError("upload a PNG or JPEG image")
        try:
            data = base64.b64decode(m.group(2), validate=True)
        except binascii.Error as exc:
            raise ValueError("the image data is damaged") from exc
        if len(data) > MAX_LOGO_BYTES:
            raise ValueError("the logo is larger than 200 KB")
        if not data.startswith(_MAGIC[m.group(1)]):
            raise ValueError("the file is not the image type it claims to be")
        return v


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
