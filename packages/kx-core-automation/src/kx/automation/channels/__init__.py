"""Old home of the channel adapters (now the `whatsapp/`, `email/` and `calls/` slices) and of their shared
error / HTTP retry helpers (now `_shared/channels.py`).

Old import path `kx.automation.channels` (ADR-0001): the code moved to `kx.automation._shared.channels`.
This package re-exports it (private names too, through `__getattr__`)
until nothing imports the old path.
"""

from kx.automation._shared import channels as _moved
from kx.automation._shared.channels import *  # noqa: F403


def __getattr__(name: str) -> object:
    return getattr(_moved, name)
