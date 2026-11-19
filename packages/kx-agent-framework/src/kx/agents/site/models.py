"""D55: what the browser tells the assistant (`SiteContext`) and what the assistant may ask the website to do
(`SiteAction`). The website (Arifa-Pack, Next.js) already animates every one of these with its own code: the
assistant decides *what* to show, the website decides *how*. No action carries a selector, a free URL or a
price; every one is checked against the site manifest before it leaves the server.
"""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

PageKey = Literal["home", "products", "product", "about", "quality", "clients", "contact", "other"]
_ID: Final = r"^[a-z0-9][a-z0-9_-]{0,127}$"
_SECTION: Final = r"^[a-z][a-z0-9-]{0,63}$"


def visit_thread(session_id: object) -> str:
    """D58: one conversation per website visit, whichever way the visitor talks (chat or voice)."""
    return f"web:{session_id}"


class SiteContext(BaseModel):
    """Where the visitor is right now (sent with every message and every page or product change)."""

    model_config = ConfigDict(extra="ignore")

    path: str = Field(default="/", max_length=300)
    page: PageKey = "home"
    locale: Literal["en", "ar"] = "en"
    family: str | None = Field(default=None, pattern=_ID)  # the catalogue filter (?family=)
    product_id: str | None = Field(default=None, pattern=_ID)  # the product page open (PG-03)
    quick_view_id: str | None = Field(default=None, pattern=_ID)  # the quick view open (PR-04)
    section: str | None = Field(default=None, pattern=_SECTION)  # the section in view
    enquiry_list: list[Annotated[str, Field(pattern=_ID)]] = Field(default_factory=list, max_length=50)
    input: Literal["text", "voice"] = "text"

    def summary(self) -> str:
        """One line for the model's prompt."""
        bits = [f"page {self.page} ({self.path})"]
        if self.family:
            bits.append(f"family filter {self.family}")
        if self.product_id or self.quick_view_id:
            bits.append(f"looking at product {self.product_id or self.quick_view_id}")
        if self.section:
            bits.append(f"section {self.section}")
        if self.enquiry_list:
            bits.append(f"enquiry list: {', '.join(self.enquiry_list[:10])}")
        return "The visitor is on the website: " + "; ".join(bits) + "."


class Resolved(BaseModel):
    """Where the action lands, in both site languages (filled by the server, never by the model)."""

    path_en: str
    path_ar: str
    anchor: str | None = None


class _Action(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = ""  # a hash of the intent: the same intent from two layers is done once (D57)
    layer: Literal["instant", "agent"] = "agent"
    resolved: Resolved | None = None


class ShowFamily(_Action):
    type: Literal["show_family"] = "show_family"
    family: str = Field(pattern=_ID)


class ShowProduct(_Action):
    type: Literal["show_product"] = "show_product"
    product_id: str = Field(pattern=_ID)
    view: Literal["quick", "page"] = "quick"


class Navigate(_Action):
    type: Literal["navigate"] = "navigate"
    page: str = Field(pattern=_ID)  # a CMS page key: home, products, about, quality, clients, contact
    section: str | None = Field(default=None, pattern=_SECTION)


class ScrollTo(_Action):
    type: Literal["scroll_to"] = "scroll_to"
    section: str = Field(pattern=_SECTION)


class FactoryTour(_Action):
    type: Literal["factory_tour"] = "factory_tour"
    chapter: str | None = Field(default=None, pattern=_ID)  # arrival, forming, lids, foil, ...
    seconds: float | None = None  # filled from the manifest


class AddToEnquiry(_Action):
    type: Literal["add_to_enquiry"] = "add_to_enquiry"
    product_ids: list[Annotated[str, Field(pattern=_ID)]] = Field(min_length=1, max_length=10)


class EnquiryFill(BaseModel):
    """The enquiry desk's own field names (CT-01, `enquiryDeskClient.ts`). Never a price."""

    model_config = ConfigDict(extra="forbid")

    family: list[str] = Field(default_factory=list, max_length=8)
    detail: str | None = Field(default=None, max_length=500)  # products by code
    size: str | None = Field(default=None, max_length=100)
    quantity: str | None = Field(default=None, max_length=100)
    often: str | None = Field(default=None, max_length=100)  # frequency
    region: str | None = Field(default=None, max_length=100)
    city: str | None = Field(default=None, max_length=100)
    name: str | None = Field(default=None, max_length=200)
    company: str | None = Field(default=None, max_length=200)
    trade: str | None = Field(default=None, max_length=100)
    channel: Literal["email", "call", "whatsapp"] | None = None
    message: str | None = Field(default=None, max_length=2000)


class FillEnquiry(_Action):
    type: Literal["fill_enquiry"] = "fill_enquiry"
    fill: EnquiryFill


ContactField = Literal["name", "company", "phone", "email"]
ConsentChannel = Literal["whatsapp", "email"]


def _contact_fields() -> list[ContactField]:
    return ["name", "phone", "email"]


def _consents() -> list[ConsentChannel]:
    return ["whatsapp", "email"]


class AskContact(_Action):
    """Show the inline contact card in the chat: name / phone / email with the WhatsApp and email consents."""

    type: Literal["ask_contact"] = "ask_contact"
    fields: list[ContactField] = Field(default_factory=_contact_fields, max_length=4)
    consents: list[ConsentChannel] = Field(default_factory=_consents, max_length=2)


SiteAction = Annotated[
    ShowFamily | ShowProduct | Navigate | ScrollTo | FactoryTour | AddToEnquiry | FillEnquiry | AskContact,
    Field(discriminator="type"),
]
ACTION_TYPES: Final = (
    ShowFamily,
    ShowProduct,
    Navigate,
    ScrollTo,
    FactoryTour,
    AddToEnquiry,
    FillEnquiry,
    AskContact,
)


def intent_id(action: _Action) -> str:
    """The same intent gets the same id whichever layer found it (the website does it once, D57)."""
    body = action.model_dump(exclude={"id", "layer", "resolved", "seconds"}, mode="json")
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:16]
