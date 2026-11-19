"""Old path of `kx.crm.assignment.router` (moved by ADR-0001).

This module is an alias of the new one (the same module object, so every name, private helper and monkeypatch
still works); it is removed once nothing in this repository or the Arifaa Auto backend imports the old path.
"""

import sys

from kx.crm.assignment import router as _moved
from kx.crm.assignment.router import *  # noqa: F403

sys.modules[__name__] = _moved
