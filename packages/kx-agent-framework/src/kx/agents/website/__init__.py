"""§6 website conversation API (chat and voice sessions; D39 public-endpoint protection).

Old import path `kx.agents.website` (ADR-0001): the code moved to `kx.agents.website.router`.
This package re-exports it (private names too, through `__getattr__`)
until nothing imports the old path.
"""

from kx.agents.website import router as _moved
from kx.agents.website.router import *  # noqa: F403


def __getattr__(name: str) -> object:
    return getattr(_moved, name)
