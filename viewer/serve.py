"""Local viewer for benchmark eval results.

Run:
    python viewer/serve.py --results-root "Q:/research/browser-use/eval/Benchmark_Eval_Results" --port 8000

Then open http://localhost:8000 in your browser.

Uses only the Python standard library so no extra dependencies are needed.
"""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import re
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

STATIC_DIR = Path(__file__).parent / "static"
SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9._\-]+$")


def _safe_name(name: str) -> str:
    name = unquote(name)
    if not SAFE_NAME_RE.match(name):
        raise ValueError(f"unsafe name: {name!r}")
    return name


def _load_task(results_root: Path, run: str, task_id: str) -> dict:
    run_s = _safe_name(run)
    task_s = _safe_name(task_id)
    if not task_s.endswith(".json"):
        task_s += ".json"
    path = results_root / run_s / task_s
    if not path.is_file():
        raise FileNotFoundError(path)
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=512)
def _task_summary(path_str: str) -> dict:
    """Cheap summary read for the task list (skips screenshots when streaming the full file)."""
    path = Path(path_str)
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:  # noqa: BLE001
        return {
            "task_id": path.stem,
            "error": f"{type(exc).__name__}: {exc}",
        }
    trace = data.get("agent_trace") or {}
    judgement = data.get("judgement") or {}
    metrics = data.get("metrics") or {}
    return {
        "task_id": path.stem,
        "prompt": trace.get("agent_task") or "",
        "verdict": judgement.get("verdict"),
        "failure_reason": judgement.get("failure_reason") or "",
        "impossible_task": judgement.get("impossible_task"),
        "steps": metrics.get("steps"),
        "duration": metrics.get("duration"),
        "cost": metrics.get("cost"),
        "screenshot_count": len(trace.get("screenshots_b64") or []),
    }


class Handler(BaseHTTPRequestHandler):
    results_root: Path = Path()

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        print("[viewer] " + (fmt % args))

    # ---- helpers --------------------------------------------------------
    def _send_json(self, status: int, payload) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(self, status: int, ctype: str, body: bytes, cache: bool = True) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if cache:
            self.send_header("Cache-Control", "public, max-age=3600")
        else:
            self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_error_json(self, status: int, message: str) -> None:
        self._send_json(status, {"error": message})

    # ---- routes ---------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if path in ("/", "/index.html"):
                self._serve_static("index.html")
                return
            if path.startswith("/static/"):
                self._serve_static(path[len("/static/"):])
                return
            if path == "/api/runs":
                self._serve_runs()
                return
            m = re.match(r"^/api/runs/([^/]+)/tasks$", path)
            if m:
                self._serve_tasks(m.group(1))
                return
            m = re.match(r"^/api/runs/([^/]+)/tasks/([^/]+)$", path)
            if m:
                self._serve_task(m.group(1), m.group(2))
                return
            m = re.match(r"^/api/runs/([^/]+)/tasks/([^/]+)/screenshots/(\d+)\.png$", path)
            if m:
                self._serve_screenshot(m.group(1), m.group(2), int(m.group(3)))
                return
            self._send_error_json(404, f"not found: {path}")
        except FileNotFoundError as exc:
            self._send_error_json(404, str(exc))
        except ValueError as exc:
            self._send_error_json(400, str(exc))
        except Exception as exc:  # noqa: BLE001
            self._send_error_json(500, f"{type(exc).__name__}: {exc}")

    # ---- handlers -------------------------------------------------------
    def _serve_static(self, rel: str) -> None:
        rel = rel.lstrip("/")
        full = (STATIC_DIR / rel).resolve()
        if STATIC_DIR.resolve() not in full.parents and full != STATIC_DIR.resolve():
            raise ValueError("path escape")
        if not full.is_file():
            raise FileNotFoundError(rel)
        ctype = mimetypes.guess_type(full.name)[0] or "application/octet-stream"
        self._send_bytes(200, ctype, full.read_bytes(), cache=False)

    def _serve_runs(self) -> None:
        runs = []
        if self.results_root.is_dir():
            for child in sorted(self.results_root.iterdir(), key=lambda p: p.name.lower()):
                if not child.is_dir():
                    continue
                json_files = list(child.glob("*.json"))
                if not json_files:
                    continue
                runs.append({
                    "name": child.name,
                    "task_count": len(json_files),
                    "modified": child.stat().st_mtime,
                })
        self._send_json(200, {"results_root": str(self.results_root), "runs": runs})

    def _serve_tasks(self, run: str) -> None:
        run_s = _safe_name(run)
        run_dir = self.results_root / run_s
        if not run_dir.is_dir():
            raise FileNotFoundError(run)
        tasks = [_task_summary(str(p)) for p in sorted(run_dir.glob("*.json"))]
        # Failures first, then by task_id.
        tasks.sort(key=lambda t: (t.get("verdict") is True, t.get("task_id") or ""))
        passed = sum(1 for t in tasks if t.get("verdict") is True)
        failed = sum(1 for t in tasks if t.get("verdict") is False)
        self._send_json(200, {
            "run": run_s,
            "summary": {"total": len(tasks), "passed": passed, "failed": failed},
            "tasks": tasks,
        })

    def _serve_task(self, run: str, task_id: str) -> None:
        data = _load_task(self.results_root, run, task_id)
        trace = data.get("agent_trace") or {}
        screenshots = trace.get("screenshots_b64") or []
        thin_trace = {k: v for k, v in trace.items() if k != "screenshots_b64"}
        thin_trace["screenshot_count"] = len(screenshots)
        self._send_json(200, {
            "task_id": _safe_name(task_id).removesuffix(".json"),
            "run": _safe_name(run),
            "agent_trace": thin_trace,
            "judgement": data.get("judgement"),
            "metrics": data.get("metrics"),
        })

    def _serve_screenshot(self, run: str, task_id: str, idx: int) -> None:
        data = _load_task(self.results_root, run, task_id)
        screenshots = (data.get("agent_trace") or {}).get("screenshots_b64") or []
        if idx < 0 or idx >= len(screenshots):
            raise FileNotFoundError(f"screenshot {idx} out of range (have {len(screenshots)})")
        raw = screenshots[idx]
        if raw.startswith("data:"):
            raw = raw.split(",", 1)[1]
        png = base64.b64decode(raw)
        self._send_bytes(200, "image/png", png, cache=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--results-root",
        default=str(Path("Benchmark_Eval_Results").resolve()),
        help="Folder containing run subfolders of per-task JSON files.",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    results_root = Path(args.results_root).expanduser().resolve()
    if not results_root.is_dir():
        print(f"[viewer] WARNING: results root does not exist: {results_root}")
    Handler.results_root = results_root

    print(f"[viewer] Results root: {results_root}")
    print(f"[viewer] Serving at http://{args.host}:{args.port}")
    with ThreadingHTTPServer((args.host, args.port), Handler) as server:
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("[viewer] shutting down")


if __name__ == "__main__":
    main()
