# How the Browser-Use Benchmark Scores Agents

This document explains, end-to-end, how the `browser-use/benchmark` project judges
an agent run on a single task, where ground truth comes from, and how the final
score for an evaluation is computed and aggregated.

All file/line references are to this repo (`browser-use/benchmark`).

---

## 1. The dataset and ground truth

### 1.1 Where the tasks live

The benchmark suite (`BU_Bench_V1`) ships as a **single encrypted file**:

- `BU_Bench_V1.enc` at the repo root.
- It is Fernet-encrypted; the key is derived deterministically from the literal
  string `"BU_Bench_V1"` via SHA-256, then base64-url-encoded
  (`run_eval.py:64-66`):

  ```python
  def load_tasks() -> list[dict]:
      key = base64.urlsafe_b64encode(hashlib.sha256(b"BU_Bench_V1").digest())
      encrypted = base64.b64decode(TASKS_FILE.read_text())
      return json.loads(Fernet(key).decrypt(encrypted))
  ```

The encryption exists to discourage accidental memorization / contamination by
training crawlers — it is *not* a security boundary; the code that decrypts it
is in the repo.

### 1.2 Task schema

After decryption, `load_tasks()` returns a `list[dict]`. Every task has these
keys (verified by inspecting all 100 tasks):

| Key | Type | Required | Notes |
| --- | --- | --- | --- |
| `task_id` | str (UUID) | yes | Stable identifier; used as the per-task result filename. |
| `confirmed_task` | str | yes | The natural-language prompt given to the agent. |
| `category` | str | yes | Bucket the task belongs to. |
| `answer` | str | **optional** | Ground truth. Present on ~40 / 100 tasks. |

### 1.3 Categories

`BU_Bench_V1` is exactly **100 tasks**, evenly split into 5 categories of 20:

| Category | Count |
| --- | --- |
| `GAIA` | 20 |
| `BrowseComp` | 20 |
| `WebBenchREAD` | 20 |
| `OM2W2` | 20 |
| `InteractionTests` | 20 |

### 1.4 What "ground truth" means here

`answer` is **not** a strict equality check. It is a freeform string that gets
embedded into the **judge LLM's prompt** as the authoritative reference, and the
judge decides whether the agent satisfied it. Two flavours are seen in the
data:

1. **Factual answer** — e.g. `"6"` for *"how many of the top 10 highest-grossing
   worldwide movies are also on the top 10 highest-grossing domestic
   movies?"* The judge compares the agent's `final_result` against this.
2. **Criteria / expected outcome** — e.g. *"The success popup should show up"*
   or *"Google Doc must be created"*. The judge looks at the trajectory +
   screenshots to verify.

For the ~60 tasks **without** an `answer`, the judge falls back to a pure
"task satisfaction" rubric (see §2.3).

This is documented inside the judge system prompt itself (`judge.py:69-77`).

---

## 2. The LLM judge

### 2.1 Judge model

The judge is **hard-coded** in `run_eval.py:43`:

```python
JUDGE_LLM = ChatGoogle(model="gemini-2.5-flash", api_key=os.getenv("GOOGLE_API_KEY"))
```

> Always Gemini 2.5 Flash, "for consistent judging across all evaluations".
> The agent under test may use any other model; only the judge is fixed.

### 2.2 What the judge sees

For every task, after the agent finishes, `run_eval.py:139-160` assembles five
inputs and hands them to `construct_judge_messages()` (`judge.py:28`):

| Input | Source |
| --- | --- |
| `task` | `task["confirmed_task"]` |
| `final_result` | `agent_history.final_result()` (or `"Agent did not return a result"`) |
| `agent_steps` | `agent_history.agent_steps()` — formatted per-step strings |
| `ground_truth` | `task.get("answer")` (may be `None`) |
| `screenshots_b64` | base64 PNGs from `agent_history.screenshot_paths()` |

Text fields are individually truncated to **40 000 chars** with an
`...[truncated]...` marker (`judge.py:23-25, 50-53`).

Screenshots are **deduplicated** preserving order, then the **last
`max_images=10` unique** ones are sent (`judge.py:55-58`):

```python
seen = set()
unique = [s for s in reversed(screenshots_b64) if s not in seen and not seen.add(s)]
selected = list(reversed(unique[:max_images]))
```

The system prompt is built dynamically: a "GROUND TRUTH VALIDATION (HIGHEST
PRIORITY)" section is **only added when `ground_truth` is non-empty**
(`judge.py:67-77`). The user prompt likewise interpolates a `<ground_truth>…
</ground_truth>` block conditionally (`judge.py:166-172`).

### 2.3 The rubric (system prompt)

The judge's evaluation framework is fully spelled out in `judge.py:79-150`.
Summarized:

1. **Ground truth (when present) is absolute** — verdict MUST be `false` if it
   is not satisfied.
2. Then, in this order of importance: Task Satisfaction → Output Quality →
   Tool Effectiveness → Agent Reasoning → Browser Handling.
3. **Verdict is strictly binary** — `true` only if the task was completed as
   requested with no fabricated content; `false` for any partial completion.
4. **Auto-fail conditions** include: blocked by captcha, missing auth, wrong
   output format, infinite loops, browser crash, page not loaded, agent
   calling `done` before completing all key points, or fabricating content
   not present in the screenshot/page.
5. **Two side-channel flags** are also requested from the judge:
   - `impossible_task` — task was fundamentally unachievable (broken site,
     auth required without credentials, etc.). Conservative — bad agent
     decisions don't count.
   - `reached_captcha` — captcha/bot detection appeared in screenshots or
     errors.

### 2.4 Structured output

The judge response is parsed into this Pydantic model (`judge.py:14-20`):

```python
class JudgementResult(BaseModel):
    reasoning: str | None
    verdict: bool
    failure_reason: str | None
    impossible_task: bool = False
    reached_captcha: bool = False
```

It is requested via the LLM's structured-output mode in `run_eval.py:158-161`:

```python
response = await JUDGE_LLM.ainvoke(
    judge_messages, output_format=JudgementResult
)
judgement: JudgementResult = response.completion
```

So the JSON schema is enforced by the LLM client, not by post-hoc parsing of
free text.

---

## 3. Per-task score

`run_eval.py:163`:

```python
score = 1 if judgement.verdict else 0
```

That's it: **every task is worth 0 or 1**. There is no partial credit, no
weighting per category, and `impossible_task` / `reached_captcha` are
**recorded but do not affect the score**.

### 3.1 Other failure paths that yield `score = 0`

- Per-task `asyncio` timeout of **1800 seconds = 30 minutes**
  (`TASK_TIMEOUT`, `run_eval.py:46, 117-129`).
- Any uncaught exception inside `run_task` (`run_eval.py:198-210`): the
  traceback is recorded and `score=0` is returned.

In both cases the judge never runs.

### 3.2 Per-task artifact

For every task, a JSON file is written to
`run_data/<run_key>_start_at_<timestamp>/<task_id>.json`
(`run_eval.py:168-187`):

```json
{
  "agent_trace": {
    "agent_task": "...",
    "final_result": "...",
    "agent_steps": ["Step 1: ...", "Step 2: ..."],
    "ground_truth": "..." | null,
    "screenshots_b64": ["<base64>", ...]
  },
  "metrics": { "steps": <int>, "duration": <float>, "cost": <float> },
  "judgement": {
    "reasoning": "...",
    "verdict": true|false,
    "failure_reason": "...",
    "impossible_task": false,
    "reached_captcha": false
  }
}
```

These are exactly the files the local viewer (`viewer/serve.py`) renders.

---

## 4. Per-run aggregate score

After every task in a run has been judged, `run_eval.py:257-276` aggregates
the results and **appends** one row to `results/<run_key>.json`:

```python
successful   = sum(1 for r in results if r.get("score") == 1)
total_steps  = sum(r.get("steps", 0)    for r in results)
total_duration = sum(r.get("duration", 0) for r in results)
total_cost   = sum(r.get("cost", 0)     for r in results)
...
runs.append({
    "run_start": run_start,
    "tasks_completed": len(results),
    "tasks_successful": successful,
    "total_steps":   total_steps,
    "total_duration": total_duration,
    "total_cost":    total_cost,
})
```

So one **"run"** = one row, identified by start timestamp; one **`results/*.json`
file** holds the list of all runs ever recorded for that
`(framework, version, browser, model)` tuple.

### 4.1 Run-level success rate

A run's headline number is simply:

> **accuracy = `tasks_successful / tasks_completed`**

(`generate_plots.py:147-152`)

Reported either as a fraction (0.0–1.0) or a percentage (×100, see
`generate_plots.py:227, 317`).

### 4.2 Concurrency / fairness notes

- Tasks run in parallel with `asyncio.Semaphore(MAX_CONCURRENT=3)`
  (`run_eval.py:45, 247`). The score is unaffected, but throughput is.
- No retries: if a task errors or times out it is a permanent 0 for that run.

---

## 5. Multi-run aggregate (the published leaderboard)

`generate_plots.py` consumes `official_results/*.json`. The model name is
parsed from the filename suffix after `_model_`
(`generate_plots.py:136`):

```python
model = f.stem.split("_model_")[-1]
```

### 5.1 Run filtering

Only runs that completed **all 100 tasks** are kept
(`generate_plots.py:20, 138`):

```python
EXPECTED_TASKS = 100
valid = [r for r in runs if r["tasks_completed"] == EXPECTED_TASKS]
```

Partial / aborted runs are dropped (with a warning). This guards against
optimistic averages when an agent crashed early on the hard tasks.

### 5.2 Bootstrap confidence intervals

For each model, the per-run accuracies (and tasks-per-hour) are summarized
with **percentile bootstrap** at 95% (`generate_plots.py:163-174`):

```python
N_BOOTSTRAP = 1000

def bootstrap_ci(values, n=N_BOOTSTRAP):
    arr = np.array(values)
    means = [np.mean(np.random.choice(arr, size=len(arr), replace=True))
             for _ in range(n)]
    return float(np.mean(arr)), float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))
```

Reported as `mean`, `err_lo = mean - p2.5`, `err_hi = p97.5 - mean`
(`generate_plots.py:222-229, 308-322`).

Note this is a CI **over runs of the same model**, not over tasks. With only a
handful of runs the interval is wide; it captures judge / browser variance, not
task-level statistical power.

### 5.3 Tasks-per-hour

A throughput metric, used as the secondary axis on the speed-vs-accuracy plot
(`generate_plots.py:155-160`):

```python
3600 * r["tasks_completed"] / r["total_duration"]
```

Cost (`total_cost`) is recorded but not currently plotted.

---

## 6. End-to-end summary

```
                ┌──────────────────────┐
                │ BU_Bench_V1.enc      │
                │  (100 tasks; Fernet) │
                └──────────┬───────────┘
                           │  load_tasks()
                           ▼
              ┌─────────────────────────┐
              │ for each task (≤3 in    │
              │ parallel, ≤30 min each) │
              └──────────┬──────────────┘
                         ▼
        ┌───────────────────────────────────┐
        │ Agent runs in chosen browser/LLM  │
        │ → final_result, steps, screenshots│
        └──────────┬────────────────────────┘
                   ▼
        ┌─────────────────────────────────────┐
        │ Judge: Gemini 2.5 Flash             │
        │ inputs: task, final_result,         │
        │   steps (≤40k chars each),          │
        │   last 10 unique screenshots,       │
        │   ground_truth (if any)             │
        │ output: JudgementResult (verdict…)  │
        └──────────┬──────────────────────────┘
                   ▼
        score = 1 if verdict else 0
                   │
                   ▼
     write run_data/<run>/<task_id>.json
                   │
                   ▼
     aggregate → append row to results/<run>.json
       { tasks_completed, tasks_successful,
         total_steps, total_duration, total_cost }
                   │
                   ▼
   generate_plots.py:
     - drop runs where tasks_completed ≠ 100
     - per-model: accuracy = successful / completed
     - bootstrap 95% CI over runs
```

### TL;DR

- **Ground truth** is an optional freeform `answer` string on ~40% of tasks,
  embedded into the judge's prompt — not a string-equality check.
- **Per-task score** is binary (`0` or `1`), produced by **Gemini 2.5 Flash**
  acting as a vision+text judge over the task, the trajectory, up to 10
  deduped screenshots, and (when present) the ground truth.
- **Per-run score** is `tasks_successful / tasks_completed`.
- **Leaderboard score** is the **mean of run accuracies** for a model, with a
  **percentile bootstrap 95% CI** (n=1000) — restricted to runs that
  completed all 100 tasks.
