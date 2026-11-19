"""Per-language content: English is complete, other languages hold only what is translated.

`localize(data, "ar")` is what an Arabic page shows: the Arabic values laid over the English ones, field by
field (design doc §6: "Text without an Arabic entry stays in English"). Lists of objects (page blocks, FAQs)
are merged item by item, so a translator fills in only the texts of block 3 without copying the whole page.
"""

from __future__ import annotations

from typing import Any, Final

BASE_LOCALE: Final = "en"


def deep_merge(base: Any, over: Any) -> Any:
    """`over` laid on `base`. Dicts merge key by key; lists of dicts merge by position; anything else in
    `over` replaces `base`, except None / "" / [] which mean "not translated"."""
    if over is None or over in ("", []):
        return base
    if isinstance(base, dict) and isinstance(over, dict):
        out = dict(base)
        for key, value in over.items():
            out[key] = deep_merge(base.get(key), value) if key in base else value
        return out
    if isinstance(base, list) and isinstance(over, list) and all(isinstance(x, dict) for x in base):
        merged = [deep_merge(b, o) for b, o in zip(base, over, strict=False)]
        return merged + base[len(over) :]
    return over


def localize(data: dict[str, Any], locale: str) -> dict[str, Any]:
    base: dict[str, Any] = data.get(BASE_LOCALE) or {}
    if locale == BASE_LOCALE or locale not in data:
        return base
    merged: dict[str, Any] = deep_merge(base, data[locale])
    return merged


def locales_of(data: dict[str, Any]) -> list[str]:
    return [BASE_LOCALE, *sorted(k for k, v in data.items() if k != BASE_LOCALE and v)]
