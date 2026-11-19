"""Knowledge base over approved CMS content (products, machines, FAQs, certifications): search
with embeddings and full text.

Old import path `kx.agents.knowledge` (ADR-0001): the code moved to `kx.agents.knowledge.service`.
This package re-exports it (private names too, through `__getattr__`)
until nothing imports the old path.
"""

from kx.agents.knowledge import service as _moved
from kx.agents.knowledge.service import *  # noqa: F403


def __getattr__(name: str) -> object:
    return getattr(_moved, name)
