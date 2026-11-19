"""AI audit trail (D46): Phoenix on our server over OpenTelemetry (OTLP / HTTP).

Old import path `kx.agents.tracing` (ADR-0001): the code moved to `kx.agents.tracing.service`.
This package re-exports it (private names too, through `__getattr__`)
until nothing imports the old path.
"""

from kx.agents.tracing import service as _moved
from kx.agents.tracing.service import *  # noqa: F403


def __getattr__(name: str) -> object:
    return getattr(_moved, name)
