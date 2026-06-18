"""Browserbase -- https://browserbase.com

Proxies and captcha solving enabled.
Requires: BROWSERBASE_API_KEY, BROWSERBASE_PROJECT_ID env vars.
"""

import os
from contextvars import ContextVar

import httpx

from browsers import retry_on_429

_session_id: ContextVar[str | None] = ContextVar("browserbase_session_id", default=None)


def current_session_id() -> str | None:
    return _session_id.get()


async def connect() -> str:
    async def _create():
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://api.browserbase.com/v1/sessions",
                headers={"X-BB-API-Key": os.environ["BROWSERBASE_API_KEY"]},
                json={
                    "projectId": os.environ["BROWSERBASE_PROJECT_ID"],
                    "proxies": True,
                    "browserSettings": {"solveCaptchas": True},
                },
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json()

    data = await retry_on_429(_create)
    _session_id.set(data["id"])
    return data["connectUrl"]


async def disconnect() -> None:
    session_id = _session_id.get()
    if not session_id:
        return
    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                f"https://api.browserbase.com/v1/sessions/{session_id}",
                headers={"X-BB-API-Key": os.environ["BROWSERBASE_API_KEY"]},
                json={"status": "REQUEST_RELEASE"},
                timeout=30,
            )
    finally:
        _session_id.set(None)
