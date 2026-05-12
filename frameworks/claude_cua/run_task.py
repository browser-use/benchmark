"""Run a single benchmark task using Claude Computer Use Agent.

CUA controls its own desktop environment (Xvfb + browser). The browser parameter
is meaningless for this framework -- it uses "integrated" as a placeholder.

The agent loop:
1. Launch Xvfb virtual display + browser
2. Send task to Claude with the computer tool
3. Loop: Claude emits actions -> execute on desktop -> screenshot -> send back
4. Collect steps and final result into ExecutionResult
"""

import asyncio
import os
import sys
import time
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


async def execute(task_description: str) -> ExecutionResult:
    """Run the Claude CUA agent loop on a task.

    TODO: Implement the full CUA agent loop:
    1. Start Xvfb + browser via subprocess
    2. Take initial screenshot
    3. Send to Anthropic Messages API with computer_20251124 tool
    4. Loop: parse tool_use blocks, execute actions, screenshot, send tool_result
    5. Collect all steps and final text response
    """
    start = time.time()

    # import anthropic
    # client = anthropic.Anthropic()
    #
    # tools = [{"type": "computer_20251124", "name": "computer",
    #           "display_width_px": 1920, "display_height_px": 1080}]
    # messages = [{"role": "user", "content": task_description}]
    #
    # steps = []
    # screenshots_b64 = []
    # for _ in range(50):  # max iterations
    #     response = client.beta.messages.create(
    #         model="claude-sonnet-4-20250514", max_tokens=4096,
    #         tools=tools, messages=messages, betas=["computer-use-2025-11-24"],
    #     )
    #     ... execute actions, collect screenshots, break on end_turn ...

    duration = time.time() - start

    return ExecutionResult(
        final_result="NOT IMPLEMENTED",
        steps=[],
        screenshots_b64=[],
        num_steps=0,
        duration_seconds=duration,
        cost=0,
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
