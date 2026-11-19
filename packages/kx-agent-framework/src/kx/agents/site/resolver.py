"""D57 layer 1: the instant resolver. No model, well under 50 ms: when a visitor's words clearly name
something on the website (a product code, a family, a page, the factory film), the page starts moving at once
while the agent is still writing its answer. The website runs the same rules on its side (layer 0) from the
same manifest and the same test vectors (`docs/handoff/vectors/site-nav.json`).

Precision over recall: it acts only when the intent is clear (a code, or a family / page named together with
a "show me" word, or a message that is little more than the name). Anything else is left to the agent, which
can also navigate (layer 2). A wrong move is worse than no move.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Final

from kx.agents.site.actions import resolve_action
from kx.agents.site.models import FactoryTour, Navigate, ShowFamily, ShowProduct, SiteAction, SiteContext
from kx.core.contracts import ManifestProduct, SiteManifest

# "show me / open / take me to / where is" in the assistant's six languages
_NAV_WORDS: Final = (
    "show",
    "open",
    "go to",
    "take me",
    "see",
    "view",
    "display",
    "where is",
    "where are",
    "where can",
    "let me see",
    "find",
    "look at",
    "browse",
    "bring up",
    "do you have",
    "do you sell",
    "do you make",
    "أرني",
    "اريني",
    "اعرض",
    "افتح",
    "اذهب",
    "خذني",
    "وين",
    "أين",
    "اين",
    "شوف",
    "ابي اشوف",
    "عندكم",
    "هل لديكم",
    "दिखाओ",
    "दिखाइए",
    "दिखाएं",
    "खोलो",
    "खोलिए",
    "कहाँ",
    "आपके पास",
    "காட்டு",
    "காட்டுங்கள்",
    "திற",
    "எங்கே",
    "显示",
    "打开",
    "看看",
    "给我看",
    "在哪",
    "有没有",
    "පෙන්වන්න",
    "විවෘත කරන්න",
    "කොහෙද",
)
_TOUR_WORDS: Final = (
    "factory tour",
    "factory film",
    "factory video",
    "tour of the factory",
    "walk the floor",
    "the film",
    "video of the factory",
    "show me the factory",
    "see the factory",
    "how you make",
    "how it is made",
    "جولة",
    "فيديو المصنع",
    "المصنع",
    "कारखाना",
    "फैक्ट्री",
    "தொழிற்சாலை",
    "工厂",
    "කර්මාන්තශාලාව",
)
_SELF_WORDS: Final = ("self range", "retail", "pack of", "packet", "تجزئة", "عبوة")
_TRADE_WORDS: Final = ("trade", "carton", "bulk", "wholesale", "كرتون", "جملة")
_CODE: Final = re.compile(r"(?<![\w-])([a-z]{1,4}-?\d{1,6}|\d{3,6})(?![\w-])", re.IGNORECASE)
_SHORT_MESSAGE_TOKENS: Final = 5
_CONTACT_DETAILS: Final = re.compile(r"@|\+?\d[\d\s().-]{6,}\d")  # an email or a phone number


@dataclass(frozen=True, slots=True)
class Match:
    action: SiteAction
    why: str


def normalise(text: str) -> str:
    """Lower case, no diacritics or tatweel, one Arabic alef / ya / ta marbuta form, single spaces."""
    t = unicodedata.normalize("NFKC", text).lower()
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")  # Arabic harakat and other marks
    t = t.replace("ـ", "")
    for a, b in (("أ", "ا"), ("إ", "ا"), ("آ", "ا"), ("ى", "ي"), ("ة", "ه")):
        t = t.replace(a, b)
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s-]", " ", t)).strip()


def _has(text: str, phrase: str) -> bool:
    p = normalise(phrase)
    if not p:
        return False
    if p.isascii():
        return re.search(rf"(?<!\w){re.escape(p)}(?!\w)", text) is not None
    return p in text  # Arabic, Devanagari, Tamil, Chinese, Sinhala: no reliable word boundaries


def _nav_intent(text: str) -> bool:
    return any(_has(text, w) for w in _NAV_WORDS)


def _products_by_code(manifest: SiteManifest, text: str) -> list[ManifestProduct]:
    codes = {m.group(1).replace(" ", "") for m in _CODE.finditer(text)}
    if not codes:
        return []
    out = []
    for p in manifest.products:
        keys = {p.ref.lower(), p.id.lower(), (p.catalogue_code or "").lower()}
        keys |= {k.replace("-", "") for k in keys}
        if codes & keys:
            out.append(p)
    return out


def _narrow(found: list[ManifestProduct], text: str, context: SiteContext | None) -> list[ManifestProduct]:
    """Two products can share a reference (the trade carton AC-2 and the self-range pack ARF-8342)."""
    if len(found) <= 1:
        return found
    wants_self = any(_has(text, w) for w in _SELF_WORDS)
    wants_trade = any(_has(text, w) for w in _TRADE_WORDS)
    by_range = found
    if wants_self != wants_trade:  # only when the visitor said which one
        by_range = [p for p in found if (p.range == "self") == wants_self]
        if len(by_range) == 1:
            return by_range
    if context and context.family:
        in_family = [p for p in (by_range or found) if p.family == context.family]
        if len(in_family) == 1:
            return in_family
    return by_range or found


def instant_actions(text: str, manifest: SiteManifest, context: SiteContext | None = None) -> list[Match]:
    """At most one confident action for the visitor's words (empty = leave it to the agent). A message with a
    phone number or an email is the visitor answering a question, never a request to move the page."""
    if _CONTACT_DETAILS.search(text):
        return []
    t = normalise(text)
    if not t:
        return []
    short = len(t.split()) <= _SHORT_MESSAGE_TOKENS
    nav = _nav_intent(t)

    # 1. a product code ("8325", "AC-1", "ac-8325"): always worth showing
    found = _narrow(_products_by_code(manifest, t), t, context)
    if len(found) == 1:
        return _done(ShowProduct(product_id=found[0].id, layer="instant"), manifest, context, "product code")
    if found and len({p.family for p in found}) == 1:  # ambiguous code: show the family, the agent asks which
        return _done(ShowFamily(family=found[0].family, layer="instant"), manifest, context, "ambiguous code")

    # 2. the factory film
    if any(_has(t, w) for w in _TOUR_WORDS) and (nav or short):
        chapter = next((c.id for c in manifest.chapters if _has(t, c.title) and not _has(t, "factory")), None)
        return _done(FactoryTour(chapter=chapter, layer="instant"), manifest, context, "factory tour")

    # 3. a family, by any of its names or synonyms in any language; the longest phrase wins ("foil
    #    containers" is the containers family, not foil), and a tie between two families is left to the agent
    if nav or short:
        best: dict[str, int] = {}
        for fam in manifest.families:
            words = [fam.name, fam.id.replace("-", " "), *fam.names.values()]
            words += [w for ws in fam.synonyms.values() for w in ws]
            hits = [len(normalise(w)) for w in words if w and _has(t, w)]
            if hits:
                best[fam.id] = max(hits)
        if best:
            top = max(best.values())
            winners = [f for f, n in best.items() if n == top]
            if len(winners) == 1:
                fam_id = winners[0]
                if context and context.page == "products" and context.family == fam_id and not nav:
                    return []  # already there
                return _done(ShowFamily(family=fam_id, layer="instant"), manifest, context, "family")

    # 4. a page or one of its sections ("contact", "about", "certificates", "where are you")
    if nav or short:
        for page in manifest.pages:
            for sec in page.sections:
                if any(_has(t, k) for k in sec.keywords if len(k) > 3):
                    return _done(
                        Navigate(page=page.key, section=sec.id, layer="instant"), manifest, context, "section"
                    )
            if page.key not in ("home", "products") and (_has(t, page.title) or _has(t, page.key)):
                return _done(Navigate(page=page.key, layer="instant"), manifest, context, "page")
    return []


def _done(action: SiteAction, manifest: SiteManifest, context: SiteContext | None, why: str) -> list[Match]:
    resolved = resolve_action(action, manifest, context)
    return [Match(resolved, why)] if resolved is not None else []
