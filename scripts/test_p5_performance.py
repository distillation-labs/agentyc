"""Tests for the Phase 5 performance evidence contract and checker."""

from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from check_p5_performance import (
    P5ArtifactError,
    validate_artifact_directory,
    validate_report,
)
from p5_performance import (
    ARTIFACT_ROOT,
    MIN_P95_SAMPLES,
    MIN_P99_SAMPLES,
    build_offline_report,
    publish_artifact,
)
from p5_statistics import bootstrap_ci, summarize_distribution


class Phase5EvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.report, cls.samples = build_offline_report(
            fixture_names=["small-form", "nested-frame", "oopif-shell"],
            samples=2,
            warmups=10,
            bootstrap_resamples=10,
            blocking=False,
            command=["python3", "scripts/run_p5_performance.py", "--mode", "offline", "--smoke"],
            nonce="test-p5-evidence",
        )
        cls.tempdir = tempfile.TemporaryDirectory(dir=ARTIFACT_ROOT)
        cls.artifact_dir = publish_artifact(Path(cls.tempdir.name) / "generation", cls.report, cls.samples)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tempdir.cleanup()

    def test_sample_counts_are_explicit_and_blocking_thresholds_are_enforced(self) -> None:
        cell = self.report["cells"][0]
        self.assertEqual(cell["sample_accounting"]["attempted"], 2)
        self.assertEqual(cell["sample_accounting"]["valid"], 2)
        self.assertEqual(MIN_P95_SAMPLES, 200)
        self.assertEqual(MIN_P99_SAMPLES, 1_000)

        broken = copy.deepcopy(self.report)
        broken["cells"][0]["blocking"] = True
        with self.assertRaises(P5ArtifactError):
            validate_report(broken, mode="offline")

    def test_offline_samples_and_cell_summaries_are_deterministic(self) -> None:
        first_report, first_samples = build_offline_report(
            fixture_names=["small-form"],
            samples=2,
            warmups=10,
            bootstrap_resamples=10,
            blocking=False,
            command=["p5-test"],
            nonce="deterministic-p5",
        )
        second_report, second_samples = build_offline_report(
            fixture_names=["small-form"],
            samples=2,
            warmups=10,
            bootstrap_resamples=10,
            blocking=False,
            command=["p5-test"],
            nonce="deterministic-p5",
        )
        self.assertEqual(first_samples, second_samples)
        self.assertEqual(first_report["cells"], second_report["cells"])
        self.assertEqual(first_report["scenario_probes"], second_report["scenario_probes"])

    def test_bootstrap_confidence_intervals_are_deterministic(self) -> None:
        values = [float(index) for index in range(1, 21)]
        first = bootstrap_ci(values, statistic_percentile=95, seed=1234, resamples=25)
        second = bootstrap_ci(values, statistic_percentile=95, seed=1234, resamples=25)
        self.assertEqual(first, second)
        self.assertEqual(
            summarize_distribution(values, seed=99, bootstrap_resamples=25),
            summarize_distribution(values, seed=99, bootstrap_resamples=25),
        )

    def test_missing_matrix_cell_fails_closed(self) -> None:
        broken = copy.deepcopy(self.report)
        broken["cells"].pop()
        with self.assertRaises(P5ArtifactError):
            validate_report(broken, mode="offline")

    def test_stale_artifact_fails_closed(self) -> None:
        future = datetime.now(timezone.utc) + timedelta(seconds=2)
        with self.assertRaises(P5ArtifactError):
            validate_artifact_directory(
                self.artifact_dir,
                mode="offline",
                now=future,
                max_age_seconds=1,
            )

    def test_provenance_and_redaction_are_required(self) -> None:
        missing_provenance = copy.deepcopy(self.report)
        missing_provenance.pop("provenance")
        with self.assertRaises(P5ArtifactError):
            validate_report(missing_provenance, mode="offline")

        missing_redaction = copy.deepcopy(self.report)
        missing_redaction.pop("redaction_status")
        with self.assertRaises(P5ArtifactError):
            validate_report(missing_redaction, mode="offline")

        unapplied = copy.deepcopy(self.report)
        unapplied["redaction_status"]["status"] = "not_applied"
        with self.assertRaises(P5ArtifactError):
            validate_report(unapplied, mode="offline")

    def test_legacy_and_guessed_metrics_are_rejected(self) -> None:
        guessed = copy.deepcopy(self.report)
        guessed["cells"][0]["metrics"]["snapshot_latency_ms"]["measurement_basis"] = "guessed"
        with self.assertRaises(P5ArtifactError):
            validate_report(guessed, mode="offline")

        legacy = copy.deepcopy(self.report)
        legacy["kind"] = "direct-benchmark-baseline"
        with self.assertRaises(P5ArtifactError):
            validate_report(legacy, mode="offline")

    def test_published_artifact_has_redacted_raw_samples_and_generation_binding(self) -> None:
        checked = validate_artifact_directory(self.artifact_dir, mode="offline", max_age_seconds=3600)
        self.assertEqual(checked["phase"], 5)
        self.assertEqual(checked["evidence_mode"], "offline")
        self.assertTrue((self.artifact_dir / "baseline-manifest.json").is_file())
        self.assertEqual(
            json.loads((self.artifact_dir / "baseline-manifest.json").read_text()),
            checked["baseline_manifest"],
        )
        self.assertTrue((self.artifact_dir / "generation-manifest.json").is_file())
        required_envelope = {
            "schema_version",
            "build_tuple",
            "environment",
            "timestamp",
            "command",
            "result",
            "redaction_status",
        }
        for name in ("baseline-manifest.json", "generation-manifest.json"):
            with self.subTest(name=name):
                artifact = json.loads((self.artifact_dir / name).read_text())
                self.assertTrue(required_envelope.issubset(artifact))
                self.assertEqual(artifact["redaction_status"]["status"], "applied")
        self.assertTrue((self.artifact_dir / "COMMIT").is_file())


if __name__ == "__main__":
    unittest.main()
