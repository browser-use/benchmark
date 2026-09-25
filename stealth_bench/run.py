# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "cryptography==50.0.0",
#   "httpx==0.28.1",
#   "openai==2.53.0",
#   "playwright==1.63.0",
#   "python-dotenv==1.2.2",
# ]
# ///
"""Reproduce Stealth Bench V2: deterministic access probe, independent visual judge."""
from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import os
import random
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path

from cryptography.fernet import Fernet
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
BROWSERS = {
    "headless": {"browser": "local", "headless": True, "proxy": "none"},
    "headful": {"browser": "local", "headless": False, "proxy": "none"},
    "headless-proxy": {"browser": "local", "headless": True, "proxy": "browser-use"},
    "headful-proxy": {"browser": "local", "headless": False, "proxy": "browser-use"},
    "browser-use": {"browser": "cloud", "solve_captchas": True},
    "browser-use-no-solver": {"browser": "cloud", "solve_captchas": False},
    "browserbase": {"browser": "browserbase", "verified": False},
    "browserbase-verified": {"browser": "browserbase", "verified": True},
    "kernel": {"browser": "kernel"},
    "anchor": {"browser": "anchor"},
    "hyperbrowser": {"browser": "hyperbrowser"},
    "steel": {"browser": "steel"},
    "browserless": {"browser": "browserless", "solve_captchas": True},
}
PROVIDER_KEYS = {
    "local": [], "cloud": ["BROWSER_USE_API_KEY"],
    "browserbase": ["BROWSERBASE_API_KEY", "BROWSERBASE_PROJECT_ID"],
    "kernel": ["ONKERNEL_API_KEY"], "anchor": ["ANCHORBROWSER_API_KEY"],
    "hyperbrowser": ["HYPERBROWSER_API_KEY"], "steel": ["STEEL_API_KEY"],
    "browserless": ["BROWSERLESS_API_KEY"],
}


def load_tasks() -> list[dict]:
    key = base64.urlsafe_b64encode(hashlib.sha256(b"Stealth_Bench_V2").digest())
    ciphertext = base64.b64decode((ROOT / "Stealth_Bench_V2.enc").read_text())
    raw = Fernet(key).decrypt(ciphertext)
    protocol = json.loads((ROOT / "stealth_bench/v2/protocol.json").read_text())
    if hashlib.sha256(raw).hexdigest() != protocol["dataset_sha256"]:
        raise ValueError("Dataset checksum mismatch")
    tasks = json.loads(raw)
    if len(tasks) != 100 or len({t["task_id"] for t in tasks}) != 100:
        raise ValueError("Dataset must contain exactly 100 unique task IDs")
    return tasks


def summarize(rows: list[dict]) -> dict:
    """Keep missing measurements visible; never turn infrastructure gaps into blocks."""
    counts: dict[str, int] = {}
    for row in rows:
        category = row["classification"]
        counts[category] = counts.get(category, 0) + 1
    unresolved = counts.get("uncertain", 0) + counts.get("judge_or_capture_error", 0)
    passed = counts.get("accessible", 0)
    return {
        "attempts": len(rows),
        "confirmed_access": passed,
        "unresolved": unresolved,
        "confirmed_access_percent": 100 * passed / len(rows) if rows else None,
        "possible_access_percent": 100 * (passed + unresolved) / len(rows) if rows else None,
        "classifications": counts,
        "note": "Unresolved attempts have no score; bounds retain the full denominator.",
    }


async def run(args: argparse.Namespace) -> None:
    load_dotenv(ROOT / ".env")
    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY is required for the independent judge")
    options = {**BROWSERS[args.browser], "observation_seconds": 60}
    keys = list(PROVIDER_KEYS[options["browser"]])
    if options.get("proxy") == "browser-use":
        keys.append("BROWSER_USE_API_KEY")
    for key in keys:
        if key != "BROWSERBASE_PROJECT_ID" and not os.environ.get(key):
            raise SystemExit(f"{key} is required")
    tasks = load_tasks()
    if args.task_ids:
        requested = args.task_ids.split(",")
        index = {t["task_id"]: t for t in tasks}
        if len(set(requested)) != len(requested) or set(requested) - index.keys():
            raise SystemExit("Unknown or duplicate task IDs")
        tasks = [index[i] for i in requested]
    output = args.output or ROOT / "run_data/stealth_v2" / (
        args.browser + "-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    )
    output.mkdir(parents=True, exist_ok=False)
    manifest = {
        "browser": args.browser, "options": options, "repetitions": args.repetitions,
        "task_ids": [t["task_id"] for t in tasks], "task_order_seed_base": 20260927,
        "judge": "gpt-5.6-luna",
        "judge_reasoning_effort": "low", "started_at": datetime.now(timezone.utc).isoformat(),
        "source_sha256": {
            name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in ("stealth_bench/v2/probe.py", "stealth_bench/v2/judge.py", "Stealth_Bench_V2.enc")
        },
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    plumbing = {k: v for k, v in os.environ.items() if k in {
        "PATH", "HOME", "TMPDIR", "DISPLAY", "LD_LIBRARY_PATH", "PLAYWRIGHT_BROWSERS_PATH"
    }}
    rows = []
    semaphore = asyncio.Semaphore(args.concurrency)

    async def attempt(task: dict, repetition: int) -> None:
        async with semaphore:
            workspace = (output / f"r{repetition}" / task["task_id"]).resolve()
            workspace.mkdir(parents=True)
            task_path = workspace / "task.json"
            task_path.write_text(json.dumps(task))
            env = {**plumbing, "EVAL_WORKSPACE": str(workspace), "EVAL_TASK_PATH": str(task_path),
                   "EVAL_RESULT_PATH": str(workspace / "result.json"),
                   "EVAL_OPTIONS_JSON": json.dumps(options)}
            env.update({k: os.environ[k] for k in keys if k in os.environ})
            with (workspace / "probe.log").open("w") as log:
                process = await asyncio.create_subprocess_exec(
                    sys.executable, str(ROOT / "stealth_bench/v2/probe.py"),
                    env=env, stdout=log, stderr=log, start_new_session=True)
                try:
                    await asyncio.wait_for(process.wait(), 300)
                except TimeoutError:
                    os.killpg(process.pid, signal.SIGKILL)
                    await process.wait()
            judge_env = {**plumbing, "EVAL_WORKSPACE": str(workspace),
                         "EVAL_JUDGMENT_PATH": str(workspace / "judgment.json"),
                         "EVAL_JUDGE_API_KEY": os.environ["OPENAI_API_KEY"],
                         "EVAL_JUDGE_MODEL": "gpt-5.6-luna"}
            with (workspace / "judge.log").open("w") as log:
                process = await asyncio.create_subprocess_exec(
                    sys.executable, str(ROOT / "stealth_bench/v2/judge.py"),
                    env=judge_env, stdout=log, stderr=log, start_new_session=True)
                try:
                    await asyncio.wait_for(process.wait(), 210)
                except TimeoutError:
                    os.killpg(process.pid, signal.SIGKILL)
                    await process.wait()
            judgment_path = workspace / "judgment.json"
            verdict = json.loads(judgment_path.read_text()) if judgment_path.exists() else {
                "status": "error", "failure_class": "judge_or_capture_error"}
            category = "accessible" if verdict["status"] == "pass" else verdict["failure_class"]
            row = {"task_id": task["task_id"], "repetition": repetition, "classification": category,
                   "score": None if verdict["status"] == "error" else verdict["score"]}
            rows.append(row)
            (output / "results.json").write_text(json.dumps(rows, indent=2) + "\n")
            (output / "summary.json").write_text(json.dumps(summarize(rows), indent=2) + "\n")
            print(f"R{repetition} {task['task_id']}: {category}", flush=True)

    for repetition in range(1, args.repetitions + 1):
        ordered = list(tasks)
        random.Random(20260927 + repetition).shuffle(ordered)
        await asyncio.gather(*(attempt(task, repetition) for task in ordered))
    print(json.dumps(summarize(rows), indent=2))
    print(f"Private evidence: {output}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", choices=BROWSERS, required=True)
    parser.add_argument("--task-ids", help="Exact comma-separated IDs; omitted runs all 100")
    parser.add_argument("--repetitions", type=int, choices=(1, 2), default=2)
    parser.add_argument("--concurrency", type=int, default=5)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not 1 <= args.concurrency <= 20:
        parser.error("--concurrency must be between 1 and 20")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
