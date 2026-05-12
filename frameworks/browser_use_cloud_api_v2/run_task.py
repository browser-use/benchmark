"""Run a single benchmark task using the Browser Use Cloud API v2.

Dispatches a task via POST /api/v2/tasks, polls GET /api/v2/tasks/{id}
until completion, then maps the response into ExecutionResult for the judge.
"""

import asyncio
import base64
import os
import sys
from functools import partial
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from dotenv import load_dotenv
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

ACCEPTED_PARAMS: dict[str, str] = {}

API_BASE = "https://api.browser-use.com/api/v2"
POLL_INTERVAL = 5
TERMINAL_STATUSES = {"finished", "stopped"}

# V2 SupportedLLMs mapped from our model registry names.
# Only models the v2 API actually supports are listed here.
V2_MODEL_MAP = {
    "bu-2-0": "browser-use-2.0",
    "bu-1-0": "browser-use-llm",
    "gpt-4.1": "gpt-4.1",
    "gpt-4.1-mini": "gpt-4.1-mini",
    "o4-mini": "o4-mini",
    "o3": "o3",
    "gemini-2.5-flash": "gemini-2.5-flash",
    "gemini-2.5-pro": "gemini-2.5-pro",
    "gemini-3-pro-preview": "gemini-3-pro-preview",
    "gemini-3-flash-preview": "gemini-3-flash-preview",
    "gpt-4o": "gpt-4o",
    "gpt-4o-mini": "gpt-4o-mini",
    "claude-sonnet-4-5": "claude-sonnet-4-5-20250929",
    "claude-sonnet-4-6": "claude-sonnet-4-6",
    "claude-opus-4-5": "claude-opus-4-5-20251101",
    "claude-3-7-sonnet": "claude-3-7-sonnet-20250219",
}


def _headers() -> dict:
    return {"X-Browser-Use-API-Key": os.environ["BROWSER_USE_API_KEY"]}


def _create_task(task_description: str, model: str) -> dict:
    """Create a v2 task and return the response (id, sessionId)."""
    api_model = V2_MODEL_MAP.get(model, model)
    resp = httpx.post(
        f"{API_BASE}/tasks",
        headers=_headers(),
        json={"task": task_description, "llm": api_model},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def _get_task(task_id: str) -> dict:
    """Poll task status."""
    resp = httpx.get(
        f"{API_BASE}/tasks/{task_id}",
        headers=_headers(),
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def _fetch_screenshot_b64(url: str) -> str | None:
    """Download a screenshot URL and return base64-encoded bytes."""
    try:
        resp = httpx.get(url, timeout=30)
        resp.raise_for_status()
        return base64.b64encode(resp.content).decode()
    except Exception:
        return None


def _format_step(step: dict) -> str:
    """Format a v2 TaskStepView to match browser-use agent_steps() format.

    Ground truth format:
        Step N:
        Actions: [json array with indent=1]
        Result M: <extracted content>  (not available from v2 API)
    """
    import json as _json

    step_text = f"Step {step.get('number', '?')}:\n"

    actions_raw = step.get("actions", [])
    if actions_raw:
        parsed = []
        for a in actions_raw:
            try:
                parsed.append(_json.loads(a))
            except (_json.JSONDecodeError, TypeError):
                parsed.append(a)
        step_text += f"Actions: {_json.dumps(parsed, indent=1)}\n"

    return step_text


def _duration_seconds(task_data: dict) -> float:
    """Compute duration from startedAt/finishedAt timestamps."""
    from datetime import datetime

    started = task_data.get("startedAt")
    finished = task_data.get("finishedAt")
    if not started or not finished:
        return 0.0
    try:
        t0 = datetime.fromisoformat(started.replace("Z", "+00:00"))
        t1 = datetime.fromisoformat(finished.replace("Z", "+00:00"))
        return max((t1 - t0).total_seconds(), 0.0)
    except Exception:
        return 0.0


async def execute(task_description: str, model_name: str) -> ExecutionResult:
    """Create a v2 task, poll until done, return ExecutionResult."""
    created = _create_task(task_description, model_name)
    task_id = created["id"]
    print(f"V2 task created: {task_id}")

    # Poll until terminal
    while True:
        await asyncio.sleep(POLL_INTERVAL)
        task_data = _get_task(task_id)
        status = task_data.get("status", "")
        if status in TERMINAL_STATUSES:
            break
        print(f"  V2 task {task_id} status: {status}")

    steps = task_data.get("steps", [])
    agent_steps = [_format_step(s) for s in steps]

    # Collect screenshots from step URLs
    screenshots_b64 = []
    for step in steps:
        url = step.get("screenshotUrl")
        if url:
            img = _fetch_screenshot_b64(url)
            if img:
                screenshots_b64.append(img)

    output = task_data.get("output") or "No output returned"
    cost_str = task_data.get("cost") or "0"
    cost = float(cost_str)
    duration = _duration_seconds(task_data)

    return ExecutionResult(
        final_result=output,
        steps=agent_steps,
        screenshots_b64=screenshots_b64,
        num_steps=len(steps),
        duration_seconds=duration,
        cost=cost,
    )


async def main():
    validate_params(parse_params(), ACCEPTED_PARAMS)
    task_index = int(os.environ["TASK_INDEX"])
    model_name = os.environ["MODEL"]
    eval_id = os.environ["EVAL_ID"]
    benchmark = os.environ.get("BENCHMARK", "BU_Bench_V1")

    tasks = load_tasks(benchmark)
    if len(tasks) == 100:
        tasks = interleave(tasks)
    task = tasks[task_index]
    task["_index"] = task_index

    LaminarService.initialize()
    LaminarService.attach_evaluation(eval_id)

    execute_fn = partial(execute, model_name=model_name)
    await run_and_judge(task, execute_fn)


if __name__ == "__main__":
    asyncio.run(main())
