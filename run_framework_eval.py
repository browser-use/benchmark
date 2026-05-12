"""Run BU_Bench_V1 through any registered framework adapter.

The public verifier decrypts BU_Bench_V1.enc in memory, runs the selected
adapter, judges each trace, writes summaries under ignored results/, and writes
task-level traces under ignored run_data/. Do not publish run_data/ artifacts:
they include decrypted task text, ground truth, model outputs, and screenshots.

Examples:
    uv run python run_framework_eval.py --list-frameworks
    uv run python run_framework_eval.py --framework browser-use --browser browser-use-cloud --model bu-2-0
    uv run python run_framework_eval.py --framework browser-use-cloud-api-v3 --model bu-ultra
    uv run python run_framework_eval.py --framework bcode-v012 --framework-ref v0.1.2 --model gpt-5 --tasks 5

Useful options:
    --framework <framework>
    --framework-ref <version-or-commit>
    --browser <browser-or-integrated>
    --model <model>
    --tasks 10
    --parallel 3
    --params key=value,key=value

Adapter prerequisites:
    browser-use:
        Install the desired browser-use package/ref into the uv environment.
    browser-use-cloud-api-v2, browser-use-cloud-api-v3:
        Set BROWSER_USE_API_KEY; no browser provider is needed.
    bcode, bcode-v012:
        Install bcode at $HOME/.bcode/bin/bcode; set BROWSER_USE_API_KEY and
        model provider keys.
    browserbase-agent:
        Run `npm install --prefix frameworks/browserbase_agent`; set
        BROWSERBASE_API_KEY and BROWSERBASE_PROJECT_ID.
    claude-code-harness, codex-harness, pi-harness:
        Install the agent CLI, clone browser-use/browser-harness at the desired
        ref to /tmp/browser-harness, and install it into the uv environment.
    claude-code-harness-js:
        Install Claude Code, clone/install browser-use/browser-harness-js, and
        put browser-harness-js on PATH.
    claude-code-harness-ab:
        Install Claude Code and agent-browser, then install its browser assets.
    claude-code-harness-bu-cli:
        Install Claude Code and browser-use CLI at the desired ref.
    pibt:
        Install pi, clone/install browser-use/pi-agent-extensions to
        /tmp/pi-agent-extensions, and install its JS dependencies.
    but:
        Install browser-use/browser-use-terminal to /tmp/but with
        `uv sync --project /tmp/but`.
    but-rust:
        Build /tmp/but-rust/target/release/browser-use-terminal and provide
        browser-harness to the worker.
"""

import argparse
import asyncio
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from frameworks import FRAMEWORKS, framework_to_module, interleave, load_tasks

ROOT_DIR = Path(__file__).resolve().parent


def _safe_part(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-") or "unknown"


def _selected_indices(total: int, args: argparse.Namespace) -> list[int]:
    if args.task_indices:
        indices = [int(x.strip()) for x in args.task_indices.split(",") if x.strip()]
    else:
        count = args.tasks if args.tasks is not None else total - args.task_start
        indices = list(range(args.task_start, min(args.task_start + count, total)))
    bad = [i for i in indices if i < 0 or i >= total]
    if bad:
        raise SystemExit(f"Task index out of range: {bad[:5]} for benchmark size {total}")
    return indices


async def _run_one(
    *,
    task_index: int,
    framework: str,
    model: str,
    browser: str,
    benchmark: str,
    params: str,
    run_data_dir: Path,
    task_results_dir: Path,
    task_timeout: int | None,
) -> dict:
    module_name = framework_to_module(framework)
    runner = ROOT_DIR / "frameworks" / module_name / "run_task.py"
    if not runner.exists():
        return {
            "task_index": task_index,
            "task_id": None,
            "score": 0,
            "steps": 0,
            "duration": 0,
            "cost": 0,
            "error": f"Missing framework runner: {runner}",
        }

    result_file = task_results_dir / f"task_{task_index}.json"
    env = os.environ.copy()
    env.update(
        {
            "MODEL": model,
            "TASK_INDEX": str(task_index),
            "EVAL_ID": "local",
            "FRAMEWORK": framework,
            "BROWSER": browser,
            "BENCHMARK": benchmark,
            "PARAMS": params,
            "LOCAL_RESULT_FILE": str(result_file),
            "RUN_DATA_DIR": str(run_data_dir),
            "BROWSER_USE_SETUP_LOGGING": "false",
        }
    )
    if task_timeout is not None:
        env["TASK_TIMEOUT"] = str(task_timeout)
    if os.environ.get("NO_INTERLEAVE") == "1":
        env["NO_INTERLEAVE"] = "1"

    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        str(runner),
        cwd=str(ROOT_DIR),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if stdout:
        print(stdout.decode("utf-8", errors="replace"), end="")
    if stderr:
        print(stderr.decode("utf-8", errors="replace"), end="", file=sys.stderr)

    if result_file.exists():
        return json.loads(result_file.read_text(encoding="utf-8"))

    return {
        "task_index": task_index,
        "task_id": None,
        "score": 0,
        "steps": 0,
        "duration": 0,
        "cost": 0,
        "error": f"Runner exited {proc.returncode} without writing {result_file}",
    }


async def _run_all(args: argparse.Namespace) -> list[dict]:
    tasks = load_tasks(args.benchmark)
    if not args.no_interleave:
        tasks = interleave(tasks)
    indices = _selected_indices(len(tasks), args)

    framework_info = FRAMEWORKS[args.framework]
    browser = args.browser or framework_info.browsers[0]
    if browser not in framework_info.browsers:
        valid = ", ".join(framework_info.browsers)
        raise SystemExit(
            f"Browser {browser!r} is not supported by {args.framework!r}. "
            f"Valid browsers: {valid}"
        )

    run_start = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_key = (
        f"{args.benchmark}_framework_{_safe_part(args.framework)}"
        f"_browser_{_safe_part(browser)}"
        f"_model_{_safe_part(args.model)}"
    )
    run_data_dir = ROOT_DIR / "run_data" / f"{run_key}_start_at_{run_start}"
    task_results_dir = run_data_dir / "_task_results"
    results_file = ROOT_DIR / "results" / f"{run_key}.json"

    print(
        f"Running {len(indices)} task(s): benchmark={args.benchmark} "
        f"framework={args.framework} browser={browser} model={args.model}"
    )
    if framework_info.repo:
        print(f"Framework repo: {framework_info.repo} ref={args.framework_ref}")

    if args.parallel < 1:
        raise SystemExit("--parallel must be >= 1")
    semaphore = asyncio.Semaphore(args.parallel)

    async def guarded(i: int) -> dict:
        async with semaphore:
            return await _run_one(
                task_index=i,
                framework=args.framework,
                model=args.model,
                browser=browser,
                benchmark=args.benchmark,
                params=args.params,
                run_data_dir=run_data_dir,
                task_results_dir=task_results_dir,
                task_timeout=args.task_timeout,
            )

    results = await asyncio.gather(*(guarded(i) for i in indices))

    run_entry = {
        "run_start": run_start,
        "benchmark": args.benchmark,
        "framework": args.framework,
        "framework_ref": args.framework_ref,
        "browser": browser,
        "model": args.model,
        "params": args.params,
        "task_indices": indices,
        "tasks_completed": len(results),
        "tasks_successful": sum(1 for r in results if r.get("score") == 1),
        "total_steps": sum(int(r.get("steps", 0) or 0) for r in results),
        "total_duration": sum(float(r.get("duration", 0) or 0) for r in results),
        "total_cost": sum(float(r.get("cost", 0) or 0) for r in results),
        "task_results": [
            {
                "task_id": r.get("task_id"),
                "task_index": r.get("task_index"),
                "score": r.get("score"),
                "steps": r.get("steps", 0),
                "duration": r.get("duration", 0),
                "cost": r.get("cost", 0),
                **({"error": r["error"]} if r.get("error") else {}),
            }
            for r in results
        ],
    }

    results_file.parent.mkdir(parents=True, exist_ok=True)
    previous = json.loads(results_file.read_text()) if results_file.exists() else []
    previous.append(run_entry)
    results_file.write_text(json.dumps(previous, indent=2), encoding="utf-8")

    print(
        f"Run complete: {run_entry['tasks_successful']}/{run_entry['tasks_completed']} "
        f"successful, {run_entry['total_steps']} steps, "
        f"{run_entry['total_duration']:.1f}s, ${run_entry['total_cost']:.2f}"
    )
    print(f"Summary: {results_file}")
    print(f"Trace artifacts: {run_data_dir}")
    return results


def _print_frameworks() -> None:
    for name, info in sorted(FRAMEWORKS.items()):
        browsers = ", ".join(info.browsers)
        repo = f" repo={info.repo}" if info.repo else ""
        notes = f" ({info.notes})" if info.notes else ""
        print(f"{name}: browsers=[{browsers}]{repo}{notes}")


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Run public BU_Bench_V1 reverification")
    parser.add_argument("--benchmark", default="BU_Bench_V1")
    parser.add_argument("--framework", choices=sorted(FRAMEWORKS), default="browser-use")
    parser.add_argument("--framework-ref", default="installed")
    parser.add_argument("--browser", default=None)
    parser.add_argument("--model", default="bu-2-0")
    parser.add_argument("--params", default="")
    parser.add_argument("--tasks", type=int, default=None)
    parser.add_argument("--task-start", type=int, default=0)
    parser.add_argument("--task-indices", default="")
    parser.add_argument("--parallel", type=int, default=1)
    parser.add_argument("--task-timeout", type=int, default=None)
    parser.add_argument(
        "--no-interleave",
        action="store_true",
        help="Use raw encrypted task order instead of the distributed runner order.",
    )
    parser.add_argument("--list-frameworks", action="store_true")
    args = parser.parse_args()

    if args.list_frameworks:
        _print_frameworks()
        return

    if args.no_interleave:
        os.environ["NO_INTERLEAVE"] = "1"

    asyncio.run(_run_all(args))


if __name__ == "__main__":
    main()
