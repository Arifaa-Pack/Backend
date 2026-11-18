"""18 Nov editable company details: field checks, company.yaml fallback, and what the quote prints."""

from __future__ import annotations

import base64
from dataclasses import replace

import pytest
from test_quote_document import DOC

from kx.core.errors import RuleViolation
from kx.crm.pdf import Letterhead, QuoteRenderer
from kx.crm.services.company import BankDetails, CompanyProfile, effective, iban_ok, logo_data_uri

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
YAML = Letterhead(
    name="ARIFA PACKING & PACKAGING LLC", address="Ajman", trn="REPLACE", phone="+971 6 748 5384"
)


def test_iban_checksum() -> None:
    assert iban_ok("AE07 0331 2345 6789 0123 456")  # the standard UAE example
    assert not iban_ok("AE08 0331 2345 6789 0123 456")  # one digit off
    assert not iban_ok("not an iban")


@pytest.mark.parametrize(
    ("data", "field"),
    [
        ({"trn": "12345"}, "trn"),
        ({"email": "sales-at-arifa"}, "email"),
        ({"phone": "call us"}, "phone"),
        ({"logo": "data:image/gif;base64,R0lGOD"}, "logo"),
        ({"logo": "data:image/png;base64," + base64.b64encode(b"\xff\xd8\xff not a png").decode()}, "logo"),
        ({"bank": {"iban": "AE08 0331 2345 6789 0123 456"}}, "iban"),
        ({"bank": {"swift": "NB"}}, "swift"),
    ],
)
def test_wrong_details_are_refused(data: dict[str, object], field: str) -> None:
    with pytest.raises(ValueError, match=field if field not in ("iban", "swift") else ""):
        CompanyProfile.model_validate(data)


def test_empty_fields_fall_back_to_company_yaml() -> None:
    profile = CompanyProfile(trn="100 000 000 000 003", bank=BankDetails(iban="ae070331234567890123456"))
    lh = effective(YAML, profile)
    assert (lh.name, lh.phone, lh.trn) == (YAML.name, YAML.phone, "100000000000003")
    assert lh.iban == "AE07 0331 2345 6789 0123 456"  # normalised in groups of four


def test_the_quote_prints_logo_and_bank_details_only_when_set() -> None:
    renderer = QuoteRenderer()
    plain = renderer.html(DOC)
    assert "Bank details" not in plain and "<img" not in plain
    logo = logo_data_uri(PNG, "image/png")
    lh = effective(
        DOC.letterhead,
        CompanyProfile.model_validate(
            {
                "logo": logo,
                "bank": {
                    "account_name": "Arifa Packing",
                    "bank_name": "Emirates NBD",
                    "iban": "AE070331234567890123456",
                    "swift": "EBILAEAD",
                },
            }
        ),
    )
    html = renderer.html(replace(DOC, letterhead=lh))
    assert 'class="logo"' in html and "Bank details" in html and "AE07 0331 2345 6789 0123 456" in html
    assert "EBILAEAD" in html and "Emirates NBD" in html
    with pytest.raises(RuleViolation):
        logo_data_uri(b"GIF89a", "image/gif")
