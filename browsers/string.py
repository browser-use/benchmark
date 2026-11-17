"""String -- https://usestring.ai

Residential proxy and captcha solving.
Requires: STRING_API_KEY env var. STRING_CDP_URL optionally overrides the endpoint.

There is no session API: connecting to the CDP WebSocket starts a fresh browser,
and closing the connection (browser.stop()) ends it. The API key is sent as an
Authorization header on the WebSocket handshake rather than in the URL.
"""

import os
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

DEFAULT_CDP_URL = "wss://request.usestring.ai/v1/wss"

# proxy=premium is String's residential pool (also its default).
SETTINGS = {"proxy": "premium", "solveCaptcha": "true"}


def headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {os.environ['STRING_API_KEY']}"}


async def connect() -> str:
    parts = urlsplit(os.environ.get("STRING_CDP_URL") or DEFAULT_CDP_URL)
    # Keep the override's own parameters as given, blank and repeated ones
    # included, and replace only the keys this benchmark sets.
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key not in SETTINGS
    ]
    query.extend(SETTINGS.items())
    return urlunsplit(parts._replace(query=urlencode(query)))


async def disconnect() -> None:
    pass
