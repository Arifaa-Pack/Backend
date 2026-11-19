"""SRS §7 / D22 / D54: prices never appear in website content. One rule, shared by every package that takes
content in (the CMS package when an editor saves, the automation webhook kept for the old Payload contract).

* a field whose **name** looks like money (`price`, `cost`, `margin`, `discount`, `tier`, a currency code)
  is never stored;
* a **text** that states an amount of money (`AED 1.20`, `3.5 dirhams`, `$2`) is refused, using the same
  multilingual reader as the price guard (`kx.core.price_guard.extract_prices`). Percentages are allowed
  ("80 % recycled aluminium" is a product fact, not a price).

Prices, tiers, MOQ for quoting and lead times live only in the CRM (§10).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

from kx.core.price_guard import extract_prices

PRICE_KEY_WORDS: Final = ("price", "cost", "margin", "discount", "tier", "aed", "usd")


def is_price_key(key: str) -> bool:
    k = key.lower()
    return any(w in k for w in PRICE_KEY_WORDS)


def drop_price_keys[V](mapping: Mapping[str, V]) -> dict[str, V]:
    """The mapping without price-like keys (top level only: the webhook's flat specification)."""
    return {k: v for k, v in mapping.items() if not is_price_key(k)}


def find_price(value: Any, path: str = "") -> str | None:
    """The path of the first price found in `value` (a price-like key, or a text stating money), else None.
    Walks nested dicts and lists; `path` reads like `products.en.specs.material`."""
    if isinstance(value, Mapping):
        for key, item in value.items():
            here = f"{path}.{key}" if path else str(key)
            if is_price_key(str(key)):
                return here
            found = find_price(item, here)
            if found is not None:
                return found
        return None
    if isinstance(value, list | tuple):
        for i, item in enumerate(value):
            found = find_price(item, f"{path}[{i}]")
            if found is not None:
                return found
        return None
    if isinstance(value, str) and any(not m.is_percentage for m in extract_prices(value)):
        return path or "(text)"
    return None
