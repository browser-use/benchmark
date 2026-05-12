"""Run a single benchmark task using pi (the @earendil-works/pi-coding-agent CLI)
driving browser-harness.

This framework is a near-mirror of `claude_code_harness`, except the coding
agent is `pi` instead of Claude Code. The browser side is unchanged: we still
pre-provision a live browser-harness daemon (Python `admin.start_remote_daemon`)
and let the agent drive it via `browser-harness <<'PY' ... PY` heredocs.

Joint system being benchmarked: (pi + browser-harness + Claude model).
Restricted to Claude models, mirroring CCH. Pin `pi_version` and
`framework_ref` for reproducible comparisons.

Pi event-stream notes (`pi --mode json`):
- First line is a `session` header (`{"type":"session",...}`).
- `tool_execution_start` / `tool_execution_end` carry tool calls + results.
- `message_end` carries finished assistant messages with `content` blocks
  (text/thinking) -- same shape as Claude's content-block list.
- There is no terminal `result` event with `total_cost_usd`. We therefore
  collect cost per-turn from `turn_end.message.usage` if present and 0 otherwise.
- `agent_end` is the terminal lifecycle event.
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
    "pi_version": "pi (@earendil-works/pi-coding-agent) npm version; consumed by the workflow install step (default: latest)",
    "framework_repo": "Override GitHub repo for browser-harness install (e.g. fork/browser-harness). Consumed by the workflow install step.",
    "thinking": "pi thinking level: off|minimal|low|medium|high|xhigh (default: high)",
    "task_timeout": "Per-task wall-clock timeout in seconds, sets TASK_TIMEOUT for run_and_judge (default: 1800).",
}

SYSTEM_PROMPT_FILE = Path(__file__).resolve().parent / "system_prompt.md"
SHOTS_DIR = Path("/tmp/shots")
FINAL_ANSWER_RE = re.compile(r"FINAL ANSWER:\s*(.+?)\s*$", re.MULTILINE)


def _require_claude_model(model_name: str) -> str:
    """This framework only supports Claude models, mirroring CCH."""
    if not model_name.startswith("claude-"):
        raise ValueError(
            f"pi-harness requires a Claude model. Got: {model_name!r}. "
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
        raise ValueError(f"Unsupported browser for pi-harness: {browser_name}")
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


def _build_pi_cmd(
    task_description: str,
    model_name: str,
    thinking: str,
    system_prompt: str,
) -> list[str]:
    cmd = [
        "pi",
        "--mode",
        "json",
        "--provider",
        "anthropic",
        "--model",
        model_name,
        "--thinking",
        thinking,
        "--no-session",
        "--no-context-files",
        "--no-extensions",
        "--no-skills",
        "--no-prompt-templates",
        "--no-themes",
        "--offline",
        "--append-system-prompt",
        system_prompt,
        task_description,
    ]
    return cmd


def _stringify_content(content) -> str:
    """Flatten a content-block list (or string) to a single string."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for c in content:
            if isinstance(c, dict):
                t = c.get("type")
                if t == "text":
                    parts.append(c.get("text", ""))
                elif t == "thinking":
                    parts.append(c.get("thinking", ""))
                elif t == "image":
                    parts.append("<image>")
                else:
                    try:
                        parts.append(json.dumps(c, separators=(",", ":")))
                    except Exception:
                        parts.append(str(c))
            else:
                parts.append(str(c))
        return "\n".join(parts)
    try:
        return json.dumps(content, default=str)
    except Exception:
        return str(content)


def _format_assistant_message(message: dict) -> list[str]:
    """Turn an assistant message_end's content blocks into step strings.

    Tool-use blocks are skipped here (they are emitted as `tool_execution_*`
    events separately) so we don't double-count them.
    """
    steps: list[str] = []
    content = message.get("content", []) or []
    if not isinstance(content, list):
        return steps
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            text = (block.get("text") or "").strip()
            if text:
                steps.append(f"text: {text[:2000]}")
        elif btype == "thinking":
            text = (block.get("thinking") or "").strip()
            if text:
                steps.append(f"thinking: {text[:2000]}")
        # tool_use blocks are handled by tool_execution_* events.
    return steps


def _format_tool_call(tool_name: str, args) -> str:
    """Format a tool_execution_start event into a step string (matching CCH)."""
    if not isinstance(args, dict):
        try:
            return f"{tool_name}: {json.dumps(args, separators=(',', ':'))[:2000]}"
        except Exception:
            return tool_name
    if tool_name == "bash":
        return f"Bash: {(args.get('command') or '').strip()[:2000]}"
    if tool_name in ("edit", "write", "read"):
        path = args.get("file_path") or args.get("path") or ""
        return f"{tool_name.capitalize()}: {path}"
    try:
        return f"{tool_name}: {json.dumps(args, separators=(',', ':'))[:2000]}"
    except Exception:
        return tool_name


def _format_tool_result(tool_name: str, result, is_error: bool) -> str | None:
    """Format a tool_execution_end event into a step string."""
    prefix = "tool_error" if is_error else "tool_result"
    content = _stringify_content(result).strip()
    if not content:
        return None
    return f"{prefix}: {content[:2000]}"


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
        print(f"[pi-stderr] {s}", flush=True)


async def execute(task_description: str) -> ExecutionResult:
    params = validate_params(parse_params(), ACCEPTED_PARAMS)
    model_name = _require_claude_model(os.environ["MODEL"])
    browser_name = os.environ.get("BROWSER", "browser-use-cloud")
    task_index = os.environ.get("TASK_INDEX", "0")
    thinking = params.get("thinking", "high")
    # task_timeout is consumed in main() before run_and_judge wraps execute.

    bu_name = f"eval-{task_index}"
    _reset_shots_dir()

    # Pre-provision the browser so pi starts with a live CDP attach.
    _start_browser(browser_name, bu_name)

    env = {
        **os.environ,
        "BU_NAME": bu_name,
        "DISABLE_TELEMETRY": "1",
        # Pi-specific: skip startup network ops so a flaky pi.dev doesn't
        # block the run, and disable install/update telemetry.
        "PI_OFFLINE": "1",
        "PI_SKIP_VERSION_CHECK": "1",
        "PI_TELEMETRY": "0",
    }

    system_prompt = SYSTEM_PROMPT_FILE.read_text()
    cmd = _build_pi_cmd(task_description, model_name, thinking, system_prompt)

    start = time.time()
    steps: list[str] = []
    last_assistant_text = ""
    total_cost = 0.0
    saw_agent_end = False
    stderr_buf: list[str] = []

    # pi stream-json lines can be huge (tool results with full page HTML/text).
    # Same workaround as CCH: read raw chunks and split on newlines.
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=HARNESS_DIR,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        limit=256 * 1024 * 1024,  # 256 MiB safety cap
    )

    stderr_task = asyncio.create_task(_drain_stderr(proc, stderr_buf))

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
                print(f"[pi-stdout-raw] {line[:500]}", flush=True)
                continue

            etype = event.get("type")

            if etype == "tool_execution_start":
                s = _format_tool_call(event.get("toolName") or "?", event.get("args"))
                if s:
                    steps.append(s)
                    print(f"[step {len(steps):>3}] {s[:500]}", flush=True)

            elif etype == "tool_execution_end":
                s = _format_tool_result(
                    event.get("toolName") or "?",
                    event.get("result"),
                    bool(event.get("isError")),
                )
                if s:
                    steps.append(s)
                    print(f"[step {len(steps):>3}] {s[:500]}", flush=True)

            elif etype == "message_end":
                msg = event.get("message", {}) or {}
                if msg.get("role") == "assistant":
                    new_steps = _format_assistant_message(msg)
                    for s in new_steps:
                        steps.append(s)
                        print(f"[step {len(steps):>3}] {s[:500]}", flush=True)
                    # Track latest assistant text for FINAL ANSWER extraction.
                    txt = _stringify_content(msg.get("content"))
                    if txt:
                        last_assistant_text = txt

            elif etype == "turn_end":
                # Some pi providers carry usage on the final assistant message.
                msg = event.get("message", {}) or {}
                usage = msg.get("usage") or {}
                cost = usage.get("cost") or usage.get("total_cost") or usage.get("total_cost_usd")
                if isinstance(cost, (int, float)):
                    total_cost += float(cost)

            elif etype == "agent_end":
                saw_agent_end = True
                # Final fallback: scan the full message list for the last
                # assistant message in case message_end was missed.
                msgs = event.get("messages", []) or []
                for m in reversed(msgs):
                    if isinstance(m, dict) and m.get("role") == "assistant":
                        txt = _stringify_content(m.get("content"))
                        if txt:
                            last_assistant_text = last_assistant_text or txt
                            break

        # Wait for the process (stdout closed implies near-exit)
        try:
            await asyncio.wait_for(proc.wait(), timeout=60)
        except asyncio.TimeoutError:
            print("[pi-runner] proc did not exit within 60s of stdout close; killing", flush=True)
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
        _stop_browser(browser_name, bu_name)

    duration = time.time() - start
    stderr_tail = "\n".join(stderr_buf[-50:])

    # Hard error: pi exited non-zero AND we never saw agent_end.
    if not saw_agent_end and proc.returncode not in (0, None):
        raise RuntimeError(
            f"pi exited with code {proc.returncode} before emitting agent_end. "
            f"steps_captured={len(steps)} duration={duration:.1f}s stderr_tail:\n{stderr_tail[-2000:]}"
        )

    # Extract FINAL ANSWER from the last assistant text.
    match = FINAL_ANSWER_RE.search(last_assistant_text or "")
    answer = match.group(1).strip() if match else (last_assistant_text.strip() or "")

    if not saw_agent_end:
        # Soft error: pi exited 0 but never emitted agent_end. Surface but keep data.
        final_result = f"[pi_no_agent_end] {answer}" if answer else "[pi_no_agent_end] Agent did not complete task."
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
