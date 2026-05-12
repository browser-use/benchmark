"""Run a single benchmark task using browser-use-terminal (`but`).

`but` is a browser-specific LLM agent harness: it owns its own agent loop,
provides an editable Python REPL tool with raw CDP helpers
(`goto_url`, `js`, `capture_screenshot`, `click_at_xy`, `fill_input`, ...),
streams screenshots inline to the model, and persists a JSONL event log
per session. See https://github.com/browser-use/browser-use-terminal.

Browser wiring: we pre-provision a `browser-use-cloud` session via the
v3 API (same pattern as bcode/cch) and hand `but` the WebSocket CDP URL
via `--browser cdp --cdp-ws <ws>` (also exported as `BU_CDP_WS` env;
`but`'s `_first_env("BU_CDP_WS", ...)` honors it as a fallback). `but`
attaches to our pre-allocated browser instead of provisioning one.

Invocation:
    uv run browser-use-terminal run \\
        --state-dir <per-task-dir> \\
        --provider <p> --model <m> \\
        --browser cdp --cdp-ws <ws> \\
        --max-turns 80 \\
        "<system_prompt>\\n\\n<task>"

`but run` is synchronous: it blocks until the agent calls `done` or hits
`--max-turns`, prints a session metadata JSON to stdout, then exits. The
agent's per-turn signals (tool calls, model usage, screenshots, final
result) live in `<state-dir>/sessions/<session_id>/events.jsonl`. We
parse that file to extract steps, cost, and the final result, and walk
`<artifact_dir>/browser/screenshots/` to feed PNGs to the judge.

Provider resolution: benchmark aliases get an explicit `--provider`
chosen by substring (claude->anthropic, gpt->openai, glm->zai, qwen->qwen).
The `openai` provider in `but` reads `OPENAI_API_KEY` (already a
workflow secret); the `codex` provider needs Codex subscription auth
that we do not have on CI, so it is NOT auto-selected.
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
    "max_turns": "Maximum model/tool turns before failing the run (default: 80, passed to `but run --max-turns`).",
    "framework_repo": "Override the GitHub repo for browser-use-terminal install (default: browser-use/browser-use-terminal). Consumed by the workflow install step.",
    "agent_mode": "Override the agent instruction mode for `but` (auto|browser|codex, default: leave unset -> `but` picks).",
}

# `but` is installed as a uv-managed Python package at /tmp/but in the
# workflow install step. We invoke it via `uv run --project /tmp/but
# browser-use-terminal run ...` from that workdir so the project's
# console_scripts entry point resolves.
BUT_PROJECT_DIR = "/tmp/but"

# system_prompt.md sits next to this file.
SYSTEM_PROMPT_PATH = Path(__file__).resolve().parent / "system_prompt.md"

# State dir + screenshot scan path. One per task to avoid cross-talk.
STATE_ROOT = Path("/tmp/but_state")

# Map benchmark model alias to (provider, model). Order matters: claude
# before gpt because "gpt" is a common prefix and we want claude to win
# on `claude-*` slugs.
_PROVIDER_KEYPHRASES: tuple[tuple[str, str], ...] = (
    ("claude", "anthropic"),
    ("gpt", "openai"),
    ("o1", "openai"),
    ("o3", "openai"),
    ("o4", "openai"),
    ("glm", "zai"),
    ("qwen", "qwen"),
)


def _resolve_provider(model_name: str) -> str:
    lower = model_name.lower()
    for key, provider in _PROVIDER_KEYPHRASES:
        if key in lower:
            return provider
    raise ValueError(
        f"but: cannot infer provider for model {model_name!r}. "
        f"Add a keyphrase to _PROVIDER_KEYPHRASES in frameworks/but/run_task.py."
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
    """Allocate a browser-use-cloud session. Returns (browser_id, cdp_ws)."""
    browser_id = None
    info = _bu("/browsers", "POST", {})
    browser_id = info["id"]
    try:
        cdp_ws = json.loads(
            urllib.request.urlopen(f"{info['cdpUrl']}/json/version", timeout=15).read()
        )["webSocketDebuggerUrl"]
        return browser_id, cdp_ws
    except Exception:
        _stop_browser(browser_id)
        raise


def _stop_browser(browser_id: str | None) -> None:
    if not browser_id:
        return
    try:
        _bu(f"/browsers/{browser_id}", "PATCH", {"action": "stop"})
    except Exception as e:
        print(f"Warning: failed to stop browser {browser_id}: {e}")


def _format_step_from_event(event: dict) -> str | None:
    """Turn one events.jsonl entry into a short step string (or None)."""
    etype = event.get("type") or ""
    payload = event.get("payload") or {}
    if etype == "tool.started":
        name = payload.get("name") or "?"
        args = payload.get("arguments") or {}
        # Python REPL: dump the code field (sometimes named 'code' or 'source').
        if name in ("python", "python_browser"):
            code = (args.get("code") or args.get("source") or "").strip()
            return f"python: {code[:2000]}"
        if name in ("bash", "shell", "shell_start"):
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
    if etype == "assistant.message" or etype == "message.assistant":
        text = (payload.get("text") or payload.get("content") or "").strip()
        return f"text: {text[:2000]}" if text else None
    if etype == "reasoning" or etype == "assistant.reasoning":
        text = (payload.get("text") or payload.get("content") or "").strip()
        return f"thinking: {text[:2000]}" if text else None
    return None


def _read_events(events_path: Path) -> list[dict]:
    if not events_path.exists():
        return []
    events: list[dict] = []
    with events_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events


def _collect_screenshots(artifact_dir: Path) -> list[str]:
    """Read every PNG/JPEG `but` wrote to <artifact>/browser/screenshots/."""
    shots_dir = artifact_dir / "browser" / "screenshots"
    if not shots_dir.exists():
        return []
    paths = sorted(p for p in shots_dir.iterdir() if p.is_file() and p.suffix.lower() in (".png", ".jpeg", ".jpg"))
    return [base64.b64encode(p.read_bytes()).decode() for p in paths]


def _find_session_dir(state_dir: Path, session_id: str | None) -> Path | None:
    """Resolve the session dir from state_dir. If session_id is unknown,
    pick the most recently modified session subdir."""
    sessions_root = state_dir / "sessions"
    if not sessions_root.exists():
        return None
    if session_id:
        candidate = sessions_root / session_id
        if candidate.exists():
            return candidate
    subdirs = [p for p in sessions_root.iterdir() if p.is_dir()]
    if not subdirs:
        return None
    return max(subdirs, key=lambda p: p.stat().st_mtime)


async def _drain_stderr(proc: asyncio.subprocess.Process, buf: list[str]) -> None:
    assert proc.stderr is not None
    while line := await proc.stderr.readline():
        s = line.decode("utf-8", errors="replace").rstrip("\n")
        buf.append(s)
        print(f"[but-stderr] {s}", flush=True)


async def _iter_lines(stream: asyncio.StreamReader):
    buf = bytearray()
    while chunk := await stream.read(1 << 16):
        buf.extend(chunk)
        while (nl := buf.find(b"\n")) >= 0:
            yield bytes(buf[:nl])
            del buf[: nl + 1]
    if buf:
        yield bytes(buf)


async def execute(task_description: str) -> ExecutionResult:
    params = parse_params()
    validate_params(params, ACCEPTED_PARAMS)
    model = os.environ["MODEL"]
    provider = _resolve_provider(model)
    max_turns = int(params.get("max_turns") or 80)
    task_idx = os.environ.get("TASK_INDEX", "0")

    # Pre-provision the browser. `but` honors `BU_CDP_WS` natively AND we
    # pass `--cdp-ws` explicitly with `--browser cdp` to make the attach
    # deterministic and visible in the spawn cmdline.
    browser_id, cdp_ws = _start_browser()

    # Isolate state dir per task so concurrent runs in the same workflow
    # don't collide on the JSONL event log or screenshot dir.
    state_dir = STATE_ROOT / f"task-{task_idx}-{int(time.time() * 1000)}"
    if state_dir.exists():
        shutil.rmtree(state_dir)
    state_dir.mkdir(parents=True)

    # Laminar parent-span: same pattern as bcode. `but` does not (yet)
    # honor LMNR_PARENT_SPAN_CONTEXT, so this is a forward-compat hook --
    # passing the env var costs nothing on the current version.
    parent_span_context = Laminar.serialize_span_context()

    try:
        system_prompt = SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
        full_task = f"{system_prompt.strip()}\n\nTask:\n{task_description}"
    except Exception:
        _stop_browser(browser_id)
        raise

    env = {
        **os.environ,
        "BU_CDP_WS": cdp_ws,
        # Default state-dir for `but`. Explicitly passed below too.
        "LLM_BROWSER_STATE_DIR": str(state_dir),
    }
    if parent_span_context:
        env["LMNR_PARENT_SPAN_CONTEXT"] = parent_span_context

    # NOTE: `--state-dir` is a TOP-LEVEL arg on browser-use-terminal -- it
    # must come BEFORE the `run` subcommand, otherwise argparse rejects it
    # as an unrecognized argument on `run`. Same for `--config`.
    cmd = [
        "uv", "run", "--project", BUT_PROJECT_DIR, "--no-sync",
        "browser-use-terminal",
        "--state-dir", str(state_dir),
        "run",
        "--provider", provider,
        "--model", model,
        "--browser", "cdp",
        "--cdp-ws", cdp_ws,
        "--max-turns", str(max_turns),
    ]
    agent_mode = (params.get("agent_mode") or "").strip().lower()
    if agent_mode:
        cmd.extend(["--agent-mode", agent_mode])
    cmd.append(full_task)

    start = time.time()
    steps: list[str] = []
    final_text = ""
    total_cost = 0.0
    errors: list[str] = []
    stderr_buf: list[str] = []
    stdout_chunks: list[str] = []
    session_id: str | None = None

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=BUT_PROJECT_DIR,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=256 * 1024 * 1024,
        )
        stderr_task = asyncio.create_task(_drain_stderr(proc, stderr_buf))
    except Exception:
        _stop_browser(browser_id)
        raise

    try:
        async for raw in _iter_lines(proc.stdout):
            line = raw.decode("utf-8", errors="replace").rstrip("\n")
            if line:
                stdout_chunks.append(line)
                print(f"[but-stdout] {line[:500]}", flush=True)

        try:
            await asyncio.wait_for(proc.wait(), timeout=60)
        except asyncio.TimeoutError:
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

    # Parse the trailing JSON metadata `but run` prints (session.to_dict()).
    # Even if we fail to parse, we can still recover from the events.jsonl.
    try:
        joined = "\n".join(stdout_chunks).strip()
        # Take the last balanced {...} block -- `but run` prints exactly one.
        last_brace_open = joined.rfind("{")
        if last_brace_open != -1:
            meta = json.loads(joined[last_brace_open:])
            session_id = str(meta.get("id") or "") or None
    except Exception:
        session_id = None

    session_dir = _find_session_dir(state_dir, session_id)
    events: list[dict] = []
    artifact_dir: Path | None = None
    if session_dir is not None:
        events_path = session_dir / "events.jsonl"
        events = _read_events(events_path)
        artifact_dir = session_dir / "artifacts"

    for event in events:
        if (s := _format_step_from_event(event)):
            steps.append(s)
        etype = event.get("type") or ""
        payload = event.get("payload") or {}
        if etype == "session.done":
            done_result = (payload.get("result") or "").strip()
            if done_result:
                final_text = done_result
        elif etype == "model.usage":
            cost_usd = payload.get("cost_usd")
            if cost_usd is not None:
                try:
                    total_cost += float(cost_usd)
                except (TypeError, ValueError):
                    pass
        elif etype in ("tool.failed", "error", "session.failed"):
            err = payload.get("error") or payload.get("message") or ""
            if err:
                errors.append(str(err))
                print(f"[but-error] {str(err)[:500]}", flush=True)

    # Fallback: scrape the last assistant message if `done` was never called.
    if not final_text:
        for event in reversed(events):
            if (event.get("type") or "") in ("assistant.message", "message.assistant"):
                payload = event.get("payload") or {}
                text = (payload.get("text") or payload.get("content") or "").strip()
                if text:
                    final_text = text
                    break

    if proc.returncode not in (0, None) and not final_text and not steps:
        raise RuntimeError(
            f"but exited with code {proc.returncode} before producing output. "
            f"stderr_tail:\n{chr(10).join(stderr_buf[-50:])[-2000:]}"
        )

    answer = (final_text or "").strip()
    if errors and not answer:
        final_result = f"[but_error] {errors[0][:500]}"
    elif errors:
        final_result = f"[but_error_recovered] {answer}"
    else:
        final_result = answer or "[but_no_output]"

    screenshots = _collect_screenshots(artifact_dir) if artifact_dir is not None else []

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
