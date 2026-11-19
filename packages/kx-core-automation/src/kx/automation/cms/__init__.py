"""§7 / D22: the CMS tells the platform what was published or unpublished (signed webhook).

Old import path `kx.automation.cms` (ADR-0001): the code moved to `kx.automation.cms.service`.
This package re-exports it (private names too, through `__getattr__`)
until nothing imports the old path.
"""

from kx.automation.cms import service as _moved
from kx.automation.cms.service import *  # noqa: F403


def __getattr__(name: str) -> object:
    return getattr(_moved, name)
