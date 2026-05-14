"""Run a single benchmark task using Claude Code driving browser-harness.

This framework wraps Claude Code (the CLI coding agent) around the browser-harness
repo: Claude Code owns the agent loop, we just hand it a task and a workdir
pre-loaded with the harness + a live browser daemon, then stream-parse its output.

The joint system being benchmarked is (Claude Code + browser-harness + Claude model).
Pin `claude_code_version` and `framework_ref` for reproducible comparisons.
"""

import asyncio
import base64
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

# Add project root to path for sibling imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

# Harness is installed via `uv pip install /tmp/browser-harness` in the workflow,
# which exposes `admin`, `helpers`, `run`, `daemon` as top-level modules.
HARNESS_DIR = "/tmp/browser-harness"

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
    "framework_repo": "Override GitHub repo for browser-harness install (e.g. fork/browser-harness). Consumed by the workflow install step.",
    "use_bare": "Pass --bare to claude to skip hook/MCP/plugin auto-discovery (true/false, default: true)",
    "task_timeout": "Per-task wall-clock timeout in seconds, sets TASK_TIMEOUT for run_and_judge (default: 1800).",
}

SYSTEM_PROMPT_FILE = Path(__file__).resolve().parent / "system_prompt.md"
SHOTS_DIR = Path("/tmp/shots")
FINAL_ANSWER_RE = re.compile(r"FINAL ANSWER:\s*(.+?)\s*$", re.MULTILINE)

# Subtypes Claude Code emits in the terminal `result` event. Anything other than
# 'success' means the agent did not complete the task (usually a limit was hit).
# See: https://docs.claude.com/en/docs/claude-code/headless (stream-json spec)
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
            f"claude-code-harness requires a Claude model. Got: {model_name!r}. "
            f"Supported model aliases start with 'claude-' (see models.py)."
        )
    return model_name


def _reset_shots_dir() -> None:
    if SHOTS_DIR.exists():
        shutil.rmtree(SHOTS_DIR)
    SHOTS_DIR.mkdir(parents=True)


def _collect_screenshots() -> list[str]:
    """Read every PNG written to /tmp/shots in step order as base64."""
    if not SHOTS_DIR.exists():
        return []
    paths = sorted(p for p in SHOTS_DIR.glob("*.png") if p.is_file())
    return [base64.b64encode(p.read_bytes()).decode() for p in paths]


def _start_browser(browser_name: str, bu_name: str) -> dict:
    """Provision a browser for the harness to attach to. Returns the cloud browser dict."""
    if browser_name != "browser-use-cloud":
        raise ValueError(f"Unsupported browser for claude-code-harness: {browser_name}")
    sys.path.insert(0, HARNESS_DIR)
    from admin import start_remote_daemon  # type: ignore

    return start_remote_daemon(name=bu_name)


def _stop_browser(browser_name: str, bu_name: str) -> None:
    try:
        sys.path.insert(0, HARNESS_DIR)
        from admin import stop_remote_daemon  # type: ignore

        if browser_name == "browser-use-cloud":
            stop_remote_daemon(name=bu_name)
    except Exception as e:
        print(f"Warning: failed to stop harness daemon: {e}")


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
    """Turn a single assistant message content block into a step string."""
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
    """Turn a user message tool_result block into a step string."""
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
    # Cap per-step size so Laminar payloads stay reasonable.
    return f"{prefix}: {content[:2000]}"


def _format_event_steps(event: dict) -> list[str]:
    """Extract step strings from any stream-json event. Empty list = not a step."""
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
    """Parse the terminal `result` event. Returns (subtype, is_error, errors)."""
    subtype = event.get("subtype") or RESULT_SUCCESS
    is_error = bool(event.get("is_error"))
    errors_raw = event.get("errors") or []
    errors = [str(e) for e in errors_raw] if isinstance(errors_raw, list) else [str(errors_raw)]
    return subtype, is_error, errors


async def _drain_stderr(proc: asyncio.subprocess.Process, buf: list[str]) -> None:
    """Read stderr line-by-line, echo to our stdout, and buffer for later reporting."""
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
        # Surface to GitHub Actions log in real time.
        print(f"[claude-stderr] {s}", flush=True)


async def execute(task_description: str) -> ExecutionResult:
    params = validate_params(parse_params(), ACCEPTED_PARAMS)
    model_name = _require_claude_model(os.environ["MODEL"])
    browser_name = os.environ.get("BROWSER", "browser-use-cloud")
    task_index = os.environ.get("TASK_INDEX", "0")
    max_turns = int(params.get("max_turns", "100"))
    max_budget_usd = float(params.get("max_budget_usd", "10"))
    use_bare = params.get("use_bare", "true").lower() != "false"
    # task_timeout is consumed in main() before run_and_judge wraps execute.

    bu_name = f"eval-{task_index}"
    _reset_shots_dir()

    # Pre-provision the browser so Claude starts with a live CDP attach.
    _start_browser(browser_name, bu_name)

    try:
        env = {
            **os.environ,
            "BU_NAME": bu_name,
            "DISABLE_TELEMETRY": "1",
            "DISABLE_AUTOUPDATER": "1",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        }

        cmd = _build_claude_cmd(
            task_description, model_name, max_turns, max_budget_usd, use_bare
        )
    except Exception:
        _stop_browser(browser_name, bu_name)
        raise

    start = time.time()
    steps: list[str] = []
    final_text = ""
    total_cost = 0.0
    result_subtype: str | None = None
    result_is_error = False
    result_errors: list[str] = []
    stderr_buf: list[str] = []

    # claude stream-json lines can be huge (tool_result blocks with full page
    # HTML/text, assistant messages with signed thinking blocks). Default
    # asyncio StreamReader line buffer is 64 KiB which raises ValueError on
    # long lines, and even a larger limit has a ceiling. Read raw chunks and
    # split on newlines ourselves.
    proc: asyncio.subprocess.Process | None = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=HARNESS_DIR,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=256 * 1024 * 1024,  # 256 MiB safety cap
        )
        stderr_task = asyncio.create_task(_drain_stderr(proc, stderr_buf))
    except Exception:
        if proc is not None and proc.returncode is None:
            proc.kill()
            try:
                await asyncio.wait_for(proc.wait(), timeout=10)
            except asyncio.TimeoutError:
                pass
        _stop_browser(browser_name, bu_name)
        raise

    async def _iter_stdout_lines():
        """Yield one stream-json line at a time, regardless of line length."""
        assert proc.stdout is not None
        buf = bytearray()
        CHUNK = 1 << 16  # 64 KiB
        while True:
            chunk = await proc.stdout.read(CHUNK)
            if not chunk:
                if buf:
                    yield bytes(buf)
                    buf.clear()
                return
            buf.extend(chunk)
            # Emit every complete line in the buffer.
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
                # Non-JSON line from claude (shouldn't happen in stream-json, but be safe)
                print(f"[claude-stdout-raw] {line}", flush=True)
                continue

            new_steps = _format_event_steps(event)
            for s in new_steps:
                steps.append(s)
                # Echo each step so GitHub Actions log shows live progress.
                print(f"[step {len(steps):>3}] {s[:500]}", flush=True)

            # Terminal event
            if event.get("type") == "result":
                final_text = event.get("result") or ""
                total_cost = float(event.get("total_cost_usd") or 0.0)
                result_subtype, result_is_error, result_errors = _summarize_result_event(event)
                print(
                    f"[claude-result] subtype={result_subtype} is_error={result_is_error} "
                    f"cost=${total_cost:.4f} errors={result_errors}",
                    flush=True,
                )

        # Wait for the process (stdout closed implies near-exit)
        try:
            await asyncio.wait_for(proc.wait(), timeout=60)
        except asyncio.TimeoutError:
            print("[claude-runner] proc did not exit within 60s of stdout close; killing", flush=True)
            proc.kill()
            await proc.wait()

        # Drain remaining stderr
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
        _stop_browser(browser_name, bu_name)

    duration = time.time() - start
    stderr_tail = "\n".join(stderr_buf[-50:])

    # If we never saw a `result` event AND claude exited non-zero, that is a true
    # hard error (e.g. CLI startup failure, killed by OS). Surface it.
    if result_subtype is None and proc.returncode not in (0, None):
        raise RuntimeError(
            f"claude exited with code {proc.returncode} before emitting a result event. "
            f"steps_captured={len(steps)} duration={duration:.1f}s stderr_tail:\n{stderr_tail[-2000:]}"
        )

    # Determine final_result text.
    match = FINAL_ANSWER_RE.search(final_text or "")
    answer = match.group(1).strip() if match else (final_text.strip() or "")

    if result_subtype and result_subtype != RESULT_SUCCESS:
        # Agent hit a limit or errored but Claude Code reported it cleanly.
        # Preserve the datapoint: tag the final_result with the subtype and let the
        # judge score whatever was accomplished.
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

    # Propagate task_timeout param to run_and_judge before it wraps execute().
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
