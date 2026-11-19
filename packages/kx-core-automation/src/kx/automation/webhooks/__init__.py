"""Provider webhooks (WhatsApp, Telnyx): verify the request, record it in one transaction, queue
the slow work.

Old import path `kx.automation.webhooks` (ADR-0001): the code moved to `kx.automation.webhooks.router`.
This package re-exports it (private names too, through `__getattr__`)
until nothing imports the old path.
"""

from kx.automation.webhooks import router as _moved
from kx.automation.webhooks.router import *  # noqa: F403


def __getattr__(name: str) -> object:
    return getattr(_moved, name)
