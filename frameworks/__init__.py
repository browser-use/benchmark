"""Framework registry and shared local evaluation flow.

This public runner intentionally avoids the remote dispatch/tracing stack.
It loads the encrypted benchmark file, executes one task with a framework
adapter, judges the trace, and writes local JSON artifacts under ignored paths.
"""

import asyncio
import base64
import hashlib
import json
import os
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from browser_use import ChatGoogle
from cryptography.fernet import Fernet

from judge import JudgementResult, construct_judge_messages

ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_TASK_TIMEOUT = 1800


def _task_timeout() -> int:
    raw = os.environ.get("TASK_TIMEOUT")
    if not raw:
        return DEFAULT_TASK_TIMEOUT
    try:
        return int(raw)
    except ValueError:
        return DEFAULT_TASK_TIMEOUT


def parse_params() -> dict[str, str]:
    """Parse PARAMS env var. Format: key=value,key=value."""
    raw = os.environ.get("PARAMS", "")
    if not raw:
        return {}
    params: dict[str, str] = {}
    for part in raw.split(","):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        key = key.strip()
        if key:
            params[key] = value.strip()
    return params


def validate_params(params: dict[str, str], accepted: dict[str, str]) -> dict[str, str]:
    unknown = set(params) - set(accepted)
    if unknown:
        accepted_keys = ", ".join(sorted(accepted)) or "none"
        raise ValueError(
            f"Unknown params: {', '.join(sorted(unknown))}. Accepted: {accepted_keys}"
        )
    return params


@dataclass
class ExecutionResult:
    final_result: str
    steps: list[str]
    screenshots_b64: list[str]
    num_steps: int
    duration_seconds: float
    cost: float = 0.0


@dataclass
class FrameworkInfo:
    browsers: list[str]
    repo: str | None = None
    max_concurrent_override: int | None = None
    notes: str = ""


FRAMEWORKS: dict[str, FrameworkInfo] = {
    "browser-use": FrameworkInfo(
        browsers=[
            "browser-use-cloud",
            "anchor",
            "browserbase",
            "browserless",
            "driver",
            "hyperbrowser",
            "local_headful",
            "local_headless",
            "onkernel",
            "rebrowser",
            "steel",
        ],
        repo="browser-use/browser-use",
    ),
    "browser-use-cloud-api-v2": FrameworkInfo(browsers=["integrated"]),
    "browser-use-cloud-api-v3": FrameworkInfo(browsers=["integrated"]),
    "bcode": FrameworkInfo(browsers=["browser-use-cloud"], repo="browser-use/browsercode"),
    "bcode-v012": FrameworkInfo(
        browsers=["browser-use-cloud"],
        repo="browser-use/browsercode",
        notes="Alias for the bcode adapter used with framework_ref v0.1.2.",
    ),
    "browserbase-agent": FrameworkInfo(browsers=["integrated"]),
    "stagehand": FrameworkInfo(
        browsers=["browserbase", "local_headless"],
        repo="browserbase/stagehand",
        notes="Adapter scaffold; executor must be completed before use.",
    ),
    "claude-code-harness": FrameworkInfo(
        browsers=["browser-use-cloud"], repo="browser-use/browser-harness"
    ),
    "claude-code-harness-js": FrameworkInfo(
        browsers=["browser-use-cloud"], repo="browser-use/browser-harness-js"
    ),
    "claude-code-harness-ab": FrameworkInfo(
        browsers=["browser-use-cloud"], repo="vercel-labs/agent-browser"
    ),
    "claude-code-harness-bu-cli": FrameworkInfo(
        browsers=["browser-use-cloud"], repo="browser-use/browser-use"
    ),
    "codex-harness": FrameworkInfo(
        browsers=["browser-use-cloud"], repo="browser-use/browser-harness"
    ),
    "pi-harness": FrameworkInfo(
        browsers=["browser-use-cloud"], repo="browser-use/browser-harness"
    ),
    "pibt": FrameworkInfo(
        browsers=["browser-use-cloud"], repo="browser-use/pi-agent-extensions"
    ),
    "but": FrameworkInfo(
        browsers=["browser-use-cloud"], repo="browser-use/browser-use-terminal"
    ),
    "but-rust": FrameworkInfo(
        browsers=["browser-use-cloud"], repo="browser-use/browser-use-terminal"
    ),
    "claude-cua": FrameworkInfo(
        browsers=["integrated"],
        notes="Adapter scaffold; not used for the published BU_Bench_V1 runs.",
    ),
}


def framework_to_module(framework: str) -> str:
    if framework == "bcode-v012":
        return "bcode"
    return framework.replace("-", "_")


def interleave(tasks: list[dict]) -> list[dict]:
    """Reorder 100 tasks, 20 per section, matching the distributed runner."""
    if os.environ.get("NO_INTERLEAVE") == "1":
        return tasks
    if len(tasks) != 100:
        return tasks
    reordered = []
    for i in range(20):
        for d in range(5):
            reordered.append(tasks[d * 20 + i])
    return reordered


def _encrypted_task_file(benchmark: str) -> Path:
    candidates = [
        ROOT_DIR / f"{benchmark}.enc",
        ROOT_DIR / f"{benchmark.upper()}.enc",
        ROOT_DIR / "benchmarks" / f"{benchmark}.enc",
    ]
    for path in candidates:
        if path.exists():
            return path
    raise FileNotFoundError(
        f"Could not find encrypted task file for {benchmark}. Expected "
        f"{ROOT_DIR / (benchmark + '.enc')}"
    )


def load_tasks(benchmark: str) -> list[dict]:
    """Load tasks from the encrypted public artifact without writing plaintext."""
    task_file = _encrypted_task_file(benchmark)
    key = base64.urlsafe_b64encode(hashlib.sha256(benchmark.encode()).digest())
    encrypted = base64.b64decode(task_file.read_text())
    return json.loads(Fernet(key).decrypt(encrypted))


JUDGE_LLM = None


def _get_judge_llm():
    global JUDGE_LLM
    if JUDGE_LLM is None:
        JUDGE_LLM = ChatGoogle(
            model=os.environ.get("JUDGE_MODEL", "gemini-2.5-flash"),
            api_key=os.getenv("GOOGLE_API_KEY"),
        )
    return JUDGE_LLM


async def _evaluate_task(judge_messages) -> JudgementResult:
    response = await _get_judge_llm().ainvoke(
        judge_messages, output_format=JudgementResult
    )
    return response.completion


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _maybe_write_task_artifact(
    task: dict,
    result: ExecutionResult | None,
    judgement: dict[str, Any] | None,
    score: int,
    error: str | None = None,
    tb: str | None = None,
) -> None:
    run_data_dir_raw = os.environ.get("RUN_DATA_DIR")
    if not run_data_dir_raw:
        return
    task_id = task.get("task_id", f"task_{task.get('_index', 'unknown')}")
    payload: dict[str, Any] = {
        "task_id": task_id,
        "score": score,
        "judgement": judgement,
    }
    if result is not None:
        payload["agent_trace"] = {
            "agent_task": task.get("confirmed_task"),
            "final_result": result.final_result,
            "agent_steps": result.steps,
            "ground_truth": task.get("answer"),
            "screenshots_b64": result.screenshots_b64,
        }
        payload["metrics"] = {
            "steps": result.num_steps,
            "duration": result.duration_seconds,
            "cost": result.cost,
        }
    if error:
        payload["error"] = error
    if tb:
        payload["traceback"] = tb
    _write_json(Path(run_data_dir_raw) / f"{task_id}.json", payload)


def _maybe_write_local_result(data: dict[str, Any]) -> None:
    output = os.environ.get("LOCAL_RESULT_FILE")
    if output:
        _write_json(Path(output), data)


async def run_and_judge(
    task: dict,
    execute_fn: Callable[[str], Awaitable[ExecutionResult]],
) -> dict[str, Any]:
    """Execute one task, judge it, and return a task-level result dict."""
    task_id = task.get("task_id", "unknown")
    print(f"Running task: {task_id}")

    try:
        result = await asyncio.wait_for(
            execute_fn(task["confirmed_task"]), timeout=_task_timeout()
        )
        judge_messages = construct_judge_messages(
            task=task["confirmed_task"],
            final_result=result.final_result,
            agent_steps=result.steps,
            ground_truth=task.get("answer"),
            screenshots_b64=result.screenshots_b64,
        )
        judgement = await _evaluate_task(judge_messages)
        judgement_data = judgement.model_dump()
        score = 1 if judgement.verdict else 0
        print(f"Task {task_id} completed: score={score}")

        data = {
            "task_id": task_id,
            "task_index": task.get("_index"),
            "score": score,
            "steps": result.num_steps,
            "duration": result.duration_seconds,
            "cost": result.cost,
            "judgement": judgement_data,
        }
        _maybe_write_task_artifact(task, result, judgement_data, score)
        _maybe_write_local_result(data)
        return data

    except asyncio.TimeoutError:
        error_msg = f"Timed out after {_task_timeout()}s"
        print(f"Task {task_id} timed out after {_task_timeout()}s")
        data = {
            "task_id": task_id,
            "task_index": task.get("_index"),
            "score": 0,
            "steps": 0,
            "duration": _task_timeout(),
            "cost": 0,
            "error": error_msg,
        }
        _maybe_write_task_artifact(task, None, None, 0, error=error_msg)
        _maybe_write_local_result(data)
        return data

    except Exception as e:
        error_msg = f"{type(e).__name__}: {e}"
        tb = traceback.format_exc()
        print(f"Task {task_id} failed: {error_msg}")
        data = {
            "task_id": task_id,
            "task_index": task.get("_index"),
            "score": 0,
            "steps": 0,
            "duration": 0,
            "cost": 0,
            "error": error_msg,
            "traceback": tb,
        }
        _maybe_write_task_artifact(task, None, None, 0, error=error_msg, tb=tb)
        _maybe_write_local_result(data)
        return data
