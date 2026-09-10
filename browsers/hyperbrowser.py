"""Hyperbrowser -- https://hyperbrowser.ai

Stealth mode with residential proxy.
Requires: HYPERBROWSER_API_KEY env var.
"""

import os
from contextvars import ContextVar

import httpx

from browsers import retry_on_429

_session_id: ContextVar[str | None] = ContextVar(
    "hyperbrowser_session_id", default=None
)


def current_session_id() -> str | None:
    return _session_id.get()


async def connect() -> str:
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
    _session_id.set(data.get("sessionId") or data.get("id"))
    return data["wsEndpoint"]


async def disconnect() -> None:
    session_id = _session_id.get()
    if not session_id:
        return
    try:
        async with httpx.AsyncClient() as client:
            await client.put(
                f"https://api.hyperbrowser.ai/api/session/{session_id}/stop",
                headers={"x-api-key": os.environ["HYPERBROWSER_API_KEY"]},
                timeout=30,
            )
    finally:
        _session_id.set(None)
