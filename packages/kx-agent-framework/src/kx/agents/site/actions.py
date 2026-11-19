"""D55: every site action is checked against the site manifest before it leaves the server.

An action for a product, family, page, section or tour chapter the website does not have is dropped (and
logged), so the assistant can never send the visitor somewhere wrong. A valid action gets its `resolved`
paths in both site languages (the page locale decides which one the website uses, not the chat language) and
its intent id (D57: the same intent found by two layers is done once). Text in an enquiry fill is checked for
prices like everything else the AI writes.
"""

from __future__ import annotations

from kx.agents.site.models import (
    AddToEnquiry,
    AskContact,
    FactoryTour,
    FillEnquiry,
    Navigate,
    Resolved,
    ScrollTo,
    ShowFamily,
    ShowProduct,
    SiteAction,
    SiteContext,
    intent_id,
)
from kx.core.contracts import SiteManifest
from kx.core.logging import get_logger
from kx.core.no_prices import find_price

log = get_logger(__name__)
FLOOR_SECTION = "floor"  # HM-05 "Walk the floor": where the factory film's chapters live


def _ar(path: str) -> str:
    return "/ar" if path == "/" else "/ar" + path


def _with_anchor(path: str, anchor: str | None) -> Resolved:
    if not anchor:
        return Resolved(path_en=path, path_ar=_ar(path))
    return Resolved(path_en=f"{path}#{anchor}", path_ar=f"{_ar(path)}#{anchor}", anchor=anchor)


def _page_of_section(manifest: SiteManifest, section: str, prefer: str | None) -> str | None:
    pages = [p for p in manifest.pages if any(s.id == section for s in p.sections)]
    if not pages:
        return None
    for p in pages:
        if p.key == prefer:
            return p.key
    return pages[0].key


def resolve_action(
    action: SiteAction, manifest: SiteManifest, context: SiteContext | None = None
) -> SiteAction | None:
    """The action with its target filled in, or None when the website has no such thing."""
    pages = {p.key: p for p in manifest.pages}
    current = context.page if context else None
    match action:
        case ShowFamily():
            fam = next((f for f in manifest.families if f.id == action.family), None)
            if fam is None:
                return _drop(action, "unknown family")
            action.resolved = Resolved(path_en=fam.path_en, path_ar=fam.path_ar)
        case ShowProduct():
            prod = next((p for p in manifest.products if p.id == action.product_id), None)
            if prod is None:
                return _drop(action, "unknown product")
            if action.view == "page" and not manifest.product_pages:
                action.view = "quick"  # PG-03 is not live on the site yet: the quick view shows it
            action.resolved = Resolved(path_en=prod.path_en, path_ar=prod.path_ar)
        case Navigate():
            page = pages.get(action.page)
            if page is None:
                return _drop(action, "unknown page")
            if action.section and not any(s.id == action.section for s in page.sections):
                return _drop(action, "unknown section")
            action.resolved = _with_anchor(page.path_en, action.section)
        case ScrollTo():
            key = _page_of_section(manifest, action.section, current)
            if key is None:
                return _drop(action, "unknown section")
            if current not in (None, key, "other") and key != current:
                # the section lives on another page: go there instead of scrolling here
                there = Navigate(page=key, section=action.section, layer=action.layer)
                return resolve_action(there, manifest)
            action.resolved = _with_anchor(pages[key].path_en, action.section)
        case FactoryTour():
            if action.chapter is not None:
                chapter = next((c for c in manifest.chapters if c.id == action.chapter), None)
                if chapter is None:
                    return _drop(action, "unknown chapter")
                action.seconds = chapter.seconds
            home = pages.get("home")
            action.resolved = _with_anchor(home.path_en if home else "/", FLOOR_SECTION)
        case AddToEnquiry():
            known = {p.id for p in manifest.products}
            ids = [i for i in dict.fromkeys(action.product_ids) if i in known]
            if not ids:
                return _drop(action, "unknown products")
            action.product_ids = ids
        case FillEnquiry():
            if find_price(action.fill.model_dump()) is not None:
                return _drop(action, "a price in the enquiry text")
            known_fam = {f.id for f in manifest.families}
            action.fill.family = [f for f in action.fill.family if f in known_fam]
            contact = pages.get("contact")
            action.resolved = _with_anchor(contact.path_en if contact else "/contact", "enquiry")
        case AskContact():
            pass
    action.id = intent_id(action)
    return action


def _drop(action: SiteAction, why: str) -> SiteAction | None:
    """Always None: the action is not sent (logged so a gap in the manifest or a model slip shows up)."""
    log.info("site.action_dropped", type=action.type, why=why)
    return None
