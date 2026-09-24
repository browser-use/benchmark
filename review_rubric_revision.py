"""Validate the encrypted revision; optionally write private before/after diffs."""

import argparse
import base64
import difflib
import hashlib
import json
import re
from pathlib import Path

from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parent


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def decrypt(path: Path, key_name: str) -> dict:
    key = base64.urlsafe_b64encode(hashlib.sha256(key_name.encode()).digest())
    return json.loads(Fernet(key).decrypt(base64.b64decode(path.read_bytes())))


def index_tasks(tasks: list[dict], label: str) -> dict[str, dict]:
    """Index tasks only after validating the raw population and IDs."""
    if len(tasks) != 200:
        raise ValueError(f"{label} task population changed")
    ids = [task.get("id") for task in tasks]
    if any(not task_id for task_id in ids):
        raise ValueError(f"{label} task has no ID")
    if len(ids) != len(set(ids)):
        raise ValueError(f"{label} task IDs are not unique")
    return {task["id"]: task for task in tasks}


def validate_revision(root: Path = ROOT) -> tuple[dict, dict, dict, dict]:
    manifest = json.loads((root / "rubric_revision.json").read_text())
    base_path = root / "snapshots/BU_Bench_V2_2026-08-25.enc"
    candidate_path = root / "BU_Bench_V2.enc"
    for path, field in [
        (base_path, "base_encrypted_sha256"),
        (candidate_path, "candidate_encrypted_sha256"),
    ]:
        if hashlib.sha256(path.read_bytes()).hexdigest() != manifest[field]:
            raise ValueError(f"Encrypted artifact hash mismatch: {path.name}")
    original = decrypt(base_path, "BU_Bench_V2")
    candidate = decrypt(candidate_path, "BU_Bench_V2")
    cases = decrypt(root / "BU_Bench_V2_review_cases.enc", "BU_Bench_V2_review_cases")
    before = index_tasks(original["tasks"], "Base")
    after = index_tasks(candidate["tasks"], "Candidate")
    changes = {change["task_id"]: change for change in manifest["changes"]}
    if set(before) != set(after):
        raise ValueError("Task population or IDs changed")
    if (
        candidate["revision"] != manifest["revision"]
        or cases["revision"] != candidate["revision"]
    ):
        raise ValueError("Revision mismatch")
    for task_id, task in after.items():
        old = before[task_id]
        if task_id not in changes:
            if task != old:
                raise ValueError(f"Unlisted task change: {task_id}")
            continue
        change = changes[task_id]
        missing = object()
        changed_fields = {
            key
            for key in set(old) | set(task)
            if old.get(key, missing) != task.get(key, missing)
        }
        if not changed_fields <= {"task", "rubric", "task_sha", "rubric_sha", "revision"}:
            raise ValueError(f"Unapproved task field change: {task_id}")
        if task["weights"] != old["weights"] or sum(task["weights"].values()) != 100:
            raise ValueError(f"Weight change: {task_id}")
        if task["canary"] != old["canary"] or task["canary"] in task["task"]:
            raise ValueError(f"Canary changed or exposed: {task_id}")
        item_ids = set(
            re.findall(r"^(A\d+_[a-zA-Z0-9_]+)\b", task["rubric"], re.MULTILINE)
        )
        if item_ids != set(task["weights"]):
            raise ValueError(f"Item/weight mismatch: {task_id}")
        for field in ("task", "rubric"):
            if digest(old[field]) != change[f"before_{field}_sha256"]:
                raise ValueError(f"Base content hash mismatch: {task_id}/{field}")
            if digest(task[field]) != change[f"after_{field}_sha256"] or task[
                field + "_sha"
            ] != digest(task[field]):
                raise ValueError(f"Candidate content hash mismatch: {task_id}/{field}")
        if change["task_changed"] != (old["task"] != task["task"]):
            raise ValueError(f"Task-change declaration mismatch: {task_id}")
        if not any(case["task_id"] == task_id for case in cases["cases"]):
            raise ValueError(f"No review cases: {task_id}")
    for case in cases["cases"]:
        if case["task_id"] not in changes or not set(case["expected"]["items"]) <= set(
            after[case["task_id"]]["weights"]
        ):
            raise ValueError("Review case references an unknown task/item")
    return manifest, before, after, cases


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-private-diffs", action="store_true")
    args = parser.parse_args()
    manifest, before, after, cases = validate_revision()
    print(
        f"Validated {len(manifest['changes'])} revised tasks; other tasks and all weights unchanged."
    )
    print(
        f"{len(cases['cases'])} synthetic semantic review cases; no judge accuracy result implied."
    )
    if args.write_private_diffs:
        output = ROOT / "run_data/rubric-review"
        output.mkdir(parents=True, exist_ok=True)
        for change in manifest["changes"]:
            task_id = change["task_id"]
            diff = []
            for field in ("task", "rubric"):
                diff.extend(
                    difflib.unified_diff(
                        before[task_id][field].splitlines(keepends=True),
                        after[task_id][field].splitlines(keepends=True),
                        fromfile=f"original/{task_id}/{field}",
                        tofile=f"candidate/{task_id}/{field}",
                    )
                )
            (output / f"{task_id}.diff").write_text("".join(diff))
        (output / "review-cases.private.enc").write_bytes(
            (ROOT / "BU_Bench_V2_review_cases.enc").read_bytes()
        )
        print(
            f"Private plaintext diffs and encrypted review cases written to ignored {output}. "
            "Do not publish the diffs."
        )


if __name__ == "__main__":
    main()
