"""Run a single benchmark task using the browser-use agent framework."""

import os
import sys
import asyncio
import base64
from pathlib import Path
from functools import partial

# Add project root to path for sibling imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from dotenv import load_dotenv
from browser_use import Agent, Browser
from lmnr import observe
from browsers import BROWSERS
from models import MODELS
from laminar import LaminarService
from frameworks import (
    ExecutionResult,
    load_tasks,
    interleave,
    run_and_judge,
    parse_params,
    validate_params,
)

load_dotenv()

ACCEPTED_PARAMS: dict[str, str] = {
    "use_vision": "Enable/disable vision (screenshots) for the agent (true/false, default: true)",
    "framework_repo": "Override GitHub repo for browser-use install (e.g. Alezander9/alex-browser-use). Consumed by the workflow install step, not the runner.",
}


def encode_screenshots(paths: list[str]) -> list[str]:
    result = []
    for p in paths:
        path = Path(p)
        if path.exists():
            result.append(base64.b64encode(path.read_bytes()).decode())
    return result


@observe(span_type="EXECUTOR")
async def execute(
    task_description: str, llm, browser_name: str, use_vision: bool = True
) -> ExecutionResult:
    """Run a browser-use agent on the task and return a standardized result."""
    provider = BROWSERS[browser_name]
    cdp_url = await provider.connect()
    if cdp_url:
        browser = Browser(cdp_url=cdp_url)
    else:
        headless = getattr(provider, "HEADLESS", True)
        browser = Browser(headless=headless)

    agent = Agent(
        task=task_description,
        llm=llm,
        browser=browser,
        use_judge=False,
        use_vision=use_vision,
    )
    try:
        history = await agent.run()
    finally:
        try:
            await browser.kill()
        except Exception:
            pass
        await provider.disconnect()

    return ExecutionResult(
        final_result=history.final_result() or "Agent did not return a result",
        steps=history.agent_steps(),
        screenshots_b64=encode_screenshots(
            [p for p in history.screenshot_paths() if p is not None]
        ),
        num_steps=history.number_of_steps(),
        duration_seconds=history.total_duration_seconds(),
        cost=history.usage.total_cost if history.usage else 0,
    )


async def main():
    params = validate_params(parse_params(), ACCEPTED_PARAMS)
    task_index = int(os.environ["TASK_INDEX"])
    model_name = os.environ["MODEL"]
    eval_id = os.environ["EVAL_ID"]
    browser_name = os.environ.get("BROWSER", "browser-use-cloud")
    benchmark = os.environ.get("BENCHMARK", "BU_Bench_V1")

    use_vision = params.get("use_vision", "true").lower() != "false"

    tasks = load_tasks(benchmark)
    if len(tasks) == 100:
        tasks = interleave(tasks)
    task = tasks[task_index]
    task["_index"] = task_index

    LaminarService.initialize()
    LaminarService.attach_evaluation(eval_id)

    llm = MODELS[model_name]()
    execute_fn = partial(
        execute, llm=llm, browser_name=browser_name, use_vision=use_vision
    )
    await run_and_judge(task, execute_fn)


if __name__ == "__main__":
    asyncio.run(main())
