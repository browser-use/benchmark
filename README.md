<picture>
  <source media="(prefers-color-scheme: light)" srcset="https://github.com/user-attachments/assets/2ccdb752-22fb-41c7-8948-857fc1ad7e24"">
  <source media="(prefers-color-scheme: dark)" srcset="https://github.com/user-attachments/assets/774a46d5-27a0-490c-b7d0-e65fcbbfa358">
  <img alt="Shows a black Browser Use Logo in light color mode and a white one in dark color mode." src="https://github.com/user-attachments/assets/2ccdb752-22fb-41c7-8948-857fc1ad7e24"  width="full">
</picture>

---

<div align="center">
<a href="#demos"><img src="https://media.browser-use.tools/badges/demos" alt="Demos"></a>
<img width="16" height="1" alt="">
<a href="https://docs.browser-use.com"><img src="https://media.browser-use.tools/badges/docs" alt="Docs"></a>
<img width="16" height="1" alt="">
<a href="https://browser-use.com/posts"><img src="https://media.browser-use.tools/badges/blog" alt="Blog"></a>
<img width="16" height="1" alt="">
<a href="https://browsermerch.com"><img src="https://media.browser-use.tools/badges/merch" alt="Merch"></a>
<img width="100" height="1" alt="">
<a href="https://github.com/browser-use/browser-use"><img src="https://media.browser-use.tools/badges/github" alt="Github Stars"></a>
<img width="4" height="1" alt="">
<a href="https://x.com/intent/user?screen_name=browser_use"><img src="https://media.browser-use.tools/badges/twitter" alt="Twitter"></a>
<img width="4 height="1" alt="">
<a href="https://link.browser-use.com/discord"><img src="https://media.browser-use.tools/badges/discord" alt="Discord"></a>
<img width="4" height="1" alt="">
<a href="https://cloud.browser-use.com?utm_source=github&utm_medium=benchmark_readme"><img src="https://media.browser-use.tools/badges/cloud" height="48" alt="Browser-Use Cloud"></a>
</div>

<h1 align="center">Open-Source Benchmarks</h1>

<br/>

---

<br/>

## BU Bench V2

**200 web tasks scored against weighted findings rubrics**

| Evaluation set | Tasks | File |
| --- | ---: | --- |
| Full BU Bench V2 | 200 | [BU_Bench_V2.enc](BU_Bench_V2.enc) |
| BU Bench V2 — 55-task subset | 55 | [BU_Bench_V2_55.json](BU_Bench_V2_55.json) (task IDs only) |

The 55-task subset selects public IDs `bu2-001` through `bu2-055` from the full release. It is the public overlap with the original 60-task benchmark; five original tasks were omitted and the remaining tasks were renumbered. Use the public IDs in the subset file, not the first 55 historical IDs.

Tasks, rubrics and weights live once, in the encrypted 200-task release. `BU_Bench_V2_55.json` pins that file's SHA-256 and lists the subset IDs; it does not contain a second dataset. Label scores with the set used: **200 tasks**, **55-task subset**, or **legacy 60 tasks**.

**Rubric revision candidate:** [corrections and review instructions](RUBRIC_REVISION.md).
This branch updates seven task contracts; it has not been regraded and does not
update the chart above. The prior encrypted snapshot and exact change hashes are
preserved. Pin a revision when comparing results.

The tasks are encrypted to keep their text out of web crawlers and model training data. Please do not publish decrypted tasks or rubrics in plaintext or use them for model training.

<details>
<summary>Select the 55-task subset</summary>

After decrypting `BU_Bench_V2.enc` into a Python object named `benchmark`:

```python
import hashlib
import json
from pathlib import Path

subset = json.loads(Path("BU_Bench_V2_55.json").read_text())
assert hashlib.sha256(Path(subset["source"]).read_bytes()).hexdigest() == subset["source_sha256"]
by_id = {task["id"]: task for task in benchmark["tasks"]}
tasks = [by_id[task_id] for task_id in subset["task_ids"]]
assert len(tasks) == subset["task_count"]
```

This selects evaluation records, including the judge's rubric and weights. Pass only the task instructions to the evaluated agent; keep the rubric and weights for the judge.

</details>

### Historical results — 60 tasks

<img alt="Legacy 60-task BU Bench V2 results - Mean rubric score by model and cost per task, including GPT-6 Astra" src="official_plots/bu_bench_v2_astra.jpg" width="100%">

These results use the earlier 60-task set, not the full 200-task release or the 55-task subset. Compare model scores only on the same task set.

### Running BU Bench V2 (default)

```bash
uv sync --frozen
cp .env.example .env
# Set BROWSER_USE_API_KEY for the agent and cloud browser.
# Set OPENAI_API_KEY for the findings judge.
uv run python run_eval.py --tasks 5
# Omit --tasks to run all 200 tasks.
```

The runner decrypts V2 in memory and uses the [findings judge](findings_judge.py)
with **gpt-5.6-luna, xhigh reasoning**. Each task's whole rubric is judged in one
call. Code applies its unequal item weights; `score` is continuous from 0 to 1.
The canary and reward-hacking policy can zero the whole task. Reward hacking
requires concrete evidence of fabrication or manipulation; an ordinary task error
or wrong target alone is scored under its rubric items. Both `raw_score`
(before that penalty) and final `score`, findings, and flags are saved.

Use `--model gpt-5.6-luna --agent-reasoning xhigh` for a Luna executor.
`--task-ids bu2-171 bu2-185` selects exact cases; `--max-steps`,
`--task-timeout` (seconds), and `--concurrency` set explicit execution limits.
Use `--judge-model` and `--judge-reasoning` to override the OpenAI judge settings,
or `--browser local_headless` to use local Chromium (install it first with
`uv run browser-use install`). The selected model must support images and
structured output. `OPENAI_API_KEY` must have access to it. OpenAI-compatible gateways can be selected with `OPENAI_BASE_URL`; the run records the endpoint host. Overrides are recorded
with every run; changing the judge affects comparability.

Results go to ignored `results/`; detailed evidence and judge configuration go
to ignored `run_data/`. The headline metric is **mean weighted score**, not the
fraction of perfect tasks. The adapter version is recorded independently of
the dataset revision. Missing/duplicate findings are judge errors. Clipped agent
evidence is recorded in `evidence_clipped_sections` and shown as a warning; it
does not automatically suppress the judge's score or exclude the task from the mean.
The judge assesses each item using the final output and all available evidence.
Every `not_assessable` finding names its reason:
`missing_evidence` means the judge cannot settle an item because required evidence
the run produced is unavailable; it names the missing evidence and withholds the
task score. A clipped task instruction or rubric also withholds the score because
the judging question is incomplete. `absent_scope` is a rubric-defined missing deliverable,
empty scope, or inapplicable branch and retains the historical zero item credit.
Missing agent work is not a collector failure. Diagnostic credit is retained
separately for incomplete evaluations. Any unscored task, including missing
required evidence or clipped instructions/rubrics, makes the full-set mean
unavailable and the run exit nonzero. Judge/API/schema failures
are unscored, preserve the
trace, make the overall mean unavailable, and exit nonzero. The separate mean
over scored tasks is explicitly labeled. Agent execution failures still count
as zero; timed-out runs are judged on their partial evidence.

By default this runner uses Browser Use 0.11.5 / `bu-2-0`, a 30-minute limit and 100 steps.
It supplies tool results, final output, text from the agent's managed files, and
up to 50 unique screenshots sampled across the run within a byte budget. Downloaded binary files and
files created outside the agent's managed filesystem are not extracted. This
is a runnable public harness, not a reproduction of the published 60-task
BrowserCode setup: the executor, task cohort, limits, and image selection differ.
Weights and reward-hacking penalties are unchanged. Adapter 2.1.1 removes the
automatic score exclusion for clipped agent evidence; record the adapter version
when comparing results with earlier runs.

Local traces contain decrypted tasks, rubrics, screenshots, and deliverables.
Do not publish or commit them. Offline runner tests: `uv run python -m unittest discover -s tests`.

<br/>

---

<br/>

## Stealth Bench V1

**71 tasks for evaluating browser stealth across anti-bot protections**

<picture>
  <source media="(prefers-color-scheme: light)" srcset="stealth_bench/official_plots/accuracy_by_browser_light.png">
  <source media="(prefers-color-scheme: dark)" srcset="stealth_bench/official_plots/accuracy_by_browser_dark.png">
  <img alt="Stealth Bench - Accuracy by Browser" src="stealth_bench/official_plots/accuracy_by_browser_light.png" width="100%">
</picture>

<picture>
  <source media="(prefers-color-scheme: light)" srcset="stealth_bench/official_plots/category_heatmap_light.png">
  <source media="(prefers-color-scheme: dark)" srcset="stealth_bench/official_plots/category_heatmap_dark.png">
  <img alt="Stealth Bench - Category Heatmap" src="stealth_bench/official_plots/category_heatmap_light.png" width="100%">
</picture>

**Tasks:** [Stealth Bench V1 task set](Stealth_Bench_V1.enc) (80 tasks, encrypted; the plots use a 71-task subset).

The tasks are encrypted to keep their text out of web crawlers and model training data.

Read more in our [blog post](https://browser-use.com/posts/stealth-benchmark).

### Running the Stealth Benchmark

**1. Install dependencies**
```bash
pip install uv
uv sync
```

**2. Set up your `.env`** (see [`.env.example`](.env.example))
```bash
cp .env.example .env
# Fill in GOOGLE_API_KEY (required for the judge LLM)
# Fill in the API key for the browser provider you want to test
```

**3. Run the evaluation** (decrypts in memory; uses the legacy binary judge)
```bash
uv run python run_eval.py --benchmark Stealth_Bench_V1 --browser <provider>
```

Available providers: `browser-use-cloud`, `anchor`, `browserbase`, `browserless`, `hyperbrowser`, `onkernel`, `steel`, `local_headful`, `local_headless`

**Results and official data:** [`stealth_bench/`](stealth_bench/)

<br/>

---

<br/>

## BU Bench V1

**100 hand-selected tasks for evaluating browser automation agents**

### Comparing Agent Frameworks

<picture>
  <source media="(prefers-color-scheme: light)" srcset="official_plots/best_of_frameworks_public_light.png">
  <source media="(prefers-color-scheme: dark)" srcset="official_plots/best_of_frameworks_public_dark.png">
  <img alt="BU Bench V1 Comparing Agent Frameworks" src="official_plots/best_of_frameworks_public_light.png" width="100%">
</picture>

### Comparing Models for Browser Use

<picture>
  <source media="(prefers-color-scheme: light)" srcset="official_plots/browser_use_framework_by_model_light.png">
  <source media="(prefers-color-scheme: dark)" srcset="official_plots/browser_use_framework_by_model_dark.png">
  <img alt="BU Bench V1 Comparing Models for Browser Use" src="official_plots/browser_use_framework_by_model_light.png" width="100%">
</picture>

### Comparing Models for BrowserCode

<picture>
  <source media="(prefers-color-scheme: light)" srcset="official_plots/browser_harness_by_model_light.png">
  <source media="(prefers-color-scheme: dark)" srcset="official_plots/browser_harness_by_model_dark.png">
  <img alt="BU Bench V1 Comparing Models for BrowserCode" src="official_plots/browser_harness_by_model_light.png" width="100%">
</picture>

**Tasks:** [BU Bench V1 task set](BU_Bench_V1.enc) (100 tasks, encrypted; shared by all three comparisons above).

The tasks are encrypted to keep their text out of web crawlers and model training data.

### Running BU Bench V1 (legacy)

**1. Install dependencies**
```bash
pip install uv
uv sync
```

**2. Set up your `.env`** (see [`.env.example`](.env.example))
```bash
cp .env.example .env
# Fill in BROWSER_USE_API_KEY (required for ChatBrowserUse and cloud browsers)
# Fill in GOOGLE_API_KEY (required for judge LLM)
```

**3. Run evaluation**
```bash
uv run python run_eval.py --benchmark BU_Bench_V1
```

Results are saved to `results/` and detailed traces to `run_data/`.

### Re-verifying Framework Results

Use `run_framework_eval.py` to rerun BU_Bench_V1 through a framework adapter.
It decrypts `BU_Bench_V1.enc` in memory and writes local outputs to ignored
`results/` and `run_data/`.
The framework adapters and `run_batch.py` retain the legacy V1 binary judge;
use `run_eval.py` for V2 findings judging.

```bash
uv run python run_framework_eval.py --list-frameworks
uv run python run_framework_eval.py --framework browser-use --browser browser-use-cloud --model bu-2-0
```

See the comment at the top of `run_framework_eval.py` for framework-specific
setup, options, and examples.

Important: `run_data/` traces include decrypted task text, ground truth, model
outputs, and screenshots. They are gitignored for local verification only. Do
not publish or commit them.

### Swapping Models

Edit `run_eval.py` to change the model:

```python
# Default: ChatBrowserUse (recommended)
agent = Agent(task=task["confirmed_task"], llm=ChatBrowserUse(), browser=browser)

# OpenAI
agent = Agent(task=task["confirmed_task"], llm=ChatOpenAI(model="gpt-4.1"), browser=browser)

# Anthropic
agent = Agent(task=task["confirmed_task"], llm=ChatAnthropic(model="claude-sonnet-4-5"), browser=browser)

# Google
agent = Agent(task=task["confirmed_task"], llm=ChatGoogle(model="gemini-2.5-flash"), browser=browser)
```

### About BU Bench

100 tasks drawn from established benchmarks and custom challenges:

| Source | Tasks | Description |
|--------|-------|-------------|
| Custom | 20 | Page interaction challenges |
| WebBench | 20 | Web browsing tasks |
| Mind2Web 2 | 20 | Multi-step web navigation |
| GAIA | 20 | General AI assistant tasks (web-based) |
| BrowseComp | 20 | Browser comprehension tasks |

WebBench, Mind2Web 2, and BrowseComp are released under the MIT license. GAIA has no explicit license; to comply with its data policies, we only include tasks from the "fully public" validation split, and all tasks are base64 encoded and encrypted to prevent data contamination.

Tasks were hand-selected for difficulty and verified to be achievable. Each task has been validated to confirm it can be completed successfully.

Important: The task set is encrypted and base64 encoded to keep its text out of web crawlers and model training data. Please do not publish the tasks in plaintext or use them in model training data.

#### Task Format

| Field | Description |
|-------|-------------|
| `task_id` | Unique identifier |
| `confirmed_task` | Task instruction |
| `category` | Source benchmark |
| `answer` | Ground truth (if applicable) |

<br/>

---

<br/>

## Online-Mind2Web

The [Online-Mind2Web](https://github.com/OSU-NLP-Group/Online-Mind2Web) benchmark is evaluated across agent frameworks.

<picture>
  <source media="(prefers-color-scheme: light)" srcset="online-mind2web/official_plots/success_rate_light.png">
  <source media="(prefers-color-scheme: dark)" srcset="online-mind2web/official_plots/success_rate_dark.png">
  <img alt="Online-Mind2Web Success Rate" src="online-mind2web/official_plots/success_rate_light.png" width="100%">
</picture>

**Tasks:** [Official Online-Mind2Web dataset](https://huggingface.co/datasets/osunlp/Online-Mind2Web) (300 tasks; Hugging Face access required).

<br/>

---

<br/>

## Attributions

### WebBench
MIT License | https://webbench.ai/
```bibtex
@misc{webbench2025,
  title = {WebBench: AI Web Browsing Agent Benchmark},
  author = {{Halluminate and Skyvern}},
  year = {2025},
  note = {\url{https://webbench.ai/}},
}
```

### Mind2Web 2 (OMI2W-2)
MIT License | https://openreview.net/forum?id=AUaW6DS9si
```bibtex
@inproceedings{
    gou2025mind2web2,
    title={Mind2Web 2: Evaluating Agentic Search with Agent-as-a-Judge},
    author={Boyu Gou and Zanming Huang and Yuting Ning and Yu Gu and Michael Lin and Botao Yu and Andrei Kopanev and Weijian Qi and Yiheng Shu and Jiaman Wu and Chan Hee Song and Bernal Jimenez Gutierrez and Yifei Li and Zeyi Liao and Hanane Nour Moussa and TIANSHU ZHANG and Jian Xie and Tianci Xue and Shijie Chen and Boyuan Zheng and Kai Zhang and Zhaowei Cai and Viktor Rozgic and Morteza Ziyadi and Huan Sun and Yu Su},
    booktitle={The Thirty-ninth Annual Conference on Neural Information Processing Systems Datasets and Benchmarks Track},
    year={2025},
    url={https://openreview.net/forum?id=AUaW6DS9si}
}
```

### BrowseComp
MIT License | https://cdn.openai.com/pdf/5e10f4ab-d6f7-442e-9508-59515c65e35d/browsecomp.pdf
```bibtex
@techreport{wei2025browsecomp,
  author = {Jason Wei and Zhiqing Sun and Spencer Papay and Scott McKinney and Jeffrey Han and Isa Fulford and Hyung Won Chung and Alex Tachard Passos and William Fedus and Amelia Glaese},
  title = {BrowseComp: A Simple Yet Challenging Benchmark for Browsing Agents},
  institution = {OpenAI},
  year = {2025},
  url = {https://cdn.openai.com/pdf/5e10f4ab-d6f7-442e-9508-59515c65e35d/browsecomp.pdf},
}
```

### GAIA
No license (public validation split only) | https://huggingface.co/datasets/gaia-benchmark/GAIA
```bibtex
@misc{mialon2023gaia,
  title={GAIA: a benchmark for General AI Assistants},
  author={Gregoire Mialon and Clementine Fourrier and Craig Swift and Thomas Wolf and Yann LeCun and Thomas Scialom},
  year={2023},
  eprint={2311.12983},
  archivePrefix={arXiv},
  primaryClass={cs.CL}
}
```
