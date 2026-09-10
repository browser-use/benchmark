import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import TypeVar

import httpx

T = TypeVar("T")

# Session creation is a non-idempotent POST for these providers. Retry only
# cases where a duplicate server-side session is not expected.
_SAFE_RETRYABLE_STATUS = {425, 429}
_SAFE_RETRYABLE_TRANSPORT = (httpx.ConnectError, httpx.ConnectTimeout)


async def retry_session_create(
    fn: Callable[[], Awaitable[T]],
    *,
    deadline: float = 90.0,
    max_wait: float = 30.0,
) -> T:
    """Call fn(), retrying only safe session-create failures within a deadline."""
    started_at = time.monotonic()
    attempt = 0
    last_error: httpx.HTTPStatusError | httpx.TransportError | None = None

    while True:
        remaining = deadline - (time.monotonic() - started_at)
        if remaining <= 0:
            if last_error is not None:
                print(
                    f"[{type(last_error).__name__}] Session create retry deadline "
                    f"exhausted after {deadline:.1f}s"
                )
                raise last_error
            raise asyncio.TimeoutError(
                f"Session create timed out after {deadline:.1f}s"
            )

        try:
            return await asyncio.wait_for(fn(), timeout=remaining)
        except httpx.HTTPStatusError as e:
            if e.response.status_code not in _SAFE_RETRYABLE_STATUS:
                raise
            last_error = e
            wait = min(
                2**attempt,
                max_wait,
                deadline - (time.monotonic() - started_at),
            )
            if wait <= 0:
                print(
                    f"[http {e.response.status_code}] Session create retry "
                    f"deadline exhausted after {deadline:.1f}s"
                )
                raise
            print(
                f"[http {e.response.status_code}] Safe session-create retry "
                f"{attempt + 1} in {wait:.1f}s"
            )
            await asyncio.sleep(wait)
            attempt += 1
        except _SAFE_RETRYABLE_TRANSPORT as e:
            last_error = e
            wait = min(
                2**attempt,
                max_wait,
                deadline - (time.monotonic() - started_at),
            )
            if wait <= 0:
                print(
                    f"[{type(e).__name__}] Session create retry deadline "
                    f"exhausted after {deadline:.1f}s"
                )
                raise
            print(
                f"[{type(e).__name__}] Safe session-create retry "
                f"{attempt + 1} in {wait:.1f}s"
            )
            await asyncio.sleep(wait)
            attempt += 1
        except asyncio.TimeoutError:
            raise


retry_on_429 = retry_session_create
retry_transient_provider_error = retry_session_create
