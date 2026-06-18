import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

import httpx

T = TypeVar("T")


def _is_retryable_status(exc: httpx.HTTPStatusError) -> bool:
    return exc.response.status_code in {408, 425, 429, 500, 502, 503, 504}


async def retry_transient_provider_error(
    fn: Callable[[], Awaitable[T]],
    max_retries: int = 10,
    max_wait: int = 30,
) -> T:
    """Call fn(), retrying transient provider API failures with backoff."""
    for attempt in range(max_retries + 1):
        try:
            return await fn()
        except httpx.HTTPStatusError as e:
            if not _is_retryable_status(e) or attempt == max_retries:
                raise
            wait = min(2**attempt, max_wait)
            print(
                f"[http {e.response.status_code}] Transient provider API error, "
                f"retry {attempt + 1}/{max_retries} in {wait}s"
            )
            await asyncio.sleep(wait)
        except (httpx.TimeoutException, httpx.NetworkError) as e:
            if attempt == max_retries:
                raise
            wait = min(2**attempt, max_wait)
            print(
                f"[{type(e).__name__}] Transient provider API error, "
                f"retry {attempt + 1}/{max_retries} in {wait}s"
            )
            await asyncio.sleep(wait)


retry_on_429 = retry_transient_provider_error
