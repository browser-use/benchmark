"""Main benchmark evaluation script.

Usage:
    uv run python run_eval.py                              # Stealth Bench (default) + browser-use-cloud + bu-2-0
    uv run python run_eval.py --benchmark bu               # BU Bench
    uv run python run_eval.py --browser anchor
    uv run python run_eval.py --browser local_headless     # use local headless Chromium
    uv run python run_eval.py --tasks 5                    # run only 5 tasks

Default is Stealth Bench (`Stealth_Bench_V1.enc`). Pass `--benchmark bu` for BU Bench (`BU_Bench_V1.enc`).

Available browsers: browser-use-cloud (default), anchor, browserbase,
    browserless, hyperbrowser, local_headful, local_headless, onkernel,
    rebrowser, steel
"""

# Fix for MacOS users using uv without SSL certificate setup
import certifi, os

os.environ.setdefault("SSL_CERT_FILE", certifi.where())

import logging

os.environ["BROWSER_USE_SETUP_LOGGING"] = (
    "false"  # Must be set before importing browser_use
)
logging.basicConfig(
    level=logging.CRITICAL
)  # Suppress all logs including shutdown warnings

import argparse
import asyncio
import base64, hashlib, json, traceback
from datetime import datetime
from pathlib import Path
from cryptography.fernet import Fernet
from dotenv import load_dotenv
from browser_use import Agent, Browser, ChatGoogle
from browser_use.llm import ChatBrowserUse
from browsers import PROVIDERS, get_provider
from judge import construct_judge_messages, JudgementResult

load_dotenv()

# Judge LLM - always use gemini-2.5-flash for consistent judging across all evaluations
JUDGE_LLM = ChatGoogle(model="gemini-2.5-flash", api_key=os.getenv("GOOGLE_API_KEY"))

# encrypted task blobs at repo root; Fernet key label must match encryption recipe
BENCHMARK_CONFIG: dict[str, tuple[str, bytes]] = {
    "bu": ("BU_Bench_V1.enc", b"BU_Bench_V1"),
    "stealth": ("Stealth_Bench_V1.enc", b"Stealth_Bench_V1"),
}

MAX_CONCURRENT = 3
TASK_TIMEOUT = 1800  # 30 minutes max per task

AGENT_FRAMEWORK_NAME = "BrowserUse"
AGENT_FRAMEWORK_VERSION = "0.11.5"
MODEL_NAME = "bu-2-0"


def encode_screenshots(paths: list[str]) -> list[str]:
    """Encode screenshot files to base64. Skips files that don't exist."""
    result = []
    for p in paths:
        path = Path(p)
        if path.exists():
            result.append(base64.b64encode(path.read_bytes()).decode())
    return result


def load_tasks(enc_filename: str, key_material: bytes) -> list[dict]:
    enc_path = Path(__file__).parent / enc_filename
    key = base64.urlsafe_b64encode(hashlib.sha256(key_material).digest())
    encrypted = base64.b64decode(enc_path.read_text())
    return json.loads(Fernet(key).decrypt(encrypted))


async def create_browser(browser_provider) -> Browser:
    """Create a Browser instance from a provider module.

    browser-use-cloud uses the native use_cloud=True path.
    Local providers launch browser-use's built-in Chromium.
    All other providers return a CDP URL for Browser(cdp_url=...).
    """
    if browser_provider is None:
        return Browser(use_cloud=True, cloud_timeout=30)
    cdp_url = await browser_provider.connect()
    if cdp_url is None:
        return Browser(headless=getattr(browser_provider, "HEADLESS", True))
    return Browser(cdp_url=cdp_url)


async def run_task(
    task: dict,
    semaphore: asyncio.Semaphore,
    browser_provider=None,
    llm=None,
    run_data_dir: Path = None,
) -> dict:
    """Run a single task. Returns result dict with score (0 on failure).

    Args:
        browser_provider: Browser provider module (None = browser-use-cloud).
        llm: LLM to use. Defaults to ChatBrowserUse().
        run_data_dir: Directory for trace output.
    """
    async with semaphore:
        try:
            task_id = task.get("task_id", "unknown")
            print(f"Running task: {task_id}")

            browser = await create_browser(browser_provider)

            # To swap model: replace ChatBrowserUse() with your LLM (e.g. ChatOpenAI, ChatAnthropic)
            # You can use any OpenAI API compatible model by changing base_url. You can use ollama too. See https://docs.browser-use.com/supported-models for info
            agent = Agent(
                task=task["confirmed_task"],
                llm=llm or ChatBrowserUse(model="bu-2-0"),
                browser=browser,
            )

            try:
                agent_history = await asyncio.wait_for(
                    agent.run(), timeout=TASK_TIMEOUT
                )
            except asyncio.TimeoutError:
                await browser.stop()
                if browser_provider:
                    await browser_provider.disconnect()
                print(f"Task {task_id} timed out after {TASK_TIMEOUT}s")
                return {
                    "task_id": task_id,
                    "score": 0,
                    "steps": 0,
                    "duration": TASK_TIMEOUT,
                    "cost": 0,
                    "error": f"Task timed out after {TASK_TIMEOUT}s",
                }

            if browser_provider:
                await browser_provider.disconnect()

            # Collect task metrics from agent history
            steps = agent_history.number_of_steps()
            duration = agent_history.total_duration_seconds()
            cost = agent_history.usage.total_cost if agent_history.usage else 0

            # Collect judge inputs from agent history
            agent_task = task["confirmed_task"]
            final_result = (
                agent_history.final_result() or "Agent did not return a result"
            )
            agent_steps = agent_history.agent_steps()
            ground_truth = task.get("answer")
            screenshots_b64 = encode_screenshots(
                [p for p in agent_history.screenshot_paths() if p is not None]
            )

            # Run judge
            judge_messages = construct_judge_messages(
                task=agent_task,
                final_result=final_result,
                agent_steps=agent_steps,
                ground_truth=ground_truth,
                screenshots_b64=screenshots_b64,
            )
            response = await JUDGE_LLM.ainvoke(
                judge_messages, output_format=JudgementResult
            )
            judgement: JudgementResult = response.completion

            score = 1 if judgement.verdict else 0
            print(
                f"Task {task_id} completed: score={score}, verdict={judgement.verdict}"
            )

            # Save trace to run_data/
            run_data_dir.mkdir(parents=True, exist_ok=True)
            trace = {
                "agent_task": agent_task,
                "final_result": final_result,
                "agent_steps": agent_steps,
                "ground_truth": ground_truth,
                "screenshots_b64": screenshots_b64,
            }
            metrics = {"steps": steps, "duration": duration, "cost": cost}
            (run_data_dir / f"{task_id}.json").write_text(
                json.dumps(
                    {
                        "agent_trace": trace,
                        "metrics": metrics,
                        "judgement": judgement.model_dump(),
                    },
                    indent=2,
                )
            )

            return {
                "task_id": task_id,
                "score": score,
                "steps": steps,
                "duration": duration,
                "cost": cost,
                "judgement": judgement.model_dump(),
            }

        except Exception as e:
            error_type = type(e).__name__
            error_msg = f"{error_type}: {e}"
            print(f"Task {task.get('task_id', 'unknown')} failed: {error_msg}")
            return {
                "task_id": task.get("task_id"),
                "score": 0,
                "steps": 0,
                "duration": 0,
                "cost": 0,
                "error": error_msg,
                "traceback": traceback.format_exc(),
            }


async def main():
    parser = argparse.ArgumentParser(
        description="Run Stealth Bench V1 (default) or BU Bench V1 (--benchmark bu)"
    )
    parser.add_argument(
        "--benchmark",
        choices=tuple(BENCHMARK_CONFIG.keys()),
        default="stealth",
        help="Task set (default: stealth). bu=BU Bench V1 (100 tasks), stealth=Stealth Bench V1 (80 tasks)",
    )
    parser.add_argument(
        "--browser",
        default="browser-use-cloud",
        choices=["browser-use-cloud"] + PROVIDERS,
        help="Browser provider (default: browser-use-cloud)",
    )
    parser.add_argument(
        "--tasks",
        type=int,
        default=None,
        help="Number of tasks to run (default: all)",
    )
    args = parser.parse_args()

    # Resolve browser provider (None = use native browser-use-cloud path)
    browser_name = args.browser
    if browser_name == "browser-use-cloud":
        browser_provider = None
    else:
        browser_provider = get_provider(browser_name)

    # Paths: benchmark (stealth | bu) is a folder under run_data/ and results/, not in run_key
    run_start = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_key = (
        f"{AGENT_FRAMEWORK_NAME}_{AGENT_FRAMEWORK_VERSION}_browser_{browser_name}_model_{MODEL_NAME}"
    )
    root = Path(__file__).parent
    run_data_dir = root / "run_data" / args.benchmark / f"{run_key}_start_at_{run_start}"
    results_file = root / "results" / args.benchmark / f"{run_key}.json"

    enc_name, key_material = BENCHMARK_CONFIG[args.benchmark]
    tasks = load_tasks(enc_name, key_material)
    if args.tasks:
        tasks = tasks[: args.tasks]
    sem = asyncio.Semaphore(MAX_CONCURRENT)
    results = await asyncio.gather(
        *[
            run_task(
                t, sem, browser_provider=browser_provider, run_data_dir=run_data_dir
            )
            for t in tasks
        ]
    )

    # Aggregate metrics
    successful = sum(1 for r in results if r.get("score") == 1)
    total_steps = sum(r.get("steps", 0) for r in results)
    total_duration = sum(r.get("duration", 0) for r in results)
    total_cost = sum(r.get("cost", 0) for r in results)

    # Save results (append to existing runs)
    results_file.parent.mkdir(parents=True, exist_ok=True)
    runs = json.loads(results_file.read_text()) if results_file.exists() else []
    runs.append(
        {
            "run_start": run_start,
            "tasks_completed": len(results),
            "tasks_successful": successful,
            "total_steps": total_steps,
            "total_duration": total_duration,
            "total_cost": total_cost,
        }
    )
    results_file.write_text(json.dumps(runs, indent=2))

    print(
        f"Run complete: {successful}/{len(results)} tasks successful, {total_steps} steps, {total_duration:.1f}s, ${total_cost:.2f}"
    )


if __name__ == "__main__":
    asyncio.run(main())
