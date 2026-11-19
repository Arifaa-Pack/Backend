"""Agent tools: thin async wrappers over the injected CrmGateway and the knowledge base.

Old import path `kx.agents.tools` (ADR-0001): the code moved to `kx.agents.tools.service`.
This package re-exports it (private names too, through `__getattr__`)
until nothing imports the old path.
"""

from kx.agents.tools import service as _moved
from kx.agents.tools.service import *  # noqa: F403


def __getattr__(name: str) -> object:
    return getattr(_moved, name)
