"""OnKernel -- https://onkernel.com

Stealth mode (includes proxy and reCAPTCHA solver).
Requires: ONKERNEL_API_KEY env var.
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
                "https://api.onkernel.com/browsers",
                headers={"Authorization": f"Bearer {os.environ['ONKERNEL_API_KEY']}"},
                json={"stealth": True},
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json()

    data = await retry_on_429(_create)
    _session_id = data.get("id")
    return data["cdp_ws_url"]


async def disconnect() -> None:
    global _session_id
    if not _session_id:
        return
    async with httpx.AsyncClient() as client:
        await client.delete(
            f"https://api.onkernel.com/browsers/{_session_id}",
            headers={"Authorization": f"Bearer {os.environ['ONKERNEL_API_KEY']}"},
            timeout=30,
        )
    _session_id = None
