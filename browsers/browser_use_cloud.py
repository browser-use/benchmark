"""browser-use Cloud -- https://browser-use.com

Requires: BROWSER_USE_API_KEY env var.
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
                "https://api.browser-use.com/api/v2/browsers",
                headers={"X-Browser-Use-API-Key": os.environ["BROWSER_USE_API_KEY"]},
                json={},
                timeout=90,
            )
            resp.raise_for_status()
            return resp.json()

    data = await retry_on_429(_create)
    _session_id = data["id"]
    return data["cdpUrl"]


async def disconnect() -> None:
    global _session_id
    if not _session_id:
        return
    async with httpx.AsyncClient() as client:
        await client.patch(
            f"https://api.browser-use.com/api/v2/browsers/{_session_id}",
            headers={"X-Browser-Use-API-Key": os.environ["BROWSER_USE_API_KEY"]},
            json={"action": "stop"},
            timeout=30,
        )
    _session_id = None
