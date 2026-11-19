"""Staff alerts: the §16 reminder ladder (approvals and change requests) and the below-minimum
alert, in alert hours 08:00-22:00 Asia/Dubai.

Old import path `kx.automation.alerts` (ADR-0001): the code moved to `kx.automation.alerts.service`.
This package re-exports it (private names too, through `__getattr__`)
until nothing imports the old path.
"""

from kx.automation.alerts import service as _moved
from kx.automation.alerts.service import *  # noqa: F403


def __getattr__(name: str) -> object:
    return getattr(_moved, name)
