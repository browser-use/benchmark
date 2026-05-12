"""browser-use cloud browser provider."""

import os

import httpx

from browsers.util import retry_on_429

MAX_CONCURRENT = 200

_session_id: str | None = None


def _api_base() -> str:
    base = os.environ.get("BU_CLOUD_API_BASE", "https://api.browser-use.com").rstrip("/")
    version = os.environ.get("BU_CLOUD_API_VERSION", "v2")
    return f"{base}/api/{version}"


def _api_key() -> str:
    return os.environ.get("BU_CLOUD_API_KEY") or os.environ["BROWSER_USE_API_KEY"]


async def connect() -> str:
    global _session_id

    async def _create():
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{_api_base()}/browsers",
                headers={"X-Browser-Use-API-Key": _api_key()},
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
        resp = await client.patch(
            f"{_api_base()}/browsers/{_session_id}",
            headers={"X-Browser-Use-API-Key": _api_key()},
            json={"action": "stop"},
            timeout=30,
        )
        resp.raise_for_status()
    _session_id = None
