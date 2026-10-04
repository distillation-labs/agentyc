import copy
import unittest
from datetime import datetime, timezone

from scripts import artifact_envelope
from scripts import check_phase_4_extension as checker


class Phase4CheckerTests(unittest.TestCase):
    def test_active_phase4_manifest_passes_deterministic_gate(self):
        result = checker.check(checker.ROOT)
        self.assertEqual(result["phase"], 4)
        self.assertEqual(result["status"], "active")
        self.assertFalse(result["release_eligible"])
        self.assertTrue(result["deterministic"])

    def test_manifest_and_artifact_are_bounded_and_non_live(self):
        manifest = checker.manifest(checker.ROOT)
        artifact = checker.artifact(checker.ROOT)
        self.assertFalse(manifest["release_eligible"])
        self.assertFalse(artifact["release_eligible"])
        self.assertTrue(artifact["nonclaims"])
        self.assertTrue(manifest["source_hashes"])
        self.assertTrue(manifest["evidence_artifacts"])

    def test_artifact_envelope_uses_report_phase(self):
        report = artifact_envelope.envelope(
            {"phase": 4, "status": "passed"},
            kind="phase4-test",
            command=["python3", "focused-test"],
        )
        self.assertEqual(report["build_tuple"]["phase"], 4)
        self.assertEqual(report["provenance"]["build_tuple"]["phase"], 4)

    def _complete_records(self):
        manifest = copy.deepcopy(checker.manifest(checker.ROOT))
        artifact = copy.deepcopy(checker.artifact(checker.ROOT))
        timestamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        named = [{"name": "phase4-extension-review", "path": checker.ARTIFACT.as_posix()}]
        for record in (manifest, artifact):
            record["status"] = "complete"
            record["evidence_mode"] = "headed_existing_profile"
            record["timestamp"] = timestamp
            record["nonce"] = "phase4-complete-evidence-v1"
            record["evidence_artifacts"] = copy.deepcopy(named)
            record["provenance"]["status"] = "complete"
            record["provenance"]["evidence_mode"] = "headed_existing_profile"
            record["provenance"]["timestamp"] = timestamp
            record["provenance"]["nonce"] = "phase4-complete-evidence-v1"
            record["provenance"]["evidence_artifacts"] = copy.deepcopy(named)
        return manifest, artifact

    def test_complete_provenance_contract_passes_when_fresh_and_bound(self):
        manifest, artifact = self._complete_records()
        checker.validate_complete_evidence(checker.ROOT, manifest, artifact)

    def test_complete_manifest_missing_provenance_fails_closed(self):
        manifest, artifact = self._complete_records()
        del manifest["provenance"]
        with self.assertRaisesRegex(checker.Phase4Error, "missing provenance"):
            checker.validate_complete_evidence(checker.ROOT, manifest, artifact)

    def test_complete_manifest_with_stale_source_hash_fails_closed(self):
        manifest, artifact = self._complete_records()
        path = next(iter(manifest["source_hashes"]))
        manifest["source_hashes"][path] = "0" * 64
        manifest["provenance"]["source_hashes"] = manifest["source_hashes"]
        with self.assertRaisesRegex(checker.Phase4Error, "source hash is stale"):
            checker.validate_complete_evidence(checker.ROOT, manifest, artifact)

    def test_complete_manifest_with_stale_timestamp_fails_closed(self):
        manifest, artifact = self._complete_records()
        manifest["timestamp"] = "2020-01-01T00:00:00Z"
        manifest["provenance"]["timestamp"] = manifest["timestamp"]
        with self.assertRaisesRegex(checker.Phase4Error, "timestamp is stale"):
            checker.validate_complete_evidence(checker.ROOT, manifest, artifact)

    def test_complete_manifest_provenance_mismatch_fails_closed(self):
        manifest, artifact = self._complete_records()
        artifact["provenance"]["nonce"] = "phase4-other-evidence-v1"
        with self.assertRaisesRegex(checker.Phase4Error, "provenance nonce mismatch"):
            checker.validate_complete_evidence(checker.ROOT, manifest, artifact)


if __name__ == "__main__":
    unittest.main()
