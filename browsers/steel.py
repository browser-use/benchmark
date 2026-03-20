"""Steel -- https://steel.dev

Residential proxy and captcha solving.
Requires: STEEL_API_KEY env var.

Note: Steel's WebSocket host does not support IPv6. The monkey-patch below
forces IPv4 resolution for connect.steel.dev to avoid 502 errors.
"""

import os
import socket

import httpx

from browsers import retry_on_429

_session_id: str | None = None

_original_getaddrinfo = socket.getaddrinfo


def _getaddrinfo_ipv4_for_steel(host, port, family=0, *args, **kwargs):
    if host == "connect.steel.dev" and family == 0:
        family = socket.AF_INET
    return _original_getaddrinfo(host, port, family, *args, **kwargs)


socket.getaddrinfo = _getaddrinfo_ipv4_for_steel


async def connect() -> str:
    global _session_id
    api_key = os.environ["STEEL_API_KEY"]

    async def _create():
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://api.steel.dev/v1/sessions",
                headers={"steel-api-key": api_key},
                json={"useProxy": True, "solveCaptcha": True},
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json()

    data = await retry_on_429(_create)
    _session_id = data.get("id")
    return f"{data['websocketUrl']}&apiKey={api_key}"


async def disconnect() -> None:
    global _session_id
    if not _session_id:
        return
    async with httpx.AsyncClient() as client:
        await client.delete(
            f"https://api.steel.dev/v1/sessions/{_session_id}",
            headers={"steel-api-key": os.environ["STEEL_API_KEY"]},
            timeout=30,
        )
    _session_id = None
