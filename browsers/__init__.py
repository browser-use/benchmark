"""Browser provider registry.

Each provider module exports:
    async def connect() -> str    -- returns a CDP WebSocket URL
    async def disconnect() -> None -- cleans up the session

Usage:
    from browsers import get_provider
    provider = get_provider("anchor")
    cdp_url = await provider.connect()
    ...
    await provider.disconnect()
"""

import asyncio
import importlib

import httpx

PROVIDERS = [
    "anchor",
    "browserbase",
    "browserless",
    "driver",
    "hyperbrowser",
    "local_headful",
    "local_headless",
    "onkernel",
    "rebrowser",
    "steel",
]


def get_provider(name: str):
    """Import and return a browser provider module by name."""
    if name not in PROVIDERS:
        raise ValueError(f"Unknown browser provider: {name}. Available: {PROVIDERS}")
    return importlib.import_module(f"browsers.{name}")


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
