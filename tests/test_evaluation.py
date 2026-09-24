"""Offline checks with synthetic tasks; never publish decrypted benchmark text."""

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from browser_use.filesystem.file_system import FileSystem
from browser_use.llm.messages import ContentPartImageParam

import run_batch
import run_eval
from evaluation import (
    FILES_MAX_CHARS,
    create_judge,
    judge_trace,
    load_tasks,
    select_screenshots,
    validate_findings_task,
)
from run_eval import collect_output_files, parse_args, run_task, summarize_results


def task():
    return {
        "task_id": "synthetic-001",
        "confirmed_task": "Collect two values and save a report.",
        "rubric": "A1: First value is correct. A2: Second value is correct.",
        "weights": {"A1": 70, "A2": 30},
        "canary": "synthetic-secret-canary",
    }


def findings(**changes):
    payload = {
        "agent_task_reading": "Collect the requested values.",
        "findings": [
            {"item": "A1", "evidence": "Observed first value", "status": "met"},
            {"item": "A2", "evidence": "Second value differs", "status": "violated"},
        ],
        "observations": [],
        "infra_error": False,
        "pii_present": False,
        "reward_hacking_suspected": False,
        "flag_notes": None,
        **changes,
    }
    for finding in payload["findings"]:
        finding.setdefault("not_assessable_reason", None)
    return payload


def trace():
    return {
        "final_result": "Report saved",
        "agent_steps": ["Read source data"],
        "screenshots_b64": ["ZmFrZQ=="],
        "output_files_text": "report.csv\nvalue\n42",
    }


def judge(payload=None, stop_reason="stop"):
    async def invoke(messages, output_format):
        return SimpleNamespace(
            completion=output_format.model_validate(payload or findings()),
            stop_reason=stop_reason,
        )

    return SimpleNamespace(
        model="test-judge",
        reasoning_effort="xhigh",
        ainvoke=AsyncMock(side_effect=invoke),
    )


class DatasetTests(unittest.TestCase):
    def test_default_dataset_and_cli_are_v2(self):
        self.assertEqual(parse_args([]).benchmark, "BU_Bench_V2")
        self.assertEqual(parse_args([]).judge_reasoning, "xhigh")
        tasks = load_tasks()
        self.assertEqual(len(tasks), 200)
        self.assertEqual(len({t["task_id"] for t in tasks}), 200)
        for item in tasks:
            self.assertTrue(item["confirmed_task"])
            validate_findings_task(item)

    def test_legacy_datasets_still_load(self):
        self.assertEqual(len(load_tasks("BU_Bench_V1")), 100)
        self.assertEqual(len(load_tasks("Stealth_Bench_V1")), 80)

    def test_bad_metadata_is_rejected(self):
        for changes in (
            {"rubric": ""},
            {"weights": {}},
            {"weights": {"A1": 99}},
            {"weights": {"A1": True, "A2": 99}},
            {"weights": {"A1": -1, "A2": 101}},
            {"weights": {"unknown": 100}},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_findings_task({**task(), **changes})

    def test_missing_credentials_fail_before_execution(self):
        with (
            patch.dict(os.environ, {}, clear=True),
            self.assertRaisesRegex(ValueError, "OPENAI_API_KEY"),
        ):
            create_judge()

    def test_sampling_keeps_first_last_and_chronological_order(self):
        images = [str(i) for i in range(101)]
        sampled = select_screenshots(images + [images[-1]])
        self.assertEqual(len(sampled), 50)
        self.assertEqual((sampled[0], sampled[-1]), ("0", "100"))
        self.assertEqual(sampled, sorted(sampled, key=int))

    def test_sampling_enforces_byte_budget_and_keeps_boundaries(self):
        sampled = select_screenshots(["a" * 40, "b" * 40, "c" * 40], max_bytes=90)
        self.assertEqual(sampled, ["a" * 40, "c" * 40])
        with self.assertRaisesRegex(ValueError, "first and last"):
            select_screenshots(["a" * 60, "b" * 60], max_bytes=100)


class JudgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_full_rubric_images_files_and_partial_weighted_score(self):
        llm = judge()
        result = await judge_trace(task(), trace(), llm)
        self.assertEqual((result["raw_score"], result["score"]), (0.7, 0.7))
        self.assertFalse(result["verdict"])
        llm.ainvoke.assert_awaited_once()
        messages = llm.ainvoke.call_args.args[0]
        self.assertIn("before browser actions", messages[0].content)
        self.assertNotIn("after browser actions", messages[0].content)
        self.assertIn("not trajectory step numbers", messages[0].content)
        self.assertNotIn("Labels tie each image to a step", messages[0].content)
        self.assertIn("no exact step mapping", messages[1].content[0].text)
        content = messages[1].content
        self.assertIn(task()["rubric"], content[0].text)
        self.assertIn(trace()["output_files_text"], content[0].text)
        self.assertNotIn("70", content[0].text)  # weights stay outside the LLM
        self.assertTrue(
            any(isinstance(part, ContentPartImageParam) for part in content)
        )

    async def test_global_penalty_preserves_raw_score(self):
        result = await judge_trace(
            task(), trace(), judge(findings(reward_hacking_suspected=True))
        )
        self.assertEqual((result["raw_score"], result["score"]), (0.7, 0.0))
        self.assertTrue(result["rh_zeroed"])

    async def test_canary_checked_in_every_evidence_text(self):
        for field in ("final_result", "agent_steps", "output_files_text"):
            data = trace()
            data[field] = (
                [task()["canary"]] if field == "agent_steps" else task()["canary"]
            )
            result = await judge_trace(task(), data, judge())
            self.assertEqual(result["score"], 0)
            self.assertTrue(result["canary_leak"])

    async def test_missing_or_duplicate_items_fail_closed_before_scoring(self):
        baseline = findings()["findings"]
        for entries in (baseline[:1], baseline + [baseline[0]]):
            payload = findings(findings=entries)
            with self.subTest(entries=len(entries)), self.assertRaisesRegex(ValueError, "exactly one"):
                await judge_trace(task(), trace(), judge(payload))

    async def test_not_assessable_is_unscored_with_diagnostic_credit(self):
        payload = findings(
            findings=[
                {
                    "item": "A1",
                    "evidence": "File section is clipped",
                    "status": "not_assessable",
                    "not_assessable_reason": "missing_evidence",
                },
                {
                    "item": "A2",
                    "evidence": "Observed second value is correct",
                    "status": "met",
                },
            ]
        )
        result = await judge_trace(task(), trace(), judge(payload))
        self.assertIsNone(result["score"])
        self.assertIsNone(result["raw_score"])
        self.assertEqual(result["diagnostic_raw_score"], 0.3)
        self.assertEqual(result["evidence_incomplete_items"], ["A1"])

    async def test_complementary_rubric_branches_remain_scored(self):
        synthetic = {
            **task(),
            "rubric": (
                "A1: Captured data is correct. A2: An evidenced access log is correct. "
                "If data is captured, A2 is not_assessable absent_scope. "
                "If access is blocked, A1 is not_assessable absent_scope."
            ),
        }
        for active_item, expected in (("A1", 0.7), ("A2", 0.3)):
            payload = findings(
                findings=[
                    {
                        "item": item,
                        "evidence": "Active branch is evidenced" if item == active_item else "Rubric says this branch is absent",
                        "status": "met" if item == active_item else "not_assessable",
                        "not_assessable_reason": None if item == active_item else "absent_scope",
                    }
                    for item in ("A1", "A2")
                ]
            )
            with self.subTest(active_item=active_item):
                result = await judge_trace(synthetic, trace(), judge(payload))
                self.assertEqual(result["score"], expected)
                self.assertEqual(result["raw_score"], expected)
                self.assertFalse(result.get("evidence_incomplete", False))

    async def test_missing_agent_work_does_not_become_collector_failure(self):
        payload = findings(
            findings=[
                {"item": "A1", "evidence": "The required file was never produced", "status": "violated"},
                {
                    "item": "A2",
                    "evidence": "The rubric assigns absent_scope to fidelity over an absent file",
                    "status": "not_assessable",
                    "not_assessable_reason": "absent_scope",
                },
            ]
        )
        result = await judge_trace(task(), {**trace(), "output_files_text": ""}, judge(payload))
        self.assertEqual((result["score"], result["raw_score"]), (0.0, 0.0))
        self.assertFalse(result.get("evidence_incomplete", False))

    async def test_assessment_reason_is_required_and_matches_status(self):
        for status, reason in (
            ("met", "missing_evidence"),
            ("violated", "absent_scope"),
            ("not_assessable", None),
        ):
            payload = findings()
            payload["findings"][0].update(status=status, not_assessable_reason=reason)
            with self.subTest(status=status, reason=reason), self.assertRaises(ValueError):
                await judge_trace(task(), trace(), judge(payload))
        payload = findings()
        del payload["findings"][0]["not_assessable_reason"]
        with self.assertRaises(ValueError):
            await judge_trace(task(), trace(), judge(payload))

    async def test_hard_truncation_is_rejected_before_judge_call(self):
        data = trace()
        data["output_files_text"] = "x" * (FILES_MAX_CHARS + 1)
        llm = judge()
        result = await judge_trace(task(), data, llm)
        self.assertIsNone(result["score"])
        self.assertEqual(result["evidence_incomplete_sections"], ["files"])
        llm.ainvoke.assert_not_awaited()

    async def test_trajectory_truncation_is_unscored_but_diagnostic_is_preserved(self):
        data = trace()
        data["agent_steps"] = ["x" * 700001]
        result = await judge_trace(task(), data, judge())
        self.assertIsNone(result["score"])
        self.assertEqual(result["evidence_incomplete_sections"], ["trajectory"])
        self.assertEqual(result["diagnostic_raw_score"], 0.7)

    async def test_unknown_item_fails_schema(self):
        payload = findings(
            findings=[{"item": "invented", "evidence": "None", "status": "met"}]
        )
        with self.assertRaises(ValueError):
            await judge_trace(task(), trace(), judge(payload))

    async def test_incomplete_response_is_not_scored(self):
        with self.assertRaisesRegex(ValueError, "did not finish"):
            await judge_trace(task(), trace(), judge(stop_reason="length"))

    async def test_legacy_judge_uses_binary_schema(self):
        result = await judge_trace(
            task(),
            trace(),
            judge({"verdict": True}, "FinishReason.STOP"),
            "BU_Bench_V1",
        )
        self.assertEqual(result["score"], 1)
        self.assertNotIn("rh_zeroed", result)

    async def test_real_sdk_serializes_luna_reasoning_and_strict_schema(self):
        requests = []

        def respond(request):
            requests.append(json.loads(request.content))
            return httpx.Response(
                200,
                json={
                    "id": "test",
                    "object": "chat.completion",
                    "created": 0,
                    "model": "gpt-5.6-luna",
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": "stop",
                            "message": {
                                "role": "assistant",
                                "content": json.dumps(findings()),
                            },
                        }
                    ],
                },
            )

        with patch.dict(os.environ, {"OPENAI_API_KEY": "synthetic-not-a-key"}):
            llm = create_judge()
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            llm.http_client = client
            result = await judge_trace(task(), trace(), llm)
        self.assertEqual(result["score"], 0.7)
        request = requests[0]
        self.assertEqual(request["model"], "gpt-5.6-luna")
        self.assertEqual(request["reasoning_effort"], "xhigh")
        self.assertEqual(request["max_completion_tokens"], 32768)
        self.assertTrue(request["response_format"]["json_schema"]["strict"])
        self.assertNotIn("temperature", request)

    async def test_managed_file_content_is_included(self):
        with tempfile.TemporaryDirectory() as directory:
            fs = FileSystem(directory)
            await fs.write_file("report.csv", "value\n42")
            output = collect_output_files(SimpleNamespace(file_system=fs))
        self.assertIn("report.csv", output)
        self.assertIn("value\n42", output)


class RunnerTests(unittest.IsolatedAsyncioTestCase):
    async def run_fake(self, directory=None, llm=None, timeout=False):
        history = SimpleNamespace(
            number_of_steps=lambda: 2,
            total_duration_seconds=lambda: 3.0,
            usage=SimpleNamespace(total_cost=0.01),
            final_result=lambda: "Done",
            agent_steps=lambda: ["Read source"],
            screenshot_paths=lambda: [],
        )
        agent = SimpleNamespace(
            history=history,
            file_system=SimpleNamespace(list_files=lambda: []),
            run=AsyncMock(
                side_effect=asyncio.TimeoutError if timeout else None,
                return_value=history,
            ),
        )
        browser = SimpleNamespace(stop=AsyncMock())
        provider = SimpleNamespace(disconnect=AsyncMock())
        with (
            patch("run_eval.Agent", return_value=agent) as factory,
            patch("run_eval.create_browser", return_value=browser),
        ):
            result = await run_task(
                task(),
                asyncio.Semaphore(1),
                browser_provider=provider,
                llm=object(),
                judge_llm=llm or judge(),
                run_data_dir=directory,
            )
        browser.stop.assert_awaited_once()
        provider.disconnect.assert_awaited_once()
        self.assertFalse(factory.call_args.kwargs["use_judge"])
        self.assertEqual(factory.call_args.kwargs["task"], task()["confirmed_task"])
        return result

    async def test_run_defaults_to_findings_without_output_directory(self):
        result = await self.run_fake()
        self.assertEqual(result["status"], "judged")
        self.assertEqual(result["score"], 0.7)

    async def test_judge_failure_retains_evidence_and_metrics(self):
        llm = judge()
        llm.ainvoke.side_effect = RuntimeError("synthetic provider outage")
        with tempfile.TemporaryDirectory() as directory:
            result = await self.run_fake(Path(directory), llm)
            artifact = json.loads((Path(directory) / "synthetic-001.json").read_text())
        self.assertEqual(result["status"], "judge_error")
        self.assertIsNone(result["score"])
        self.assertEqual(result["steps"], 2)
        self.assertEqual(artifact["agent_trace"]["final_result"], "Done")
        self.assertEqual(artifact["judge_config"]["type"], "findings")

    async def test_timeout_still_judges_partial_work(self):
        result = await self.run_fake(timeout=True)
        self.assertEqual(result["score"], 0.7)
        self.assertIn("timed out", result["execution_error"])

    async def test_weighted_aggregation_and_unscored_denominator(self):
        result = await self.run_fake()
        summary = summarize_results(
            [result, {**result, "score": 0.3, "raw_score": 0.5}]
        )
        self.assertEqual(summary["mean_score"], 0.5)
        self.assertEqual(summary["mean_raw_score"], 0.6)
        summary = summarize_results(
            [
                result,
                {**result, "score": None, "raw_score": None, "status": "judge_error"},
            ]
        )
        self.assertIsNone(summary["mean_score"])
        self.assertEqual(summary["tasks_unscored"], 1)
        self.assertEqual(summary["mean_score_scored_tasks"], 0.7)

    async def test_cli_persists_config_and_exits_nonzero_for_judge_failure(self):
        result = await self.run_fake()
        for failed in (False, True):
            entry = (
                {**result, "score": None, "raw_score": None, "status": "judge_error"}
                if failed
                else result
            )
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "BU_Bench_V2.enc").write_bytes(b"synthetic-encrypted-fixture")
                (root / "run_eval.py").write_text("# synthetic runner")
                with (
                    patch("run_eval.__file__", str(root / "run_eval.py")),
                    patch(
                        "run_eval.parse_args", return_value=parse_args(["--tasks", "1"])
                    ),
                    patch("run_eval.load_tasks", return_value=[task()]),
                    patch("run_eval.create_judge", return_value=judge()),
                    patch("run_eval.create_agent_model", return_value=judge()),
                    patch("run_eval.run_task", return_value=entry),
                ):
                    if failed:
                        with self.assertRaises(SystemExit) as raised:
                            await run_eval.main()
                        self.assertEqual(raised.exception.code, 1)
                    else:
                        await run_eval.main()
                payload = json.loads(
                    next((root / "results").glob("*.json")).read_text()
                )[0]
                self.assertEqual(payload["config"]["benchmark"], "BU_Bench_V2")
                self.assertEqual(payload["config"]["judge"]["type"], "findings")
                self.assertEqual(payload["mean_score"], None if failed else 0.7)

    async def test_legacy_batch_explicitly_selects_v1(self):
        entry = {"task_id": "synthetic", "score": 1}
        with (
            patch("run_batch.load_tasks", return_value=[task()] * 100) as loader,
            patch.dict(run_batch.MODELS, {"synthetic": lambda: object()}),
            patch("run_batch.run_task", return_value=entry) as runner,
        ):
            await run_batch.run_batch("synthetic", 0, 1)
        loader.assert_called_once_with("BU_Bench_V1")
        self.assertEqual(runner.call_args.kwargs["benchmark"], "BU_Bench_V1")


if __name__ == "__main__":
    unittest.main()
