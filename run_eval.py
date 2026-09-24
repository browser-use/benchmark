"""Run BU Bench V2 with the weighted findings judge by default.

uv run python run_eval.py --tasks 5
uv run python run_eval.py --browser local_headless
uv run python run_eval.py --benchmark BU_Bench_V1  # legacy binary judge
"""

import argparse
import asyncio
import base64
import hashlib
import json
import logging
import os
import traceback
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

import certifi

os.environ.setdefault("SSL_CERT_FILE", certifi.where())
os.environ["BROWSER_USE_SETUP_LOGGING"] = "false"  # Before importing browser_use
logging.basicConfig(level=logging.CRITICAL)

from browser_use import Agent, Browser
from browser_use.llm import ChatBrowserUse, ChatOpenAI
from dotenv import load_dotenv

from browsers import PROVIDERS, get_provider
from evaluation import (
    BENCHMARKS,
    DEFAULT_BENCHMARK,
    create_judge,
    judge_config,
    judge_trace,
    load_tasks,
    validate_findings_task,
)

load_dotenv()

MAX_CONCURRENT = 3
TASK_TIMEOUT = 1800
AGENT_FRAMEWORK_NAME = "BrowserUse"
AGENT_FRAMEWORK_VERSION = version("browser-use")
MODEL_NAME = "bu-2-0"


def create_agent_model(model: str = MODEL_NAME, reasoning: str = "xhigh"):
    if model == "bu-2-0":
        return ChatBrowserUse(model=model)
    if not os.getenv("OPENAI_API_KEY"):
        raise ValueError("OPENAI_API_KEY is required for the selected agent model")
    return ChatOpenAI(
        model=model,
        reasoning_effort=reasoning,
        max_completion_tokens=32768,
        temperature=None,
        frequency_penalty=None,
        timeout=300,
        max_retries=2,
    )


def select_tasks(
    tasks: list[dict], task_ids: list[str] | None = None, limit: int | None = None
):
    if task_ids:
        by_id = {task["task_id"]: task for task in tasks}
        unknown = set(task_ids) - by_id.keys()
        if unknown or len(task_ids) != len(set(task_ids)):
            raise ValueError("Task IDs must be unique and present in the dataset")
        tasks = [by_id[task_id] for task_id in task_ids]
    return tasks[:limit] if limit is not None else tasks


def encode_screenshots(paths: list[str]) -> list[str]:
    return [
        base64.b64encode(Path(p).read_bytes()).decode()
        for p in paths
        if Path(p).is_file()
    ]


def collect_output_files(agent) -> str:
    """Collect source text of files managed by this agent, including PDF/DOCX."""
    fs = agent.file_system
    return "\n\n".join(
        f"--- {name} ---\n{fs.get_file(name).read()}" for name in fs.list_files()
    )


def save_task(run_data_dir: Path | None, task_id: str, payload: dict) -> None:
    if run_data_dir is not None:
        run_data_dir.mkdir(parents=True, exist_ok=True)
        (run_data_dir / f"{task_id}.json").write_text(json.dumps(payload, indent=2))


async def create_browser(
    browser_provider, timeout_seconds: int = TASK_TIMEOUT
) -> Browser:
    if browser_provider is None:
        return Browser(
            use_cloud=True, cloud_timeout=max(1, (timeout_seconds + 59) // 60)
        )
    cdp_url = await browser_provider.connect()
    if cdp_url is None:
        return Browser(headless=getattr(browser_provider, "HEADLESS", True))
    return Browser(cdp_url=cdp_url)


async def run_task(
    task: dict,
    semaphore: asyncio.Semaphore,
    browser_provider=None,
    llm=None,
    run_data_dir: Path | None = None,
    *,
    benchmark: str = DEFAULT_BENCHMARK,
    judge_llm=None,
    task_timeout: int = TASK_TIMEOUT,
    max_steps: int = 100,
) -> dict:
    """Execute and judge one task, retaining evidence if judging fails."""
    async with semaphore:
        task_id = task.get("task_id", "unknown")
        browser = None
        phase = "setup"
        result = {
            "task_id": task_id,
            "score": None,
            "raw_score": None,
            "steps": 0,
            "duration": 0,
            "cost": 0,
        }
        artifact = {"benchmark": benchmark}
        try:
            if benchmark == DEFAULT_BENCHMARK:
                validate_findings_task(task)
            judge_llm = judge_llm or create_judge(benchmark)
            artifact["judge_config"] = judge_config(benchmark, judge_llm)
            print(f"Running task: {task_id}")
            browser = await create_browser(
                browser_provider, timeout_seconds=task_timeout
            )
            agent = Agent(
                task=task["confirmed_task"],
                llm=llm or ChatBrowserUse(model=MODEL_NAME),
                browser=browser,
                use_judge=False,  # Only the benchmark judge evaluates the run.
            )
            phase = "execution"
            try:
                history = await asyncio.wait_for(
                    agent.run(max_steps=max_steps), timeout=task_timeout
                )
            except asyncio.TimeoutError:
                # Preserve and judge partial work instead of discarding its evidence.
                history = agent.history
                result["execution_error"] = f"Task timed out after {task_timeout}s"

            result.update(
                steps=history.number_of_steps(),
                duration=history.total_duration_seconds(),
                cost=history.usage.total_cost if history.usage else 0,
            )
            phase = "evidence"
            trace = {
                "agent_task": task["confirmed_task"],
                "final_result": history.final_result()
                or "Agent did not return a result",
                "agent_steps": history.agent_steps(),
                "ground_truth": task.get("answer"),
                "screenshots_b64": encode_screenshots(
                    [p for p in history.screenshot_paths() if p is not None]
                ),
                "output_files_text": collect_output_files(agent),
            }
            artifact.update(
                agent_trace=trace,
                metrics={key: result[key] for key in ("steps", "duration", "cost")},
                # Retained locally to allow re-judging the exact task version.
                task=task,
            )
            result["status"] = "awaiting_judge"
            save_task(run_data_dir, task_id, {**artifact, **result})
            phase = "judge"
            result.update(await judge_trace(task, trace, judge_llm, benchmark))
            if result["score"] is None:
                result["status"] = "evidence_incomplete"
                print(f"Task {task_id}: evidence incomplete; no benchmark score")
            else:
                result["status"] = "judged"
                print(
                    "Task",
                    task_id,
                    "raw=",
                    result["raw_score"],
                    "final=",
                    result["score"],
                )
        except Exception as exc:
            # A broken judge/configuration is not an agent score of zero.
            result.update(
                status=f"{phase}_error",
                error=f"{type(exc).__name__}: {exc}",
                traceback=traceback.format_exc(),
            )
            if phase == "execution":
                result.update(score=0.0, raw_score=0.0)
            print(f"Task {task_id}: {result['status']}: {result['error']}")
        finally:
            cleanup_errors = []
            if browser is not None:
                try:
                    await browser.stop()
                except Exception as exc:
                    cleanup_errors.append(f"browser: {exc}")
            if browser_provider is not None:
                try:
                    await browser_provider.disconnect()
                except Exception as exc:
                    cleanup_errors.append(f"provider: {exc}")
            if cleanup_errors:
                result["cleanup_errors"] = cleanup_errors
        save_task(run_data_dir, task_id, {**artifact, **result})
        return result


def summarize_results(results: list[dict]) -> dict:
    scored = [r for r in results if r["score"] is not None]
    complete = len(scored) == len(results)
    mean = sum(r["score"] for r in scored) / len(scored) if scored else None
    raw_mean = sum(r["raw_score"] for r in scored) / len(scored) if scored else None
    return {
        "tasks_completed": len(results),
        "tasks_scored": len(scored),
        "tasks_unscored": len(results) - len(scored),
        "tasks_successful": sum(r["score"] == 1 for r in scored),
        "judge_errors": sum(r["status"] == "judge_error" for r in results),
        "evidence_incomplete": sum(
            r["status"] == "evidence_incomplete" for r in results
        ),
        "execution_errors": sum(
            r["status"] == "execution_error" or "execution_error" in r for r in results
        ),
        "mean_score": mean if complete else None,
        "mean_raw_score": raw_mean if complete else None,
        "mean_score_scored_tasks": mean,
        "mean_raw_score_scored_tasks": raw_mean,
        "rh_zeroed": sum(bool(r.get("rh_zeroed")) for r in results),
        "total_steps": sum(r["steps"] for r in results),
        "total_duration": sum(r["duration"] for r in results),
        "total_cost": sum(r["cost"] for r in results),
    }


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Run BU Bench V2 with the findings judge (default)"
    )
    parser.add_argument(
        "--browser",
        default="browser-use-cloud",
        choices=["browser-use-cloud"] + PROVIDERS,
    )
    parser.add_argument("--benchmark", default=DEFAULT_BENCHMARK, choices=BENCHMARKS)
    parser.add_argument(
        "--tasks", type=int, default=None, help="Number of tasks (default: all)"
    )
    parser.add_argument(
        "--judge-model",
        help="Override judge model (default: gpt-5.6-luna for V2; gemini-2.5-flash for V1)",
    )
    parser.add_argument(
        "--judge-reasoning",
        default="xhigh",
        choices=["none", "minimal", "low", "medium", "high", "xhigh"],
        help="Reasoning effort for the V2 OpenAI judge",
    )
    parser.add_argument(
        "--model",
        default=MODEL_NAME,
        choices=["bu-2-0", "gpt-5.6-luna", "gpt-6-astra"],
        help="Executor model; separate from --judge-model",
    )
    parser.add_argument(
        "--agent-reasoning",
        default="xhigh",
        choices=["none", "minimal", "low", "medium", "high", "xhigh"],
    )
    parser.add_argument(
        "--task-ids", nargs="+", help="Exact task IDs, in execution order"
    )
    parser.add_argument("--task-timeout", type=int, default=TASK_TIMEOUT)
    parser.add_argument("--max-steps", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=MAX_CONCURRENT)
    args = parser.parse_args(argv)
    if min(args.task_timeout, args.max_steps, args.concurrency) < 1:
        parser.error("Timeout, max steps, and concurrency must be positive")
    if args.tasks is not None and args.tasks < 1:
        parser.error("--tasks must be positive")
    return args


async def main():
    args = parse_args()
    tasks = select_tasks(load_tasks(args.benchmark), args.task_ids, args.tasks)
    # Fail on missing judge credentials before launching any browser sessions.
    judge_llm = create_judge(args.benchmark, args.judge_model, args.judge_reasoning)
    config = judge_config(args.benchmark, judge_llm)
    agent_llm = create_agent_model(args.model, args.agent_reasoning)
    browser_provider = (
        None if args.browser == "browser-use-cloud" else get_provider(args.browser)
    )
    run_start = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    run_key = f"{args.benchmark}_{AGENT_FRAMEWORK_NAME}_{AGENT_FRAMEWORK_VERSION}_browser_{args.browser}_model_{args.model}"
    root = Path(__file__).parent
    run_data_dir = root / "run_data" / f"{run_key}_start_at_{run_start}"
    results_file = root / "results" / f"{run_key}.json"
    run_config = {
        "benchmark": args.benchmark,
        "runner_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "dataset_sha256": hashlib.sha256(
            (root / f"{args.benchmark}.enc").read_bytes()
        ).hexdigest(),
        "judge": config,
        "agent_model": args.model,
        "agent_reasoning": getattr(agent_llm, "reasoning_effort", None),
        "agent_max_completion_tokens": getattr(
            agent_llm, "max_completion_tokens", None
        ),
        "task_ids": [t["task_id"] for t in tasks],
        "agent_framework_version": AGENT_FRAMEWORK_VERSION,
        "task_timeout_seconds": args.task_timeout,
        "max_steps": args.max_steps,
        "max_concurrent": args.concurrency,
    }
    run_data_dir.mkdir(parents=True)
    (run_data_dir / "config.json").write_text(json.dumps(run_config, indent=2))
    print(
        f"{args.benchmark}: {len(tasks)} tasks, judge={config['type']} / {config['model']} / {config['reasoning_effort']}"
    )
    sem = asyncio.Semaphore(args.concurrency)
    results = await asyncio.gather(
        *[
            run_task(
                t,
                sem,
                browser_provider=browser_provider,
                run_data_dir=run_data_dir,
                benchmark=args.benchmark,
                judge_llm=judge_llm,
                llm=agent_llm,
                task_timeout=args.task_timeout,
                max_steps=args.max_steps,
            )
            for t in tasks
        ]
    )
    summary = summarize_results(results)
    results_file.parent.mkdir(parents=True, exist_ok=True)
    runs = json.loads(results_file.read_text()) if results_file.exists() else []
    runs.append(
        {
            "run_start": run_start,
            "config": run_config,
            **summary,
            "task_results": results,
        }
    )
    results_file.write_text(json.dumps(runs, indent=2))
    if summary["mean_score"] is not None:
        print(
            f"Mean weighted score: {summary['mean_score']:.2%} (raw: {summary['mean_raw_score']:.2%})"
        )
    else:
        print(
            f"Incomplete evaluation: {summary['tasks_unscored']} unscored tasks; see {run_data_dir}"
        )
    print(f"Results: {results_file}")
    if summary["tasks_unscored"]:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
