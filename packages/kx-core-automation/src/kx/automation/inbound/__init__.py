"""Recording what providers tell us: inbound messages, delivery statuses, bounces and complaints.

Old import path `kx.automation.inbound` (ADR-0001): the code moved to `kx.automation.inbound.service`.
This package re-exports it (private names too, through `__getattr__`)
until nothing imports the old path.
"""

from kx.automation.inbound import service as _moved
from kx.automation.inbound.service import *  # noqa: F403


def __getattr__(name: str) -> object:
    return getattr(_moved, name)
