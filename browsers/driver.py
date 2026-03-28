import os

import httpx

from browsers import retry_on_429

_sessions: list[str] = []

CDP_PROXY_URL = os.environ.get(
    "CDP_PROXY_URL", "https://bu-compat.driver.dev"
).rstrip("/")


async def connect() -> str:
    async def _create():
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{CDP_PROXY_URL}/v1/proxy/session",
                headers={"Authorization": f"Bearer {os.environ['DRIVER_API_KEY']}"},
                json={"captchaSolver": True, "type": "hosted", "country": "CA"},
                timeout=60,
            )
            resp.raise_for_status()
            return resp.json()

    data = await retry_on_429(_create)
    _sessions.append(data["data"]["sessionId"])
    return data["data"]["cdpUrl"]


async def disconnect() -> None:
    if not _sessions:
        return
    session_id = _sessions.pop()
    try:
        async with httpx.AsyncClient() as client:
            await client.delete(
                f"{CDP_PROXY_URL}/v1/proxy/session/{session_id}",
                headers={"Authorization": f"Bearer {os.environ['DRIVER_API_KEY']}"},
                timeout=30,
            )
    except Exception:
        pass  # Best effort cleanup
