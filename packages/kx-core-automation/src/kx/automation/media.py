"""Old path of `kx.automation._shared.media` (moved by ADR-0001).

This module is an alias of the new one (the same module object, so every name, private helper and monkeypatch
still works); it is removed once nothing in this repository or the Arifaa Auto backend imports the old path.
"""

import sys

from kx.automation._shared import media as _moved
from kx.automation._shared.media import *  # noqa: F403

sys.modules[__name__] = _moved
