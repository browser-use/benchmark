"""Run all 200 BU Bench V2 tasks with BrowserCode and the findings judge."""

import asyncio
import hashlib
import json
import traceback
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

from bcode_runner import (
    execute,
    preflight,
)
from evaluation import (
    DEFAULT_BENCHMARK,
    create_judge,
    judge_config,
    judge_trace,
    load_tasks,
)

ROOT = Path(__file__).resolve().parent


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2))
    temporary.replace(path)


async def run_task(task, semaphore, *, run_dir, args, judge_llm):
    async with semaphore:
        task_dir = run_dir / task["task_id"]
        task_dir.mkdir()
        # Only the instruction goes to BrowserCode. Judge material is written
        # after execution, separately from the agent's workspace.
        result = {
            "task_id": task["task_id"],
            "score": None,
            "raw_score": None,
            "steps": 0,
            "duration": 0.0,
            "cost": 0.0,
            "status": "execution_error",
        }
        phase = "execution"
        try:
            executed = await execute(
                task["confirmed_task"],
                task_dir,
                binary=args.bcode_bin,
                model=args.model,
                effort=args.agent_reasoning,
                timeout=args.task_timeout,
                catalog_path=args.catalog_path,
            )
            result.update(executed["metrics"])
            if not executed["metrics"]["steps"]:
                raise RuntimeError(
                    "BrowserCode produced no model/tool evidence; see events.jsonl and stderr.log"
                )
            if executed["execution_errors"]:
                result["execution_errors"] = executed["execution_errors"]
            write_json(task_dir / "task.json", task)
            write_json(task_dir / "result.json", {**result, "status": "awaiting_judge"})
            phase = "judge"
            result.update(
                await judge_trace(
                    task, executed["trace"], judge_llm, artifact_dir=task_dir
                )
            )
            result["status"] = (
                "judged" if result["score"] is not None else "evidence_incomplete"
            )
        except Exception as exc:  # noqa: BLE001 - persist each task failure and continue the batch
            result.update(
                status=f"{phase}_error",
                error=f"{type(exc).__name__}: {exc}",
                traceback=traceback.format_exc(),
            )
        write_json(task_dir / "result.json", result)
        label = (
            f"{result['score']:.1%}"
            if result["score"] is not None
            else result["status"]
        )
        print(f"{task['task_id']}: {label}", flush=True)
        return result


def summarize_results(results):
    scored = [result for result in results if result["score"] is not None]
    complete = len(scored) == len(results)
    mean = sum(r["score"] for r in scored) / len(scored) if scored else None
    raw = sum(r["raw_score"] for r in scored) / len(scored) if scored else None
    return {
        "tasks_completed": len(results),
        "tasks_scored": len(scored),
        "tasks_unscored": len(results) - len(scored),
        "mean_score": mean if complete else None,
        "mean_raw_score": raw if complete else None,
        "mean_score_scored_tasks": mean,
        "mean_raw_score_scored_tasks": raw,
        "execution_errors": sum(
            bool(r.get("execution_errors")) or r["status"] == "execution_error"
            for r in results
        ),
        "judge_errors": sum(r["status"] == "judge_error" for r in results),
        "evidence_incomplete": sum(
            r["status"] == "evidence_incomplete" for r in results
        ),
        "rh_zeroed": sum(bool(r.get("rh_zeroed")) for r in results),
        "total_steps": sum(r["steps"] for r in results),
        "total_duration": sum(r["duration"] for r in results),
        "total_cost": sum(r["cost"] for r in results),
    }


async def main(args):
    load_dotenv(ROOT / ".env")
    from run_eval import select_shard, select_tasks

    all_tasks = load_tasks()
    if len(all_tasks) != 200:
        raise ValueError("BU Bench V2 must contain 200 tasks")
    tasks = select_shard(all_tasks, args.shard_index, args.shard_count)
    tasks = select_tasks(tasks, args.task_ids, args.tasks)
    judge = create_judge(DEFAULT_BENCHMARK, args.judge_model, args.judge_reasoning)
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S_%f")
    run_dir = args.output_dir.expanduser().resolve() / f"BU_Bench_V2_bcode_{stamp}"
    run_dir.mkdir(parents=True)
    try:
        executor = await preflight(
            args.bcode_bin,
            args.bcode_version,
            args.model,
            args.agent_reasoning,
            run_dir / "preflight-state",
        )
        args.bcode_bin = executor["binary"]
        args.catalog_path = executor["catalog_path"]
        config = {
            "benchmark": DEFAULT_BENCHMARK,
            "executor": "bcode",
            "executor_config": executor,
            "dataset_sha256": hashlib.sha256(
                (ROOT / "BU_Bench_V2.enc").read_bytes()
            ).hexdigest(),
            "runner_source_sha256": hashlib.sha256(
                Path(__file__).read_bytes()
            ).hexdigest(),
            "executor_source_sha256": hashlib.sha256(
                (ROOT / "bcode_runner.py").read_bytes()
            ).hexdigest(),
            "judge": {
                **judge_config(DEFAULT_BENCHMARK, judge),
                "screenshot_timing": "after_tool_event",
            },
            "task_ids": [task["task_id"] for task in tasks],
            "task_timeout_seconds": args.task_timeout,
            "parallel": args.concurrency,
            "browser": {
                "provider": "browser-use-cloud",
                "api_version": "v2",
                "timeout_minutes": (args.task_timeout + 59) // 60,
            },
        }
        write_json(run_dir / "config.json", config)
        print(
            f"BU Bench V2: {len(tasks)} tasks; BrowserCode {executor['version']} / {args.model} / {args.agent_reasoning}; findings judge {judge.model} / {judge.reasoning_effort}",
            flush=True,
        )
        if args.check:
            print(
                f"Preflight passed; no tasks executed. Configuration: {run_dir / 'config.json'}"
            )
            return
        semaphore = asyncio.Semaphore(args.concurrency)
        results = await asyncio.gather(
            *(
                run_task(task, semaphore, run_dir=run_dir, args=args, judge_llm=judge)
                for task in tasks
            )
        )
        summary = summarize_results(results)
        write_json(
            run_dir / "results.json",
            {"config": config, **summary, "task_results": results},
        )
        if summary["mean_score"] is not None:
            print(
                f"Mean weighted score: {summary['mean_score']:.2%} (raw: {summary['mean_raw_score']:.2%})"
            )
        else:
            print(
                f"Incomplete evaluation: {summary['tasks_unscored']} unscored tasks; {summary['tasks_scored']} scored"
            )
        print(f"Results and evidence: {run_dir}")
        if summary["tasks_unscored"]:
            raise SystemExit(1)
    finally:
        client = getattr(judge, "client", None)
        if client is not None:
            await client.close()
