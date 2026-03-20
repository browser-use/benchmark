"""Browserless -- https://browserless.io

Stealth route with residential proxy.
Requires: BROWSERLESS_API_KEY env var.
"""

import os


async def connect() -> str:
    token = os.environ["BROWSERLESS_API_KEY"]
    return (
        f"wss://production-sfo.browserless.io/stealth?token={token}&proxy=residential"
    )


async def disconnect() -> None:
    pass
