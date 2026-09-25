"""Artifact-integrity checks; semantic cases still require human/judge review."""

import base64
import hashlib
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cryptography.fernet import Fernet

from review_rubric_revision import ROOT, read_base_artifact, validate_revision


class RevisionTests(unittest.TestCase):
    def historical_baseline(self):
        manifest = json.loads((ROOT / "rubric_revision.json").read_text())
        try:
            return read_base_artifact(ROOT, manifest)
        except ValueError as error:
            if isinstance(error.__cause__, (OSError, subprocess.CalledProcessError)):
                self.skipTest(str(error))
            raise

    def copy_revision_fixture(self, root):
        baseline = root / "original.enc"
        baseline.write_bytes(self.historical_baseline())
        for name in (
            "rubric_revision.json", "BU_Bench_V2.enc", "BU_Bench_V2_review_cases.enc"
        ):
            shutil.copy2(ROOT / name, root / name)
        return baseline

    def test_only_declared_contracts_change(self):
        self.historical_baseline()
        manifest, before, after, cases = validate_revision()
        expected = {
            "bu2-001",
            "bu2-005",
            "bu2-007",
            "bu2-016",
            "bu2-020",
            "bu2-044",
            "bu2-047",
            "bu2-154",
            "bu2-014",
            "bu2-028",
            "bu2-029",
            "bu2-048",
            "bu2-053",
            "bu2-088",
            "bu2-113",
            "bu2-163",
            "bu2-187",
            "bu2-084",
            "bu2-099",
            "bu2-185",
        }
        self.assertEqual({c["task_id"] for c in manifest["changes"]}, expected)
        self.assertEqual(
            {c["task_id"] for c in manifest["changes"] if c["task_changed"]},
            {
                "bu2-001",
                "bu2-005",
                "bu2-007",
                "bu2-020",
                "bu2-044",
                "bu2-047",
                "bu2-154",
                "bu2-014",
                "bu2-028",
                "bu2-029",
                "bu2-088",
                "bu2-113",
                "bu2-185",
            },
        )
        allowed = {"task", "rubric", "task_sha", "rubric_sha", "revision"}
        missing = object()
        for task_id in after:
            changed_fields = {
                key
                for key in set(before[task_id]) | set(after[task_id])
                if before[task_id].get(key, missing) != after[task_id].get(key, missing)
            }
            self.assertTrue(changed_fields <= allowed)
            self.assertEqual(after[task_id]["weights"], before[task_id]["weights"])
        self.assertEqual(len(cases["cases"]), 34)
        self.assertEqual(manifest["status"], "main_not_regraded")

    def test_baseline_from_history_is_exact_published_snapshot(self):
        self.assertEqual(
            hashlib.sha256(self.historical_baseline()).hexdigest(),
            "fe0fc1eede3197d9eaffd42af3ac7b11cc15743d401e95496f3eb03f0d023a0e",
        )

    def test_current_dataset_is_pinned_and_legacy_v2_files_are_absent(self):
        manifest = json.loads((ROOT / "rubric_revision.json").read_text())
        self.assertEqual(
            hashlib.sha256((ROOT / "BU_Bench_V2.enc").read_bytes()).hexdigest(),
            manifest["candidate_encrypted_sha256"],
        )
        self.assertEqual(manifest["release_version"], "2.1")
        for name in (
            "BU_Bench_V2_55.json",
            "snapshots/BU_Bench_V2_55_2026-08-25.json",
            "snapshots/BU_Bench_V2_2026-08-25.enc",
        ):
            self.assertFalse((ROOT / name).exists())

    def test_explicit_baseline_works_without_git_and_is_hash_checked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = self.copy_revision_fixture(root)
            with patch("review_rubric_revision.subprocess.run") as git:
                _, before, after, _ = validate_revision(root, base_artifact=baseline)
                git.assert_not_called()
                self.assertEqual(set(before), set(after))
                baseline.write_bytes(b"wrong baseline")
                with self.assertRaisesRegex(ValueError, "historical baseline"):
                    validate_revision(root, base_artifact=baseline)

    def test_missing_history_explains_explicit_baseline_option(self):
        manifest = json.loads((ROOT / "rubric_revision.json").read_text())
        with (
            patch("review_rubric_revision.subprocess.run", side_effect=OSError),
            self.assertRaisesRegex(ValueError, "--base-artifact"),
        ):
            read_base_artifact(ROOT, manifest)

    def test_weight_edit_rejected_even_with_updated_artifact_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = self.copy_revision_fixture(root)
            _, _, after, _ = validate_revision(root, base_artifact=baseline)
            task = after["bu2-014"]
            key = next(iter(task["weights"]))
            task["weights"][key] += 1
            manifest = json.loads((root / "rubric_revision.json").read_text())
            payload = {"tasks": list(after.values()), "revision": manifest["revision"]}
            fernet = Fernet(
                base64.urlsafe_b64encode(hashlib.sha256(b"BU_Bench_V2").digest())
            )
            artifact = base64.b64encode(fernet.encrypt(json.dumps(payload).encode()))
            (root / "BU_Bench_V2.enc").write_bytes(artifact)
            manifest["candidate_encrypted_sha256"] = hashlib.sha256(
                artifact
            ).hexdigest()
            (root / "rubric_revision.json").write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, "Unapproved task field change"):
                validate_revision(root, base_artifact=baseline)

    def test_duplicate_task_ids_rejected_before_indexing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = self.copy_revision_fixture(root)
            candidate = json.loads(
                Fernet(
                    base64.urlsafe_b64encode(hashlib.sha256(b"BU_Bench_V2").digest())
                ).decrypt(base64.b64decode((ROOT / "BU_Bench_V2.enc").read_bytes()))
            )
            candidate["tasks"].append(candidate["tasks"][0])
            fernet = Fernet(
                base64.urlsafe_b64encode(hashlib.sha256(b"BU_Bench_V2").digest())
            )
            artifact = base64.b64encode(fernet.encrypt(json.dumps(candidate).encode()))
            (root / "BU_Bench_V2.enc").write_bytes(artifact)
            manifest = json.loads((root / "rubric_revision.json").read_text())
            manifest["candidate_encrypted_sha256"] = hashlib.sha256(
                artifact
            ).hexdigest()
            (root / "rubric_revision.json").write_text(json.dumps(manifest, indent=2))
            with self.assertRaisesRegex(ValueError, "Candidate task population"):
                validate_revision(root, base_artifact=baseline)


if __name__ == "__main__":
    unittest.main()
