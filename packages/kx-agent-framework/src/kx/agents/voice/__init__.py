"""Voice: speech vendor routing by language and the LiveKit Agents worker entrypoint.

Old import path `kx.agents.voice` (ADR-0001): the code moved to `kx.agents.voice.service`.
This package re-exports it (private names too, through `__getattr__`)
until nothing imports the old path.
"""

from kx.agents.voice import service as _moved
from kx.agents.voice.service import *  # noqa: F403


def __getattr__(name: str) -> object:
    return getattr(_moved, name)
