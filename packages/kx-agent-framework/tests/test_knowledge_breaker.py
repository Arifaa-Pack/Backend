"""D-8: a circuit breaker on the query embedding, so knowledge search stops waiting for a server that is down."""

from __future__ import annotations

from kx.agents.knowledge import KnowledgeBase
from kx.agents.testing import HashEmbedder
from kx.core.fallback import CircuitBreaker


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class _Flaky(HashEmbedder):
    """Counts calls; down until `up` is set."""

    def __init__(self) -> None:
        super().__init__()
        self.calls = 0
        self.up = False

    async def embed_query(self, text: str) -> list[float]:
        self.calls += 1
        if not self.up:
            raise ConnectionError("embeddings container down")
        return await super().embed_query(text)


async def test_breaker_skips_the_embedding_while_open_and_retries_after_cool_down() -> None:
    clock = _Clock()
    embedder = _Flaky()
    breaker = CircuitBreaker(failures=3, window_seconds=60, open_seconds=300, clock=clock)
    kb = KnowledgeBase(None, embedder, query_timeout_seconds=0.05, breaker=breaker)  # type: ignore[arg-type]

    for _ in range(3):  # three failures in the window open the breaker
        assert await kb._query_vector("pet bottles") is None
    assert embedder.calls == 3 and breaker.state == "open"

    for _ in range(10):  # open: straight to the full-text fallback, no call and no 2 s wait
        assert await kb._query_vector("pet bottles") is None
    assert embedder.calls == 3

    clock.now += 301  # half-open: one trial call; it fails, so cool down again
    assert await kb._query_vector("pet bottles") is None
    assert embedder.calls == 4 and breaker.state == "open"
    assert await kb._query_vector("pet bottles") is None and embedder.calls == 4

    clock.now += 301
    embedder.up = True  # the server is back: the trial call succeeds and closes the breaker
    assert await kb._query_vector("pet bottles") is not None
    assert breaker.state == "closed" and embedder.calls == 5
    assert await kb._query_vector("jars") is not None and embedder.calls == 6
