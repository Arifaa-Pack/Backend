"""Daily raw-material price checks (T12): results stored as observations and announced to the
CRM as price proposals.

Old import path `kx.automation.scraping` (ADR-0001): the code moved to `kx.automation.scraping.service`.
This package re-exports it (private names too, through `__getattr__`)
until nothing imports the old path.
"""

from kx.automation.scraping import service as _moved
from kx.automation.scraping.service import *  # noqa: F403


def __getattr__(name: str) -> object:
    return getattr(_moved, name)
