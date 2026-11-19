"""CT-01: the website's enquiry desk, as the CRM records it (design doc §6, WP-C3).

The desk keeps its own field names (`enquiryDeskClient.ts`), so the website sends what it already has. The CRM
gets one record: the contact claim (the D-4 rules decide whether it is a known buyer), what they need, the
WhatsApp / email opt-ins with their wording (D59), and one summary with English labels for the salesperson.
Values stay as the visitor typed them; nothing here is a price, and nothing is invented.
"""

from __future__ import annotations

import re
import uuid
from decimal import Decimal, InvalidOperation
from typing import Annotated, Final, Literal

from pydantic import BaseModel, Field

from kx.core.contracts import ContactPatch, RequirementPatch, SiteManifest, WebEnquiry

_ID: Final = r"^[a-z0-9][a-z0-9_-]{0,127}$"
_CHANNEL: Final[dict[str, Literal["whatsapp", "email", "phone"]]] = {
    "whatsapp": "whatsapp",
    "email": "email",
    "call": "phone",
}
_QTY: Final = re.compile(r"^\s*(\d(?:[\d,.]*\d)?)\s*(k|thousand)?\b", re.IGNORECASE)
_UNITS: Final = (("carton", "carton"), ("ctn", "carton"), ("kg", "kg"), ("roll", "roll"), ("pack", "packet"))


class EnquiryIn(BaseModel):
    """The enquiry desk's fields (their own names), the enquiry list and the consents."""

    family: list[Annotated[str, Field(pattern=_ID)]] = Field(default_factory=list, max_length=8)
    products: list[Annotated[str, Field(pattern=_ID)]] = Field(default_factory=list, max_length=50)
    detail: str | None = Field(default=None, max_length=500)  # products by code, in their words
    size: str | None = Field(default=None, max_length=100)
    quantity: str | None = Field(default=None, max_length=100)  # "20,000 a month"
    often: str | None = Field(default=None, max_length=100)  # how often they buy
    region: str | None = Field(default=None, max_length=100)
    city: str | None = Field(default=None, max_length=100)
    name: str | None = Field(default=None, max_length=200)
    company: str | None = Field(default=None, max_length=200)
    trade: str | None = Field(default=None, max_length=100)  # restaurant, caterer, retail, ...
    channel: Literal["email", "call", "whatsapp"] | None = None  # how they want the answer
    email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=32)
    extra: str | None = Field(default=None, max_length=500)
    message: str | None = Field(default=None, max_length=2000)
    locale: Literal["en", "ar"] = "en"
    whatsapp_opt_in: bool = False
    email_opt_in: bool = False
    consent_wording: str | None = Field(default=None, max_length=2000)  # the words next to the ticks
    session_id: uuid.UUID | None = None  # the chat visit, when there is one (then send its token)
    turnstile_token: str = Field(default="", max_length=4096)  # required without a session


def quantity_of(text: str | None) -> tuple[Decimal | None, str | None]:
    """The number a buyer typed ("20,000 a month", "50k pcs") and its unit, or (None, None) when unsure.
    The words themselves always reach the salesperson in the summary."""
    if not text or not (m := _QTY.match(text)):
        return None, None
    try:
        number = Decimal(re.sub(r"[,\s]", "", m.group(1)).rstrip("."))
    except InvalidOperation:
        return None, None
    if m.group(2):
        number *= 1000
    if number <= 0:
        return None, None
    lower = text.lower()
    unit = next((u for word, u in _UNITS if word in lower), "pcs")
    return number, unit


def to_enquiry(body: EnquiryIn, manifest: SiteManifest | None) -> WebEnquiry:
    """The CRM's record of the enquiry (raises ValueError without a phone or an email)."""
    if not (body.phone or body.email):
        raise ValueError("an enquiry needs a phone number or an email")
    families = {f.id: f.name for f in manifest.families} if manifest else {}
    products = (
        {p.id: f"{p.name} ({p.ref})" if p.ref else p.name for p in manifest.products} if manifest else {}
    )
    picked = [products[i] for i in dict.fromkeys(body.products) if i in products]
    family_names = [families[i] for i in dict.fromkeys(body.family) if i in families]
    qty, unit = quantity_of(body.quantity)
    where = ", ".join(x for x in (body.city, body.region) if x)
    lines = [
        "Website enquiry (Contact page)",
        _line("Products", "; ".join(picked)),
        _line("Families", ", ".join(family_names)),
        _line("What they need", body.detail),
        _line("Size", body.size),
        _line("Quantity", body.quantity),
        _line("How often", body.often),
        _line("Delivery", where),
        _line("Trade", body.trade),
        _line(
            "Reply by",
            {"call": "Phone call", "email": "Email", "whatsapp": "WhatsApp"}.get(body.channel or ""),
        ),
        _line("Message", body.message),
        _line("Anything else", body.extra),
    ]
    contact = ContactPatch(
        name=body.name,
        company=body.company,
        phone=body.phone,
        email=body.email,
        preferred_channel=_CHANNEL.get(body.channel or ""),
        language=body.locale,
        whatsapp_opt_in=body.whatsapp_opt_in,
        email_opt_in=body.email_opt_in,
        consent_wording=body.consent_wording,
        consent_via="web_form",
    )
    product_text = body.detail or "; ".join(picked) or ", ".join(family_names) or None
    requirement = RequirementPatch(
        product_text=product_text[:500] if product_text else None,
        quantity=qty,
        unit=unit,
        application=body.trade,
        delivery_city=body.city,
    )
    return WebEnquiry(
        contact=contact,
        requirement=requirement if requirement.model_dump(exclude_none=True) else None,
        summary="\n".join(line for line in lines if line)[:4000],
        website_products=tuple(i for i in dict.fromkeys(body.products) if i in products),
    )


def _line(label: str, value: str | None) -> str:
    return f"{label}: {value.strip()}" if value and value.strip() else ""
