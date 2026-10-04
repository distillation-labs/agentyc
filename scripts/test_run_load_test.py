"""Focused tests for the deterministic Phase 7 load-test model."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

import run_load_test as load_test


class ParseSpacesTests(unittest.TestCase):
    def test_accepts_plan_shape_and_resolves_max_at_execution(self) -> None:
        parsed = load_test.parse_spaces("1,2,4,8,max")
        self.assertEqual(parsed, [1, 2, 4, 8, "max"])
        report, _ = load_test.build_report("1,2,4,8,max", parsed)
        self.assertEqual(report["space_levels"], [1, 2, 4, 8, load_test.MAX_SPACES])

    def test_rejects_invalid_duplicate_and_out_of_bounds_values(self) -> None:
        for value in ("", "1,,2", "1,1", "0", "65", "one"):
            with self.subTest(value=value), self.assertRaises(load_test.LoadTestError):
                load_test.parse_spaces(value)


class ModelTests(unittest.TestCase):
    def test_overload_is_accounted_and_typed(self) -> None:
        cell, rows = load_test.simulate_cell(40, cell_index=0)
        self.assertEqual(cell["offered_operations"], 80)
        self.assertEqual(cell["admitted_operations"], 72)
        self.assertEqual(cell["rejected_operations"], 8)
        self.assertEqual(cell["overload_outcomes"]["rejected_by_type"], {"ReadQueueFull": 8})
        self.assertEqual(sum(row["sample_status"] == "rejected_overload" for row in rows), 8)

    def test_repeated_model_output_is_identical(self) -> None:
        first_report, first_rows = load_test.build_report("2,8", [2, 8])
        second_report, second_rows = load_test.build_report("2,8", [2, 8])
        self.assertEqual(first_report, second_report)
        self.assertEqual(first_rows, second_rows)
        self.assertEqual(first_report["cells"][1]["latency_ms"]["p99"], 14.625)

    def test_resource_observation_is_not_fabricated(self) -> None:
        resources = load_test.resource_report(8, 100)
        for metric in ("cpu_percent", "rss_bytes", "file_descriptors", "threads"):
            self.assertIsNone(resources[metric]["value"])
            self.assertEqual(resources[metric]["status"], "not_measured_offline")


class ArtifactTests(unittest.TestCase):
    def test_artifact_path_must_be_under_artifacts_and_not_symlinked(self) -> None:
        with self.assertRaises(load_test.LoadTestError):
            load_test.safe_artifact_dir("/tmp/outside-load-test")
        with tempfile.TemporaryDirectory(dir=load_test.ARTIFACT_ROOT) as temp_dir:
            link = Path(temp_dir) / "linked"
            link.symlink_to(Path(temp_dir), target_is_directory=True)
            with self.assertRaises(load_test.LoadTestError):
                load_test.safe_artifact_dir(link / "child")

    def test_cli_writes_enveloped_redacted_bounded_artifacts(self) -> None:
        with tempfile.TemporaryDirectory(dir=load_test.ARTIFACT_ROOT) as temp_dir:
            output_dir = Path(temp_dir) / "nested"
            relative = output_dir.relative_to(load_test.ROOT)
            with patch.object(load_test, "ARTIFACT_ROOT", load_test.ARTIFACT_ROOT.resolve()):
                code = load_test.main(["--spaces", "1,2,4,8,max", "--artifact-dir", str(relative)])
            self.assertEqual(code, 0)
            report_path = output_dir / load_test.REPORT_NAME
            samples_path = output_dir / load_test.SAMPLES_NAME
            self.assertTrue(report_path.is_file())
            self.assertTrue(samples_path.is_file())
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["kind"], "direct-load-test")
            self.assertEqual(report["evidence_mode"], "offline")
            self.assertFalse(report["release_eligible"])
            self.assertEqual(report["redaction_status"]["status"], "applied")
            self.assertEqual(report["safety"]["chrome_attach"], "never")
            final_cell = report["cells"][-1]
            self.assertEqual(final_cell["latency_ms"].keys(), {"p50", "p95", "p99"})
            self.assertEqual(final_cell["overload_outcomes"]["rejected_by_type"], {"ReadQueueFull": 56})
            samples = samples_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(samples), report["artifacts"]["sample_records"])
            records = [json.loads(row) for row in samples]
            self.assertTrue(all(record["sample_status"] for record in records))
            self.assertTrue(any(record.get("outcome_code") == "ReadQueueFull" for record in records))
            self.assertLess(report_path.stat().st_size, load_test.MAX_ARTIFACT_BYTES)
            self.assertLess(samples_path.stat().st_size, load_test.MAX_ARTIFACT_BYTES)


if __name__ == "__main__":
    unittest.main()
