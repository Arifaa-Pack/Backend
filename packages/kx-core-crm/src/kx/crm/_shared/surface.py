"""The lazy name lookup behind every slice's `api.py` (ADR-0001 rule 5): a slice publishes the names other
slices may use, and each one is imported from its real module the first time it is asked for."""

from __future__ import annotations

import importlib
from typing import Any


def resolve(namespace: dict[str, Any], exports: dict[str, str], name: str) -> Any:
    """`module.__getattr__` for an api.py: import `exports[name]`'s module, cache the value, return it."""
    try:
        module_name = exports[name]
    except KeyError:
        raise AttributeError(f"module {namespace['__name__']!r} has no attribute {name!r}") from None
    value = getattr(importlib.import_module(module_name), name)
    namespace[name] = value
    return value
