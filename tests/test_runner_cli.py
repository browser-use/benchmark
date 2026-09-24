"""Executor and run contract regression checks."""

import unittest
from unittest.mock import patch
from run_eval import create_agent_model, create_judge, parse_args, select_tasks


class RunnerConfigurationTests(unittest.TestCase):
    def test_executor_choice_does_not_override_judge(self):
        args = parse_args(
            [
                "--model",
                "gpt-6-astra",
                "--agent-reasoning",
                "xhigh",
                "--max-steps",
                "200",
                "--task-timeout",
                "3600",
                "--task-ids",
                "bu2-171",
                "bu2-185",
            ]
        )
        self.assertEqual(args.model, "gpt-6-astra")
        self.assertIsNone(args.judge_model)
        self.assertEqual(args.max_steps, 200)
        self.assertEqual(args.task_ids, ["bu2-171", "bu2-185"])
        with patch.dict("os.environ", {"OPENAI_API_KEY": "synthetic"}):
            llm = create_agent_model(args.model, args.agent_reasoning)
            judge = create_judge(args.benchmark, args.judge_model, args.judge_reasoning)
        self.assertEqual(llm.model, "gpt-6-astra")
        self.assertEqual(judge.model, "gpt-5.6-luna")
        self.assertEqual(judge.reasoning_effort, "xhigh")
        self.assertEqual(llm.reasoning_effort, "xhigh")
        self.assertEqual(llm.max_completion_tokens, 32768)

    def test_task_selection_preserves_explicit_order_and_rejects_errors(self):
        tasks = [{"task_id": "a"}, {"task_id": "b"}]
        self.assertEqual(select_tasks(tasks, ["b", "a"], 1), [{"task_id": "b"}])
        for ids in (["missing"], ["a", "a"]):
            with self.assertRaises(ValueError):
                select_tasks(tasks, ids)

    def test_openai_executor_requires_credentials(self):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(ValueError, "OPENAI_API_KEY"):
                create_agent_model("gpt-5.6-luna")
