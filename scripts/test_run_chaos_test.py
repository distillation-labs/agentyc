from __future__ import annotations

import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

spec = importlib.util.spec_from_file_location("phase7_chaos_runner", SCRIPTS / "run_chaos_test.py")
if spec is None or spec.loader is None:
    raise RuntimeError("could not load run_chaos_test.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class ChaosRunnerTests(unittest.TestCase):
    def test_default_report_accounts_all_faults_and_unknown_without_replay(self) -> None:
        report = runner.build_report(seeds=2, repetitions=100)
        self.assertEqual(report["fault_inventory"], list(runner.run_release_gate.CHAOS_FAULTS))
        self.assertEqual(report["attempted_seed_repetitions"], 200)
        self.assertEqual(runner.validate_report(report, seeds=2, repetitions=100), [])
        for result in report["seed_results"]:
            self.assertEqual(result["status"], "reconciled_unknown")
            self.assertEqual([fault["fault"] for fault in result["fault_results"]], list(runner.run_release_gate.CHAOS_FAULTS))
            self.assertTrue(all(fault["accounted"] and not fault["mutation_replayed"] for fault in result["fault_results"]))
            self.assertEqual(result["replay_command"][result["replay_command"].index("--seed-start") + 1], str(result["seed"]))

    def test_seed_and_repetition_bounds_are_enforced(self) -> None:
        for kwargs in (
            {"seeds": 0},
            {"seeds": runner.MAX_SEEDS + 1},
            {"seed_start": -1},
            {"repetitions": runner.MIN_REPETITIONS - 1},
            {"repetitions": runner.MAX_REPETITIONS + 1},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(runner.ChaosRunnerError):
                runner.build_report(**kwargs)

    def test_validation_rejects_missing_fault_and_unsafe_unknown_handling(self) -> None:
        report = runner.build_report(seeds=1)
        report["seed_results"][0]["fault_results"].pop()
        report["seed_results"][0]["status"] = "success"
        self.assertTrue(runner.validate_report(report, seeds=1, repetitions=100))

    def test_cli_writes_redacted_artifact_envelope_under_artifacts(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / "artifacts") as temporary:
            output_dir = Path(temporary) / "chaos"
            relative_output = output_dir.relative_to(ROOT).as_posix()
            with redirect_stdout(io.StringIO()):
                exit_code = runner.main(["--seeds", "1", "--manifest", "tests/test-manifest.yaml", "--artifact-dir", relative_output])
            self.assertEqual(exit_code, 0)
            artifact = json.loads((output_dir / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(artifact["kind"], "phase7-chaos-test")
        self.assertEqual(artifact["redaction_status"]["status"], "applied")
        self.assertFalse(artifact["redaction_status"]["secrets"])
        self.assertEqual(artifact["chrome_safety"], {"launch": "never", "download": "never", "attach": "never"})
        self.assertEqual(runner.run_release_gate.validate_artifact_envelope(artifact, require_phase=7), [])


if __name__ == "__main__":
    unittest.main()
