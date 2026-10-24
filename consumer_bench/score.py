import json
from collections import Counter
from pathlib import Path

OUTCOMES = {"success", "failure", "policy_hold", "unknown", "unstarted", "pending"}


def summarize(tasks, outcomes):
    task_ids = [task["id"] for task in tasks]
    if len(task_ids) != len(set(task_ids)):
        raise ValueError("Dataset contains duplicate task IDs")
    included = set(task_ids)
    rows = {}
    for row in outcomes:
        task_id = row["task_id"]
        if task_id in rows:
            raise ValueError(f"Duplicate result for {task_id}")
        if row["outcome"] not in OUTCOMES:
            raise ValueError(f"Unknown outcome for {task_id}")
        rows[task_id] = row
    result = {}
    for endpoint in ("all", "cart", "public_information"):
        selected = [task for task in tasks if endpoint == "all" or task["endpoint"] == endpoint]
        counts = Counter(rows.get(task["id"], {"outcome": "unknown"})["outcome"] for task in selected)
        total = len(selected)
        result[endpoint] = {
            "planned": total,
            "successes": counts["success"],
            "failures": counts["failure"],
            "policyHolds": counts["policy_hold"],
            "unknown": counts["unknown"],
            "unstarted": counts["unstarted"],
            "pending": counts["pending"],
            "successRate": counts["success"] / total if total else None,
            "rowsPresent": sum(task["id"] in rows for task in selected),
        }
    result["excludedResults"] = len(set(rows) - included)
    result["allTasksTerminal"] = all(rows.get(task_id, {}).get("outcome") in {"success", "failure", "policy_hold", "unknown"} for task_id in task_ids)
    return result


def main():
    root = Path(__file__).resolve().parent
    data = json.loads((root / "results.json").read_text())
    dataset = json.loads((root / "dataset-v10.json").read_text())
    for run in data["runs"]:
        if run.get("outcomes") is not None:
            print(run["label"], json.dumps(summarize(dataset["tasks"], run["outcomes"])))


if __name__ == "__main__":
    main()
