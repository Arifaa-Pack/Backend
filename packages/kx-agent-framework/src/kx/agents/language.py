"""Old path of `kx.agents.roles.language` (moved by ADR-0001).

This module is an alias of the new one (the same module object, so every name, private helper and monkeypatch
still works); it is removed once nothing in this repository or the Arifaa Auto backend imports the old path.
"""

import sys

from kx.agents.roles import language as _moved
from kx.agents.roles.language import *  # noqa: F403

sys.modules[__name__] = _moved
