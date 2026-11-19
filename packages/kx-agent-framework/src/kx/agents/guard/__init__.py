"""Reply safety gate: every AI sentence that can reach a buyer passes the price guard and the
rules here.

Old import path `kx.agents.guard` (ADR-0001): the code moved to `kx.agents.guard.service`.
This package re-exports it (private names too, through `__getattr__`)
until nothing imports the old path.
"""

from kx.agents.guard import service as _moved
from kx.agents.guard.service import *  # noqa: F403


def __getattr__(name: str) -> object:
    return getattr(_moved, name)
