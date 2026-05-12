"""Run a single benchmark task using the Stagehand agent framework.

Stagehand is a TypeScript framework. This Python entry point:
1. Loads the task and wires up Laminar (shared infra)
2. Shells out to node executor.js which runs the Stagehand agent
3. Parses the JSON result from stdout into ExecutionResult
4. Feeds it into the shared judge flow
"""

import json
import os
import subprocess
import sys
import asyncio
from pathlib import Path

# Add project root to path for sibling imports
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

EXECUTOR_DIR = Path(__file__).resolve().parent
EXECUTOR_SCRIPT = EXECUTOR_DIR / "executor.js"


async def execute(task_description: str) -> ExecutionResult:
    """Run the Stagehand agent via node subprocess."""
    browser_name = os.environ.get("BROWSER", "browserbase")

    env = {**os.environ, "TASK_DESCRIPTION": task_description, "BROWSER": browser_name}
    proc = subprocess.run(
        ["node", str(EXECUTOR_SCRIPT)],
        capture_output=True,
        text=True,
        timeout=900,
        env=env,
        cwd=str(EXECUTOR_DIR),
    )

    if proc.returncode != 0:
        raise RuntimeError(f"Stagehand executor failed: {proc.stderr}")

    data = json.loads(proc.stdout)
    return ExecutionResult(
        final_result=data.get("final_result", ""),
        steps=data.get("steps", []),
        screenshots_b64=data.get("screenshots_b64", []),
        num_steps=data.get("num_steps", 0),
        duration_seconds=data.get("duration_seconds", 0),
        cost=data.get("cost", 0),
    )


async def main():
    validate_params(parse_params(), ACCEPTED_PARAMS)
    task_index = int(os.environ["TASK_INDEX"])
    eval_id = os.environ["EVAL_ID"]
    benchmark = os.environ.get("BENCHMARK", "BU_Bench_V1")

    tasks = load_tasks(benchmark)
    if len(tasks) == 100:
        tasks = interleave(tasks)
    task = tasks[task_index]
    task["_index"] = task_index

    LaminarService.initialize()
    LaminarService.attach_evaluation(eval_id)

    await run_and_judge(task, execute)


if __name__ == "__main__":
    asyncio.run(main())
