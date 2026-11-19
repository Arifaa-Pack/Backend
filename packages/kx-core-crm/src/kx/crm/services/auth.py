"""Old path of `kx.crm.auth.service` (moved by ADR-0001).

This module is an alias of the new one (the same module object, so every name, private helper and monkeypatch
still works); it is removed once nothing in this repository or the Arifaa Auto backend imports the old path.
"""

import sys

from kx.crm.auth import service as _moved
from kx.crm.auth.service import *  # noqa: F403

sys.modules[__name__] = _moved
