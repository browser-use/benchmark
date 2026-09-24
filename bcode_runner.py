"""BrowserCode execution and task-local evidence collection for BU Bench V2."""

import asyncio
import base64
import hashlib
import json
import math
import os
import shutil
import signal
import time
import zipfile
from pathlib import Path
from xml.etree import ElementTree

import httpx
import websockets

BCODE_VERSION = "0.1.20"
DEFAULT_MODEL = "openai/gpt-6-luna"
DEFAULT_REASONING = "low"
PROVIDER_KEYS = {
    "openai": "OPENAI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "google": "GOOGLE_GENERATIVE_AI_API_KEY",
    "xai": "XAI_API_KEY",
}
EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")
PRE_PROMPT = """You are a coding agent with browser access working autonomously to complete a task.
A browser is preconfigured: await session.connect() inside browser_execute attaches to it.
Calling session.Page.captureScreenshot() returns the image and auto-attaches it to your next turn so you can see it inline.
Save every file deliverable under {outputs}. Those files and your final response are returned to the user.
Use the browser and available tools to obtain evidence. Your final response should be clear and honest.
To search the web, prefer DuckDuckGo. If using Google, open its homepage and type the query instead of opening a cold search URL.

Task: {task}"""


def resolve_model(value, effort=None):
    if "@" in value:
        value, suffix = value.rsplit("@", 1)
        if effort is not None and effort != suffix:
            raise ValueError("Conflicting @effort and --agent-reasoning")
        effort = suffix
    if "/" not in value:
        value = "openai/" + value
    provider, model = value.split("/", 1)
    if provider not in PROVIDER_KEYS or not model:
        raise ValueError(
            f"Use a provider/model ID with one of: {', '.join(PROVIDER_KEYS)}"
        )
    effort = effort or DEFAULT_REASONING
    if effort not in EFFORTS:
        raise ValueError(f"Unsupported reasoning effort {effort!r}")
    return value, effort


def provider_config(model, effort):
    provider, model_id = model.split("/", 1)
    if provider == "openai":
        variant = {
            "forceReasoning": True,
            "reasoningEffort": effort,
            "reasoningSummary": "auto",
            "include": ["reasoning.encrypted_content"],
        }
    elif provider == "openrouter":
        variant = {"reasoning": {"effort": effort}}
    else:
        # Other providers use their catalog's variants; preflight checks presence.
        return {"experimental": {"fetch_use": True}}
    return {
        "experimental": {"fetch_use": True},
        "provider": {provider: {"models": {model_id: {"variants": {effort: variant}}}}},
    }


def agent_env(model, effort, state_dir, catalog_path=None, *, fetch_use=True):
    provider = model.split("/", 1)[0]
    env = {
        key: os.environ[key]
        for key in (
            "HOME",
            "PATH",
            "LANG",
            "LC_ALL",
            "TMPDIR",
            "USER",
            "SHELL",
            "SSL_CERT_FILE",
            "SSL_CERT_DIR",
            "HTTPS_PROXY",
            "HTTP_PROXY",
            "NO_PROXY",
        )
        if key in os.environ
    }
    for key in (PROVIDER_KEYS[provider], "BROWSER_USE_API_KEY"):
        if os.environ.get(key):
            env[key] = os.environ[key]
    # No inherited judge-only key, benchmark config, external plugins or agent sessions.
    env.update(
        DO_NOT_TRACK="1",
        OPENCODE_DISABLE_AUTOUPDATE="true",
        XDG_CONFIG_HOME=str(state_dir / "config"),
        XDG_DATA_HOME=str(state_dir / "data"),
        XDG_CACHE_HOME=str(state_dir / "cache"),
        OPENCODE_CONFIG_CONTENT=json.dumps(provider_config(model, effort)),
    )
    config = json.loads(env["OPENCODE_CONFIG_CONTENT"])
    config["experimental"]["fetch_use"] = fetch_use
    env["OPENCODE_CONFIG_CONTENT"] = json.dumps(config)
    if provider == "openai" and os.environ.get("OPENAI_BASE_URL"):
        config = json.loads(env["OPENCODE_CONFIG_CONTENT"])
        config["provider"]["openai"]["options"] = {
            "baseURL": os.environ["OPENAI_BASE_URL"]
        }
        env["OPENCODE_CONFIG_CONTENT"] = json.dumps(config)
    if catalog_path is not None:
        env["OPENCODE_MODELS_PATH"] = str(catalog_path)
        env["OPENCODE_DISABLE_MODELS_FETCH"] = "true"
    return env


async def preflight(
    binary,
    expected_version,
    model,
    effort,
    state_dir,
    catalog_client=None,
    *,
    browser="browser-use-cloud",
    fetch_use=True,
):
    binary = Path(binary).expanduser().resolve()
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise ValueError(f"BrowserCode is not installed at {binary}; see README setup")
    required = [PROVIDER_KEYS[model.split("/", 1)[0]]]
    if browser == "browser-use-cloud" or fetch_use:
        required.append("BROWSER_USE_API_KEY")
    for key in required:
        if not os.environ.get(key):
            raise ValueError(f"{key} is required")
    # Freeze the public catalog once for the whole run. A fresh bcode starts
    # from its embedded snapshot while refreshing asynchronously; otherwise a
    # newly released model can silently get text-only capabilities and zero limits.
    state_dir.mkdir(parents=True, exist_ok=True)
    catalog_path = state_dir / "model_catalog.json"
    client = catalog_client or httpx.AsyncClient(timeout=30)
    try:
        response = await client.get("https://models.dev/api.json")
        response.raise_for_status()
        catalog_data = response.json()
    finally:
        if catalog_client is None:
            await client.aclose()
    provider, model_id = model.split("/", 1)
    if model_id not in catalog_data.get(provider, {}).get("models", {}):
        raise ValueError(f"The public model catalog does not contain {model}")
    catalog_path.write_text(json.dumps({provider: catalog_data[provider]}))
    env = agent_env(model, effort, state_dir, catalog_path, fetch_use=fetch_use)
    effective_config = env["OPENCODE_CONFIG_CONTENT"]
    env["OPENCODE_CONFIG_CONTENT"] = json.dumps(
        {"experimental": {"fetch_use": fetch_use}}
    )

    async def probe(*args):
        proc = await asyncio.create_subprocess_exec(
            str(binary),
            "--pure",
            *args,
            env=env,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), 60)
        except BaseException:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()
            raise
        if proc.returncode:
            raise ValueError(
                f"BrowserCode {args[0]} preflight failed: {stderr.decode(errors='replace')[-1000:]}"
            )
        return stdout.decode(errors="replace")

    reported = (await probe("--version")).strip().splitlines()[-1]
    if reported.removeprefix("v") != expected_version.removeprefix("v"):
        raise ValueError(
            f"Expected bcode {expected_version}, installed binary reports {reported}"
        )
    catalog = await probe("models", model.split("/", 1)[0], "--verbose")
    lines = catalog.splitlines()
    try:
        index = lines.index(model)
        metadata, _ = json.JSONDecoder().raw_decode(
            "\n".join(lines[index + 1 :]).lstrip()
        )
    except (ValueError, IndexError):
        raise ValueError(f"BrowserCode's catalog does not contain {model}") from None
    variants = metadata.get("variants", {})
    if effort not in variants or not variants[effort]:
        raise ValueError(
            f"BrowserCode does not resolve a {effort!r} variant for {model}"
        )
    if not metadata.get("capabilities", {}).get("attachment"):
        raise ValueError(f"BrowserCode does not declare image support for {model}")
    if not metadata.get("capabilities", {}).get("input", {}).get("image"):
        raise ValueError(f"BrowserCode does not declare image input for {model}")
    if not metadata.get("limit", {}).get("context") or not metadata.get(
        "limit", {}
    ).get("output"):
        raise ValueError(f"BrowserCode has invalid token limits for {model}")
    # Validate the catalog's supported effort before applying the explicit wire options.
    env["OPENCODE_CONFIG_CONTENT"] = effective_config
    resolved = (await probe("models", provider, "--verbose")).splitlines()
    metadata, _ = json.JSONDecoder().raw_decode(
        "\n".join(resolved[resolved.index(model) + 1 :]).lstrip()
    )
    with binary.open("rb") as stream:
        binary_hash = hashlib.file_digest(stream, "sha256").hexdigest()
    return {
        "binary": str(binary),
        "version": reported,
        "binary_sha256": binary_hash,
        "catalog_path": str(catalog_path),
        "catalog_sha256": hashlib.sha256(catalog_path.read_bytes()).hexdigest(),
        "model": model,
        "reasoning_effort": effort,
        "resolved_model": metadata,
        "provider_config": json.loads(effective_config),
    }


class ScreenshotRecorder:
    """Independent CDP captures, saved with the completed tool's step number."""

    def __init__(self, url, directory):
        self.url, self.directory = url, directory
        self.ws = None
        self.counter = 0
        self.frames = []
        self.errors = []

    async def connect(self):
        self.ws = await websockets.connect(
            self.url, open_timeout=15, max_size=32 * 1024 * 1024
        )

    async def command(self, method, params=None, session_id=None):
        self.counter += 1
        request = {"id": self.counter, "method": method, "params": params or {}}
        if session_id:
            request["sessionId"] = session_id
        await self.ws.send(json.dumps(request))
        async with asyncio.timeout(5):
            while True:
                response = json.loads(await self.ws.recv())
                if response.get("id") != request["id"]:
                    continue
                if "error" in response:
                    raise RuntimeError(response["error"].get("message", "CDP error"))
                return response.get("result", {})

    async def capture(self, step):
        try:
            async with asyncio.timeout(15):
                targets = (await self.command("Target.getTargets"))["targetInfos"]
                for target in targets:
                    if target.get("type") != "page":
                        continue
                    session = (
                        await self.command(
                            "Target.attachToTarget",
                            {"targetId": target["targetId"], "flatten": True},
                        )
                    )["sessionId"]
                    try:
                        shot = await self.command(
                            "Page.captureScreenshot", {"format": "png"}, session
                        )
                        name = f"{len(self.frames) + 1:05d}-step-{step}.png"
                        (self.directory / name).write_bytes(
                            base64.b64decode(shot["data"])
                        )
                        self.frames.append(
                            {
                                "path": name,
                                "step": step,
                                "target_id": target["targetId"],
                                "captured_at": time.time(),
                            }
                        )
                    finally:
                        await self.command(
                            "Target.detachFromTarget", {"sessionId": session}
                        )
        except Exception as exc:  # noqa: BLE001 - preserve execution when CDP capture fails
            self.errors.append(f"step {step}: {type(exc).__name__}: {exc}")

    async def close(self):
        if self.ws:
            await self.ws.close()


def collect_outputs(directory):
    from openpyxl import load_workbook
    from pypdf import PdfReader

    texts, images, errors = [], [], []
    for path in sorted(directory.rglob("*")):
        if not path.is_file():
            continue
        name = str(path.relative_to(directory))
        if path.is_symlink() or not path.resolve().is_relative_to(directory.resolve()):
            errors.append(f"Skipped symlink outside deliverables: {name}")
            continue
        try:
            if path.stat().st_size > 20_000_000:
                raise ValueError("file exceeds 20 MB extraction limit")
            suffix = path.suffix.lower()
            if suffix in (".png", ".jpg", ".jpeg", ".webp"):
                if (
                    len(images) >= 20
                    or sum(len(i["data"]) for i in images) + path.stat().st_size * 4 / 3
                    > 8_000_000
                ):
                    raise ValueError("deliverable image budget exceeded")
                images.append(
                    {
                        "name": name,
                        "mime": "image/jpeg"
                        if suffix in (".jpg", ".jpeg")
                        else "image/" + suffix[1:],
                        "data": base64.b64encode(path.read_bytes()).decode(),
                    }
                )
                text = "Image deliverable attached to judge input."
            elif suffix == ".pdf":
                text = "\n".join(
                    page.extract_text() or "" for page in PdfReader(path).pages
                )
                if not text.strip():
                    raise ValueError("PDF has no extractable text")
            elif suffix == ".xlsx":
                workbook = load_workbook(path, read_only=True, data_only=True)
                try:
                    text = "\n".join(
                        f"Sheet: {sheet.title}\n"
                        + "\n".join(
                            "\t".join("" if v is None else str(v) for v in row)
                            for row in sheet.iter_rows(values_only=True)
                        )
                        for sheet in workbook
                    )
                finally:
                    workbook.close()
            elif suffix == ".docx":
                with zipfile.ZipFile(path) as archive:
                    root = ElementTree.fromstring(archive.read("word/document.xml"))
                    text = "\n".join(
                        element.text or ""
                        for element in root.iter()
                        if element.tag.endswith("}t")
                    )
            else:
                text = path.read_text(encoding="utf-8")
            texts.append(f"File: {name}\n{text}")
        except Exception as exc:  # noqa: BLE001 - retain unreadable deliverables and their errors
            errors.append(f"{name}: {type(exc).__name__}: {exc}")
    return "\n\n".join(texts), images, errors


async def stop_process(proc):
    if proc is None:
        return
    # bcode can leave tool subprocesses alive after its own process exits.
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    if proc.returncode is None:
        try:
            await asyncio.wait_for(proc.wait(), 3)
        except TimeoutError:
            pass
    # The parent can exit while a tool child ignores SIGTERM.
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    await proc.wait()


def find_chrome(binary=None):
    candidates = (
        [binary]
        if binary
        else [
            shutil.which("google-chrome"),
            shutil.which("google-chrome-stable"),
            shutil.which("chromium"),
            shutil.which("chromium-browser"),
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        ]
    )
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return str(Path(candidate).resolve())
    raise ValueError(
        "Install Google Chrome/Chromium or pass --chrome-bin /path/to/chrome"
    )


async def start_local_browser(task_dir, *, binary=None, headless=True):
    """Own a fresh Chrome process/profile and an ephemeral loopback CDP port."""
    binary = find_chrome(binary)
    profile = task_dir / "browser-profile"
    profile.mkdir()
    command = [
        binary,
        f"--user-data-dir={profile}",
        "--remote-debugging-address=127.0.0.1",
        "--remote-debugging-port=0",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-dev-shm-usage",
        "--window-size=1920,1080",
    ]
    if headless:
        command.append("--headless=new")
    if os.environ.get("CI") or os.geteuid() == 0:
        command.append("--no-sandbox")
    command.append("about:blank")
    with (task_dir / "browser.log").open("wb") as log:
        proc = await asyncio.create_subprocess_exec(
            *command,
            stdout=log,
            stderr=log,
            stdin=asyncio.subprocess.DEVNULL,
            start_new_session=True,
        )
    try:
        async with asyncio.timeout(30):
            port_file = profile / "DevToolsActivePort"
            while True:
                if proc.returncode is not None:
                    raise RuntimeError("Chrome exited during startup; see browser.log")
                if port_file.is_file():
                    lines = port_file.read_text().splitlines()
                    if len(lines) >= 2:
                        return proc, f"ws://127.0.0.1:{int(lines[0])}{lines[1]}"
                await asyncio.sleep(0.1)
    except BaseException:
        await stop_process(proc)
        raise


async def execute(
    task_text,
    task_dir,
    *,
    binary,
    model,
    effort,
    timeout,
    cloud_client=None,
    catalog_path=None,
    browser="browser-use-cloud",
    chrome_bin=None,
    fetch_use=True,
):
    workspace = task_dir / "workspace"
    outputs = workspace / "outputs"
    shots = task_dir / "screenshots"
    for path in (outputs, shots, task_dir / "agent_screenshots"):
        path.mkdir(parents=True, exist_ok=False)
    env = agent_env(
        model, effort, task_dir / "state", catalog_path, fetch_use=fetch_use
    )
    env.update(
        PWD=str(workspace), BCODE_SCREENSHOT_DIR=str(task_dir / "agent_screenshots")
    )
    trace = {
        "final_result": "",
        "agent_steps": [],
        "screenshots_b64": [],
        "screenshot_steps": [],
        "screenshot_timing": "after",
        "output_files_text": "",
        "output_images": [],
        "evidence_errors": [],
    }
    metrics = {"steps": 0, "duration": 0.0, "cost": 0.0}
    browser_id, proc, recorder, chrome_proc = None, None, None, None
    errors = []
    start = time.monotonic()
    own_client = cloud_client is None
    client = cloud_client or httpx.AsyncClient(timeout=90)
    base = os.environ.get("BU_CLOUD_API_BASE", "https://api.browser-use.com").rstrip(
        "/"
    )
    try:
        if browser.startswith("local_"):
            chrome_proc, cdp = await start_local_browser(
                task_dir, binary=chrome_bin, headless=browser == "local_headless"
            )
        else:
            headers = {"X-Browser-Use-API-Key": os.environ["BROWSER_USE_API_KEY"]}
            for attempt in range(6):
                response = await client.post(
                    base + "/api/v2/browsers",
                    headers=headers,
                    json={"timeout": math.ceil(timeout / 60)},
                )
                if response.status_code != 429 or attempt == 5:
                    break
                await asyncio.sleep(min(2**attempt, 15))
            response.raise_for_status()
            data = response.json()
            browser_id = data["id"]
            cdp = data["cdpUrl"]
            if not cdp.startswith(("ws://", "wss://")):
                response = await client.get(cdp.rstrip("/") + "/json/version")
                response.raise_for_status()
                cdp = response.json()["webSocketDebuggerUrl"]
        env["BU_CDP_WS"] = cdp
        recorder = ScreenshotRecorder(cdp, shots)
        await recorder.connect()
        command = [
            str(binary),
            "--pure",
            "run",
            "--model",
            model,
            "--variant",
            effort,
            "--format",
            "json",
            "--thinking",
            "--dangerously-skip-permissions",
            "--",
            PRE_PROMPT.format(outputs=outputs, task=task_text),
        ]
        with (task_dir / "stderr.log").open("wb") as stderr:
            proc = await asyncio.create_subprocess_exec(
                *command,
                cwd=workspace,
                env=env,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=stderr,
                start_new_session=True,
                limit=32 * 1024 * 1024,
            )

            async def consume():
                with (task_dir / "events.jsonl").open("wb") as events:
                    async for raw in proc.stdout:
                        events.write(raw)
                        events.flush()
                        try:
                            event = json.loads(raw)
                        except (json.JSONDecodeError, UnicodeDecodeError):
                            continue
                        part = event.get("part") or {}
                        kind = event.get("type")
                        if kind == "step_finish":
                            metrics["cost"] += float(part.get("cost") or 0)
                        elif kind in ("text", "reasoning"):
                            text = part.get("text") or ""
                            trace["agent_steps"].append(f"{kind}: {text}")
                            if kind == "text" and text.strip():
                                trace["final_result"] = text
                        elif kind == "tool_use":
                            state = part.get("state") or {}
                            trace["agent_steps"].append(
                                json.dumps(
                                    {
                                        "tool": part.get("tool"),
                                        "input": state.get("input"),
                                        "output": state.get("output"),
                                        "error": state.get("error"),
                                        "status": state.get("status"),
                                    },
                                    ensure_ascii=False,
                                )
                            )
                            if part.get("tool") in (
                                "browser_execute",
                                "browser-execute",
                            ) and state.get("status") in ("completed", "error"):
                                await recorder.capture(len(trace["agent_steps"]))
                        elif kind == "error":
                            errors.append(
                                json.dumps(event.get("error"), ensure_ascii=False)
                            )
                await proc.wait()

            try:
                await asyncio.wait_for(consume(), timeout)
            except TimeoutError:
                errors.append(
                    f"Task timed out after {timeout}s; grading preserved partial work"
                )
            finally:
                await stop_process(proc)
            if proc.returncode and not errors:
                errors.append(f"BrowserCode exited with status {proc.returncode}")
    finally:
        await stop_process(proc)
        await stop_process(chrome_proc)
        if browser_id:
            try:
                stopped = await client.patch(
                    base + f"/api/v2/browsers/{browser_id}",
                    headers=headers,
                    json={"action": "stop"},
                )
                stopped.raise_for_status()
            except httpx.HTTPError as exc:
                errors.append(f"Cloud cleanup failed: {type(exc).__name__}")
        if own_client:
            await client.aclose()
        if recorder:
            try:
                await recorder.close()
            except Exception as exc:  # noqa: BLE001 - cleanup must continue to Cloud stop
                errors.append(
                    f"Screenshot connection cleanup failed: {type(exc).__name__}"
                )
            (task_dir / "screenshot_manifest.json").write_text(
                json.dumps(recorder.frames, indent=2)
            )
            for frame in recorder.frames:
                try:
                    image = base64.b64encode(
                        (shots / frame["path"]).read_bytes()
                    ).decode()
                except OSError as exc:
                    recorder.errors.append(
                        f"Cannot read {frame['path']}: {type(exc).__name__}"
                    )
                    continue
                trace["screenshots_b64"].append(image)
                trace["screenshot_steps"].append(frame["step"])
            trace["evidence_errors"].extend(recorder.errors)
        trace["output_files_text"], trace["output_images"], file_errors = (
            collect_outputs(outputs)
        )
        trace["evidence_errors"].extend(file_errors)
        metrics.update(
            steps=len(trace["agent_steps"]), duration=time.monotonic() - start
        )
        if errors:
            trace["agent_steps"].append("Executor notes: " + "\n".join(errors))
        if trace["evidence_errors"]:
            trace["agent_steps"].append(
                "Evidence collection errors: " + "\n".join(trace["evidence_errors"])
            )
        (task_dir / "trace.json").write_text(json.dumps(trace))
    return {"trace": trace, "metrics": metrics, "execution_errors": errors}
