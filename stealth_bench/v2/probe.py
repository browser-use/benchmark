#!/usr/bin/env python3
"""Short deterministic website-access probe using stock Chromium or Browser Use Cloud."""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from urllib.parse import urlsplit

BLOCK_PATTERNS = (
    r"verify (?:that )?you(?: are|'re) (?:a )?human",
    r"unusual traffic from your computer",
    r"checking (?:your browser|if the site connection is secure)",
    r"performing security verification",
    r"you(?: have|'ve) been blocked",
    r"you have been denied access",
    r"access to this page has been denied",
    r"blocked by (?:network security|security)",
    r"press (?:&|and) hold",
    r"pardon our interruption",
    r"confirm you are not a robot",
    r"please (?:verify|confirm) (?:that )?you(?: are|'re) (?:a )?human",
    r"enable javascript and cookies to continue",
    r"enable js and disable any ad blocker",
)
BLOCK_TITLES = re.compile(
    r"^(just a moment|access denied|robot or human|attention required|security check|403 forbidden)", re.I
)


def block_signals(snapshot: dict) -> list[str]:
    """Candidate screening only; never substitutes for the independent final judge."""
    text = snapshot.get("text", "").lower()
    title = snapshot.get("title", "")
    signals = [pattern for pattern in BLOCK_PATTERNS if re.search(pattern, text[:6000])]
    if BLOCK_TITLES.search(title):
        signals.append("block_title")
    if "/sorry/" in snapshot.get("url", "") and "google." in snapshot.get("url", ""):
        signals.append("google_sorry")
    if len(text) < 6000 and "captcha" in text and re.search(r"robot|challenge|verify|verification", text):
        signals.append("visible_captcha_text")
    return signals


def cloud_api(method: str, path: str, payload: dict) -> dict:
    request = urllib.request.Request(
        "https://api.browser-use.com/api/v2/browsers" + path,
        data=json.dumps(payload).encode(),
        headers={"X-Browser-Use-API-Key": os.environ["BROWSER_USE_API_KEY"], "Content-Type": "application/json"},
        method=method,
    )
    with urllib.request.urlopen(request, timeout=90) as response:
        return json.load(response)


def provider_request(method: str, url: str, headers: dict, payload: dict | None = None) -> dict:
    # Steel's API edge rejects urllib's request signature (403/1010).
    # The same account/payload succeeds with its documented HTTPX-style transport.
    if url.startswith("https://api.steel.dev/"):
        import httpx

        with httpx.Client(timeout=90) as client:
            response = client.request(method, url, headers=headers, json=payload)
            response.raise_for_status()
            return response.json() if response.content else {}
    request = urllib.request.Request(
        url, data=None if payload is None else json.dumps(payload).encode(),
        headers={**headers, "Content-Type": "application/json"}, method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            body = response.read()
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as error:
        detail = error.read(2000).decode(errors="replace")
        for name, value in os.environ.items():
            if value and ("KEY" in name or "TOKEN" in name or "SECRET" in name):
                detail = detail.replace(value, "[redacted]")
        error.provider_detail = detail[:800]
        raise


def provider_spec(mode: str, options: dict) -> tuple[str, dict, dict]:
    """Explicit requested settings; credentials never enter retained metadata."""
    viewport = {"width": 1365, "height": 900}
    if mode == "browserbase":
        payload = {
            "proxies": [{"type": "browserbase", "geolocation": {"country": "US"}}],
            "browserSettings": {
                "solveCaptchas": True, "viewport": viewport,
                "verified": options.get("verified", False),
            },
            "timeout": 300,
        }
        if os.environ.get("BROWSERBASE_PROJECT_ID"):
            payload["projectId"] = os.environ["BROWSERBASE_PROJECT_ID"]
        return "https://api.browserbase.com/v1/sessions", {
            "X-BB-API-Key": os.environ["BROWSERBASE_API_KEY"]
        }, payload
    if mode == "kernel":
        return "https://api.onkernel.com/browsers", {
            "Authorization": "Bearer " + os.environ["ONKERNEL_API_KEY"]
        }, {"stealth": True, "headless": False, "timeout_seconds": 300, "viewport": viewport}
    if mode == "anchor":
        return "https://api.anchorbrowser.io/v1/sessions", {
            "anchor-api-key": os.environ["ANCHORBROWSER_API_KEY"]
        }, {
            "session": {"proxy": {"type": "anchor_residential", "active": True}},
            "browser": {
                "captcha_solver": {"active": True}, "extra_stealth": {"active": True},
                "adblock": {"active": True}, "popup_blocker": {"active": True},
                "force_popups_as_tabs": {"active": True},
            },
        }
    if mode == "hyperbrowser":
        return "https://api.hyperbrowser.ai/api/session", {
            "x-api-key": os.environ["HYPERBROWSER_API_KEY"]
        }, {"useStealth": True, "useProxy": True, "solveCaptchas": True}
    if mode == "steel":
        return "https://api.steel.dev/v1/sessions", {
            "steel-api-key": os.environ["STEEL_API_KEY"]
        }, {"useProxy": True, "solveCaptcha": True}
    raise ValueError("unknown provider")


def acquire_provider(mode: str, options: dict) -> tuple[str, dict, tuple | None]:
    if mode == "browserless":
        token = os.environ["BROWSERLESS_API_KEY"]
        return (
            f"wss://production-sfo.browserless.io/stealth?token={token}&proxy=residential"
            "&proxyCountry=us&solveCaptchas=true&timeout=300000",
            {"route": "stealth", "proxy": "residential", "proxy_country": "us",
             "solve_captchas": True, "session_timeout_ms": 300000},
            None,
        )
    url, headers, payload = provider_spec(mode, options)
    data = provider_request("POST", url, headers, payload)
    if mode == "browserbase":
        sid, cdp = data["id"], data["connectUrl"]
        cleanup = ("POST", url + "/" + sid, headers, {"status": "REQUEST_RELEASE"})
    elif mode == "kernel":
        sid, cdp = data["session_id"], data["cdp_ws_url"]
        cleanup = ("DELETE", url + "/" + sid, headers)
    elif mode == "anchor":
        sid = data["data"]["id"]
        cdp = f"wss://connect.anchorbrowser.io?apiKey={headers['anchor-api-key']}&sessionId={sid}"
        cleanup = ("DELETE", url + "/" + sid, headers)
    elif mode == "hyperbrowser":
        sid, cdp = data.get("sessionId") or data["id"], data["wsEndpoint"]
        cleanup = ("PUT", url + "/" + sid + "/stop", headers)
    else:
        sid = data["id"]
        cdp = data["websocketUrl"] + "&apiKey=" + headers["steel-api-key"]
        cleanup = ("DELETE", url + "/" + sid, headers)
    retained = {k: v for k, v in payload.items() if k != "projectId"}
    return cdp, retained, cleanup


def local_launch_options(options: dict) -> tuple[dict, dict]:
    """Build stock-browser options; keep credentials out of retained evidence."""
    headless = options.get("headless", True)
    if not isinstance(headless, bool):
        raise ValueError("headless must be a boolean")
    proxy_mode = options.get("proxy", "none")
    if proxy_mode not in {"none", "browser-use"}:
        raise ValueError("proxy must be none or browser-use")
    launch = {"headless": headless, "args": ["--no-sandbox"]}
    retained = {"headless": headless, "proxy": proxy_mode, "captcha_solver": False}
    if proxy_mode == "browser-use":
        country = options.get("proxy_country_code", "us").lower()
        if not re.fullmatch(r"[a-z]{2}", country):
            raise ValueError("proxy_country_code must be a two-letter code")
        key = os.environ.get("BROWSER_USE_API_KEY")
        if not key:
            raise ValueError("proxy API key unavailable")
        session = uuid.uuid4().hex
        launch["proxy"] = {
            "server": "https://proxy.browser-use.com:443",
            "username": f"user-country-{country}-session-{session}",
            "password": key,
        }
        retained.update(proxy_server=launch["proxy"]["server"], proxy_country_code=country, proxy_session_id=session)
    return launch, retained


async def start_virtual_display():
    """Allocate a private X display without exposing it on the network."""
    process = await asyncio.create_subprocess_exec(
        "Xvfb",
        "-displayfd",
        "1",
        "-screen",
        "0",
        "1365x900x24",
        "-nolisten",
        "tcp",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        number = (await asyncio.wait_for(process.stdout.readline(), timeout=10)).decode().strip()
        if not number.isdecimal():
            raise RuntimeError("Xvfb did not allocate a display")
        return process, ":" + number
    except BaseException:
        if process.returncode is None:
            process.kill()
        await process.wait()
        raise


async def probe(task: dict, options: dict, workspace: Path) -> dict:
    from playwright.async_api import async_playwright

    allowed = {
        "browser", "observation_seconds", "proxy_country_code", "solve_captchas", "headless", "proxy", "verified"
    }
    unknown = set(options) - allowed
    if unknown:
        raise ValueError(f"unknown access-probe options: {sorted(unknown)}")
    mode = options.get("browser", "local")
    if mode not in {"local", "cloud", "browserbase", "kernel", "anchor", "hyperbrowser", "steel", "browserless"}:
        raise ValueError("unsupported browser")
    if "verified" in options and (mode != "browserbase" or not isinstance(options["verified"], bool)):
        raise ValueError("verified is a Browserbase boolean option")
    if mode != "local" and ("headless" in options or "proxy" in options):
        raise ValueError("headless and proxy options are only supported for the local browser")
    duration = int(options.get("observation_seconds", 45))
    if not 10 <= duration <= 120:
        raise ValueError("observation_seconds must be 10..120")
    url = task["website"]
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("website must be a public HTTP(S) URL without credentials")
    session_id = None
    provider_cleanup = None
    evidence = {
        "task_id": task["task_id"],
        "requested_url": url,
        "snapshots": [],
        "responses": [],
        "navigation_error": None,
        "browser_mode": mode,
        "observation_seconds": duration,
        "protocol": "stealth-access-v2.2-60s" if duration == 60 else "stealth-access-v2.1",
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    async with async_playwright() as playwright:
        browser = None
        display_process = None
        try:
            if mode == "cloud":
                payload = {
                    "timeout": 5,
                    "solveCaptchas": options.get("solve_captchas", True),
                    "proxyCountryCode": options.get("proxy_country_code", "us"),
                    "browserScreenWidth": 1365,
                    "browserScreenHeight": 900,
                }
                data = await asyncio.to_thread(cloud_api, "POST", "", payload)
                session_id = data["id"]
                evidence["cloud_session_id"] = session_id
                evidence["cloud_configuration"] = payload
                browser = await playwright.chromium.connect_over_cdp(data["cdpUrl"], timeout=60000)
                context = browser.contexts[0]
                page = await context.new_page()
            elif mode != "local":
                cdp, configuration, provider_cleanup = await asyncio.to_thread(acquire_provider, mode, options)
                evidence["provider_configuration"] = configuration
                browser = await playwright.chromium.connect_over_cdp(cdp, timeout=60000)
                context = browser.contexts[0]
                page = await context.new_page()
                await page.set_viewport_size({"width": 1365, "height": 900})
            else:
                launch, retained = local_launch_options(options)
                if not launch["headless"]:
                    display_process, display = await start_virtual_display()
                    launch["env"] = {**os.environ, "DISPLAY": display}
                    retained["display"] = "Xvfb"
                evidence["local_configuration"] = retained
                browser = await playwright.chromium.launch(**launch)
                context = await browser.new_context(viewport={"width": 1365, "height": 900}, locale="en-US")
                page = await context.new_page()
            evidence["browser_version"] = browser.version
            evidence["user_agent"] = await page.evaluate("navigator.userAgent")
            page.on(
                "response",
                lambda response: evidence["responses"].append({"url": response.url[:1500], "status": response.status})
                if response.request.is_navigation_request() and response.frame == page.main_frame
                else None,
            )
            start = time.monotonic()
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=20000)
            except Exception as error:
                evidence["navigation_error"] = str(error).split("\n")[0][:500]
            for index, when in enumerate((max(8, duration // 3), duration)):
                await asyncio.sleep(max(0, start + when - time.monotonic()))
                shot = workspace / f"access-{index}.png"
                snapshot = {"url": page.url, "elapsed_seconds": round(time.monotonic() - start, 3)}
                try:
                    snapshot["title"] = await page.title()
                    snapshot["text"] = (await page.locator("body").inner_text(timeout=5000))[:20000]
                    await page.screenshot(path=str(shot), timeout=10000)
                    snapshot["screenshot"] = shot.name
                except Exception as error:
                    snapshot["capture_error"] = str(error).split("\n")[0][:500]
                snapshot["block_signals"] = block_signals(snapshot)
                evidence["snapshots"].append(snapshot)
            evidence["duration_seconds"] = round(time.monotonic() - start, 3)
            return evidence
        finally:
            if browser:
                try:
                    await browser.close()
                except Exception:
                    evidence["browser_close_error"] = True
            if display_process and display_process.returncode is None:
                display_process.terminate()
                try:
                    await asyncio.wait_for(display_process.wait(), timeout=5)
                except TimeoutError:
                    display_process.kill()
                    await display_process.wait()
            if provider_cleanup:
                try:
                    await asyncio.to_thread(provider_request, *provider_cleanup)
                except Exception as error:
                    evidence["cleanup_error_type"] = type(error).__name__
            if session_id:
                try:
                    await asyncio.to_thread(cloud_api, "PATCH", "/" + session_id, {"action": "stop"})
                except Exception as error:
                    # Never print credential-bearing browser URLs.
                    evidence["cleanup_error_type"] = type(error).__name__


def main() -> None:
    workspace = Path(os.environ["EVAL_WORKSPACE"])
    task = json.loads(Path(os.environ["EVAL_TASK_PATH"]).read_text())
    options = json.loads(os.environ.get("EVAL_OPTIONS_JSON", "{}"))
    started = time.monotonic()
    try:
        evidence = asyncio.run(probe(task, options, workspace))
        (workspace / "access.json").write_text(json.dumps(evidence, indent=2))
        result = {
            "status": "completed",
            "final_output": json.dumps(
                {
                    "url": evidence["snapshots"][-1]["url"],
                    "title": evidence["snapshots"][-1].get("title"),
                    "block_signals": evidence["snapshots"][-1]["block_signals"],
                }
            ),
            "metrics": {"duration_seconds": time.monotonic() - started, "steps": 1},
            "artifacts": ["access.json", "access-0.png", "access-1.png"],
            "metadata": {"browser": options.get("browser", "local"), "protocol": evidence["protocol"]},
        }
    except Exception as error:
        detail = getattr(error, "provider_detail", str(error).split("\n")[0])
        for name, value in os.environ.items():
            if value and ("KEY" in name or "TOKEN" in name or "SECRET" in name):
                detail = detail.replace(value, "[redacted]")
        (workspace / "access.json").write_text(json.dumps({
            "acquisition_error": type(error).__name__, "http_status": getattr(error, "code", None),
            "provider_detail": detail[:800]
        }))
        result = {
            "status": "completed",
            "final_output": "No browser measurement; acquisition failed.",
            "metrics": {"duration_seconds": time.monotonic() - started},
            "metadata": {"acquisition_error": type(error).__name__},
        }
    Path(os.environ["EVAL_RESULT_PATH"]).write_text(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
