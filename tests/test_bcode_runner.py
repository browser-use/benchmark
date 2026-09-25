"""Offline integration through real subprocesses, CDP messages and Cloud HTTP."""

import asyncio
import base64
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import websockets

import bcode_eval
import bcode_runner
import run_eval
from bcode_runner import execute, preflight, resolve_model
from evaluation import load_tasks

# Synthetic image bytes: the capture test checks transport/ownership, not rendering.
IMAGE = base64.b64encode(b"synthetic image").decode()
FAKE_BINARY = """#!{python}
import json, os, sys, time
from pathlib import Path
if '--version' in sys.argv:
    print('0.1.20')
    raise SystemExit
if 'models' in sys.argv:
    print('openai/gpt-6-luna')
    print(json.dumps({{'variants': {{'low': {{'reasoningEffort': 'low'}}, 'xhigh': {{'reasoningEffort': 'xhigh'}}}}, 'capabilities': {{'attachment': True, 'input': {{'image': True}}}}, 'limit': {{'context': 100000, 'output': 10000}}}}))
    raise SystemExit
label = sys.argv[-1].split('Task: ')[-1]
Path('outputs/same.csv').write_text('label,value\\n' + label + ',42')
Path('arguments.json').write_text(json.dumps({{'args': sys.argv[1:], 'cwd': os.getcwd(), 'pwd': os.environ['PWD'], 'judge_key': os.environ.get('OPENROUTER_API_KEY'), 'config': json.loads(os.environ['OPENCODE_CONFIG_CONTENT'])}}))
print(json.dumps({{'type': 'tool_use', 'part': {{'tool': 'browser_execute', 'state': {{'status': 'completed', 'input': {{'code': 'read source'}}, 'output': 'observed ' + label}}}}}}), flush=True)
time.sleep(5 if label == 'hang' else 0.15)
print(json.dumps({{'type': 'reasoning', 'part': {{'text': 'verified evidence ' + label}}}}), flush=True)
print(json.dumps({{'type': 'text', 'part': {{'text': 'done ' + label}}}}), flush=True)
print(json.dumps({{'type': 'step_finish', 'part': {{'cost': 0.012}}}}), flush=True)
"""


class ConfigurationTests(unittest.TestCase):
    def test_defaults_select_all_200_and_only_bcode_findings_path(self):
        args = run_eval.parse_args([])
        self.assertEqual(
            len(run_eval.select_tasks(load_tasks(), args.task_ids, args.tasks)), 200
        )
        self.assertEqual(
            (args.model, args.agent_reasoning, args.judge_reasoning),
            ("openai/gpt-6-luna", "xhigh", "xhigh"),
        )
        self.assertEqual(
            (args.bcode_version, args.task_timeout, args.concurrency),
            ("0.1.20", 3600, 100),
        )
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            run_eval.parse_args(["--framework", "browser-use"])

    def test_local_uses_bcode_without_fetch_by_default(self):
        args = run_eval.parse_args(["--browser", "local_headless"])
        self.assertEqual(args.executor, "bcode")
        self.assertFalse(args.fetch_use)
        self.assertTrue(
            run_eval.parse_args(
                ["--browser", "local_headless", "--fetch-use"]
            ).fetch_use
        )

    def test_public_results_exclude_private_evidence(self):
        from bcode_results import public_result

        self.assertEqual(
            public_result(
                {
                    "task_id": "bu2-001",
                    "score": 0.5,
                    "error": "private task",
                    "findings": "private rubric",
                    "traceback": "private",
                }
            ),
            {"task_id": "bu2-001", "score": 0.5},
        )

    def test_stealth_and_v1_keep_their_existing_executor_and_datasets(self):
        for benchmark, count in (("Stealth_Bench_V1", 80), ("BU_Bench_V1", 100)):
            args = run_eval.parse_args(
                ["--benchmark", benchmark, "--browser", "local_headless"]
            )
            self.assertEqual(
                (args.executor, args.model, args.browser),
                ("browser-use", "bu-2-0", "local_headless"),
            )
            self.assertEqual(len(load_tasks(benchmark)), count)
            self.assertEqual(args.concurrency, 3)
        explicit = run_eval.parse_args(["--executor", "browser-use"])
        self.assertEqual(explicit.benchmark, "BU_Bench_V2")
        self.assertEqual(explicit.model, "bu-2-0")

    def test_suffix_is_explicit_and_conflicts_fail(self):
        self.assertEqual(
            resolve_model("openrouter/openai/gpt-6-luna@low"),
            ("openrouter/openai/gpt-6-luna", "low"),
        )
        with self.assertRaisesRegex(ValueError, "Conflicting"):
            resolve_model("openai/gpt-6-luna@low", "high")

    def test_sharding_is_contiguous_and_covers_all_tasks(self):
        tasks = [{"task_id": f"bu2-{i:03d}"} for i in range(1, 201)]
        shards = [run_eval.select_shard(tasks, i, 20) for i in range(20)]
        self.assertEqual(sum(map(len, shards)), 200)
        self.assertEqual(shards[0][0]["task_id"], "bu2-001")
        self.assertEqual(shards[-1][-1]["task_id"], "bu2-200")
        self.assertEqual(
            [item["task_id"] for shard in shards for item in shard],
            [item["task_id"] for item in tasks],
        )
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            run_eval.parse_args(["--shard-index", "0"])

    def test_task_selection_is_exact_and_rejects_duplicates(self):
        tasks = [{"task_id": "a"}, {"task_id": "b"}]
        self.assertEqual(
            run_eval.select_tasks(tasks, ["b", "a"], 1), [{"task_id": "b"}]
        )
        for ids in (["a", "a"], ["missing"]):
            with self.assertRaises(ValueError):
                run_eval.select_tasks(tasks, ids)

    def test_incomplete_runs_do_not_publish_full_mean(self):
        results = [
            {
                "score": 0.7,
                "raw_score": 0.8,
                "status": "judged",
                "steps": 2,
                "duration": 1,
                "cost": 0.1,
            },
            {
                "score": None,
                "raw_score": None,
                "status": "judge_error",
                "steps": 1,
                "duration": 1,
                "cost": 0.1,
            },
        ]
        summary = bcode_eval.summarize_results(results)
        self.assertIsNone(summary["mean_score"])
        self.assertEqual(summary["mean_score_scored_tasks"], 0.7)
        self.assertEqual(summary["tasks_unscored"], 1)


class IntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.binary = self.root / "bcode"
        self.binary.write_text(FAKE_BINARY.format(python=sys.executable))
        self.binary.chmod(0o700)
        self.env = patch.dict(
            os.environ,
            {
                "BROWSER_USE_API_KEY": "synthetic-cloud",
                "OPENAI_API_KEY": "synthetic-agent",
                "OPENROUTER_API_KEY": "must-not-reach-agent",
            },
        )
        self.env.start()
        self.connections = 0

        async def cdp(ws):
            self.connections += 1
            async for raw in ws:
                request = json.loads(raw)
                method = request["method"]
                result = (
                    {"targetInfos": [{"targetId": "page", "type": "page"}]}
                    if method == "Target.getTargets"
                    else (
                        {"sessionId": "attached"}
                        if method == "Target.attachToTarget"
                        else (
                            {"data": IMAGE}
                            if method == "Page.captureScreenshot"
                            else {}
                        )
                    )
                )
                await ws.send(json.dumps({"id": request["id"], "result": result}))

        self.server = await websockets.serve(cdp, "127.0.0.1", 0)
        self.url = f"ws://127.0.0.1:{self.server.sockets[0].getsockname()[1]}"
        self.created = []
        self.stopped = []

        def cloud(request):
            if request.method == "POST":
                identity = str(len(self.created) + 1)
                self.created.append((identity, json.loads(request.content)))
                return httpx.Response(200, json={"id": identity, "cdpUrl": self.url})
            self.stopped.append(request.url.path.split("/")[-1])
            return httpx.Response(200, json={})

        self.client = httpx.AsyncClient(transport=httpx.MockTransport(cloud))
        self.catalog_client = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200, json={"openai": {"models": {"gpt-6-luna": {}}}}
                )
            )
        )

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.catalog_client.aclose()
        self.server.close()
        await self.server.wait_closed()
        self.env.stop()
        self.temporary.cleanup()

    async def test_preflight_verifies_binary_and_model_without_browsers(self):
        result = await preflight(
            self.binary,
            "0.1.20",
            "openai/gpt-6-luna",
            "low",
            self.root / "state",
            catalog_client=self.catalog_client,
        )
        self.assertEqual(result["version"], "0.1.20")
        self.assertEqual(len(result["binary_sha256"]), 64)
        self.assertEqual(self.created, [])
        with self.assertRaisesRegex(ValueError, "installed binary"):
            await preflight(
                self.binary,
                "0.1.13",
                "openai/gpt-6-luna",
                "low",
                self.root / "state",
                catalog_client=self.catalog_client,
            )
        with self.assertRaisesRegex(ValueError, "variant"):
            await preflight(
                self.binary,
                "0.1.20",
                "openai/gpt-6-luna",
                "high",
                self.root / "state",
                catalog_client=self.catalog_client,
            )

    async def test_local_preflight_needs_no_cloud_key(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "synthetic"}, clear=True):
            result = await preflight(
                self.binary,
                "0.1.20",
                "openai/gpt-6-luna",
                "low",
                self.root / "local-state",
                catalog_client=self.catalog_client,
                browser="local_headless",
                fetch_use=False,
            )
            self.assertFalse(result["provider_config"]["experimental"]["fetch_use"])
            with self.assertRaisesRegex(ValueError, "BROWSER_USE_API_KEY"):
                await preflight(
                    self.binary,
                    "0.1.20",
                    "openai/gpt-6-luna",
                    "low",
                    self.root / "fetch-state",
                    browser="local_headless",
                    fetch_use=True,
                )

    @unittest.skipUnless(
        os.getenv("BCODE_TEST_CHROME"),
        "set BCODE_TEST_CHROME for real Chrome transport smoke",
    )
    async def test_real_local_chrome_isolation_capture_and_cleanup(self):
        processes = []
        real_start = bcode_runner.start_local_browser

        async def start(*args, **kwargs):
            proc, cdp = await real_start(*args, **kwargs)
            processes.append(proc)
            return proc, cdp

        with patch.object(bcode_runner, "start_local_browser", side_effect=start):
            results = await asyncio.gather(
                *(
                    execute(
                        label,
                        self.root / label,
                        binary=self.binary,
                        model="openai/gpt-6-luna",
                        effort="low",
                        timeout=30,
                        browser="local_headless",
                        chrome_bin=os.environ["BCODE_TEST_CHROME"],
                        fetch_use=False,
                        cloud_client=self.client,
                    )
                    for label in ("local-alpha", "local-beta")
                )
            )
        self.assertEqual(self.created, [])
        self.assertEqual(len(processes), 2)
        self.assertNotEqual(processes[0].pid, processes[1].pid)
        for result, proc in zip(results, processes):
            self.assertIsNotNone(proc.returncode)
            self.assertEqual(len(result["trace"]["screenshots_b64"]), 1)
            self.assertTrue(
                base64.b64decode(result["trace"]["screenshots_b64"][0]).startswith(
                    b"\x89PNG"
                )
            )
            self.assertEqual(result["trace"]["evidence_errors"], [])

    async def test_concurrent_tasks_keep_files_screenshots_and_browser_ownership(self):
        results = await asyncio.gather(
            *(
                execute(
                    label,
                    self.root / label,
                    binary=self.binary,
                    model="openai/gpt-6-luna",
                    effort="low",
                    timeout=3600,
                    cloud_client=self.client,
                )
                for label in ("alpha", "beta")
            )
        )
        for label, result in zip(("alpha", "beta"), results):
            trace = result["trace"]
            self.assertEqual(trace["final_result"], "done " + label)
            self.assertIn("observed " + label, trace["agent_steps"][0])
            self.assertIn(label + ",42", trace["output_files_text"])
            self.assertEqual(trace["screenshot_steps"], [1])
            self.assertEqual(trace["screenshots_b64"], [IMAGE])
            self.assertEqual(
                (self.root / label / "screenshots/00001-step-1.png").read_bytes(),
                base64.b64decode(IMAGE),
            )
            args = json.loads(
                (self.root / label / "workspace/arguments.json").read_text()
            )
            self.assertEqual(args["cwd"], args["pwd"])
            self.assertIsNone(args["judge_key"])
            self.assertIn("--thinking", args["args"])
            self.assertEqual(args["args"][args["args"].index("--variant") + 1], "low")
            variant = args["config"]["provider"]["openai"]["models"]["gpt-6-luna"][
                "variants"
            ]["low"]
            self.assertTrue(variant["forceReasoning"])
            self.assertEqual(result["metrics"]["cost"], 0.012)
        self.assertEqual(sorted(self.stopped), ["1", "2"])
        self.assertTrue(all(body["timeout"] == 60 for _, body in self.created))

    async def test_timeout_preserves_partial_tool_and_file_evidence(self):
        result = await execute(
            "hang",
            self.root / "hang",
            binary=self.binary,
            model="openai/gpt-6-luna",
            effort="low",
            timeout=0.4,
            cloud_client=self.client,
        )
        self.assertIn("hang,42", result["trace"]["output_files_text"])
        self.assertIn("observed hang", result["trace"]["agent_steps"][0])
        self.assertTrue(
            any("timed out" in error for error in result["execution_errors"])
        )
        self.assertEqual(self.stopped, ["1"])
        self.assertTrue((self.root / "hang/trace.json").exists())

    async def test_cdp_setup_failure_still_stops_its_browser(self):
        with (
            patch.object(
                bcode_runner.ScreenshotRecorder,
                "connect",
                new=AsyncMock(side_effect=RuntimeError("synthetic CDP failure")),
            ),
            self.assertRaisesRegex(RuntimeError, "CDP failure"),
        ):
            await execute(
                "alpha",
                self.root / "bad",
                binary=self.binary,
                model="openai/gpt-6-luna",
                effort="low",
                timeout=2,
                cloud_client=self.client,
            )
        self.assertEqual(self.stopped, ["1"])
        self.assertTrue((self.root / "bad/trace.json").exists())

    async def test_runner_grades_partial_execution_and_retains_judge_artifacts(self):
        from test_evaluation import judge, task

        args = SimpleNamespace(
            bcode_bin=self.binary,
            model="openai/gpt-6-luna",
            agent_reasoning="low",
            task_timeout=0.4,
            catalog_path=None,
        )
        original_execute = execute

        async def local_execute(*pos, **kwargs):
            return await original_execute(*pos, **kwargs, cloud_client=self.client)

        llm = judge()
        with (
            patch.object(bcode_eval, "execute", side_effect=local_execute),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            result = await bcode_eval.run_task(
                {**task(), "confirmed_task": "hang"},
                asyncio.Semaphore(1),
                run_dir=self.root,
                args=args,
                judge_llm=llm,
            )
        self.assertEqual(result["score"], 0.7)
        messages = llm.ainvoke.call_args.args[0]
        self.assertIn("after browser actions", messages[0].content)
        self.assertIn(
            "step 1",
            " ".join(getattr(part, "text", "") for part in messages[1].content),
        )
        self.assertTrue(result["execution_errors"])
        self.assertTrue((self.root / "synthetic-001/judge_input.json").exists())
        self.assertTrue((self.root / "synthetic-001/task.json").exists())
        self.assertEqual(self.stopped, ["1"])

    async def test_cleanup_failure_still_stops_cloud_and_keeps_evidence(self):
        close = bcode_runner.ScreenshotRecorder.close

        async def failed_close(recorder):
            await close(recorder)
            raise RuntimeError("synthetic close failure")

        with patch.object(bcode_runner.ScreenshotRecorder, "close", failed_close):
            result = await execute(
                "alpha",
                self.root / "close-fail",
                binary=self.binary,
                model="openai/gpt-6-luna",
                effort="low",
                timeout=2,
                cloud_client=self.client,
            )
        self.assertEqual(self.stopped, ["1"])
        self.assertEqual(result["trace"]["final_result"], "done alpha")
        self.assertIn("cleanup failed", result["execution_errors"][0])
        self.assertTrue((self.root / "close-fail/trace.json").exists())

    async def test_full_cli_orchestration_records_selection_and_exits_on_unscored(self):
        from test_evaluation import judge, task

        llm = judge()
        llm.client = SimpleNamespace(
            close=AsyncMock(), base_url="https://synthetic.invalid/v1"
        )

        async def local_preflight(*pos, **kwargs):
            return await preflight(*pos, **kwargs, catalog_client=self.catalog_client)

        async def local_execute(*pos, **kwargs):
            self.assertTrue(Path(kwargs["catalog_path"]).is_file())
            return await execute(*pos, **kwargs, cloud_client=self.client)

        tasks = [
            {**task(), "task_id": "bu2-001", "confirmed_task": "alpha"},
            {**task(), "task_id": "bu2-002", "confirmed_task": "beta"},
        ]
        with (
            patch.object(
                bcode_eval,
                "load_tasks",
                return_value=tasks
                + [{**tasks[0], "task_id": f"bu2-{i:03d}"} for i in range(3, 201)],
            ),
            patch.object(bcode_eval, "create_judge", return_value=llm),
            patch.object(bcode_eval, "preflight", side_effect=local_preflight),
            patch.object(bcode_eval, "execute", side_effect=local_execute),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            await run_eval.main(
                [
                    "--bcode-bin",
                    str(self.binary),
                    "--output-dir",
                    str(self.root / "cli"),
                    "--task-ids",
                    "bu2-002",
                ]
            )
            result_path = next((self.root / "cli").glob("*/results.json"))
            result = json.loads(result_path.read_text())
            self.assertEqual(result["config"]["task_ids"], ["bu2-002"])
            self.assertEqual(result["mean_score"], 0.7)
            self.assertEqual(result["config"]["judge"]["type"], "findings")
            llm.ainvoke.side_effect = RuntimeError("synthetic judge failure")
            with self.assertRaises(SystemExit) as failure:
                await run_eval.main(
                    [
                        "--bcode-bin",
                        str(self.binary),
                        "--output-dir",
                        str(self.root / "cli-bad"),
                        "--tasks",
                        "2",
                    ]
                )
            self.assertEqual(failure.exception.code, 1)
            result = json.loads(
                next((self.root / "cli-bad").glob("*/results.json")).read_text()
            )
            self.assertIsNone(result["mean_score"])
            self.assertEqual(result["judge_errors"], 2)
            before = len(self.created)
            await run_eval.main(
                [
                    "--bcode-bin",
                    str(self.binary),
                    "--output-dir",
                    str(self.root / "check"),
                    "--check",
                ]
            )
            self.assertEqual(len(self.created), before)
        self.assertEqual(llm.client.close.await_count, 3)


if __name__ == "__main__":
    unittest.main()


class ProcessCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def test_legacy_framework_entrypoint_directs_v2_to_the_default_runner(self):
        from run_framework_eval import _run_all

        with self.assertRaisesRegex(SystemExit, "Use run_eval.py"):
            await _run_all(SimpleNamespace(benchmark="BU_Bench_V2"))

    async def test_exited_parent_does_not_leave_sigterm_ignoring_child(self):
        script = """import os,signal,time
pid=os.fork()
if pid:
    print(pid,flush=True)
    raise SystemExit
signal.signal(signal.SIGTERM,signal.SIG_IGN)
while True: time.sleep(1)
"""
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            script,
            stdout=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        child = int(await proc.stdout.readline())
        await asyncio.sleep(0.1)
        try:
            await bcode_runner.stop_process(proc)
            # A reaped orphan can briefly remain a zombie under container init.
            for _ in range(100):
                stat = Path(f"/proc/{child}/stat")
                if not stat.exists() or stat.read_text().split()[2] == "Z":
                    break
                await asyncio.sleep(0.01)
            else:
                self.fail("tool child survived process-group cleanup")
        finally:
            with contextlib.suppress(ProcessLookupError):
                os.kill(child, 9)
