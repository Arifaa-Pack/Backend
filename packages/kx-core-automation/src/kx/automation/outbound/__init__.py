"""OutboundService, the single way out for every message (opt-out, contact hours, rate limits,
kill switch, send grants), with its helpers.

Old import path `kx.automation.outbound` (ADR-0001): the code moved to `kx.automation.outbound.service`.
This package re-exports it (private names too, through `__getattr__`)
until nothing imports the old path.
"""

from kx.automation.outbound import service as _moved
from kx.automation.outbound.service import *  # noqa: F403


def __getattr__(name: str) -> object:
    return getattr(_moved, name)
