"""§8.1 STOP in automation: applying a stop (suppression list) and the one-click unsubscribe page.

Old import path `kx.automation.optout` (ADR-0001): the code moved to `kx.automation.optout.service`.
This package re-exports it (private names too, through `__getattr__`)
until nothing imports the old path.
"""

from kx.automation.optout import service as _moved
from kx.automation.optout.service import *  # noqa: F403


def __getattr__(name: str) -> object:
    return getattr(_moved, name)
