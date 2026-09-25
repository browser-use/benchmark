"""Validate the encrypted revision; optionally write private before/after diffs."""

import argparse
import base64
import difflib
import hashlib
import json
import re
import subprocess
from pathlib import Path

from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parent

# One prospective scoring redesign; all other tasks retain original weights.
APPROVED_WEIGHT_REVISIONS = {
    "bu2-185": (
        "a3a3b9bf46595905b842c65560b9054a46746dccd8bb70469de29165ba0814a4",
        "de3256e1d436612e9b546532da0aa21d45c7b9e0ceabdc6fdd91e3b8528f0ba4",
    ),
}


def weights_digest(weights: dict) -> str:
    return digest(json.dumps(weights, sort_keys=True, separators=(",", ":")))


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def decrypt_bytes(artifact: bytes, key_name: str) -> dict:
    key = base64.urlsafe_b64encode(hashlib.sha256(key_name.encode()).digest())
    return json.loads(Fernet(key).decrypt(base64.b64decode(artifact)))


def decrypt(path: Path, key_name: str) -> dict:
    return decrypt_bytes(path.read_bytes(), key_name)


def read_base_artifact(
    root: Path, manifest: dict, base_artifact: Path | None = None
) -> bytes:
    """Read the pinned baseline without keeping an obsolete dataset in the checkout."""
    if base_artifact is not None:
        artifact = base_artifact.read_bytes()
    else:
        commit = manifest["base_commit"]
        if not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise ValueError("Base commit must be a full lowercase Git SHA")
        try:
            artifact = subprocess.run(
                ["git", "-C", str(root), "show", f"{commit}:BU_Bench_V2.enc"],
                check=True,
                capture_output=True,
            ).stdout
        except (OSError, subprocess.CalledProcessError) as exc:
            raise ValueError(
                "Pinned baseline is absent from local Git history. "
                "Use a full clone, fetch the base_commit from rubric_revision.json, "
                "or pass --base-artifact /path/to/original/BU_Bench_V2.enc. "
                "Normal benchmark execution does not need the historical baseline."
            ) from exc
    if hashlib.sha256(artifact).hexdigest() != manifest["base_encrypted_sha256"]:
        raise ValueError("Encrypted artifact hash mismatch: historical baseline")
    return artifact


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


def validate_revision(
    root: Path = ROOT, *, base_artifact: Path | None = None
) -> tuple[dict, dict, dict, dict]:
    manifest = json.loads((root / "rubric_revision.json").read_text())
    candidate_path = root / "BU_Bench_V2.enc"
    if hashlib.sha256(candidate_path.read_bytes()).hexdigest() != manifest[
        "candidate_encrypted_sha256"
    ]:
        raise ValueError(f"Encrypted artifact hash mismatch: {candidate_path.name}")
    original = decrypt_bytes(
        read_base_artifact(root, manifest, base_artifact), "BU_Bench_V2"
    )
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
        allowed_fields = {"task", "rubric", "task_sha", "rubric_sha", "revision"}
        if task_id in APPROVED_WEIGHT_REVISIONS:
            allowed_fields |= {"title", "summary", "weights", "weights_sha"}
        if not changed_fields <= allowed_fields:
            raise ValueError(f"Unapproved task field change: {task_id}")
        if task.get("revision") != manifest["revision"]:
            raise ValueError(f"Task revision mismatch: {task_id}")
        if task_id in APPROVED_WEIGHT_REVISIONS:
            expected_before, expected_after = APPROVED_WEIGHT_REVISIONS[task_id]
            if (
                weights_digest(old["weights"]) != expected_before
                or weights_digest(task["weights"]) != expected_after
                or change.get("before_weights_sha256") != expected_before
                or change.get("after_weights_sha256") != expected_after
                or task.get("weights_sha") != expected_after
            ):
                raise ValueError(f"Unapproved weight revision: {task_id}")
            for field in ("title", "summary"):
                if (
                    digest(old[field]) != change.get(f"before_{field}_sha256")
                    or digest(task[field]) != change.get(f"after_{field}_sha256")
                ):
                    raise ValueError(f"Metadata hash mismatch: {task_id}/{field}")
        elif task["weights"] != old["weights"]:
            raise ValueError(f"Weight change: {task_id}")
        if any(type(w) is not int or w <= 0 for w in task["weights"].values()):
            raise ValueError(f"Invalid weights: {task_id}")
        if sum(task["weights"].values()) != 100:
            raise ValueError(f"Weight total changed: {task_id}")
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
        if (
            not any(case["task_id"] == task_id for case in cases["cases"])
            and change.get("review_status") != "pending_saved_trace_review"
        ):
            raise ValueError(f"No review cases or pending review declaration: {task_id}")
    for case in cases["cases"]:
        if case["task_id"] not in changes or not set(case["expected"]["items"]) <= set(
            after[case["task_id"]]["weights"]
        ):
            raise ValueError("Review case references an unknown task/item")
    return manifest, before, after, cases


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-private-diffs", action="store_true")
    parser.add_argument(
        "--base-artifact",
        type=Path,
        help="Original encrypted baseline for source archives or shallow clones; hash-verified.",
    )
    args = parser.parse_args()
    manifest, before, after, cases = validate_revision(base_artifact=args.base_artifact)
    print(
        f"Validated {len(manifest['changes'])} revised tasks; "
        "only explicitly pinned scoring revisions may change weights."
    )
    print(
        f"{len(cases['cases'])} synthetic semantic review cases; no judge accuracy result implied."
    )
    pending = [
        c["task_id"] for c in manifest["changes"]
        if c.get("review_status") == "pending_saved_trace_review"
    ]
    if pending:
        print("Saved-trace review pending: " + ", ".join(pending))
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
