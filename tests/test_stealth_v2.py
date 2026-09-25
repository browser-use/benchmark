"""Offline protocol checks; never open a browser or call a provider."""
import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    path = ROOT / "stealth_bench" / name
    spec = importlib.util.spec_from_file_location("stealth_" + path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class StealthV2Tests(unittest.TestCase):
    def test_frozen_dataset_is_access_only_and_has_no_query_identifiers(self):
        from urllib.parse import urlsplit
        tasks = load("run.py").load_tasks()
        self.assertEqual(len(tasks), 100)
        self.assertEqual(len({urlsplit(t["website"]).hostname for t in tasks}), 100)
        for task in tasks:
            parts = urlsplit(task["website"])
            self.assertEqual(parts.scheme, "https")
            self.assertFalse(parts.query or parts.fragment or parts.username or parts.password)
            self.assertEqual(task["auth_keys"], [])
            self.assertEqual(task["confirmed_task"],
                f"Open {task['website']} and determine whether genuine usable website content can be accessed "
                "within 60 seconds. A remaining CAPTCHA or bot/security denial prevents access. "
                "Do not sign in, submit forms, buy anything, or use another website.")

    def test_missing_evidence_stays_unresolved_and_in_denominator(self):
        summary = load("run.py").summarize([
            {"classification": "accessible"}, {"classification": "bot_block"},
            {"classification": "uncertain"}, {"classification": "judge_or_capture_error"},
        ])
        self.assertEqual(summary["confirmed_access_percent"], 25)
        self.assertEqual(summary["possible_access_percent"], 75)
        self.assertEqual(summary["unresolved"], 2)
        self.assertEqual(summary["classifications"]["bot_block"], 1)

    def test_judge_input_omits_browser_identity_and_screening_labels(self):
        judge = load("v2/judge.py")
        evidence = {"requested_url": "https://example.com", "browser_mode": "cloud",
                    "provider_configuration": {"stealth": True}, "cloud_session_id": "private",
                    "snapshots": [{"text": "Verify you are human", "screenshot": "access-1.png",
                                   "block_signals": ["candidate"]}]}
        blind = judge.blind_evidence(evidence)
        self.assertEqual(blind, {"requested_url": "https://example.com", "snapshots": [
            {"text": "Verify you are human", "screenshot": "access-1.png"}]})
        self.assertIn("provider_configuration", evidence)

    def test_network_failure_is_not_labeled_captcha(self):
        result = load("v2/judge.py").judgment(
            {"classification": "network_error", "reason": "DNS error"}, "luna", 1)
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["failure_class"], "network_error")
        self.assertEqual(result["score"], 0)

    def test_published_results_cover_the_exact_frozen_task_ids(self):
        directory = ROOT / "stealth_bench/official_results"
        summary = json.loads((directory / "summary.json").read_text())
        rows = [json.loads(line) for line in (directory / "attempts.jsonl").read_text().splitlines()]
        task_ids = {task["task_id"] for task in load("run.py").load_tasks()}
        configurations = {row["configuration"] for row in summary["configurations"]}
        keys = {(row["configuration"], row["repetition"], row["task_id"]) for row in rows}
        expected = {(config, rep, tid) for config in configurations for rep in (1, 2) for tid in task_ids}
        self.assertEqual(len(rows), len(keys))
        self.assertEqual(keys, expected)

    def test_confirmation_preserves_full_coverage_and_retry_lineage(self):
        from collections import Counter

        directory = ROOT / "stealth_bench/official_results/confirmation-20260925"
        summary = json.loads((directory / "summary.json").read_text())
        original_summary = json.loads((directory.parent / "summary.json").read_text())
        original_scores = {r["configuration"]: r for r in original_summary["configurations"]}
        task_ids = {task["task_id"] for task in load("run.py").load_tasks()}

        def read_rows(name):
            return [json.loads(line) for line in (directory / name).read_text().splitlines()]

        def index(rows):
            result = {(r["configuration"], r["task_id"]): r for r in rows}
            self.assertEqual(len(result), len(rows))
            return result

        rows = read_rows("attempts.jsonl")
        originals = index(read_rows("original-attempts.jsonl"))
        retries = index(read_rows("retry-attempts.jsonl"))
        effective = index(rows)
        configurations = {r["configuration"] for r in summary["configurations"]}
        self.assertEqual(len(configurations), 7)
        expected = {(cfg, tid) for cfg in configurations for tid in task_ids}
        self.assertEqual(set(effective), expected)
        self.assertEqual(set(originals), expected)
        self.assertEqual(len(rows), 700)
        self.assertEqual(len(retries), summary["one_time_retries"])
        for key, row in effective.items():
            source = retries.get(key, originals[key])
            self.assertEqual(row["repetition"], 1)
            self.assertEqual(row["classification"], source["classification"])
            self.assertEqual(row["score"], source["score"])
            self.assertEqual(row["retry_used"], key in retries)
            if key in retries:
                self.assertEqual(originals[key]["classification"], "judge_or_capture_error")
        for cfg in summary["configurations"]:
            selected = [r for r in rows if r["configuration"] == cfg["configuration"]]
            counts = Counter(r["classification"] for r in selected)
            self.assertEqual(len(selected), cfg["attempts"])
            self.assertEqual(dict(counts), cfg["classifications"])
            self.assertEqual(counts["accessible"], cfg["confirmed_access_percent"])
            self.assertEqual(cfg["original_two_repeat"]["confirmed_access_percent"],
                             original_scores[cfg["configuration"]]["confirmed_access_percent"])
            self.assertEqual(cfg["possible_access_percent"], cfg["confirmed_access_percent"] + cfg["unresolved"])
