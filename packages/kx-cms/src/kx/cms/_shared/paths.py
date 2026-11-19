"""Where a piece of content shows on the website (design doc v4 §1), and which cache tags the website gives
it. One place, so the sitemap, the AI's site map and the publish-time revalidation always agree.

Every English path also exists in Arabic under `/ar` (`/products` and `/ar/products`).
"""

from __future__ import annotations

from typing import Any, Final

AR_PREFIX: Final = "/ar"
# The site's own page paths; any other page document is served at /<key>
PAGE_PATHS: Final = {
    "home": "/",
    "products": "/products",
    "about": "/about",
    "quality": "/quality",
    "clients": "/clients",
    "contact": "/contact",
}
# A change in these collections shows on every page (header, footer, menu, assistant texts)
SITE_WIDE: Final = frozenset({"site"})
# Which pages read which collection (design doc v4 §3)
READ_BY: Final[dict[str, tuple[str, ...]]] = {
    "families": ("home", "products"),
    "products": ("products", "quality"),  # QA-05 one dot per product
    "tour_chapters": ("home",),
    "stations": ("quality",),
    "certificates": ("quality",),
    "faqs": ("home", "products", "contact"),
    "clients": ("clients",),
    "industries": ("clients",),
    "stories": ("clients",),
    "leadership": ("home", "about"),
}


def ar(path: str) -> str:
    return AR_PREFIX if path == "/" else AR_PREFIX + path


def page_path(key: str, data: dict[str, Any] | None = None) -> str:
    if data and isinstance(data.get("path"), str):
        return str(data["path"])
    return PAGE_PATHS.get(key, f"/{key}")


def product_path(product_id: str, *, product_pages: bool) -> str:
    """PG-03 once it is live on the site; until then the catalogue's quick view (`?p=<id>`)."""
    return f"/products/{product_id}" if product_pages else f"/products?p={product_id}"


def family_path(family_id: str) -> str:
    return f"/products?family={family_id}"


def both(path: str) -> list[str]:
    return [path, ar(path)]


def tags_and_paths(
    collection: str, key: str, data: dict[str, Any], *, product_pages: bool
) -> tuple[list[str], list[str]]:
    """The cache tags and page paths a publish of this document makes stale."""
    tags = [f"{collection}:{key}", collection, "manifest"]
    paths: list[str] = []
    if collection in SITE_WIDE:
        tags.append("site")
        for path in PAGE_PATHS.values():
            paths += both(path)
    elif collection == "pages":
        paths += both(page_path(key, data))
    elif collection == "products":
        tags.append(f"family:{data.get('family', '')}")
        if product_pages:
            paths += both(product_path(key, product_pages=True))
    for page in READ_BY.get(collection, ()):
        paths += both(PAGE_PATHS[page])
    if collection in ("pages", "products", "families"):
        tags.append("sitemap")
    return list(dict.fromkeys(tags)), list(dict.fromkeys(paths))
