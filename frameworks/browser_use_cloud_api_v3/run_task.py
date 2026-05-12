"""Run a single benchmark task using the Browser Use Cloud API v3 (BU Agent).

Dispatches a task via POST /api/v3/sessions, polls GET /api/v3/sessions/{id}
until completion, fetches session messages to reconstruct step data, then maps
into ExecutionResult for the judge.

Step data is reconstructed from the messages endpoint to match the browser-use
agent_steps() ground truth format as closely as possible. Screenshots are not
available from this API.
"""

import asyncio
import json
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

ACCEPTED_PARAMS: dict[str, str] = {
    "skills": "Enable/disable skill memory (true/false, default: true)",
}

API_BASE = "https://api.browser-use.com/api/v3"
POLL_INTERVAL = 5
TERMINAL_STATUSES = {"stopped", "error", "timed_out"}

V3_MODEL_MAP = {
    "bu-mini": "bu-mini",
    "bu-max": "bu-max",
    "bu-ultra": "bu-ultra",
}

# Map V3 tool names to browser-use action names for ground-truth-like formatting.
TOOL_NAME_MAP = {
    "browser_navigate": "navigate",
    "browser_type_text": "input",
    "browser_wait": "wait",
    "browser_click": "click",
    "browser_scroll": "scroll",
    "browser_go_back": "go_back",
    "browser_search_google": "search_google",
    "browser_analyze_state": "analyze_state",
    "done_autonomous": "done",
}


def _headers() -> dict:
    return {"X-Browser-Use-API-Key": os.environ["BROWSER_USE_API_KEY"]}


def _create_session(task_description: str, model: str, skills: bool = True) -> dict:
    api_model = V3_MODEL_MAP.get(model, model)
    resp = httpx.post(
        f"{API_BASE}/sessions",
        headers=_headers(),
        json={"task": task_description, "model": api_model, "skills": skills},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def _get_session(session_id: str) -> dict:
    resp = httpx.get(
        f"{API_BASE}/sessions/{session_id}",
        headers=_headers(),
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def _get_all_messages(session_id: str) -> list[dict]:
    """Paginate through all messages for a session."""
    all_msgs = []
    after = None
    while True:
        params = {"limit": 100}
        if after:
            params["after"] = after
        resp = httpx.get(
            f"{API_BASE}/sessions/{session_id}/messages",
            headers=_headers(),
            params=params,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        msgs = data.get("messages", [])
        all_msgs.extend(msgs)
        if not data.get("hasMore") or not msgs:
            break
        after = msgs[-1]["id"]
    return all_msgs


def _parse_messages_to_steps(messages: list[dict]) -> list[str]:
    """Convert V3 session messages into ground-truth-style step strings.

    Groups assistant tool_calls with their corresponding tool results.
    Formats each group as:
        Step N:
        Actions: [json array, indent=1]
        Result M: <tool result content>
        Error M: <tool error content>
    """
    # Parse data fields
    parsed = []
    for msg in messages:
        data_str = msg.get("data", "{}")
        try:
            data = json.loads(data_str)
        except (json.JSONDecodeError, TypeError):
            continue
        data["_role"] = msg.get("role", data.get("role", ""))
        parsed.append(data)

    # Index tool results by tool_call_id
    tool_results: dict[str, dict] = {}
    for m in parsed:
        if m["_role"] == "tool":
            tcid = m.get("tool_call_id")
            if tcid:
                tool_results[tcid] = m

    # Collect all tool_call_ids claimed by assistant messages
    claimed_ids: set[str] = set()

    # Build steps from assistant messages that have tool_calls
    steps = []
    step_num = 0
    for m in parsed:
        if m["_role"] != "assistant":
            continue
        tool_calls = m.get("tool_calls")
        if not tool_calls:
            continue

        step_num += 1
        step_text = f"Step {step_num}:\n"

        # Build actions list matching ground truth format
        actions = []
        for tc in tool_calls:
            claimed_ids.add(tc.get("id", ""))
            func = tc.get("function", {})
            raw_name = func.get("name", "unknown")
            action_name = TOOL_NAME_MAP.get(raw_name, raw_name)
            try:
                args = json.loads(func.get("arguments", "{}"))
            except (json.JSONDecodeError, TypeError):
                args = {}
            actions.append({action_name: args})

        step_text += f"Actions: {json.dumps(actions, indent=1)}\n"

        # Append results/errors from tool messages
        for j, tc in enumerate(tool_calls):
            tcid = tc.get("id")
            tr = tool_results.get(tcid)
            if not tr:
                continue
            content = tr.get("content", "")
            is_error = tr.get("is_error", False)
            if is_error and content:
                step_text += f"Error {j + 1}: {content}\n"
            elif content:
                step_text += f"Result {j + 1}: {content}\n"

        steps.append(step_text)

    # Handle orphaned tool results (e.g. done_autonomous whose assistant
    # message was not returned by the API)
    for tcid, tr in tool_results.items():
        if tcid in claimed_ids:
            continue
        tool_name = tr.get("tool_name", "")
        action_name = TOOL_NAME_MAP.get(tool_name, tool_name)
        content = tr.get("content", "")
        if not content:
            continue
        step_num += 1
        step_text = f"Step {step_num}:\n"
        action_obj = [{action_name: {}}]
        step_text += f"Actions: {json.dumps(action_obj, indent=1)}\n"
        step_text += f"Result 1: {content}\n"
        steps.append(step_text)

    return steps


def _duration_seconds(session_data: dict) -> float:
    from datetime import datetime

    created = session_data.get("createdAt")
    updated = session_data.get("updatedAt")
    if not created or not updated:
        return 0.0
    try:
        t0 = datetime.fromisoformat(created.replace("Z", "+00:00"))
        t1 = datetime.fromisoformat(updated.replace("Z", "+00:00"))
        return max((t1 - t0).total_seconds(), 0.0)
    except Exception:
        return 0.0


async def execute(
    task_description: str, model_name: str, skills: bool = True
) -> ExecutionResult:
    """Create a v3 session, poll until done, fetch messages, return ExecutionResult."""
    session_data = _create_session(task_description, model_name, skills=skills)
    session_id = session_data["id"]
    print(f"V3 session created: {session_id}")

    while True:
        await asyncio.sleep(POLL_INTERVAL)
        session_data = _get_session(session_id)
        status = session_data.get("status", "")
        if status in TERMINAL_STATUSES:
            break
        print(f"  V3 session {session_id} status: {status}")

    output = session_data.get("output")
    if isinstance(output, dict):
        output = json.dumps(output)
    output = output or "No output returned"

    cost_str = session_data.get("totalCostUsd") or "0"
    cost = float(cost_str)
    duration = _duration_seconds(session_data)

    # Fetch messages and reconstruct steps
    messages = _get_all_messages(session_id)
    agent_steps = _parse_messages_to_steps(messages)

    return ExecutionResult(
        final_result=output,
        steps=agent_steps,
        screenshots_b64=[],  # Not available from V3 API
        num_steps=len(agent_steps),
        duration_seconds=duration,
        cost=cost,
    )


async def main():
    params = validate_params(parse_params(), ACCEPTED_PARAMS)
    task_index = int(os.environ["TASK_INDEX"])
    model_name = os.environ["MODEL"]
    eval_id = os.environ["EVAL_ID"]
    benchmark = os.environ.get("BENCHMARK", "BU_Bench_V1")

    skills = params.get("skills", "true").lower() != "false"

    tasks = load_tasks(benchmark)
    if len(tasks) == 100:
        tasks = interleave(tasks)
    task = tasks[task_index]
    task["_index"] = task_index

    LaminarService.initialize()
    LaminarService.attach_evaluation(eval_id)

    execute_fn = partial(execute, model_name=model_name, skills=skills)
    await run_and_judge(task, execute_fn)


if __name__ == "__main__":
    asyncio.run(main())
