"""Conversation memory: a window of recent turns from `agents.turns` plus a rolling summary of
older ones.

Old import path `kx.agents.memory` (ADR-0001): the code moved to `kx.agents.memory.service`.
This package re-exports it (private names too, through `__getattr__`)
until nothing imports the old path.
"""

from kx.agents.memory import service as _moved
from kx.agents.memory.service import *  # noqa: F403


def __getattr__(name: str) -> object:
    return getattr(_moved, name)
