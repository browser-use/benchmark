import unittest
import json
from pathlib import Path

from consumer_bench.score import summarize


class ConsumerScoreTests(unittest.TestCase):
    def setUp(self):
        self.tasks = [{"id": "cart-a", "endpoint": "cart"}, {"id": "public-b", "endpoint": "public_information"}]

    def test_published_summaries_match_the_retained_outcome_rows(self):
        root = Path(__file__).resolve().parents[1] / "consumer_bench"
        tasks = json.loads((root / "dataset-v9.json").read_text())["tasks"]
        data = json.loads((root / "results.json").read_text())
        self.assertEqual(len(tasks), 502)
        self.assertEqual(len(data["dataset"]["removedTasks"]), 11)
        for run in data["runs"]:
            with self.subTest(agent=run["id"]):
                if run["outcomes"] is None:
                    self.assertIsNone(run["summary"])
                else:
                    self.assertEqual(run["summary"], summarize(tasks, run["outcomes"]))
        instinct = next(run for run in data["runs"] if run["id"] == "instinct")
        self.assertFalse(instinct["summary"]["allTasksTerminal"])
        self.assertEqual(instinct["summary"]["all"]["unstarted"], 481)

    def test_exclusions_remove_the_same_task_from_successes_and_denominator(self):
        rows = [{"task_id": "cart-a", "outcome": "failure"}, {"task_id": "public-b", "outcome": "success"}, {"task_id": "removed", "outcome": "success"}]
        score = summarize(self.tasks, rows)
        self.assertEqual(score["all"]["planned"], 2)
        self.assertEqual(score["all"]["successes"], 1)
        self.assertEqual(score["all"]["successRate"], 0.5)
        self.assertEqual(score["excludedResults"], 1)
        self.assertEqual(score["cart"]["successes"], 0)
        self.assertEqual(score["public_information"]["successes"], 1)

    def test_unstarted_and_missing_results_remain_separate_from_failures(self):
        for rows, expected_unstarted, expected_unknown in [([], 0, 2), ([{"task_id": "cart-a", "outcome": "unstarted"}], 1, 1)]:
            with self.subTest(rows=rows):
                score = summarize(self.tasks, rows)
                self.assertEqual(score["all"]["failures"], 0)
                self.assertEqual(score["all"]["unstarted"], expected_unstarted)
                self.assertEqual(score["all"]["unknown"], expected_unknown)
                self.assertFalse(score["allTasksTerminal"])

    def test_duplicate_tasks_or_results_cannot_inflate_the_score(self):
        row = {"task_id": "cart-a", "outcome": "success"}
        for tasks, rows in [(self.tasks + [self.tasks[0]], [row]), (self.tasks, [row, row])]:
            with self.subTest(tasks=tasks, rows=rows):
                with self.assertRaisesRegex(ValueError, "[Dd]uplicate"):
                    summarize(tasks, rows)

    def test_policy_holds_and_recorded_unknowns_remain_terminal_distinct_outcomes(self):
        score = summarize(self.tasks, [{"task_id": "cart-a", "outcome": "policy_hold"}, {"task_id": "public-b", "outcome": "unknown"}])
        self.assertEqual(score["all"]["policyHolds"], 1)
        self.assertEqual(score["all"]["unknown"], 1)
        self.assertEqual(score["all"]["failures"], 0)
        self.assertTrue(score["allTasksTerminal"])
        with self.assertRaisesRegex(ValueError, "Unknown outcome"):
            summarize(self.tasks, [{"task_id": "cart-a", "outcome": "complete"}])


if __name__ == "__main__":
    unittest.main()
