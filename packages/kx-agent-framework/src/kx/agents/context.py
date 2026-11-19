"""Old path of `kx.agents.runtime.context` (moved by ADR-0001).

This module is an alias of the new one (the same module object, so every name, private helper and monkeypatch
still works); it is removed once nothing in this repository or the Arifaa Auto backend imports the old path.
"""

import sys

from kx.agents.runtime import context as _moved
from kx.agents.runtime.context import *  # noqa: F403

sys.modules[__name__] = _moved
