import asyncio

import httpx


async def retry_on_429(fn, max_retries=10, max_wait=30):
    """Call fn(), retrying with capped exponential backoff on 429 responses."""
    for attempt in range(max_retries + 1):
        try:
            return await fn()
        except httpx.HTTPStatusError as e:
            if e.response.status_code != 429 or attempt == max_retries:
                raise
            wait = min(2**attempt, max_wait)
            print(f"[429] Rate limited, retry {attempt + 1}/{max_retries} in {wait}s")
            await asyncio.sleep(wait)
