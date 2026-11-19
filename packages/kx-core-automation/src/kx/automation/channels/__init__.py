"""Channel adapters (WhatsApp Cloud API, Stalwart + SES email, Telnyx TeXML calls) and their shared HTTP
retry policy.

Retries are for transport problems only (429, 5xx, timeouts, connection errors). A 4xx means the
request itself is wrong; repeating it would only repeat the error, so it fails at once.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

import httpx
from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt, wait_exponential_jitter
from tenacity.wait import wait_base

if TYPE_CHECKING:  # channels.email imports ChannelError from here: no runtime cycle
    from kx.automation.channels.email import Attachment


class ChannelError(Exception):
    """A provider refused or failed a request. `retryable` tells the delivery job whether to try again."""

    def __init__(self, provider: str, detail: str, *, retryable: bool, status: int | None = None) -> None:
        super().__init__(f"{provider}: {detail}")
        self.provider = provider
        self.detail = detail
        self.retryable = retryable
        self.status = status

    def __reduce__(self) -> tuple[Any, tuple[str, str, bool, int | None]]:
        """Picklable (live test 18 Nov): Celery pickles a task's exception; the keyword-only arguments made it
        fail, so the logs showed `UnpickleableExceptionWrapper` instead of the provider and the reason."""
        return _channel_error, (self.provider, self.detail, self.retryable, self.status)


def _channel_error(provider: str, detail: str, retryable: bool, status: int | None) -> ChannelError:
    return ChannelError(provider, detail, retryable=retryable, status=status)


class EmailTransport(Protocol):
    """Anything that sends one email (SES API, Stalwart JMAP, the vendor chain); returns the provider's id."""

    async def send(
        self,
        *,
        to_address: str,
        subject: str,
        body: str,
        attachment: Attachment | None = None,
        in_reply_to: str | None = None,
        list_unsubscribe: str | None = None,
        from_name: str | None = None,
    ) -> str: ...


def is_transient(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        return code == 429 or code >= 500
    return isinstance(exc, httpx.TransportError)  # timeouts, connect/read errors


@dataclass(frozen=True, slots=True)
class HttpRetry:
    attempts: int = 3
    wait: wait_base = field(default_factory=lambda: wait_exponential_jitter(initial=0.5, max=8))

    async def request(
        self, client: httpx.AsyncClient, provider: str, method: str, url: str, **kwargs: Any
    ) -> httpx.Response:
        """Send with retries on transient errors; raise ChannelError with the final outcome."""
        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(self.attempts),
                wait=self.wait,
                retry=retry_if_exception(is_transient),
                reraise=True,
            ):
                with attempt:
                    response = await client.request(method, url, **kwargs)
                    response.raise_for_status()
                    return response
        except httpx.HTTPStatusError as exc:
            raise ChannelError(
                provider,
                _error_detail(exc.response),
                retryable=is_transient(exc),
                status=exc.response.status_code,
            ) from exc
        except httpx.TransportError as exc:
            raise ChannelError(provider, type(exc).__name__, retryable=True) from exc
        raise AssertionError("unreachable")  # pragma: no cover - AsyncRetrying always returns or raises


def _error_detail(response: httpx.Response) -> str:
    """Provider error text without echoing request data (bodies can contain buyer content)."""
    try:
        body = response.json()
    except ValueError:
        return f"HTTP {response.status_code}"
    error = body.get("error", body) if isinstance(body, dict) else {}
    if isinstance(error, dict):
        code = error.get("code", "")
        message = error.get("message", "")
        return f"HTTP {response.status_code} {code} {message}".strip()
    return f"HTTP {response.status_code}"
