"""Hyperbrowser -- https://hyperbrowser.ai

Stealth mode with residential proxy.
Requires: HYPERBROWSER_API_KEY env var.
"""

import os

import httpx

from browsers import retry_on_429

_session_id: str | None = None


async def connect() -> str:
    global _session_id

    async def _create():
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://api.hyperbrowser.ai/api/session",
                headers={"x-api-key": os.environ["HYPERBROWSER_API_KEY"]},
                json={"useStealth": True, "useProxy": True},
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json()

    data = await retry_on_429(_create)
    _session_id = data.get("sessionId") or data.get("id")
    return data["wsEndpoint"]


async def disconnect() -> None:
    global _session_id
    if not _session_id:
        return
    async with httpx.AsyncClient() as client:
        await client.put(
            f"https://api.hyperbrowser.ai/api/session/{_session_id}/stop",
            headers={"x-api-key": os.environ["HYPERBROWSER_API_KEY"]},
            timeout=30,
        )
    _session_id = None
