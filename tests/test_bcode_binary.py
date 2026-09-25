"""Optional released-binary smoke test; all model requests terminate locally."""

import asyncio
import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import httpx

from bcode_runner import agent_env, preflight, stop_process


@unittest.skipUnless(
    os.environ.get("BCODE_TEST_BIN") and os.environ.get("BCODE_TEST_CATALOG"),
    "set BCODE_TEST_BIN and BCODE_TEST_CATALOG for the released-binary wire test",
)
class ReleasedBinaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_large_catalog_preflight_resolves_model_on_fresh_install(self):
        catalog = json.loads(Path(os.environ["BCODE_TEST_CATALOG"]).read_text())
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json=catalog)
            )
        ) as client:
            with (
                tempfile.TemporaryDirectory() as temporary,
                patch.dict(
                    os.environ,
                    {
                        "OPENAI_API_KEY": "synthetic-catalog",
                        "BROWSER_USE_API_KEY": "synthetic-unused",
                    },
                ),
            ):
                results = await asyncio.gather(
                    *(
                        preflight(
                            os.environ["BCODE_TEST_BIN"],
                            "0.1.20",
                            "openai/gpt-6-luna",
                            "low",
                            Path(temporary) / str(i),
                            catalog_client=client,
                        )
                        for i in range(4)
                    )
                )
                for result in results:
                    self.assertEqual(result["resolved_model"]["id"], "gpt-6-luna")
                    self.assertTrue(
                        result["resolved_model"]["capabilities"]["attachment"]
                    )

    async def test_native_openai_request_uses_requested_model_and_xhigh_reasoning(self):
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                requests.append({"path": self.path, "body": body})
                data = json.dumps(
                    {
                        "error": {
                            "message": "Synthetic endpoint: no model execution",
                            "type": "invalid_request_error",
                        }
                    }
                ).encode()
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with (
                tempfile.TemporaryDirectory() as temporary,
                patch.dict(
                    os.environ,
                    {
                        "OPENAI_API_KEY": "synthetic-wire-only",
                        "BROWSER_USE_API_KEY": "synthetic-unused",
                        "OPENAI_BASE_URL": f"http://127.0.0.1:{server.server_port}/v1",
                    },
                ),
            ):
                root = Path(temporary)
                env = agent_env(
                    "openai/gpt-6-luna",
                    "xhigh",
                    root / "state",
                    Path(os.environ["BCODE_TEST_CATALOG"]).resolve(),
                )
                env["PWD"] = str(root)
                proc = await asyncio.create_subprocess_exec(
                    os.environ["BCODE_TEST_BIN"],
                    "--pure",
                    "run",
                    "--model",
                    "openai/gpt-6-luna",
                    "--variant",
                    "xhigh",
                    "--format",
                    "json",
                    "--thinking",
                    "--dangerously-skip-permissions",
                    "--",
                    "Reply synthetic without calling tools.",
                    cwd=root,
                    env=env,
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    start_new_session=True,
                )
                try:
                    _, stderr = await asyncio.wait_for(proc.communicate(), 60)
                finally:
                    await stop_process(proc)
                actual = [r for r in requests if r["body"].get("model") == "gpt-6-luna"]
                self.assertTrue(actual, stderr.decode(errors="replace")[-1000:])
                self.assertEqual(actual[0]["path"], "/v1/responses")
                self.assertEqual(actual[0]["body"]["reasoning"]["effort"], "xhigh")
                self.assertIn(
                    "browser_execute",
                    [tool["name"] for tool in actual[0]["body"]["tools"]],
                )
        finally:
            await asyncio.to_thread(server.shutdown)
            server.server_close()
            thread.join()
