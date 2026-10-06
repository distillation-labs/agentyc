import copy
import json
import re
import unittest
from datetime import datetime, timezone
from pathlib import Path

from scripts import artifact_envelope
from scripts import check_phase_4_extension as checker


class Phase4CheckerTests(unittest.TestCase):
    def _current_architecture(self):
        plan = checker.read(checker.ROOT, checker.PLAN)
        match = re.search(
            r"(?ms)^## Current acceptance criteria[ \t]*\n(.*?)(?=^## |\Z)",
            plan,
        )
        self.assertIsNotNone(match)
        current_plan = f"## Current acceptance criteria\n{match.group(1)}"
        extension_manifest = json.loads(
            checker.read(checker.ROOT, Path("extension/manifest.json"))
        )
        worker = checker.read(
            checker.ROOT, Path("extension/src/tab-creation-worker.mjs")
        )
        native_messaging = checker.read(
            checker.ROOT, Path("extension/src/native-messaging.mjs")
        )
        cdp = checker.read(checker.ROOT, Path("crates/agentyc-host/src/cdp.rs"))
        return current_plan, extension_manifest, worker, native_messaging, cdp

    def test_active_phase4_manifest_passes_deterministic_gate(self):
        result = checker.check(checker.ROOT)
        self.assertEqual(result["phase"], 4)
        self.assertEqual(result["status"], "active")
        self.assertFalse(result["release_eligible"])
        self.assertTrue(result["deterministic"])
        self.assertEqual(result["live_existing_profile_mcp"], "partial")

    def test_manifest_and_artifact_are_bounded_and_non_live(self):
        manifest = checker.manifest(checker.ROOT)
        artifact = checker.artifact(checker.ROOT)
        self.assertFalse(manifest["release_eligible"])
        self.assertFalse(artifact["release_eligible"])
        self.assertTrue(artifact["nonclaims"])
        self.assertTrue(manifest["source_hashes"])
        self.assertTrue(manifest["evidence_artifacts"])

    def test_current_architecture_does_not_require_obsolete_extension_routes(self):
        checker.validate_current_architecture(*self._current_architecture())
        obsolete_sources = {
            "extension/src/debugger-bridge.mjs",
            "extension/src/frames.mjs",
            "extension/src/service-worker.mjs",
            "extension/src/sidepanel/app.mjs",
            "extension/tests/reconnect-debugger.test.mjs",
        }
        self.assertTrue(obsolete_sources.isdisjoint(
            {path.as_posix() for path in checker.PHASE4_SOURCE_PATHS}
        ))

    def test_current_create_only_and_loopback_violations_fail(self):
        plan, extension_manifest, worker, native_messaging, cdp = (
            self._current_architecture()
        )
        with self.subTest("additional tab inventory route"):
            with self.assertRaisesRegex(checker.Phase4Error, "non-creation tabs route"):
                checker.validate_current_architecture(
                    plan,
                    extension_manifest,
                    worker + "\ntabs.query({});",
                    native_messaging,
                    cdp,
                )
        with self.subTest("debugger permission"):
            bad_manifest = copy.deepcopy(extension_manifest)
            bad_manifest["permissions"].append("debugger")
            with self.assertRaisesRegex(checker.Phase4Error, "permissions exceed"):
                checker.validate_current_architecture(
                    plan, bad_manifest, worker, native_messaging, cdp
                )
        with self.subTest("non-loopback CDP endpoint"):
            with self.assertRaisesRegex(checker.Phase4Error, "loopback invariant"):
                checker.validate_current_architecture(
                    plan,
                    extension_manifest,
                    worker,
                    native_messaging,
                    cdp.replace(
                        '.strip_prefix("ws://127.0.0.1:")',
                        '.strip_prefix("ws://192.0.2.1:")',
                    ),
                )

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
        current_hashes = checker.phase4_source_hashes(checker.ROOT)
        producer_hash = checker._sha256(
            checker.ROOT, Path("scripts/check_phase_4_extension.py")
        )
        for record in (manifest, artifact):
            record["source_hashes"] = copy.deepcopy(current_hashes)
            record["provenance"]["source_hashes"] = copy.deepcopy(current_hashes)
            record["build_tuple"] = {
                "phase": 4,
                "artifact_kind": "phase-4-extension-review",
                "producer": "scripts/check_phase_4_extension.py",
                "producer_sha256": producer_hash,
            }
            record["provenance"]["build_tuple"] = copy.deepcopy(record["build_tuple"])
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
