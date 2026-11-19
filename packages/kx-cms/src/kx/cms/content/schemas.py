"""The content model (SRS §7, design doc v4 §7): one typed schema per collection.

Every document is stored per language: `{"en": {...}, "ar": {...}}`. English is complete; Arabic holds only
what is translated, and a field without an Arabic value is shown in English (design doc §6: "Text without an
Arabic entry stays in English"). Codes, numbers and media are shared. The schemas are flexible where the
website needs freedom (page blocks with free `data`) and strict where the site and the AI depend on a field
(ids, families, section ids, product codes).

Field names follow the website's own code (github.com/rizwankairoxaitech/Arifa-Pack, `src/data/types.ts`) so
the site reads the API without renaming anything: a product is `{id, family, ref, name, code, qty, range}`.
Prices do not exist in any schema, and any price in a text is refused on save (`kx.core.no_prices`).
"""

from __future__ import annotations

from typing import Annotated, Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

Slug = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,127}$")]
SectionId = Annotated[str, Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")]
HexColor = Annotated[str, Field(pattern=r"^#[0-9a-fA-F]{6}$")]
Lang = Literal["en", "ar", "hi", "ta", "zh", "si"]  # the assistant's six languages (design doc §6)
FAMILY_IDS: Final = (
    "containers",
    "foil",
    "platters",
    "cups-trays",
    "pots",
    "microwave",
    "cutlery",
    "cling-film",
)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class MediaRef(_Model):
    """A file from the media library (`asset_id`) or an outside link (`url`, e.g. a YouTube film).
    The public API adds `src`, `alt`, `width`, `height` and `content_type`."""

    asset_id: str | None = Field(default=None, pattern=r"^[0-9a-f-]{36}$")
    url: HttpUrl | None = None
    alt: str | None = Field(default=None, max_length=300)  # overrides the library's alt text here

    @model_validator(mode="after")
    def _one_source(self) -> MediaRef:
        if (self.asset_id is None) == (self.url is None):
            raise ValueError("give either asset_id or url")
        return self


class Link(_Model):
    label: str = Field(max_length=120)
    href: str = Field(max_length=500)  # a site path (/contact) or a full URL


class Seo(_Model):
    """SEO / AEO / GEO fields (SRS §7), editable without a developer."""

    title: str | None = Field(default=None, max_length=120)
    description: str | None = Field(default=None, max_length=320)
    share_image: MediaRef | None = None
    canonical: str | None = Field(default=None, max_length=500)
    noindex: bool = False
    in_sitemap: bool = True
    key_facts: list[str] = Field(default_factory=list, max_length=20)  # GEO: citation-friendly facts


class Faq(_Model):
    question: str = Field(max_length=300)
    answer: str = Field(max_length=2000)


# ---------------------------------------------------------------- site settings (one document: site/site)
class Phone(_Model):
    label: str = Field(max_length=80)  # Head office, Packaging division
    number: str = Field(max_length=40)
    whatsapp: bool = False


class Email(_Model):
    label: str = Field(max_length=80)
    address: str = Field(max_length=200, pattern=r"^[^@\s]+@[^@\s]+$")


class Address(_Model):
    label: str = Field(max_length=80)
    lines: list[str] = Field(max_length=8)
    map_url: HttpUrl | None = None
    lat: float | None = None
    lng: float | None = None


class Hours(_Model):
    text: str = Field(max_length=120)  # "Monday to Saturday, 08:00 to 20:00"
    days: list[Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]] = Field(default_factory=list)
    opens: str | None = Field(default=None, pattern=r"^\d{2}:\d{2}$")
    closes: str | None = Field(default=None, pattern=r"^\d{2}:\d{2}$")
    timezone: str = "Asia/Dubai"


class Theme(_Model):
    """The site's colours and fonts (the client's "colouring"): CSS variables the website applies."""

    colors: dict[str, HexColor] = Field(default_factory=dict)  # primary, accent, background, text, ...
    fonts: dict[str, str] = Field(default_factory=dict)  # heading, body, arabic


class AssistantTexts(_Model):
    """The words Arifa says that are not AI-written (AS-01, AS-08, AS-11)."""

    greeting: str | None = Field(default=None, max_length=500)
    recording_notice: str | None = Field(default=None, max_length=500)
    handover: str | None = Field(default=None, max_length=500)
    busy: str | None = Field(default=None, max_length=300)
    offline: str | None = Field(default=None, max_length=300)


class Announcement(_Model):
    text: str = Field(max_length=200)
    link: Link | None = None
    active: bool = False


class SiteSettings(_Model):
    company_name: str = Field(max_length=200)
    tagline: str | None = Field(default=None, max_length=200)
    phones: list[Phone] = Field(default_factory=list, max_length=10)
    emails: list[Email] = Field(default_factory=list, max_length=10)
    addresses: list[Address] = Field(default_factory=list, max_length=5)
    hours: Hours | None = None
    social: list[Link] = Field(default_factory=list, max_length=10)
    announcement: Announcement | None = None
    theme: Theme = Field(default_factory=Theme)
    assistant: AssistantTexts = Field(default_factory=AssistantTexts)
    site_url: HttpUrl | None = None
    product_pages: bool = False  # PG-03 /products/<id> is live; until then products open in the quick view


# ---------------------------------------------------------------- pages and their sections
class BlockItem(_Model):
    title: str | None = Field(default=None, max_length=200)
    text: str | None = Field(default=None, max_length=4000)
    media: MediaRef | None = None
    link: Link | None = None
    data: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


class Block(_Model):
    """One section of a page (design doc v4 §3). `section_id` is the DOM id the website renders and the AI
    scrolls to (D55): renaming it is a change for both, so it is a fixed key."""

    section_id: SectionId
    design_id: str | None = Field(default=None, max_length=10, pattern=r"^[A-Z]{2}-\d{2}$")  # HM-05
    kind: str = Field(default="section", max_length=40)  # hero, text, list, cards, steps, film, cta ...
    eyebrow: str | None = Field(default=None, max_length=80)  # "(04)"
    heading: str | None = Field(default=None, max_length=300)
    body: str | None = Field(default=None, max_length=8000)
    media: list[MediaRef] = Field(default_factory=list, max_length=30)
    items: list[BlockItem] = Field(default_factory=list, max_length=60)
    buttons: list[Link] = Field(default_factory=list, max_length=6)
    keywords: list[str] = Field(default_factory=list, max_length=30)  # words that lead the AI here
    data: dict[str, Any] = Field(default_factory=dict)  # anything else the section needs
    hidden: bool = False


class Page(_Model):
    title: str = Field(max_length=200)
    path: str = Field(pattern=r"^/[a-z0-9/-]*$", max_length=200)  # English path: /, /about
    seo: Seo = Field(default_factory=Seo)
    blocks: list[Block] = Field(default_factory=list, max_length=40)
    faqs: list[Faq] = Field(default_factory=list, max_length=40)

    @model_validator(mode="after")
    def _unique_sections(self) -> Page:
        ids = [b.section_id for b in self.blocks]
        if len(ids) != len(set(ids)):
            raise ValueError("section_id must be unique on a page")
        return self


# ---------------------------------------------------------------- catalogue
class Family(_Model):
    """A product family. `id` (the document key) is fixed: containers, foil, platters, cups-trays, pots,
    microwave, cutlery, cling-film (the site's URL filter ?family=<id>)."""

    name: str = Field(max_length=120)  # Aluminium Containers
    short: str = Field(max_length=60)  # Containers
    blurb: str | None = Field(default=None, max_length=600)
    clip: str | None = Field(default=None, max_length=120)  # the factory clip name the site plays
    hero: MediaRef | None = None
    accent_color: HexColor | None = None
    synonyms: dict[Lang, list[str]] = Field(default_factory=dict)  # what visitors call it, per language
    seo: Seo = Field(default_factory=Seo)


class Specs(_Model):
    material: str | None = Field(default=None, max_length=200)
    capacity: str | None = Field(default=None, max_length=100)
    dimensions: str | None = Field(default=None, max_length=200)
    thickness: str | None = Field(default=None, max_length=100)
    colours: str | None = Field(default=None, max_length=200)
    lid_options: str | None = Field(default=None, max_length=200)
    pieces_per_carton: str | None = Field(default=None, max_length=100)


class Suitability(_Model):
    oven: bool | None = None
    microwave: bool | None = None
    freezer: bool | None = None


class Product(_Model):
    """One product as the website shows it (PG-03). The document key is the site's id (`ac-8325`, the URL
    slug, unique). `ref` is what buyers say ("8325"; not unique: the trade carton and the self-range pack
    share it); `code` is the catalogue code (`AC-1`). D61 (19 Nov): the sellable **variants** (packings, roll
    weights, retail packs) are the ERP items in the CRM, linked to this product by its id; the CRM holds their
    prices and stock. This record never holds a price."""

    family: str = Field(pattern=r"^[a-z0-9-]{1,40}$")
    ref: str = Field(max_length=40)
    code: str | None = Field(default=None, max_length=40)
    name: str = Field(max_length=300)
    qty: str | None = Field(default=None, max_length=80)  # packing, e.g. "1 x 1000 pcs"
    range: Literal["trade", "self"] = "trade"
    short_description: str | None = Field(default=None, max_length=400)
    description: str | None = Field(default=None, max_length=6000)
    specs: Specs = Field(default_factory=Specs)
    suitability: Suitability = Field(default_factory=Suitability)
    applications: list[str] = Field(default_factory=list, max_length=20)
    custom_printing: bool | None = None
    moq_text: str | None = Field(default=None, max_length=120)  # display text only; quoting MOQ is CRM
    photos: list[MediaRef] = Field(default_factory=list, max_length=20)
    video: MediaRef | None = None
    datasheet: MediaRef | None = None
    model_3d: MediaRef | None = None
    faqs: list[Faq] = Field(default_factory=list, max_length=20)
    legacy_paths: list[str] = Field(default_factory=list, max_length=10)  # old-site addresses -> 301 here
    erp_match: list[str] = Field(
        default_factory=list, max_length=20
    )  # ERP description words that link variants
    seo: Seo = Field(default_factory=Seo)


# ---------------------------------------------------------------- other collections
class TourChapter(_Model):
    """HM-05 "Walk the floor": a chapter opens the factory film at `seconds`."""

    title: str = Field(max_length=80)
    seconds: float = Field(ge=0, le=36000)
    text: str | None = Field(default=None, max_length=1000)
    family: str | None = Field(default=None, pattern=r"^[a-z0-9-]{1,40}$")
    media: MediaRef | None = None


class Station(_Model):
    """QA-02 "The line, checked": one station of the checked line."""

    title: str = Field(max_length=80)
    text: str | None = Field(default=None, max_length=1000)
    media: MediaRef | None = None


class FaqEntry(_Model):
    question: str = Field(max_length=300)
    answer: str = Field(max_length=3000)
    page: str | None = Field(default=None, max_length=64)
    product: str | None = Field(default=None, max_length=128)


class Certificate(_Model):
    title: str = Field(max_length=200)
    issuer: str | None = Field(default=None, max_length=200)
    file: MediaRef | None = None
    valid_until: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    text: str | None = Field(default=None, max_length=2000)


class Client(_Model):
    name: str = Field(max_length=200)
    logo: MediaRef | None = None
    sample: bool = True  # CL-02: logos are samples until Arifa approves them
    industry: str | None = Field(default=None, max_length=64)


class Industry(_Model):
    """CL-01: a stop on the delivery route (bakeries, hotels, restaurants, supermarkets, caterers,
    distributors): its use, product kit and the trade it pre-selects on the enquiry desk."""

    name: str = Field(max_length=80)
    label: str | None = Field(default=None, max_length=80)
    short: str | None = Field(default=None, max_length=200)
    trade: str | None = Field(default=None, max_length=80)
    use: str | None = Field(default=None, max_length=1000)
    kit: list[str] = Field(default_factory=list, max_length=20)  # family or product ids
    media: MediaRef | None = None


class Story(_Model):
    """CL-03 customer story. `sample` = labelled as mock until Arifa approves it."""

    title: str = Field(max_length=200)
    industry: str | None = Field(default=None, max_length=64)
    trade: str | None = Field(default=None, max_length=80)
    scene: str | None = Field(default=None, max_length=2000)
    quote: str | None = Field(default=None, max_length=1000)
    author: str | None = Field(default=None, max_length=200)
    packaging: list[str] = Field(default_factory=list, max_length=20)  # product ids
    families: list[str] = Field(default_factory=list, max_length=8)
    media: MediaRef | None = None
    sample: bool = True


class Person(_Model):
    """AB-05 / AB-06: the President / CEO and the directors."""

    name: str = Field(max_length=200)
    role: str = Field(max_length=200)
    kind: Literal["ceo", "director", "other"] = "director"
    quote: str | None = Field(default=None, max_length=600)
    message: str | None = Field(default=None, max_length=6000)
    photo: MediaRef | None = None


COLLECTIONS: Final[dict[str, type[_Model]]] = {
    "site": SiteSettings,
    "pages": Page,
    "families": Family,
    "products": Product,
    "tour_chapters": TourChapter,
    "stations": Station,
    "faqs": FaqEntry,
    "certificates": Certificate,
    "clients": Client,
    "industries": Industry,
    "stories": Story,
    "leadership": Person,
}

# Collections whose published text the AI may learn from (the knowledge base). Logos have no text.
KB_COLLECTIONS: Final = frozenset(
    {
        "site",
        "pages",
        "families",
        "products",
        "tour_chapters",
        "stations",
        "faqs",
        "certificates",
        "industries",
        "stories",
        "leadership",
    }
)
# Collections the site map (D55) is built from
MANIFEST_COLLECTIONS: Final = frozenset({"site", "pages", "families", "products", "tour_chapters"})
LOCALES: Final = ("en", "ar")


def schema_of(collection: str) -> type[_Model]:
    try:
        return COLLECTIONS[collection]
    except KeyError:
        raise LookupError(collection) from None
