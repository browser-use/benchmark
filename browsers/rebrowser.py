"""Rebrowser -- https://rebrowser.net

Stealth is always-on. Proxy and captcha solving configured in dashboard.
Requires: REBROWSER_API_KEY env var.
"""

import os


async def connect() -> str:
    return f"wss://ws.rebrowser.net/?apiKey={os.environ['REBROWSER_API_KEY']}"


async def disconnect() -> None:
    pass
