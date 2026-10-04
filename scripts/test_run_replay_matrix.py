"""Focused tests for the bounded deterministic Phase 7 replay matrix."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

import run_replay_matrix as replay_matrix
from artifact_envelope import redact_for_persistence


class ReplayMatrixModelTests(unittest.TestCase):
    def test_repetitions_are_bounded_and_outcomes_are_byte_identical(self) -> None:
        for value in (replay_matrix.MIN_REPETITIONS - 1, replay_matrix.MAX_REPETITIONS + 1):
            with self.subTest(repetitions=value), self.assertRaises(replay_matrix.ReplayMatrixError):
                replay_matrix.run_matrix(repetitions=value)

        report, trace, samples = replay_matrix.run_matrix(repetitions=100, seed_start=4, seeds=2)
        self.assertEqual(report["attempted_repetitions"], 200)
        self.assertEqual(report["byte_comparison"]["divergent"], 0)
        self.assertFalse(report["release_eligible"])
        self.assertEqual(json.loads(trace)["trace"], report["trace"])
        rows = [json.loads(line) for line in samples.splitlines()]
        self.assertEqual(len(rows), 200)
        self.assertEqual(len({row["outcome_sha256"] for row in rows if row["seed"] == 4}), 1)
        self.assertEqual(len({row["outcome_sha256"] for row in rows if row["seed"] == 5}), 1)
        self.assertTrue(all(row["outcome"]["mutation_replayed"] is False for row in rows))

    def test_report_is_deterministic_before_envelope_metadata(self) -> None:
        first = replay_matrix.run_matrix(repetitions=100, seed_start=9, seeds=1)
        second = replay_matrix.run_matrix(repetitions=100, seed_start=9, seeds=1)
        self.assertEqual(first, second)

    def test_manifest_must_be_repository_local_and_valid(self) -> None:
        with self.assertRaises(replay_matrix.ReplayMatrixError):
            replay_matrix.manifest_metadata("/etc/passwd")
        with tempfile.TemporaryDirectory(dir=ROOT / "artifacts") as temp_dir:
            link = Path(temp_dir) / "manifest.yaml"
            link.symlink_to(replay_matrix.DEFAULT_MANIFEST)
            with self.assertRaises(replay_matrix.ReplayMatrixError):
                replay_matrix.manifest_metadata(link)

    def test_artifact_directory_rejects_escape_and_symlink(self) -> None:
        with self.assertRaises(replay_matrix.ReplayMatrixError):
            replay_matrix.safe_artifact_dir("/tmp/replay-matrix")
        with tempfile.TemporaryDirectory(dir=replay_matrix.ARTIFACT_ROOT) as temp_dir:
            link = Path(temp_dir) / "linked"
            link.symlink_to(Path(temp_dir), target_is_directory=True)
            with self.assertRaises(replay_matrix.ReplayMatrixError):
                replay_matrix.safe_artifact_dir(link / "child")

    def test_cli_writes_bounded_redacted_artifacts_with_envelope(self) -> None:
        with tempfile.TemporaryDirectory(dir=replay_matrix.ARTIFACT_ROOT) as temp_dir:
            output_dir = Path(temp_dir) / "matrix"
            relative = output_dir.relative_to(ROOT).as_posix()
            code = replay_matrix.main(["--repetitions", "100", "--manifest", "tests/test-manifest.yaml", "--artifact-dir", relative])
            self.assertEqual(code, 0)
            report_path = output_dir / replay_matrix.REPORT_NAME
            trace_path = output_dir / replay_matrix.TRACE_NAME
            outcomes_path = output_dir / replay_matrix.OUTCOMES_NAME
            report = json.loads(report_path.read_text(encoding="utf-8"))
            trace = json.loads(trace_path.read_text(encoding="utf-8"))
            outcomes = outcomes_path.read_text(encoding="utf-8").splitlines()
            report_size = report_path.stat().st_size
            outcomes_size = outcomes_path.stat().st_size
        self.assertFalse(report["release_eligible"])
        self.assertEqual(report["redaction_status"]["status"], "applied")
        self.assertFalse(report["redaction_status"]["secrets"])
        self.assertEqual(report["safety"]["chrome_launch"], "never")
        self.assertFalse(report["safety"]["network_access"])
        self.assertEqual(trace["trace"], report["trace"])
        self.assertEqual(trace["seed_start"], report["seed_start"])
        self.assertEqual(len(outcomes), 100)
        self.assertEqual(redact_for_persistence(report), report)
        self.assertEqual(replay_matrix.run_release_gate.validate_artifact_envelope(report, require_phase=7), [])
        self.assertLess(report_size, replay_matrix.MAX_ARTIFACT_BYTES)
        self.assertLess(outcomes_size, replay_matrix.MAX_ARTIFACT_BYTES)

    def test_divergence_fails_closed(self) -> None:
        original = replay_matrix.run_release_gate._hook_outcome
        counter = 0

        def diverging_outcome(hook: str, seed: int, trace: list[str]) -> dict[str, object]:
            nonlocal counter
            counter += 1
            result = original(hook, seed, trace)
            if counter > 1:
                result = {**result, "mutation_replayed": True}
            return result

        with patch.object(replay_matrix.run_release_gate, "_hook_outcome", side_effect=diverging_outcome):
            with self.assertRaises(replay_matrix.ReplayMatrixError):
                replay_matrix.run_matrix(repetitions=100)


if __name__ == "__main__":
    unittest.main()
