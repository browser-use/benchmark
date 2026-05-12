"""Run a single benchmark task using the Rust browser-use-terminal (`but-rust`).

This is the rust-rewrite branch of `browser-use/browser-use-terminal`. The
old Python `but` framework wraps `main`; this one wraps `rust-rewrite`.
Completely independent install path + invocation, gated on
`inputs.framework == 'but-rust'` in the workflow so it cannot affect any
other framework.

Architecture differences vs Python `but`:
- Cargo workspace; the CLI is a Rust binary at
  `<repo>/target/release/browser-use-terminal`.
- Subcommand-per-provider: `run-openai <text> --model <m>`, plus
  `run-codex`, `run-anthropic`, `run-openrouter`. No `--provider` flag.
- No `--browser` flag at all. Browser ops live in a Python worker
  process (`python/llm_browser_worker/worker.py`) spawned by Rust; that
  worker honors `BU_CDP_URL`/`BU_CDP_WS` and connects through the
  browser-harness Python package. We pre-provision a browser-use-cloud
  CDP WS the same way `but`/`bcode` do and pass it via `BU_CDP_WS`.
- State lives in SQLite at `<state_dir>/state.db`; events are read out
  via `events <session_id>` (JSON lines).
Browser harness needs to be importable in the worker venv as
`browser_harness`. The workflow's install step `uv pip install` the
browser-harness repo at `BUT_RUST_HARNESS_REF` (default: main) into the
project venv at `/tmp/but-rust/.venv` so `import browser_harness.admin`
in the worker resolves.

The runner shells out twice per task:
1. `run-openai/run-codex/...` -- agent loop, prints session_id on stdout.
2. `events <session_id>` -- JSON-lines event dump, parsed into steps +
   final result + cost + screenshot paths.
"""

import asyncio
import base64
import json
import os
import shutil
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from dotenv import load_dotenv
from lmnr import Laminar
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
    "task_timeout": "Per-task wall-clock timeout in seconds, sets TASK_TIMEOUT for run_and_judge (default: 1800).",
    "max_turns": "Maximum model/tool turns before failing (currently informational -- but-rust does not expose this on the run-* subcommand; the dataset-run subcommand has it but we are not using that path).",
    "framework_repo": "Override the GitHub repo for browser-use-terminal install (default: browser-use/browser-use-terminal). Consumed by the workflow install step.",
    "harness_repo": "Override the browser-harness GitHub repo installed into the worker venv (default: browser-use/browser-harness). Consumed by the workflow install step.",
    "harness_ref": "Override the browser-harness ref/branch/commit (default: main). Consumed by the workflow install step.",
}

# Workflow install step builds the binary here.
BUT_RUST_REPO_DIR = "/tmp/but-rust"
BUT_RUST_BIN = f"{BUT_RUST_REPO_DIR}/target/release/browser-use-terminal"

SYSTEM_PROMPT_PATH = Path(__file__).resolve().parent / "system_prompt.md"

STATE_ROOT = Path("/tmp/but_rust_state")

# Map benchmark model alias to (rust subcommand, model arg). Order matters.
_PROVIDER_SUBCMDS: tuple[tuple[str, str], ...] = (
    ("claude", "run-anthropic"),
    ("gpt", "run-openai"),
    ("o1", "run-openai"),
    ("o3", "run-openai"),
    ("o4", "run-openai"),
    # No native zai/qwen in but-rust; route via OpenRouter when the model
    # name carries an OpenRouter-compatible provider/model slug.
    ("glm", "run-openrouter"),
    ("qwen", "run-openrouter"),
)


def _resolve_subcommand(model_name: str) -> str:
    lower = model_name.lower()
    for key, subcmd in _PROVIDER_SUBCMDS:
        if key in lower:
            return subcmd
    raise ValueError(
        f"but-rust: cannot infer subcommand for model {model_name!r}. "
        f"Add a keyphrase to _PROVIDER_SUBCMDS in frameworks/but_rust/run_task.py."
    )


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


def _start_browser() -> tuple[str, str]:
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


def _format_step_from_event(event: dict) -> str | None:
    etype = event.get("type") or ""
    payload = event.get("payload") or {}
    if etype == "tool.started":
        name = payload.get("name") or "?"
        args = payload.get("arguments") or {}
        if name == "python":
            code = (args.get("code") or args.get("source") or "").strip()
            return f"python: {code[:2000]}"
        if name in ("bash", "shell"):
            cmd = (args.get("command") or args.get("script") or "").strip()
            return f"{name}: {cmd[:2000]}"
        if name in ("read", "write", "edit"):
            path = args.get("path") or args.get("filePath") or ""
            return f"{name}: {path}"
        if name == "done":
            result = (args.get("result") or "").strip()
            return f"done: {result[:2000]}"
        try:
            return f"{name}: {json.dumps(args, separators=(',', ':'))[:2000]}"
        except Exception:
            return name
    if etype in ("assistant.message", "message.assistant"):
        text = (payload.get("text") or payload.get("content") or "").strip()
        return f"text: {text[:2000]}" if text else None
    if etype in ("reasoning", "assistant.reasoning"):
        text = (payload.get("text") or payload.get("content") or "").strip()
        return f"thinking: {text[:2000]}" if text else None
    return None


async def _read_stream(stream: asyncio.StreamReader, label: str, buf: list[str], echo: bool = True) -> None:
    while line := await stream.readline():
        s = line.decode("utf-8", errors="replace").rstrip("\n")
        buf.append(s)
        if echo:
            print(f"[{label}] {s[:500]}", flush=True)


def _collect_screenshots(state_dir: Path, session_id: str) -> list[str]:
    """Read PNGs/JPEGs from `<state_dir>/artifacts/<session_id>/images/`."""
    images_dir = state_dir / "artifacts" / session_id / "images"
    if not images_dir.exists():
        return []
    paths = sorted(p for p in images_dir.iterdir() if p.is_file() and p.suffix.lower() in (".png", ".jpeg", ".jpg"))
    return [base64.b64encode(p.read_bytes()).decode() for p in paths]


async def execute(task_description: str) -> ExecutionResult:
    params = parse_params()
    validate_params(params, ACCEPTED_PARAMS)
    model = os.environ["MODEL"]
    subcommand = _resolve_subcommand(model)
    task_idx = os.environ.get("TASK_INDEX", "0")

    browser_id, cdp_ws = _start_browser()

    state_dir = STATE_ROOT / f"task-{task_idx}-{int(time.time() * 1000)}"
    if state_dir.exists():
        shutil.rmtree(state_dir)
    state_dir.mkdir(parents=True)

    parent_span_context = Laminar.serialize_span_context()

    system_prompt = SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
    full_task = f"{system_prompt.strip()}\n\nTask:\n{task_description}"

    env = {
        **os.environ,
        # The Python worker (spawned by the Rust agent loop) honors BU_CDP_WS
        # directly via `_ensure_managed_chrome`/`_ensure_cloud_browser` short
        # circuits. Pass both URL forms for robustness.
        "BU_CDP_WS": cdp_ws,
        # Force flush on one-shot CLI runs so OTLP spans actually leave the
        # process before exit (see docs/README on this branch).
        "LLM_BROWSER_LAMINAR_FLUSH_ON_FINISH": "1",
    }
    if parent_span_context:
        # Forward-compat: but-rust telemetry doesn't honor this yet, but it
        # doesn't error on unknown env either.
        env["LMNR_PARENT_SPAN_CONTEXT"] = parent_span_context

    # `--state-dir` is a TOP-LEVEL arg on the Rust CLI -- must come BEFORE
    # the subcommand.
    cmd_run = [
        BUT_RUST_BIN,
        "--state-dir", str(state_dir),
        subcommand,
        full_task,
        "--model", model,
    ]

    start = time.time()
    stdout_buf: list[str] = []
    stderr_buf: list[str] = []

    proc = await asyncio.create_subprocess_exec(
        *cmd_run,
        cwd=BUT_RUST_REPO_DIR,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        limit=256 * 1024 * 1024,
    )
    stdout_task = asyncio.create_task(_read_stream(proc.stdout, "but-rust-stdout", stdout_buf))
    stderr_task = asyncio.create_task(_read_stream(proc.stderr, "but-rust-stderr", stderr_buf))

    try:
        await proc.wait()
        await asyncio.wait_for(stdout_task, timeout=10)
        await asyncio.wait_for(stderr_task, timeout=10)
    except asyncio.TimeoutError:
        for t in (stdout_task, stderr_task):
            if not t.done():
                t.cancel()
    finally:
        if proc.returncode is None:
            proc.kill()
            try:
                await asyncio.wait_for(proc.wait(), timeout=10)
            except asyncio.TimeoutError:
                pass

    # `run-openai`/etc print the session_id as the final non-empty stdout line.
    session_id = ""
    for line in reversed(stdout_buf):
        line = line.strip()
        if line and not line.startswith("{"):
            session_id = line
            break

    if not session_id:
        _stop_browser(browser_id)
        raise RuntimeError(
            f"but-rust: no session_id captured from stdout (exit={proc.returncode}). "
            f"stderr_tail:\n{chr(10).join(stderr_buf[-50:])[-2000:]}"
        )

    # Dump events for this session.
    cmd_events = [BUT_RUST_BIN, "--state-dir", str(state_dir), "events", session_id]
    events_proc = await asyncio.create_subprocess_exec(
        *cmd_events,
        cwd=BUT_RUST_REPO_DIR,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        limit=256 * 1024 * 1024,
    )
    events_stdout, events_stderr = await events_proc.communicate()
    _stop_browser(browser_id)
    duration = time.time() - start

    events: list[dict] = []
    for line in events_stdout.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    steps: list[str] = []
    final_text = ""
    total_cost = 0.0
    errors: list[str] = []

    for event in events:
        if (s := _format_step_from_event(event)):
            steps.append(s)
        etype = event.get("type") or ""
        payload = event.get("payload") or {}
        if etype == "session.done":
            done_result = (payload.get("result") or "").strip()
            if done_result:
                final_text = done_result
        elif etype in ("model.usage", "llm.usage"):
            cost_usd = payload.get("cost_usd") or payload.get("cost")
            if cost_usd is not None:
                try:
                    total_cost += float(cost_usd)
                except (TypeError, ValueError):
                    pass
        elif etype in ("tool.failed", "session.failed", "error"):
            err = payload.get("error") or payload.get("message") or ""
            if err:
                errors.append(str(err))
                print(f"[but-rust-error] {str(err)[:500]}", flush=True)

    if not final_text:
        for event in reversed(events):
            if (event.get("type") or "") in ("assistant.message", "message.assistant"):
                text = ((event.get("payload") or {}).get("text") or "").strip()
                if text:
                    final_text = text
                    break

    if proc.returncode not in (0, None) and not final_text and not steps:
        raise RuntimeError(
            f"but-rust exited with code {proc.returncode} before producing output. "
            f"stderr_tail:\n{chr(10).join(stderr_buf[-50:])[-2000:]}"
        )

    answer = (final_text or "").strip()
    if errors and not answer:
        final_result = f"[but_rust_error] {errors[0][:500]}"
    elif errors:
        final_result = f"[but_rust_error_recovered] {answer}"
    else:
        final_result = answer or "[but_rust_no_output]"

    screenshots = _collect_screenshots(state_dir, session_id)

    return ExecutionResult(
        final_result=final_result,
        steps=steps,
        screenshots_b64=screenshots,
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
