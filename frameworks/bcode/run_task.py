"""Run a single benchmark task using bcode (browsercode).

bcode is a coding agent (opencode fork) with a built-in browser harness.
We pre-provision a browser-use-cloud session and pass its CDP URL through
`BU_CDP_WS`, which the in-process CDP `Session.connect()` reads as a
default endpoint when the agent calls `session.connect()` with no args
(v0.1.1+). bcode then runs headlessly:

    bcode run --model <provider/slug> --format json -- "<task>"

Stdout is one JSON event per line (tool_use, step_start, step_finish, text,
reasoning, error). We extract steps, final answer, and cost from these
events.

Screenshots (v0.1.2+): the bcode browser-execute hook taps every
`Page.captureScreenshot` CDP call and (a) auto-attaches the image to the
agent's next assistant turn so the model sees it inline, and (b) when
`BCODE_SCREENSHOT_DIR=<path>` is set, writes the same PNG to disk for the
eval-judge. Files are named `<sessionID>-<startedAt>-<seq>.<ext>` so
sort-by-name is sort-by-time. We point the dump dir at a per-task subdir,
read the PNGs back as base64, and hand them to the judge -- matching the
v0.0.x `/tmp/shots/` flow. Pin `framework_ref >= v0.1.2` to use this hook.

v0.1.0 vs v0.1.1 vs v0.1.2: v0.1.0 ported the harness from Python (uv +
helpers.py + daemon) to in-process TypeScript (the agent writes JS that
drives a CDP `Session` directly). v0.1.0 dropped honoring `BU_CDP_WS`;
v0.1.1 restored it as a default in `Session.connect()`. v0.1.2 added the
`Page.captureScreenshot` tap (auto-attach + `BCODE_SCREENSHOT_DIR` disk
dump). Pin `framework_ref >= v0.1.2` for screenshot-judging.
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
    "fetch_use": "Enable/disable the Browser-Use fetch-use proxy for bcode's webfetch tool (true/false, default: true on v0.1.3+ when BROWSER_USE_API_KEY is set). Setting false injects {\"experimental\":{\"fetch_use\":false}} via OPENCODE_CONFIG_CONTENT so webfetch uses the native HttpClient instead of the proxy. Use for A/B isolating v0.1.3's fetch-use rewrite from other v0.1.3 changes.",
}

PRE_PROMPT = (
    "You are a coding agent with browser access working fully autonomously. "
    "A browser is preconfigured for you: calling `await session.connect()` "
    "(no args) inside a `browser_execute` snippet attaches to it. "
    "Calling `session.Page.captureScreenshot()` returns the image and the "
    "harness auto-attaches it to your next turn so you can see it inline. "
    "Take screenshots whenever you need to verify page state. Your final "
    "assistant message is what the judge will read as your answer to the "
    "task.\n\n"
    "Work to complete the following task: {task}"
)

# bcode is installed via the official curl installer (eval.yaml) which drops
# the binary at $HOME/.bcode/bin/bcode. Resolve once at import time.
BCODE_BIN = str(Path(os.environ["HOME"]) / ".bcode" / "bin" / "bcode")
# Where bcode v0.1.2+ writes Page.captureScreenshot dumps when
# BCODE_SCREENSHOT_DIR is set. Per-task: reset before run, drained after.
SHOTS_DIR = Path("/tmp/bcode_shots")


def _reset_shots_dir() -> None:
    if SHOTS_DIR.exists():
        shutil.rmtree(SHOTS_DIR)
    SHOTS_DIR.mkdir(parents=True)


def _collect_screenshots() -> list[str]:
    """Read every PNG/JPEG bcode wrote during this task as base64.

    File naming (v0.1.2): `<sessionID>-<startedAt>-<seq>.<png|jpeg>`. Sort
    by name to recover capture order across parallel browser_execute calls
    (in practice opencode serializes tool calls within one assistant
    message, so this is just a stable order).
    """
    if not SHOTS_DIR.exists():
        return []
    paths = sorted(p for p in SHOTS_DIR.iterdir() if p.is_file() and p.suffix in (".png", ".jpeg", ".jpg"))
    return [base64.b64encode(p.read_bytes()).decode() for p in paths]


def _bu_api_base() -> str:
    """Resolve the Browser-Use Cloud API base. Default prod, override via env."""
    base = os.environ.get("BU_CLOUD_API_BASE", "https://api.browser-use.com").rstrip("/")
    version = os.environ.get("BU_CLOUD_API_VERSION", "v3")
    return f"{base}/api/{version}"


def _bu_api_key() -> str:
    return os.environ.get("BU_CLOUD_API_KEY") or os.environ["BROWSER_USE_API_KEY"]

# Map a benchmark model alias to opencode's `provider/model` slug by checking
# substrings. Avoids a per-model lookup table; new models pass through as long
# as the provider keyphrase is present in the alias.
PROVIDER_KEYPHRASES = (
    ("claude", "anthropic"),
    ("gemini", "google"),
    ("gemma", "google"),
    ("gpt", "openai"),
    ("codex", "openai"),
)


def _resolve_model_slug(model_name: str) -> str:
    if "/" in model_name:
        return model_name
    lower = model_name.lower()
    for key, provider in PROVIDER_KEYPHRASES:
        if key in lower:
            return f"{provider}/{model_name}"
    raise ValueError(
        f"bcode: cannot infer provider for model {model_name!r}. "
        f"Pass an explicit `provider/model` slug as --model, or add a keyphrase "
        f"to PROVIDER_KEYPHRASES in frameworks/bcode/run_task.py."
    )


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


def _format_step(event: dict) -> str | None:
    """Turn one --format=json event into a step string (or None to skip)."""
    etype = event.get("type")
    part = event.get("part") or {}
    if etype == "tool_use":
        tool = part.get("tool", "?")
        inp = (part.get("state") or {}).get("input") or {}
        if tool == "bash":
            return f"bash: {(inp.get('command') or '').strip()[:2000]}"
        if tool in ("read", "write", "edit"):
            return f"{tool}: {inp.get('filePath') or inp.get('path') or ''}"
        if tool in ("browser-execute", "browser_execute"):
            # v0.1.0+ renamed the snippet field python -> code; v0.0.x used
            # python. Read both so this runner works against either harness.
            snippet = (inp.get("code") or inp.get("python") or "").strip()
            return f"browser_execute: {snippet[:2000]}"
        if tool == "webfetch":
            return f"webfetch: {inp.get('url') or ''}"
        if tool in ("glob", "grep", "codesearch", "websearch"):
            return f"{tool}: {inp.get('pattern') or inp.get('query') or ''}"
        try:
            return f"{tool}: {json.dumps(inp, separators=(',', ':'))[:2000]}"
        except Exception:
            return tool
    if etype == "text":
        text = (part.get("text") or "").strip()
        return f"text: {text[:2000]}" if text else None
    if etype == "reasoning":
        text = (part.get("text") or "").strip()
        return f"thinking: {text[:2000]}" if text else None
    return None


async def _drain_stderr(proc: asyncio.subprocess.Process, buf: list[str]) -> None:
    assert proc.stderr is not None
    while line := await proc.stderr.readline():
        s = line.decode("utf-8", errors="replace").rstrip("\n")
        buf.append(s)
        print(f"[bcode-stderr] {s}", flush=True)


async def _iter_lines(stream: asyncio.StreamReader):
    """Yield one line at a time, tolerant of arbitrarily long lines."""
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
    model_slug = _resolve_model_slug(os.environ["MODEL"])

    # browser-use-cloud session via direct API. BU_CDP_WS is read by bcode's
    # in-process CDP `Session.connect()` (v0.1.1+) as a default endpoint when
    # the agent calls `session.connect()` with no args. v0.0.x's Python
    # harness daemon read the same env var. Single env-var keeps the runner
    # compatible with both harness eras.
    browser_id, cdp_ws = _start_browser()

    parent_span_context = Laminar.serialize_span_context()
    # Reset and route the screenshot dump dir BEFORE bcode starts. v0.1.2+
    # writes every Page.captureScreenshot result here (in addition to the
    # auto-attach to the agent's next turn -- same tap, two consumers).
    _reset_shots_dir()
    env = {
        **os.environ,
        "BU_CDP_WS": cdp_ws,
        "BCODE_SCREENSHOT_DIR": str(SHOTS_DIR),
    }
    if parent_span_context:
        env["LMNR_PARENT_SPAN_CONTEXT"] = parent_span_context
    # fetch_use=false -> inject opencode.json-equivalent config disabling the
    # fetch-use proxy. OPENCODE_CONFIG_CONTENT is merged with local-scope
    # precedence at startup (see packages/opencode/src/config/config.ts:593),
    # so this overrides any default bcode would have applied. Schema:
    # experimental.fetch_use: bool (v0.1.3+). When BROWSER_USE_API_KEY is set
    # AND this flag is true (default), webfetch routes via fetch.browser-use.com;
    # setting false falls back to native HttpClient. No-op on <v0.1.3 where the
    # config key is unknown -- schema validation strips unknown keys silently
    # but does not error, so this is safe to pass on older refs.
    fetch_use_param = params.get("fetch_use", "").strip().lower()
    if fetch_use_param in ("false", "0", "no", "off"):
        env["OPENCODE_CONFIG_CONTENT"] = json.dumps({"experimental": {"fetch_use": False}})
    elif fetch_use_param and fetch_use_param not in ("true", "1", "yes", "on"):
        raise ValueError(f"Invalid fetch_use={fetch_use_param!r}; expected true|false")
    # --dangerously-skip-permissions: without it, run mode auto-REJECTS every
    # permission ask (e.g. external_directory for the harness helpers cache),
    # silently failing the agent partway through. In an unattended GHA runner
    # there's no human to approve and nothing dangerous about a fresh VM.
    cmd = [
        BCODE_BIN, "run",
        "--model", model_slug,
        "--format", "json",
        "--dangerously-skip-permissions",
        "--", PRE_PROMPT.format(task=task_description),
    ]

    start = time.time()
    steps: list[str] = []
    final_text = ""
    total_cost = 0.0
    errors: list[str] = []
    stderr_buf: list[str] = []

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd="/tmp",
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
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                print(f"[bcode-stdout-raw] {line[:500]}", flush=True)
                continue

            if (s := _format_step(event)):
                steps.append(s)
                print(f"[step {len(steps):>3}] {s[:500]}", flush=True)

            if event.get("type") == "text":
                if t := ((event.get("part") or {}).get("text") or "").strip():
                    final_text = t
            elif event.get("type") == "step_finish":
                total_cost += float((event.get("part") or {}).get("cost") or 0.0)
            elif event.get("type") == "error":
                err = event.get("error")
                errors.append(err if isinstance(err, str) else json.dumps(err))
                print(f"[bcode-error] {errors[-1][:500]}", flush=True)

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

    if proc.returncode not in (0, None) and not final_text and not steps:
        raise RuntimeError(
            f"bcode exited with code {proc.returncode} before producing output. "
            f"stderr_tail:\n{chr(10).join(stderr_buf[-50:])[-2000:]}"
        )

    answer = (final_text or "").strip()
    if errors and not answer:
        final_result = f"[bcode_error] {errors[0][:500]}"
    elif errors:
        final_result = f"[bcode_error_recovered] {answer}"
    else:
        final_result = answer or "[bcode_no_output]"

    return ExecutionResult(
        final_result=final_result,
        steps=steps,
        # v0.1.2+ taps Page.captureScreenshot and writes PNGs to
        # BCODE_SCREENSHOT_DIR (set above to SHOTS_DIR). Drain them now so
        # the judge sees the same visual signal as on v0.0.x.
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
