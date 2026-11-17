"""String browser provider: connect URL, handshake auth and registry wiring."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlsplit

from browsers import BROWSERS, PROVIDERS, get_provider
from browsers import string as string_provider
from run_eval import create_browser


class StringProviderTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        env = patch.dict("os.environ", {"STRING_API_KEY": "test-key"})
        env.start()
        self.addCleanup(env.stop)

    async def test_connects_with_residential_proxy_and_solver(self):
        url = urlsplit(await string_provider.connect())
        self.assertEqual((url.scheme, url.netloc, url.path), ("wss", "request.usestring.ai", "/v1/wss"))
        self.assertEqual(parse_qs(url.query), {"proxy": ["premium"], "solveCaptcha": ["true"]})

    async def test_key_travels_in_header_not_url(self):
        url = await string_provider.connect()
        self.assertNotIn("test-key", url)
        self.assertEqual(string_provider.headers(), {"Authorization": "Bearer test-key"})

    async def test_endpoint_override_keeps_its_query(self):
        with patch.dict("os.environ", {"STRING_CDP_URL": "ws://localhost:7654/v1/wss?record=true"}):
            url = urlsplit(await string_provider.connect())
        self.assertEqual(url.netloc, "localhost:7654")
        self.assertEqual(
            parse_qs(url.query),
            {"record": ["true"], "proxy": ["premium"], "solveCaptcha": ["true"]},
        )

    async def test_missing_key_fails_before_browser_creation(self):
        with (
            patch.dict("os.environ", {}, clear=True),
            patch("run_eval.Browser") as browser,
            self.assertRaises(KeyError),
        ):
            await create_browser(string_provider)
        browser.assert_not_called()

    async def test_disconnect_is_a_no_op(self):
        self.assertIsNone(await string_provider.disconnect())

    async def test_create_browser_sends_provider_headers(self):
        with patch("run_eval.Browser") as browser:
            await create_browser(string_provider)
        browser.assert_called_once_with(
            cdp_url="wss://request.usestring.ai/v1/wss?proxy=premium&solveCaptcha=true",
            headers={"Authorization": "Bearer test-key"},
        )

    async def test_create_browser_without_provider_headers(self):
        provider = SimpleNamespace(connect=AsyncMock(return_value="wss://example.test/cdp"))
        with patch("run_eval.Browser") as browser:
            await create_browser(provider)
        browser.assert_called_once_with(cdp_url="wss://example.test/cdp", headers=None)

    def test_registered(self):
        self.assertIn("string", PROVIDERS)
        self.assertIs(BROWSERS["string"], string_provider)
        self.assertIs(get_provider("string"), string_provider)


if __name__ == "__main__":
    unittest.main()
