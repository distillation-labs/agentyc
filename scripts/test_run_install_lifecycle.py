from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import run_install_drill as drill
import run_install_lifecycle as lifecycle


class Phase4LifecycleLaneTests(unittest.TestCase):
    def _live_record(self) -> dict[str, object]:
        source = lifecycle.safe_extension_dir(None)
        fixture_url = lifecycle.safe_fixture_url(None, source)
        record = lifecycle._offline_record(source, fixture_url)
        record["evidence_mode"] = "live"
        record["status"] = "live_passed"
        record["lifecycle"].update(
            {
                "evidence_mode": "live",
                "status": "passed",
                "install": "installed",
                "update": "passed",
                "uninstall": "passed",
                "downgrade": "passed",
                "rollback": "rolled_back",
            }
        )
        record["rollback_safety"].update(
            {
                "evidence_mode": "live",
                "new_mutations": "paused",
                "pages_retained": True,
                "user_tabs_preserved": True,
                "chrome_process_terminated": False,
                "global_close_used": False,
                "incompatible_ledger_refused": True,
                "kill_switch": {"status": "armed_and_verified", "armed": True, "verified": True},
            }
        )
        return record

    def test_phase4_defaults_and_path_boundaries(self) -> None:
        self.assertEqual(lifecycle.SOURCE_EXTENSION_DIR, ROOT / "extension")
        self.assertEqual(lifecycle.DEFAULT_ARTIFACT_DIR, ROOT / "artifacts" / "p4-install-lifecycle")
        self.assertEqual(
            lifecycle.safe_artifact_dir("artifacts/p4-install-lifecycle/run"),
            ROOT / "artifacts" / "p4-install-lifecycle" / "run",
        )
        with self.assertRaises(ValueError):
            lifecycle.safe_artifact_dir("artifacts/other-lane/run")
        with self.assertRaises(ValueError):
            lifecycle.safe_artifact_dir("/tmp/agentyc-install-lifecycle")

        with tempfile.TemporaryDirectory(dir=ROOT / "artifacts") as temporary:
            linked = Path(temporary) / "linked"
            linked.symlink_to(ROOT / "artifacts" / "p4-install-lifecycle", target_is_directory=True)
            with self.assertRaises(ValueError):
                lifecycle.safe_artifact_dir(str(linked / "child"))

        self.assertEqual(lifecycle.safe_extension_dir("extension"), ROOT / "extension")
        with tempfile.TemporaryDirectory(dir=ROOT / "artifacts") as temporary:
            linked = Path(temporary) / "extension"
            linked.symlink_to(ROOT / "extension", target_is_directory=True)
            with self.assertRaises(ValueError):
                lifecycle.safe_extension_dir(str(linked))

    def test_production_uses_checked_in_fixture_without_fixture_html(self) -> None:
        source = lifecycle.safe_extension_dir(None)
        self.assertFalse((source / "fixture.html").exists())
        fixture_url = lifecycle.safe_fixture_url(None, source)
        self.assertTrue(fixture_url.startswith((ROOT / "tests" / "fixtures").resolve().as_uri()))
        self.assertEqual(lifecycle.safe_fixture_url("https://fixture.invalid/lifecycle", source), "https://fixture.invalid/lifecycle")
        with self.assertRaises(ValueError):
            lifecycle.safe_fixture_url((ROOT / "README.md").resolve().as_uri(), source)
        with self.assertRaises(ValueError):
            lifecycle.safe_fixture_url(f"{fixture_url}?unsafe=1", source)

    def test_provenance_binds_manifest_host_and_source_hashes(self) -> None:
        record = self._live_record()
        self.assertEqual(
            drill.validate_lifecycle_record(
                record,
                require_live=True,
                require_provenance=True,
                require_production_provenance=True,
            ),
            [],
        )
        provenance = record["lifecycle_provenance"]
        self.assertEqual(provenance["source_kind"], "production_extension")
        self.assertEqual(provenance["source_root"], "extension")
        self.assertEqual(
            provenance["manifest_identity"]["sha256"],
            provenance["source_hashes"]["extension/manifest.json"],
        )
        self.assertEqual(
            provenance["host_identity"]["sha256"],
            provenance["source_hashes"]["extension/native_host_manifest.macos.json"],
        )

        for mutation in (
            lambda value: value["lifecycle_provenance"]["source_hashes"].update({"extension/manifest.json": "0" * 64}),
            lambda value: value["lifecycle_provenance"]["manifest_identity"].update({"name": "impostor"}),
            lambda value: value["lifecycle_provenance"]["host_identity"].update({"name": "impostor.host"}),
            lambda value: value["lifecycle_provenance"]["fixture"].update({"content_sha256": "0" * 64}),
        ):
            broken = copy.deepcopy(record)
            mutation(broken)
            with self.subTest(mutation=mutation):
                self.assertTrue(
                    drill.validate_lifecycle_record(
                        broken,
                        require_live=True,
                        require_provenance=True,
                        require_production_provenance=True,
                    )
                )

    def test_persisted_record_is_source_bound_and_tampering_fails_closed(self) -> None:
        record = self._live_record()
        with tempfile.TemporaryDirectory(dir=drill.ARTIFACT_ROOT) as temporary:
            artifact_dir = Path(temporary) / "p4"
            path = lifecycle.write_record(artifact_dir, record)
            persisted = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(persisted["build_tuple"]["phase"], 4)
            self.assertEqual(persisted["lifecycle_provenance"]["source_kind"], "production_extension")
            loaded = drill.load_lifecycle_record(str(path), artifact_dir)
            self.assertEqual(loaded["lifecycle_provenance"], persisted["lifecycle_provenance"])

            persisted["lifecycle_provenance"]["host_identity"]["sha256"] = "0" * 64
            path.write_text(json.dumps(persisted), encoding="utf-8")
            with self.assertRaises(ValueError):
                drill.load_lifecycle_record(str(path), artifact_dir)


if __name__ == "__main__":
    unittest.main()
