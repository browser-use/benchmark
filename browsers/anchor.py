"""Anchor Browser -- https://anchorbrowser.io

Residential proxy, captcha solver, extra stealth, adblock, popup blocker.
Requires: ANCHORBROWSER_API_KEY env var.
"""

import os
from contextvars import ContextVar

import httpx

from browsers import retry_on_429

_session_id: ContextVar[str | None] = ContextVar("anchor_session_id", default=None)


def current_session_id() -> str | None:
    return _session_id.get()


async def connect() -> str:
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
    session_id = data["data"]["id"]
    _session_id.set(session_id)
    return f"wss://connect.anchorbrowser.io?apiKey={api_key}&sessionId={session_id}"


async def disconnect() -> None:
    session_id = _session_id.get()
    if not session_id:
        return
    try:
        async with httpx.AsyncClient() as client:
            await client.delete(
                f"https://api.anchorbrowser.io/v1/sessions/{session_id}",
                headers={"anchor-api-key": os.environ["ANCHORBROWSER_API_KEY"]},
                timeout=30,
            )
    finally:
        _session_id.set(None)
