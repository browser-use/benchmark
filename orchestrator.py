"""Orchestrator to dispatch benchmark batches to GitHub runners and collect results."""

import os
import json
import time
import uuid
import zipfile
import io
from datetime import datetime
from pathlib import Path
import requests
from dotenv import load_dotenv

load_dotenv()

# Configuration
GITHUB_TOKEN = os.getenv("GITHUB_PAT")
REPO = "browser-use/benchmark"  # owner/repo
WORKFLOW_FILE = "eval.yaml"

TOTAL_TASKS = 100
BATCH_SIZE = 10
MAX_CONCURRENT_BATCHES = 25
POLL_INTERVAL = 5  # seconds

# Models to evaluate: {model_name: number_of_runs}
RUNS = {
    # "ChatBrowserUse-1": 5,
    # "ChatBrowserUse-2": 5,
    # "gemini-2.5-flash": 5,
    "claude-4-5-haiku": 5,
    "claude-4-5-sonnet": 5,
    # "gemini-3-flash-preview": 5,
}

RESULTS_DIR = Path(__file__).parent / "official_results"
API_BASE = f"https://api.github.com/repos/{REPO}"
HEADERS = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}


def dispatch_batch(model: str, start: int, end: int, tracking_id: str, run_start: str) -> bool:
    """Dispatch a workflow run. Returns True if successful."""
    url = f"{API_BASE}/actions/workflows/{WORKFLOW_FILE}/dispatches"
    data = {"ref": "main", "inputs": {"model": model, "start": str(start), "end": str(end), "parallel": "3", "tracking_id": tracking_id, "run_start": run_start}}
    resp = requests.post(url, headers=HEADERS, json=data, timeout=30)
    return resp.status_code == 204


def list_artifacts() -> list[dict]:
    """List recent artifacts in the repo (paginated). Returns newest first."""
    artifacts = []
    page = 1
    while page <= 1:  # Fetch up to 100 artifacts
        url = f"{API_BASE}/actions/artifacts?per_page=100&page={page}"
        try:
            resp = requests.get(url, headers=HEADERS, timeout=30)
        except requests.exceptions.Timeout:
            print(f"Timeout listing artifacts page {page}, will retry next poll")
            break
        if resp.status_code != 200:
            break
        batch = resp.json().get("artifacts", [])
        if not batch:
            break
        artifacts.extend(batch)
        page += 1
    return artifacts


def download_artifact(artifact_id: int, retries: int = 3) -> dict | None:
    """Download and extract artifact, return parsed JSON."""
    url = f"{API_BASE}/actions/artifacts/{artifact_id}/zip"
    for attempt in range(retries):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=60)
            if resp.status_code != 200:
                return None
            with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                for name in zf.namelist():
                    if name.endswith(".json"):
                        return json.loads(zf.read(name))
            return None
        except requests.exceptions.Timeout:
            print(f"Timeout downloading artifact {artifact_id}, attempt {attempt + 1}/{retries}")
            if attempt < retries - 1:
                time.sleep(2)
    return None


def save_result(model: str, batch_result: dict):
    """Aggregate batch result into official_results file for the model."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    filename = RESULTS_DIR / f"BrowserUse_0.11.4_browser_BrowserUseCloud_model_{model}.json"
    
    # Load or create run data
    runs = json.loads(filename.read_text()) if filename.exists() else []
    
    # Find or create run entry for this run_start
    run_start = batch_result.get("run_start", "unknown")
    run_entry = next((r for r in runs if r.get("run_start") == run_start), None)
    
    if not run_entry:
        run_entry = {"run_start": run_start, "tasks_completed": 0, "tasks_successful": 0, "total_steps": 0, "total_duration": 0, "total_cost": 0}
        runs.append(run_entry)
    
    # Aggregate batch metrics
    run_entry["tasks_completed"] += batch_result.get("tasks_completed", 0)
    run_entry["tasks_successful"] += batch_result.get("tasks_successful", 0)
    run_entry["total_steps"] += batch_result.get("total_steps", 0)
    run_entry["total_duration"] += batch_result.get("total_duration", 0)
    run_entry["total_cost"] += batch_result.get("total_cost", 0)
    
    filename.write_text(json.dumps(runs, indent=2))


def main():
    # Build queue of all batches to run
    pending = []  # [(model, start, end, tracking_id, run_start)]
    for model, num_runs in RUNS.items():
        for run_idx in range(num_runs):
            run_start = datetime.now().strftime("%Y%m%d_%H%M%S") + f"_{run_idx}"
            for start in range(0, TOTAL_TASKS, BATCH_SIZE):
                end = min(start + BATCH_SIZE, TOTAL_TASKS)
                tracking_id = str(uuid.uuid4())
                pending.append((model, start, end, tracking_id, run_start))
    
    print(f"Total batches to run: {len(pending)}")
    
    dispatched = {}  # tracking_id -> (model, start, end, run_start)
    completed = set()
    
    while pending or dispatched:
        # Dispatch new batches up to limit
        while pending and len(dispatched) < MAX_CONCURRENT_BATCHES:
            model, start, end, tracking_id, run_start = pending.pop(0)
            if dispatch_batch(model, start, end, tracking_id, run_start):
                dispatched[tracking_id] = (model, start, end, run_start)
                print(f"Dispatched: {model} [{start}:{end}] run={run_start} tracking={tracking_id[:8]}...")
            else:
                print(f"Failed to dispatch: {model} [{start}:{end}]")
                pending.insert(0, (model, start, end, tracking_id, run_start))  # Retry later
                break
        
        if not dispatched:
            break
        
        # Poll for completed artifacts
        print(f"Polling... ({len(dispatched)} running, {len(pending)} pending)")
        time.sleep(POLL_INTERVAL)
        
        artifacts = list_artifacts()
        batch_artifacts = [a for a in artifacts if a.get("name", "").startswith("batch-")]
        print(f"Found {len(batch_artifacts)} batch artifacts")
        
        for artifact in artifacts:
            name = artifact.get("name", "")
            if not name.startswith("batch-"):
                continue
            tracking_id = name.replace("batch-", "")
            if tracking_id in dispatched and tracking_id not in completed:
                result = download_artifact(artifact["id"])
                if result:
                    model, start, end, run_start = dispatched[tracking_id]
                    save_result(model, result)
                    completed.add(tracking_id)
                    del dispatched[tracking_id]
                    print(f"Completed: {model} [{start}:{end}] run={run_start} -> {result.get('tasks_successful')}/{result.get('tasks_completed')} successful")
    
    print("All batches complete!")


if __name__ == "__main__":
    main()
