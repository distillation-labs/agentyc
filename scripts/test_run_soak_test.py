from __future__ import annotations

import argparse
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import run_soak_test as soak
from artifact_envelope import redact_for_persistence


class ParseDurationTests(unittest.TestCase):
    def test_parses_supported_units(self) -> None:
        self.assertEqual(soak.parse_duration("10m"), 600)
        self.assertEqual(soak.parse_duration("2h"), 7200)
        self.assertEqual(soak.parse_duration("1s"), 1)

    def test_rejects_invalid_and_overlong_values(self) -> None:
        for value in ("0m", "25h", "1d", "1.5m", "-1m", "10"):
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                soak.parse_duration(value)


class OfflineSoakReportTests(unittest.TestCase):
    def test_report_covers_modeled_steps_slopes_isolation_and_no_replay(self) -> None:
        report = soak.build_report(600)
        self.assertFalse(report["live_evidence"])
        self.assertFalse(report["release_eligible"])
        self.assertEqual(len(report["lifecycle"]["steps"]), 8)
        self.assertEqual(set(report["resource_slopes"]), {
            "rss_bytes", "file_descriptors", "threads", "queue_depth",
            "event_buffer_entries", "tabs", "ledger_entries",
        })
        self.assertTrue(all(item["within_modeled_bounds"] for item in report["resource_slopes"].values()))
        self.assertFalse(report["isolation"]["observed_live"])
        self.assertFalse(report["no_replay"]["observed_live"])
        self.assertFalse(report["safety"]["product_processes_started"])
        self.assertEqual(redact_for_persistence(report), report)

    def test_cli_writes_enveloped_artifact_inside_artifacts(self) -> None:
        with tempfile.TemporaryDirectory(dir=soak.ARTIFACT_ROOT, prefix="test-soak-") as temp_dir:
            relative_dir = Path(temp_dir).relative_to(soak.ROOT).as_posix()
            self.assertEqual(soak.main(["--duration", "10m", "--artifact-dir", relative_dir]), 0)
            artifact = json.loads((Path(temp_dir) / soak.REPORT_NAME).read_text(encoding="utf-8"))
            self.assertEqual(artifact["build_tuple"]["artifact_kind"], "phase-7-soak-test")
            self.assertEqual(artifact["duration"]["requested_seconds"], 600)
            self.assertEqual(artifact["redaction_status"]["status"], "applied")
            self.assertFalse(artifact["live_evidence"])

    def test_artifact_directory_escape_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            soak.safe_artifact_dir("../outside")


if __name__ == "__main__":
    unittest.main()
