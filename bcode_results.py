"""Optional Laminar reporting and public-safe GitHub Actions artifacts."""

import argparse
import hashlib
import json
import os
from pathlib import Path
from uuid import UUID

PUBLIC_FIELDS = (
    "task_id",
    "score",
    "raw_score",
    "steps",
    "duration",
    "cost",
    "status",
    "rh_zeroed",
    "reporting_error",
)


def public_result(result):
    # An allowlist excludes findings, errors and tracebacks that can quote rubrics.
    return {key: result[key] for key in PUBLIC_FIELDS if key in result}


def init_laminar(config):
    if not os.environ.get("LMNR_PROJECT_API_KEY"):
        return None
    from lmnr import Laminar, LaminarClient

    Laminar.initialize(
        project_api_key=os.environ["LMNR_PROJECT_API_KEY"], instruments=set()
    )
    if os.environ.get("BENCHMARK_LAMINAR_EVAL_ID"):
        return os.environ["BENCHMARK_LAMINAR_EVAL_ID"]
    evaluation = LaminarClient().evals.init(
        name=f"public-benchmark/{config.get('model', 'bcode')} {config.get('browser', '')} #{os.getenv('GITHUB_RUN_ID', 'local')}",
        group_name="public-BU-Bench-V2-200",
        metadata=config,
    )
    print(
        f"Laminar: https://www.lmnr.ai/project/{evaluation.projectId}/evaluations/{evaluation.id}",
        flush=True,
    )
    return str(evaluation.id)


def publish_laminar(evaluation_id, task, result, task_dir, config):
    from lmnr import Laminar, LaminarClient

    client = LaminarClient()
    metadata = {
        "task_id": task["task_id"],
        "category": "public-BU-Bench-V2-200",
        "harness": "bcode-public",
        "model": config["executor_config"]["model"],
        "reasoning_effort": config["executor_config"]["reasoning_effort"],
        "browser": config["browser"]["provider"],
        "dataset_sha256": config["dataset_sha256"],
        "judge_model": config["judge"]["model"],
        "github_run_id": os.getenv("GITHUB_RUN_ID", ""),
    }
    datapoint_id = client.evals.create_datapoint(
        eval_id=UUID(evaluation_id),
        data={
            "task_id": task["task_id"],
            "task_sha256": hashlib.sha256(task["confirmed_task"].encode()).hexdigest(),
        },
        metadata=metadata,
        index=int(task["task_id"].split("-")[-1]) - 1,
    )
    trace_path = task_dir / "trace.json"
    trace = json.loads(trace_path.read_text()) if trace_path.exists() else {}
    # Text/tool evidence goes to Laminar; full images stay in task artifacts.
    trace.pop("screenshots_b64", None)
    trace.pop("output_images", None)
    with Laminar.start_as_current_span(
        "evaluation.task",
        span_type="EVALUATION",
        input=metadata,
    ):
        trace_id = Laminar.get_trace_id()
        Laminar.set_span_output({"result": result, "trace": trace})
    client.evals.update_datapoint(
        eval_id=UUID(evaluation_id),
        datapoint_id=datapoint_id,
        trace_id=trace_id,
        executor_output=public_result(result),
        scores={"findings": result["score"]} if result["score"] is not None else {},
    )
    Laminar.flush()


def prepare(task_ids, limit):
    from evaluation import load_tasks
    from run_eval import select_tasks

    tasks = select_tasks(load_tasks(), task_ids.split() or None, limit)
    config = {
        "model": os.getenv("EVAL_MODEL", "openai/gpt-6-luna"),
        "browser": os.getenv("EVAL_BROWSER", "browser-use-cloud"),
        "github_run_id": os.getenv("GITHUB_RUN_ID", ""),
        "task_ids": [task["task_id"] for task in tasks],
        "dataset_sha256": hashlib.sha256(
            Path("BU_Bench_V2.enc").read_bytes()
        ).hexdigest(),
    }
    selected_ids = [task["task_id"] for task in tasks]
    evaluation_id = init_laminar(config) or ""
    print("Selected task IDs: " + json.dumps(selected_ids), flush=True)
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as output:
        output.write("task_ids=" + json.dumps(selected_ids) + "\n")
        output.write("evaluation_id=" + evaluation_id + "\n")


def export_public(root):
    for path in root.glob("*/config.json"):
        config = json.loads(path.read_text())
        safe = {
            key: config[key]
            for key in (
                "benchmark",
                "dataset_sha256",
                "runner_source_sha256",
                "executor_source_sha256",
                "judge",
                "task_ids",
                "task_timeout_seconds",
                "parallel",
                "fetch_use",
                "browser",
            )
        }
        safe["executor_config"] = {
            key: config["executor_config"][key]
            for key in (
                "version",
                "binary_sha256",
                "catalog_sha256",
                "model",
                "reasoning_effort",
            )
        }
        (path.parent / "public-config.json").write_text(json.dumps(safe, indent=2))


def aggregate(root, expected_ids):
    from bcode_eval import summarize_results

    results = [
        json.loads(path.read_text())
        for path in sorted(root.rglob("public-result.json"))
    ]
    ids = [result["task_id"] for result in results]
    if set(ids) != set(expected_ids) or len(ids) != len(expected_ids):
        raise ValueError(
            f"Expected {len(expected_ids)} unique tasks; found {len(ids)}; missing {sorted(set(expected_ids) - set(ids))}"
        )
    summary = summarize_results(results)
    Path("aggregate-results.json").write_text(
        json.dumps({**summary, "task_results": results}, indent=2)
    )
    display = {
        key: summary[key]
        for key in ("tasks_completed", "tasks_scored", "tasks_unscored", "mean_score")
    }
    print(json.dumps(display, indent=2))
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a") as stream:
            stream.write(
                "### BU Bench V2 results\n\n"
                + "\n".join(f"- {key}: {value}" for key, value in display.items())
                + "\n"
            )
    if summary["tasks_unscored"] or any(r.get("reporting_error") for r in results):
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["prepare", "export", "aggregate"])
    parser.add_argument("--task-ids", default="")
    parser.add_argument("--tasks", type=int, default=200)
    parser.add_argument("--root", type=Path, default=Path("run_data"))
    args = parser.parse_args()
    if not 1 <= args.tasks <= 200:
        parser.error("--tasks must be between 1 and 200")
    if args.command == "prepare":
        prepare(args.task_ids, args.tasks)
    elif args.command == "export":
        export_public(args.root)
    else:
        aggregate(args.root, json.loads(os.environ["EXPECTED_TASK_IDS"]))
