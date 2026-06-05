# Benchmark Eval Viewer

A small local web UI to browse benchmark eval runs and inspect per-task details
(prompt, judge reasoning, trajectory steps, and screenshots).

It mirrors the trajectory-detail experience of the Edge Shoreline copilot eval pipeline,
but reads the JSON files this benchmark already produces (`<run>/<task_id>.json`
with `agent_trace`, `judgement`, `metrics`, and `screenshots_b64`).

## Run

No extra dependencies — uses only the Python standard library.

```powershell
# From the benchmark/ folder:
python viewer/serve.py `
    --results-root "Q:/research/browser-use/eval/Benchmark_Eval_Results" `
    --port 8000
```

Then open <http://localhost:8000>.

### Arguments

| Flag | Default | Description |
| --- | --- | --- |
| `--results-root` | `./Benchmark_Eval_Results` | Folder containing run subfolders of per-task JSON files. |
| `--host` | `127.0.0.1` | Bind host. |
| `--port` | `8000` | Bind port. |

## UI

- **Run picker** — select any run folder under the results root.
- **Task list** — filterable, sorted with failures first; shows verdict, step count, duration.
- **Detail pane** — for the selected task:
  - Prompt + task_id + verdict badge
  - Judge: verdict, failure reason, reasoning, `impossible_task`, `reached_captcha`
  - Metrics: steps, duration, cost
  - Final result text
  - Trajectory: every agent step
  - Screenshots: decoded from `screenshots_b64`, click to enlarge

## HTTP API

| Method | Path | Returns |
| --- | --- | --- |
| GET | `/api/runs` | List of run folders |
| GET | `/api/runs/{run}/tasks` | Task summaries for a run |
| GET | `/api/runs/{run}/tasks/{task_id}` | Full task JSON (without base64 screenshots) |
| GET | `/api/runs/{run}/tasks/{task_id}/screenshots/{idx}.png` | Decoded screenshot PNG |
