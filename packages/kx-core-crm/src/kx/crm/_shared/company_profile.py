"""The editable company details (letterhead, logo, bank details printed on quotes) and their checks.

A plain model: `_shared/settings.py` types the `company.profile` setting with it, and the company slice
(`company/service.py`) reads and changes it. It lives in `_shared/` so the settings layer needs no slice.
"""

from __future__ import annotations

import base64
import binascii
import re
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, field_validator

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
