"""Run a single benchmark task using OpenAI Codex CLI driving browser-harness.

This framework wraps OpenAI's Codex CLI (the coding agent) around the
browser-harness repo: Codex owns the agent loop, we hand it a task and a
workdir pre-loaded with the harness + a live browser daemon, then stream-parse
its `codex exec --json` JSONL output.

The joint system being benchmarked is (Codex CLI + browser-harness + OpenAI
model). Pin `codex_version` and `framework_ref` for reproducible comparisons.

Mirrors `frameworks/claude_code_harness/run_task.py` -- same browser
provisioning (admin.start_remote_daemon under BU_NAME), same /tmp/shots
screenshot drain, same FINAL ANSWER convention -- swapping out the agent CLI
and its event schema.

Codex JSON event schema (see https://developers.openai.com/codex/noninteractive):
- `thread.started` {thread_id}
- `turn.started`
- `turn.completed` {usage: {input_tokens, cached_input_tokens, output_tokens,
  reasoning_output_tokens}}
- `turn.failed` {error: {...}}
- `item.started` {item: {id, type, ...}}
- `item.updated` {item: {...}}
- `item.completed` {item: {id, type, ...}}
   item.type in {agent_message, reasoning, command_execution, file_change,
   mcp_tool_call, web_search, plan_update, todo_list, ...}
- `error` {message}

Codex does NOT emit a per-turn cost field; we compute cost from token counts
via a small static price map (see _MODEL_PRICES). Models not in the map
report cost=0.
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
    "codex_version": "Codex CLI npm version; consumed by the workflow install step (default: latest)",
    "framework_repo": "Override GitHub repo for browser-harness install (e.g. fork/browser-harness). Consumed by the workflow install step.",
    "task_timeout": "Per-task wall-clock timeout in seconds, sets TASK_TIMEOUT for run_and_judge (default: 1800).",
    "sandbox": "Codex sandbox policy (read-only | workspace-write | danger-full-access; default: danger-full-access).",
}

SYSTEM_PROMPT_FILE = Path(__file__).resolve().parent / "system_prompt.md"
SHOTS_DIR = Path("/tmp/shots")
FINAL_ANSWER_RE = re.compile(r"FINAL ANSWER:\s*(.+?)\s*$", re.MULTILINE)

# USD/token prices for cost estimation. Codex does not surface per-turn cost;
# we compute total_cost = input * input_price + output * output_price. Cached
# input tokens are charged at the cached rate when known, otherwise full rate.
# Update as OpenAI publishes new prices. Models absent here report cost=0.
_MODEL_PRICES: dict[str, dict[str, float]] = {
    "gpt-5": {"input": 1.25e-6, "cached_input": 0.125e-6, "output": 10e-6},
}


def _model_price(model_name: str) -> dict[str, float] | None:
    if model_name in _MODEL_PRICES:
        return _MODEL_PRICES[model_name]
    # Best-effort: strip common dated suffixes.
    for key in _MODEL_PRICES:
        if model_name.startswith(key):
            return _MODEL_PRICES[key]
    return None


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
    """Provision a browser for the harness to attach to."""
    if browser_name != "browser-use-cloud":
        raise ValueError(f"Unsupported browser for codex-harness: {browser_name}")
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


def _build_codex_cmd(model_name: str, sandbox: str) -> list[str]:
    """Build the `codex exec` command. Prompt is passed via stdin.

    Notes on flags:
    - `--ask-for-approval` is NOT accepted at `codex exec` level in published
      builds (despite docs suggesting global flags propagate). `codex exec`
      is non-interactive by construction; no approval gating runs anyway.
    - `--sandbox`, `--skip-git-repo-check`, `--ignore-user-config`, and
      `--json` are exec-level flags.
    - The prompt comes on stdin via `-` so we don't have to worry about
      shell-escaping multi-MB prompts.
    """
    return [
        "codex",
        "exec",
        "--json",
        "--model",
        model_name,
        "--sandbox",
        sandbox,
        "--skip-git-repo-check",
        "--ignore-user-config",
        "-",  # read prompt from stdin
    ]


def _format_item(item: dict) -> str | None:
    """Turn a single Codex `item.completed` payload into a step string."""
    itype = item.get("type")
    if itype == "agent_message":
        text = (item.get("text") or "").strip()
        if not text:
            return None
        return f"text: {text[:2000]}"
    if itype == "reasoning":
        # Codex emits a short summary; can also be in a `summary` array.
        text = (item.get("text") or item.get("summary") or "").strip() if isinstance(
            item.get("text") or item.get("summary"), str
        ) else ""
        if not text:
            # Sometimes reasoning has a list of summary strings.
            summary = item.get("summary")
            if isinstance(summary, list):
                text = " ".join(s for s in summary if isinstance(s, str)).strip()
        if not text:
            return "reasoning"
        return f"reasoning: {text[:2000]}"
    if itype == "command_execution":
        cmd = item.get("command") or ""
        if isinstance(cmd, list):
            cmd = " ".join(str(c) for c in cmd)
        cmd = (cmd or "").strip()
        status = item.get("status") or ""
        exit_code = item.get("exit_code")
        out = item.get("aggregated_output") or item.get("output") or ""
        if isinstance(out, dict):
            out = out.get("text") or json.dumps(out, default=str)
        out = (out or "").strip()
        # Two-step: emit command itself, then result tag for visibility.
        # We compact into a single step entry so step counts stay reasonable.
        head = f"Bash: {cmd[:1500]}"
        tail = ""
        if status and status != "completed":
            tail = f" [{status}]"
        if exit_code is not None and exit_code != 0:
            tail += f" exit={exit_code}"
        if out:
            tail += f"\n-> {out[:500]}"
        return (head + tail)[:2000]
    if itype == "file_change":
        path = item.get("path") or ""
        action = item.get("action") or "write"
        return f"{action}: {path}"
    if itype == "mcp_tool_call":
        name = item.get("name") or item.get("tool") or "mcp"
        args = item.get("arguments") or item.get("input") or {}
        try:
            blob = json.dumps(args, separators=(",", ":"))[:1500]
        except Exception:
            blob = str(args)[:1500]
        return f"mcp:{name}: {blob}"
    if itype == "web_search":
        q = item.get("query") or ""
        return f"web_search: {q[:500]}"
    if itype == "plan_update" or itype == "todo_list":
        try:
            return f"{itype}: {json.dumps(item, default=str)[:1500]}"
        except Exception:
            return itype
    if itype:
        # Unknown but well-formed item type -- keep a breadcrumb.
        try:
            return f"{itype}: {json.dumps(item, default=str)[:1500]}"
        except Exception:
            return itype
    return None


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
        print(f"[codex-stderr] {s}", flush=True)


def _compose_prompt(task_description: str) -> str:
    """Combine our system prompt with the task. Codex CLI doesn't have
    `--append-system-prompt-file`; we prepend the system prompt to the user
    prompt instead. Codex also auto-loads `AGENTS.md` from the workdir, but
    we put the rules in the prompt to be explicit + version-pinned."""
    system = SYSTEM_PROMPT_FILE.read_text()
    return f"{system}\n\n---\n\nTask:\n{task_description}\n"


async def execute(task_description: str) -> ExecutionResult:
    params = validate_params(parse_params(), ACCEPTED_PARAMS)
    model_name = os.environ["MODEL"]
    browser_name = os.environ.get("BROWSER", "browser-use-cloud")
    task_index = os.environ.get("TASK_INDEX", "0")
    sandbox = params.get("sandbox", "danger-full-access")
    # task_timeout is consumed in main() before run_and_judge wraps execute.

    bu_name = f"eval-{task_index}"
    _reset_shots_dir()

    # Pre-provision the browser so Codex starts with a live CDP attach.
    _start_browser(browser_name, bu_name)

    try:
        # Codex CLI auth: `codex exec` reuses saved auth (~/.codex/auth.json) by
        # default but accepts `CODEX_API_KEY` env explicitly (the only auth env
        # supported by `codex exec` per docs). `OPENAI_API_KEY` alone is NOT read
        # by codex (it's for the OpenAI Python SDK). We mirror the workflow's
        # OPENAI_API_KEY into CODEX_API_KEY here so the same repo secret unlocks
        # both bcode (which uses OPENAI_API_KEY directly) and codex-harness.
        #
        # PATH: `uv pip install /tmp/browser-harness` puts the `browser-harness`
        # console_script at /tmp/browser-harness/.venv/bin/browser-harness, but
        # codex subprocess doesn't inherit the `uv run` PATH boost. Smoke #4
        # showed the agent self-recovered by prepending the venv dir, but that
        # cost ~4 steps. Prepend explicitly so the bare `browser-harness` heredoc
        # in our system prompt + SKILL.md works on the first try.
        harness_venv_bin = f"{HARNESS_DIR}/.venv/bin"
        existing_path = os.environ.get("PATH", "")
        env = {
            **os.environ,
            "BU_NAME": bu_name,
            "CODEX_API_KEY": os.environ.get("CODEX_API_KEY") or os.environ.get("OPENAI_API_KEY", ""),
            "PATH": f"{harness_venv_bin}:{existing_path}" if existing_path else harness_venv_bin,
        }

        cmd = _build_codex_cmd(model_name, sandbox)
        prompt = _compose_prompt(task_description)
    except Exception:
        _stop_browser(browser_name, bu_name)
        raise

    start = time.time()
    steps: list[str] = []
    final_text = ""
    total_input_tokens = 0
    total_cached_input_tokens = 0
    total_output_tokens = 0
    total_reasoning_tokens = 0
    turn_failed_error: str | None = None
    error_events: list[str] = []
    stderr_buf: list[str] = []

    proc: asyncio.subprocess.Process | None = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=HARNESS_DIR,
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=256 * 1024 * 1024,  # 256 MiB safety cap on line buffer
        )

        # Pipe the prompt in on stdin and close.
        assert proc.stdin is not None
        proc.stdin.write(prompt.encode("utf-8"))
        await proc.stdin.drain()
        proc.stdin.close()

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
        """Yield one JSONL line at a time. Codex item.completed payloads for
        command_execution events can include large aggregated_output blobs --
        read raw chunks and split on newlines, no per-line cap."""
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
                print(f"[codex-stdout-raw] {line}", flush=True)
                continue

            etype = event.get("type")
            if etype == "item.completed":
                item = event.get("item") or {}
                s = _format_item(item)
                if s:
                    steps.append(s)
                    print(f"[step {len(steps):>3}] {s[:500]}", flush=True)
                # Track latest agent_message for FINAL ANSWER extraction.
                if item.get("type") == "agent_message":
                    text = (item.get("text") or "").strip()
                    if text:
                        final_text = text
            elif etype == "turn.completed":
                usage = event.get("usage") or {}
                total_input_tokens += int(usage.get("input_tokens") or 0)
                total_cached_input_tokens += int(usage.get("cached_input_tokens") or 0)
                total_output_tokens += int(usage.get("output_tokens") or 0)
                total_reasoning_tokens += int(usage.get("reasoning_output_tokens") or 0)
                print(
                    f"[codex-turn] in={usage.get('input_tokens')} "
                    f"cached={usage.get('cached_input_tokens')} "
                    f"out={usage.get('output_tokens')} "
                    f"reasoning={usage.get('reasoning_output_tokens')}",
                    flush=True,
                )
            elif etype == "turn.failed":
                err = event.get("error") or {}
                turn_failed_error = json.dumps(err, default=str)[:500]
                print(f"[codex-turn-failed] {turn_failed_error}", flush=True)
            elif etype == "error":
                msg = event.get("message") or json.dumps(event, default=str)
                error_events.append(str(msg)[:500])
                print(f"[codex-error] {msg}", flush=True)
            elif etype == "thread.started":
                tid = event.get("thread_id")
                print(f"[codex-thread] {tid}", flush=True)

        # Wait for the process to exit cleanly.
        try:
            await asyncio.wait_for(proc.wait(), timeout=60)
        except asyncio.TimeoutError:
            print("[codex-runner] proc did not exit within 60s of stdout close; killing", flush=True)
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

    # Cost estimate from token counts.
    prices = _model_price(model_name)
    if prices:
        # Non-cached input is total_input_tokens - cached_input_tokens.
        non_cached = max(0, total_input_tokens - total_cached_input_tokens)
        cost = (
            non_cached * prices["input"]
            + total_cached_input_tokens * prices.get("cached_input", prices["input"])
            + total_output_tokens * prices["output"]
        )
    else:
        cost = 0.0

    # Determine final result text. Prefer the FINAL ANSWER line.
    match = FINAL_ANSWER_RE.search(final_text or "")
    answer = match.group(1).strip() if match else (final_text.strip() or "")

    # Failure precedence: turn.failed > error events > no agent_message > clean.
    if turn_failed_error and not answer:
        final_result = f"[codex_turn_failed] {turn_failed_error}"
    elif error_events and not answer:
        final_result = f"[codex_error] {error_events[-1]}"
    elif not final_text:
        if proc.returncode not in (0, None):
            raise RuntimeError(
                f"codex exited with code {proc.returncode} and emitted no agent_message. "
                f"steps_captured={len(steps)} duration={duration:.1f}s "
                f"stderr_tail:\n{stderr_tail[-2000:]}"
            )
        final_result = "Agent did not emit any output"
    else:
        final_result = answer or final_text.strip()
        # If FINAL ANSWER missing but we had output, surface as fallback.
        if not match:
            final_result = answer or "Agent did not emit FINAL ANSWER line"

    return ExecutionResult(
        final_result=final_result,
        steps=steps,
        screenshots_b64=_collect_screenshots(),
        num_steps=len(steps),
        duration_seconds=duration,
        cost=cost,
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
