"""Artifact-integrity checks; semantic cases still require human/judge review."""

import base64
import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from cryptography.fernet import Fernet

from review_rubric_revision import ROOT, validate_revision


class RevisionTests(unittest.TestCase):
    def test_only_declared_contracts_change(self):
        manifest, before, after, cases = validate_revision()
        expected = {
            "bu2-014",
            "bu2-029",
            "bu2-048",
            "bu2-088",
            "bu2-113",
            "bu2-171",
            "bu2-185",
        }
        self.assertEqual({c["task_id"] for c in manifest["changes"]}, expected)
        self.assertEqual(
            {c["task_id"] for c in manifest["changes"] if c["task_changed"]},
            {"bu2-029", "bu2-088", "bu2-113"},
        )
        allowed = {"task", "rubric", "task_sha", "rubric_sha", "revision"}
        for task_id in after:
            changed_fields = {
                key
                for key in set(before[task_id]) | set(after[task_id])
                if before[task_id].get(key) != after[task_id].get(key)
            }
            self.assertTrue(changed_fields <= allowed)
            self.assertEqual(after[task_id]["weights"], before[task_id]["weights"])
        self.assertEqual(len(cases["cases"]), 15)
        self.assertEqual(manifest["status"], "draft_not_regraded")

    def test_original_artifact_is_exact_published_snapshot(self):
        self.assertEqual(
            hashlib.sha256(
                (ROOT / "snapshots/BU_Bench_V2_2026-08-25.enc").read_bytes()
            ).hexdigest(),
            "fe0fc1eede3197d9eaffd42af3ac7b11cc15743d401e95496f3eb03f0d023a0e",
        )

    def test_candidate_and_historical_subset_manifests_pin_sources(self):
        candidate = json.loads((ROOT / "BU_Bench_V2_55.json").read_text())
        historical = json.loads(
            (ROOT / "snapshots/BU_Bench_V2_55_2026-08-25.json").read_text()
        )
        candidate_source = ROOT / candidate["source"]
        historical_source = ROOT / "snapshots" / historical["source"]
        self.assertEqual(
            candidate["source_sha256"],
            hashlib.sha256(candidate_source.read_bytes()).hexdigest(),
        )
        self.assertEqual(
            candidate["source_sha256"],
            "fe9ce279176fd965c9a658928a8d7562338c90934e5b4a330b4995bbfc150370",
        )
        self.assertEqual(
            historical["source_sha256"],
            hashlib.sha256(historical_source.read_bytes()).hexdigest(),
        )
        self.assertEqual(
            historical["source_sha256"],
            "fe0fc1eede3197d9eaffd42af3ac7b11cc15743d401e95496f3eb03f0d023a0e",
        )
        self.assertEqual(
            candidate["task_ids"],
            historical["task_ids"],
        )
        self.assertEqual(candidate["task_count"], historical["task_count"])

    def test_weight_edit_rejected_even_with_updated_artifact_hash(self):
        _, _, after, _ = validate_revision()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "snapshots").mkdir()
            for name in (
                "rubric_revision.json",
                "BU_Bench_V2_review_cases.enc",
                "snapshots/BU_Bench_V2_2026-08-25.enc",
            ):
                shutil.copy2(ROOT / name, root / name)
            task = after["bu2-171"]
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
            with self.assertRaisesRegex(ValueError, "Weight change"):
                validate_revision(root)


if __name__ == "__main__":
    unittest.main()
