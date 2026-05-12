"""Run a single benchmark task using PIBT (pi browser terminal).

PIBT = pi (the @earendil-works/pi-coding-agent CLI) + the
`pi-agent-extensions` package, which provides built-in browser tools via a
vendored `browser-harness-js` (CDP-based). No external Python harness, no
heredocs -- pi drives the browser through its own `cdp_*` tool surface.

Joint system being benchmarked: (pi + pi-agent-extensions + Claude model).
Restricted to Claude models, mirroring CCH/PIH.

Browser wiring: we pre-allocate a `browser-use-cloud` session via the v3 API
(same path as bcode/cch-js), resolve the CDP WebSocket URL, and pass it as
`BU_CDP_WS` in the pi subprocess env. The system prompt instructs the agent
to call `cdp_connect({ wsUrl: process.env.BU_CDP_WS })` once at the start.

Pi event-stream parsing follows PIH (`tool_execution_start/end`, `message_end`,
`turn_end.message.usage`, `agent_end`).
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
    "pi_version": "pi (@earendil-works/pi-coding-agent) npm version; consumed by the workflow install step (default: latest).",
    "framework_repo": "Override GitHub repo for pi-agent-extensions install (e.g. fork/pi-agent-extensions). Consumed by the workflow install step.",
    "thinking": "pi thinking level: off|minimal|low|medium|high|xhigh (default: high).",
    "task_timeout": "Per-task wall-clock timeout in seconds, sets TASK_TIMEOUT for run_and_judge (default: 1800).",
}

SYSTEM_PROMPT_FILE = Path(__file__).resolve().parent / "system_prompt.md"
SHOTS_DIR = Path("/tmp/shots")
FINAL_ANSWER_RE = re.compile(r"FINAL ANSWER:\s*(.+?)\s*$", re.MULTILINE)
# pi-agent-extensions are installed by the workflow install step; the runner
# uses the pi `--extensions <path>` flag (or default loader) to pick them up.
EXTENSIONS_DIR = "/tmp/pi-agent-extensions"
# Tools we surface to pi. Includes the cdp_* tools from pi-agent-extensions
# plus a minimal builtin set (bash for shots dir mgmt, read/write for general
# scaffolding). Subagent tools are intentionally omitted.
PIBT_TOOLS = "bash,read,write,cdp_connect,cdp_eval,cdp_status,cdp_targets,cdp_use_target"


def _require_claude_model(model_name: str) -> str:
    if not model_name.startswith("claude-"):
        raise ValueError(
            f"pibt requires a Claude model. Got: {model_name!r}. "
            f"Supported model aliases start with 'claude-' (see models.py)."
        )
    return model_name


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
        headers={
            "X-Browser-Use-API-Key": _bu_api_key(),
            "Content-Type": "application/json",
        },
    )
    return json.loads(urllib.request.urlopen(req, timeout=90).read() or b"{}")


def _start_browser() -> tuple[str, str]:
    """Allocate a browser-use-cloud session. Returns (browser_id, cdp_ws)."""
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


def _reset_shots_dir() -> None:
    if SHOTS_DIR.exists():
        shutil.rmtree(SHOTS_DIR)
    SHOTS_DIR.mkdir(parents=True)


def _collect_screenshots() -> list[str]:
    if not SHOTS_DIR.exists():
        return []
    return [
        base64.b64encode(p.read_bytes()).decode()
        for p in sorted(SHOTS_DIR.glob("*.png"))
        if p.is_file()
    ]


def _build_pi_cmd(
    task_description: str,
    model_name: str,
    thinking: str,
    system_prompt: str,
) -> list[str]:
    # NOTE: NOT --no-extensions (we want pi-agent-extensions to load).
    # Still pass --no-context-files / --no-skills / --no-prompt-templates /
    # --no-themes / --no-session for hermeticity. --offline disables the
    # update-check network call. --tools restricts the model to the
    # cdp_* surface plus minimal scaffolding.
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
        "--tools",
        PIBT_TOOLS,
        "--no-session",
        "--no-context-files",
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
    return steps


def _format_tool_call(tool_name: str, args) -> str:
    if not isinstance(args, dict):
        try:
            return f"{tool_name}: {json.dumps(args, separators=(',', ':'))[:2000]}"
        except Exception:
            return tool_name
    if tool_name == "bash":
        return f"Bash: {(args.get('command') or '').strip()[:2000]}"
    if tool_name == "cdp_eval":
        return f"cdp_eval: {(args.get('code') or '').strip()[:2000]}"
    if tool_name == "cdp_connect":
        url = args.get("wsUrl") or args.get("profileDir") or ""
        return f"cdp_connect: {url[:500]}"
    if tool_name in ("cdp_status", "cdp_targets"):
        return tool_name
    if tool_name == "cdp_use_target":
        return f"cdp_use_target: {args.get('targetId') or ''}"
    if tool_name in ("read", "write", "edit"):
        path = args.get("file_path") or args.get("path") or ""
        return f"{tool_name.capitalize()}: {path}"
    try:
        return f"{tool_name}: {json.dumps(args, separators=(',', ':'))[:2000]}"
    except Exception:
        return tool_name


def _format_tool_result(tool_name: str, result, is_error: bool) -> str | None:
    prefix = "tool_error" if is_error else "tool_result"
    content = _stringify_content(result).strip()
    if not content:
        return None
    return f"{prefix}: {content[:2000]}"


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
        print(f"[pi-stderr] {s}", flush=True)


async def execute(task_description: str) -> ExecutionResult:
    params = validate_params(parse_params(), ACCEPTED_PARAMS)
    model_name = _require_claude_model(os.environ["MODEL"])
    browser_name = os.environ.get("BROWSER", "browser-use-cloud")
    if browser_name != "browser-use-cloud":
        raise ValueError(f"Unsupported browser for pibt: {browser_name}")
    thinking = params.get("thinking", "high")

    _reset_shots_dir()

    # Provision a remote browser; pi attaches over CDP via cdp_connect with the
    # WS URL we hand it through env.
    browser_id, cdp_ws = _start_browser()

    env = {
        **os.environ,
        "BU_CDP_WS": cdp_ws,
        "DISABLE_TELEMETRY": "1",
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

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=EXTENSIONS_DIR,  # pi loads the package.json `pi.extensions` from CWD
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
                    txt = _stringify_content(msg.get("content"))
                    if txt:
                        last_assistant_text = txt

            elif etype == "turn_end":
                msg = event.get("message", {}) or {}
                usage = msg.get("usage") or {}
                cost = (
                    usage.get("cost")
                    or usage.get("total_cost")
                    or usage.get("total_cost_usd")
                )
                if isinstance(cost, (int, float)):
                    total_cost += float(cost)

            elif etype == "agent_end":
                saw_agent_end = True
                msgs = event.get("messages", []) or []
                for m in reversed(msgs):
                    if isinstance(m, dict) and m.get("role") == "assistant":
                        txt = _stringify_content(m.get("content"))
                        if txt:
                            last_assistant_text = last_assistant_text or txt
                            break

        try:
            await asyncio.wait_for(proc.wait(), timeout=60)
        except asyncio.TimeoutError:
            print("[pibt-runner] proc did not exit within 60s of stdout close; killing", flush=True)
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
        _stop_browser(browser_id)

    duration = time.time() - start
    stderr_tail = "\n".join(stderr_buf[-50:])

    if not saw_agent_end and proc.returncode not in (0, None):
        raise RuntimeError(
            f"pi exited with code {proc.returncode} before emitting agent_end. "
            f"steps_captured={len(steps)} duration={duration:.1f}s stderr_tail:\n{stderr_tail[-2000:]}"
        )

    match = FINAL_ANSWER_RE.search(last_assistant_text or "")
    answer = match.group(1).strip() if match else (last_assistant_text.strip() or "")

    if not saw_agent_end:
        final_result = (
            f"[pi_no_agent_end] {answer}"
            if answer
            else "[pi_no_agent_end] Agent did not complete task."
        )
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
