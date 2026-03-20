"""Anchor Browser -- https://anchorbrowser.io

Residential proxy, captcha solver, extra stealth, adblock, popup blocker.
Requires: ANCHORBROWSER_API_KEY env var.
"""

import os

import httpx

from browsers import retry_on_429

_session_id: str | None = None


async def connect() -> str:
    global _session_id
    api_key = os.environ["ANCHORBROWSER_API_KEY"]

    async def _create():
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://api.anchorbrowser.io/v1/sessions",
                headers={"anchor-api-key": api_key},
                json={
                    "session": {
                        "proxy": {"type": "anchor_residential", "active": True}
                    },
                    "browser": {
                        "adblock": {"active": True},
                        "popup_blocker": {"active": True},
                        "captcha_solver": {"active": True},
                        "extra_stealth": {"active": True},
                        "force_popups_as_tabs": {"active": True},
                    },
                },
                timeout=180,
            )
            resp.raise_for_status()
            return resp.json()

    data = await retry_on_429(_create)
    _session_id = data["data"]["id"]
    return f"wss://connect.anchorbrowser.io?apiKey={api_key}&sessionId={_session_id}"


async def disconnect() -> None:
    global _session_id
    if not _session_id:
        return
    async with httpx.AsyncClient() as client:
        await client.delete(
            f"https://api.anchorbrowser.io/v1/sessions/{_session_id}",
            headers={"anchor-api-key": os.environ["ANCHORBROWSER_API_KEY"]},
            timeout=30,
        )
    _session_id = None
