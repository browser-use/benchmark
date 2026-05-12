"""Run a single benchmark task using Claude Code driving browser-harness-js.

This is the JavaScript-CDP variant of `claude-code-harness`. Claude Code owns the
agent loop; the agent drives a remote Chrome via the `browser-harness-js` CLI
(typed CDP wrappers exposed as a single-process bun REPL). We pre-provision a
browser-use-cloud session and pass its WebSocket CDP URL via `BU_CDP_WS`; the
agent calls `session.connect({ wsUrl: process.env.BU_CDP_WS })` to attach.

The joint system being benchmarked is (Claude Code + browser-harness-js + Claude
model). Pin `claude_code_version` and `framework_ref` for reproducible
comparisons against the Python `claude-code-harness` framework.
"""

import asyncio
import base64
import json
import os
import re
import shutil
import sys
import time
import urllib.request
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

ACCEPTED_PARAMS: dict[str, str] = {
    "max_turns": "Max Claude Code agentic turns (default: 100)",
    "max_budget_usd": "Per-task API budget cap in USD (default: 10)",
    "claude_code_version": "Claude Code npm version; consumed by the workflow install step (default: latest)",
    "framework_repo": "Override GitHub repo for browser-harness-js install (e.g. fork/browser-harness-js). Consumed by the workflow install step.",
    "use_bare": "Pass --bare to claude to skip hook/MCP/plugin auto-discovery (true/false, default: true)",
    "task_timeout": "Per-task wall-clock timeout in seconds, sets TASK_TIMEOUT for run_and_judge (default: 1800).",
}

SYSTEM_PROMPT_FILE = Path(__file__).resolve().parent / "system_prompt.md"
SHOTS_DIR = Path("/tmp/shots")
WORK_DIR = Path("/tmp/cch-js-workdir")
FINAL_ANSWER_RE = re.compile(r"FINAL ANSWER:\s*(.+?)\s*$", re.MULTILINE)

# Subtypes Claude Code emits in the terminal `result` event. Anything other than
# 'success' means the agent did not complete the task (usually a limit was hit).
RESULT_SUCCESS = "success"
LIMIT_SUBTYPES = {
    "error_max_turns",
    "error_max_tokens",
    "error_max_budget_usd",
    "error_during_execution",
    "error_api_error",
}


def _require_claude_model(model_name: str) -> str:
    """This framework only supports Claude models (Claude Code requires them)."""
    if not model_name.startswith("claude-"):
        raise ValueError(
            f"claude-code-harness-js requires a Claude model. Got: {model_name!r}. "
            f"Supported model aliases start with 'claude-' (see models.py)."
        )
    return model_name


def _reset_dir(p: Path) -> None:
    if p.exists():
        shutil.rmtree(p)
    p.mkdir(parents=True)


def _collect_screenshots() -> list[str]:
    """Read every PNG written to /tmp/shots in step order as base64."""
    if not SHOTS_DIR.exists():
        return []
    paths = sorted(p for p in SHOTS_DIR.glob("*.png") if p.is_file())
    return [base64.b64encode(p.read_bytes()).decode() for p in paths]


# ---- Browser-Use Cloud session provisioning (mirrors bcode runner) ----

def _bu_api_base() -> str:
    base = os.environ.get("BU_CLOUD_API_BASE", "https://api.browser-use.com").rstrip("/")
    version = os.environ.get("BU_CLOUD_API_VERSION", "v3")
    return f"{base}/api/{version}"


def _bu_api_key() -> str:
    return os.environ.get("BU_CLOUD_API_KEY") or os.environ["BROWSER_USE_API_KEY"]


def _bu(path: str, method: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(
        f"{_bu_api_base()}{path}",
        method=method,
        data=(json.dumps(body).encode() if body is not None else None),
        headers={"X-Browser-Use-API-Key": _bu_api_key(), "Content-Type": "application/json"},
    )
    return json.loads(urllib.request.urlopen(req, timeout=90).read() or b"{}")


def _start_browser(browser_name: str) -> tuple[str, str]:
    """Allocate a browser-use-cloud session. Returns (browser_id, cdp_ws)."""
    if browser_name != "browser-use-cloud":
        raise ValueError(f"Unsupported browser for claude-code-harness-js: {browser_name}")
    info = _bu("/browsers", "POST", {})
    cdp_ws = json.loads(
        urllib.request.urlopen(f"{info['cdpUrl']}/json/version", timeout=15).read()
    )["webSocketDebuggerUrl"]
    return info["id"], cdp_ws


def _stop_browser(browser_id: str | None) -> None:
    if not browser_id:
        return
    try:
        _bu(f"/browsers/{browser_id}", "PATCH", {"action": "stop"})
    except Exception as e:
        print(f"Warning: failed to stop browser {browser_id}: {e}")


# ---- Claude Code invocation (identical to CCH except for cwd) ----

def _build_claude_cmd(
    task_description: str,
    model_name: str,
    max_turns: int,
    max_budget_usd: float,
    use_bare: bool,
) -> list[str]:
    cmd = [
        "claude",
        "-p",
        task_description,
        "--model",
        model_name,
        "--dangerously-skip-permissions",
        "--output-format",
        "stream-json",
        "--verbose",
        "--max-turns",
        str(max_turns),
        "--max-budget-usd",
        str(max_budget_usd),
        "--append-system-prompt-file",
        str(SYSTEM_PROMPT_FILE),
        "--no-session-persistence",
    ]
    if use_bare:
        cmd.append("--bare")
    return cmd


def _format_assistant_block(block: dict) -> str | None:
    btype = block.get("type")
    if btype == "tool_use":
        name = block.get("name", "?")
        inp = block.get("input", {}) or {}
        if name == "Bash":
            return f"Bash: {(inp.get('command') or '').strip()[:2000]}"
        if name in ("Edit", "Write", "Read"):
            path = inp.get("file_path") or inp.get("path") or ""
            return f"{name}: {path}"
        try:
            return f"{name}: {json.dumps(inp, separators=(',', ':'))[:2000]}"
        except Exception:
            return name
    if btype == "text":
        text = (block.get("text") or "").strip()
        if not text:
            return None
        return f"text: {text[:2000]}"
    if btype == "thinking":
        text = (block.get("thinking") or "").strip()
        if not text:
            return None
        return f"thinking: {text[:2000]}"
    return None


def _format_tool_result_block(block: dict) -> str | None:
    if block.get("type") != "tool_result":
        return None
    content = block.get("content")
    is_error = bool(block.get("is_error"))
    prefix = "tool_error" if is_error else "tool_result"
    if isinstance(content, list):
        parts = []
        for c in content:
            if isinstance(c, dict):
                if c.get("type") == "text":
                    parts.append(c.get("text", ""))
                elif c.get("type") == "image":
                    parts.append("<image>")
        content = "\n".join(parts)
    if not isinstance(content, str):
        try:
            content = json.dumps(content, default=str)
        except Exception:
            content = str(content)
    content = content.strip()
    if not content:
        return None
    return f"{prefix}: {content[:2000]}"


def _format_event_steps(event: dict) -> list[str]:
    etype = event.get("type")
    if etype == "assistant":
        msg = event.get("message", {}) or {}
        steps = []
        for block in msg.get("content", []) or []:
            s = _format_assistant_block(block)
            if s:
                steps.append(s)
        return steps
    if etype == "user":
        msg = event.get("message", {}) or {}
        steps = []
        for block in msg.get("content", []) or []:
            s = _format_tool_result_block(block)
            if s:
                steps.append(s)
        return steps
    return []


def _summarize_result_event(event: dict) -> tuple[str, bool, list[str]]:
    subtype = event.get("subtype") or RESULT_SUCCESS
    is_error = bool(event.get("is_error"))
    errors_raw = event.get("errors") or []
    errors = [str(e) for e in errors_raw] if isinstance(errors_raw, list) else [str(errors_raw)]
    return subtype, is_error, errors


async def _drain_stderr(proc: asyncio.subprocess.Process, buf: list[str]) -> None:
    assert proc.stderr is not None
    while True:
        line = await proc.stderr.readline()
        if not line:
            break
        try:
            s = line.decode("utf-8", errors="replace").rstrip("\n")
        except Exception:
            s = repr(line)
        buf.append(s)
        print(f"[claude-stderr] {s}", flush=True)


async def execute(task_description: str) -> ExecutionResult:
    params = validate_params(parse_params(), ACCEPTED_PARAMS)
    model_name = _require_claude_model(os.environ["MODEL"])
    browser_name = os.environ.get("BROWSER", "browser-use-cloud")
    max_turns = int(params.get("max_turns", "100"))
    max_budget_usd = float(params.get("max_budget_usd", "10"))
    use_bare = params.get("use_bare", "true").lower() != "false"

    _reset_dir(SHOTS_DIR)
    _reset_dir(WORK_DIR)

    # Pre-provision a remote browser; pass its WS URL to the agent via env.
    # The agent connects with `session.connect({ wsUrl: process.env.BU_CDP_WS })`.
    browser_id, cdp_ws = _start_browser(browser_name)

    env = {
        **os.environ,
        "BU_CDP_WS": cdp_ws,
        "DISABLE_TELEMETRY": "1",
        "DISABLE_AUTOUPDATER": "1",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        # browser-harness-js auto-installs bun on first run if missing; we
        # pre-installed bun in the workflow, so opt out of any check-in noise.
        "BROWSER_HARNESS_SKIP_BUN_INSTALL": "1",
    }

    cmd = _build_claude_cmd(
        task_description, model_name, max_turns, max_budget_usd, use_bare
    )

    start = time.time()
    steps: list[str] = []
    final_text = ""
    total_cost = 0.0
    result_subtype: str | None = None
    result_is_error = False
    result_errors: list[str] = []
    stderr_buf: list[str] = []

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(WORK_DIR),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        limit=256 * 1024 * 1024,
    )

    stderr_task = asyncio.create_task(_drain_stderr(proc, stderr_buf))

    async def _iter_stdout_lines():
        assert proc.stdout is not None
        buf = bytearray()
        CHUNK = 1 << 16
        while True:
            chunk = await proc.stdout.read(CHUNK)
            if not chunk:
                if buf:
                    yield bytes(buf)
                    buf.clear()
                return
            buf.extend(chunk)
            while True:
                nl = buf.find(b"\n")
                if nl < 0:
                    break
                line_bytes = bytes(buf[:nl])
                del buf[: nl + 1]
                yield line_bytes

    try:
        assert proc.stdout is not None
        async for raw in _iter_stdout_lines():
            if not raw:
                continue
            line = raw.decode("utf-8", errors="replace").rstrip("\n")
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                print(f"[claude-stdout-raw] {line}", flush=True)
                continue

            new_steps = _format_event_steps(event)
            for s in new_steps:
                steps.append(s)
                print(f"[step {len(steps):>3}] {s[:500]}", flush=True)

            if event.get("type") == "result":
                final_text = event.get("result") or ""
                total_cost = float(event.get("total_cost_usd") or 0.0)
                result_subtype, result_is_error, result_errors = _summarize_result_event(event)
                print(
                    f"[claude-result] subtype={result_subtype} is_error={result_is_error} "
                    f"cost=${total_cost:.4f} errors={result_errors}",
                    flush=True,
                )

        try:
            await asyncio.wait_for(proc.wait(), timeout=60)
        except asyncio.TimeoutError:
            print("[claude-runner] proc did not exit within 60s of stdout close; killing", flush=True)
            proc.kill()
            await proc.wait()

        try:
            await asyncio.wait_for(stderr_task, timeout=10)
        except asyncio.TimeoutError:
            stderr_task.cancel()
    finally:
        if proc.returncode is None:
            proc.kill()
            try:
                await asyncio.wait_for(proc.wait(), timeout=10)
            except asyncio.TimeoutError:
                pass
        if not stderr_task.done():
            stderr_task.cancel()
        # Best-effort: stop the bun REPL server so it doesn't leak across tasks.
        try:
            stop_proc = await asyncio.create_subprocess_exec(
                "browser-harness-js", "--stop",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(stop_proc.wait(), timeout=10)
        except Exception:
            pass
        _stop_browser(browser_id)

    duration = time.time() - start
    stderr_tail = "\n".join(stderr_buf[-50:])

    if result_subtype is None and proc.returncode not in (0, None):
        raise RuntimeError(
            f"claude exited with code {proc.returncode} before emitting a result event. "
            f"steps_captured={len(steps)} duration={duration:.1f}s stderr_tail:\n{stderr_tail[-2000:]}"
        )

    match = FINAL_ANSWER_RE.search(final_text or "")
    answer = match.group(1).strip() if match else (final_text.strip() or "")

    if result_subtype and result_subtype != RESULT_SUCCESS:
        err_suffix = f" errors={result_errors}" if result_errors else ""
        if answer:
            final_result = f"[{result_subtype}] {answer}{err_suffix}"
        else:
            final_result = f"[{result_subtype}] Agent did not complete task.{err_suffix}"
    else:
        final_result = answer or "Agent did not emit FINAL ANSWER line"

    return ExecutionResult(
        final_result=final_result,
        steps=steps,
        screenshots_b64=_collect_screenshots(),
        num_steps=len(steps),
        duration_seconds=duration,
        cost=total_cost,
    )


async def main():
    task_index = int(os.environ["TASK_INDEX"])
    eval_id = os.environ["EVAL_ID"]
    benchmark = os.environ.get("BENCHMARK", "BU_Bench_V1")

    early_params = parse_params()
    if "task_timeout" in early_params:
        os.environ["TASK_TIMEOUT"] = early_params["task_timeout"]

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
