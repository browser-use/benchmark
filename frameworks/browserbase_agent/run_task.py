"""Run a single benchmark task using the Stagehand agent SDK (client-side) on
Browserbase cloud.

We used to dispatch against the hosted Stagehand REST API at
`api.stagehand.browserbase.com/v1` (no Node deps, pure Python HTTP). That
endpoint is alpha and pinned to an old Stagehand server build that predates
the opus-4-7 temperature fix (Stagehand PRs #2006/#2018, shipped in
stagehand-server-v3 v3.6.5 on May 6 2026), so opus-4-7 + the hosted API
silently dies inside the Stagehand `fillForm` tool. Out of our control.

Instead this runner shells out to a Node executor (`executor.mjs`) that
imports `@browserbasehq/stagehand` directly, pinned in package.json to a
client release that has the fix. Same approach Browserbase tells customers
to use for production (deploy the SDK in your own runtime). Joint system
benchmarked is unchanged: (Stagehand agent + Browserbase cloud browser +
model).

Model routing: by default Stagehand auto-routes through the Browserbase
Model Gateway when only the Browserbase API key is set on the constructor
and no provider env key is present. We scrub provider keys
(ANTHROPIC/OPENAI/GOOGLE/GOOGLE_GENERATIVE_AI/GEMINI) from the spawn env
when `use_gateway` (default true) so the SDK picks the gateway path even
though our workflow secrets normally inject them globally. Set
`use_gateway=false` via params to fall back to direct-provider billing
(useful for models the gateway hasn't onboarded yet).

Concurrency: limited by the Browserbase plan, NOT by our infra. The
framework registry sets `max_concurrent_override` to match
`browsers/browserbase.py` (currently 20).
"""

import asyncio
import json
import os
import sys
import time
from functools import partial
from pathlib import Path

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

ACCEPTED_PARAMS: dict[str, str] = {
    "max_steps": "Max Stagehand agent steps per task (default: 25).",
    "use_gateway": (
        "Route inference via Browserbase Model Gateway (true/false, "
        "default: true). When true, the runner scrubs provider env keys "
        "from the Node subprocess so Stagehand auto-routes via gateway. "
        "When false, provider env keys pass through and the SDK bills the "
        "provider directly. Use false for models the gateway hasn't "
        "onboarded yet."
    ),
}

# Map benchmark model aliases to Stagehand gateway slugs. Slugs
# already containing '/' pass through verbatim.
MODEL_MAP = {
    "claude-sonnet-4-6": "anthropic/claude-sonnet-4-6",
    "claude-sonnet-4-5": "anthropic/claude-sonnet-4-5",
    "claude-opus-4-5": "anthropic/claude-opus-4-5",
    "claude-opus-4-6": "anthropic/claude-opus-4-6",
    "claude-opus-4-7": "anthropic/claude-opus-4-7",
    "gpt-5": "openai/gpt-5",
    "gpt-5-mini": "openai/gpt-5-mini",
    "gemini-2.5-flash": "google/gemini-2.5-flash",
    "gemini-2.5-pro": "google/gemini-2.5-pro",
}

EXECUTOR_DIR = Path(__file__).resolve().parent
EXECUTOR_SCRIPT = EXECUTOR_DIR / "executor.mjs"

# Provider env keys to scrub when running in gateway mode. Anything Stagehand
# might autoload (per https://docs.stagehand.dev/v3/configuration/models --
# "Error: API key not found" section).
_PROVIDER_ENV_KEYS = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GOOGLE_API_KEY",
    "GOOGLE_GENERATIVE_AI_API_KEY",
    "GEMINI_API_KEY",
)


def _resolve_model(model_name: str) -> str:
    if "/" in model_name:
        return model_name
    if model_name in MODEL_MAP:
        return MODEL_MAP[model_name]
    raise ValueError(
        f"Model '{model_name}' is not in MODEL_MAP and is not an explicit "
        f"`provider/model` slug. Extend MODEL_MAP or pass an explicit slug."
    )


def _build_env(model_slug: str, max_steps: int, use_gateway: bool) -> dict:
    """Construct the env dict for the Node subprocess.

    Forwards Browserbase creds + task config. If `use_gateway`, strips
    provider keys so Stagehand auto-routes via the Model Gateway.
    """
    env = dict(os.environ)
    env["STAGEHAND_MODEL"] = model_slug
    env["MAX_STEPS"] = str(max_steps)
    # Pass through BROWSERBASE_* unchanged (required by SDK).
    if use_gateway:
        for k in _PROVIDER_ENV_KEYS:
            env.pop(k, None)
    return env


async def execute(
    task_description: str, model_name: str, max_steps: int, use_gateway: bool
) -> ExecutionResult:
    """Spawn the Node executor, parse its single-JSON stdout into ExecutionResult."""
    model_slug = _resolve_model(model_name)
    print(
        f"Browserbase Stagehand SDK model_slug={model_slug} "
        f"max_steps={max_steps} use_gateway={use_gateway}"
    )

    env = _build_env(model_slug, max_steps, use_gateway)
    env["TASK_DESCRIPTION"] = task_description

    t0 = time.time()
    # Use asyncio subprocess so run_and_judge's outer asyncio.wait_for can
    # cancel us cleanly on timeout.
    proc = await asyncio.create_subprocess_exec(
        "node",
        str(EXECUTOR_SCRIPT),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
        cwd=str(EXECUTOR_DIR),
    )
    stdout, stderr = await proc.communicate()
    duration = time.time() - t0

    stderr_text = stderr.decode("utf-8", errors="replace")
    if stderr_text:
        # Stream Stagehand's logger output to our stdout for runner-log
        # debugging. Each line is already prefixed by the executor.
        print(stderr_text, end="")

    if proc.returncode != 0:
        # The executor's `fail()` path always exits 0 with a valid JSON
        # payload, so a non-zero return is a true crash (e.g. Node missing,
        # uncaught throw outside main, OOM). Surface as a failed datapoint
        # via the run_and_judge exception path.
        raise RuntimeError(
            f"executor.mjs crashed: returncode={proc.returncode}, "
            f"stderr_tail={stderr_text[-500:]!r}"
        )

    try:
        data = json.loads(stdout.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"executor.mjs produced invalid JSON: {e}; "
            f"stdout_head={stdout[:500]!r}"
        )

    # Prefer the executor's measured duration if it set one.
    duration_seconds = float(data.get("duration_seconds") or duration)

    return ExecutionResult(
        final_result=data.get("final_result", ""),
        steps=data.get("steps") or [],
        screenshots_b64=data.get("screenshots_b64") or [],
        num_steps=int(data.get("num_steps") or 0),
        duration_seconds=duration_seconds,
        cost=float(data.get("cost") or 0.0),
    )


async def main():
    params = validate_params(parse_params(), ACCEPTED_PARAMS)
    task_index = int(os.environ["TASK_INDEX"])
    model_name = os.environ["MODEL"]
    eval_id = os.environ["EVAL_ID"]
    benchmark = os.environ.get("BENCHMARK", "BU_Bench_V1")

    max_steps = int(params.get("max_steps", "25"))
    use_gateway = params.get("use_gateway", "true").lower() != "false"

    tasks = load_tasks(benchmark)
    if len(tasks) == 100:
        tasks = interleave(tasks)
    task = tasks[task_index]
    task["_index"] = task_index

    LaminarService.initialize()
    LaminarService.attach_evaluation(eval_id)

    execute_fn = partial(
        execute,
        model_name=model_name,
        max_steps=max_steps,
        use_gateway=use_gateway,
    )
    await run_and_judge(task, execute_fn)


if __name__ == "__main__":
    asyncio.run(main())
