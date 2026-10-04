from __future__ import annotations

import contextlib
import copy
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))


def load_script(name: str, filename: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {filename}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


checker = load_script("p1_t7_release_gate_checker", "check_release_gate.py")
decision = load_script("p1_t7_threshold_decision", "threshold_decision.py")


class CheckReleaseGateThresholdTests(unittest.TestCase):
    def _run(self, argv: list[str]) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = checker.main(argv)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_phase_zero_contract_and_output_are_unchanged(self) -> None:
        code, stdout, stderr = self._run(["--phase", "0", "--root", str(ROOT)])
        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        self.assertEqual(stdout, "check_release_gate: PASS (Phase 0 policy and disclosed evidence)\n")

    def test_phase_one_offline_passes_but_is_not_release_eligible(self) -> None:
        code, stdout, stderr = self._run(
            [
                "--phase",
                "1",
                "--root",
                str(ROOT),
                "--decision-record",
                "artifacts/p1-t7-threshold-decision.json",
                "--mode",
                "offline",
            ]
        )
        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        self.assertIn("Phase 1 P1-T7 threshold decision", stdout)
        self.assertIn("release_eligible=false", stdout)

    def test_phase_one_requires_record_and_mode(self) -> None:
        for args in (
            ["--phase", "1", "--root", str(ROOT), "--mode", "offline"],
            [
                "--phase",
                "1",
                "--root",
                str(ROOT),
                "--decision-record",
                "artifacts/p1-t7-threshold-decision.json",
            ],
        ):
            with self.subTest(args=args):
                code, stdout, stderr = self._run(args)
                self.assertEqual(code, 1)
                self.assertEqual(stdout, "")
                self.assertIn("required for Phase 1", stderr)

    def test_missing_record_field_fails_the_phase_one_gate(self) -> None:
        record = decision.load_decision_record(ROOT, "artifacts/p1-t7-threshold-decision.json")
        del record["production_path"]
        with tempfile.TemporaryDirectory(dir=ROOT / "artifacts") as temporary:
            path = Path(temporary) / "broken.json"
            path.write_text(json.dumps(record), encoding="utf-8")
            code, _stdout, stderr = self._run(
                [
                    "--phase",
                    "1",
                    "--root",
                    str(ROOT),
                    "--decision-record",
                    str(path),
                    "--mode",
                    "offline",
                ]
            )
        self.assertEqual(code, 1)
        self.assertIn("production_path", stderr)

    def test_threshold_change_without_new_decision_id_fails(self) -> None:
        record = decision.load_decision_record(ROOT, "artifacts/p1-t7-threshold-decision.json")
        record["metric_register"]["end_to_end_first_action"]["limit"]["value"] = 900.0
        record["metric_register"]["end_to_end_first_action"]["provisional_limits"][0]["value"] = 900.0
        with tempfile.TemporaryDirectory(dir=ROOT / "artifacts") as temporary:
            path = Path(temporary) / "changed.json"
            path.write_text(json.dumps(record), encoding="utf-8")
            code, _stdout, stderr = self._run(
                [
                    "--phase",
                    "1",
                    "--root",
                    str(ROOT),
                    "--decision-record",
                    str(path),
                    "--mode",
                    "offline",
                ]
            )
        self.assertEqual(code, 1)
        self.assertIn("new decision id", stderr)

    def test_live_disposable_evidence_is_rejected(self) -> None:
        record = self._live_fixture()
        record["evidence"]["provenance"]["source_class"] = "disposable_cdp"
        with tempfile.TemporaryDirectory(dir=ROOT / "artifacts") as temporary:
            directory = Path(temporary)
            raw = directory / "raw.jsonl"
            raw.write_text('{"sample": 1}\n', encoding="utf-8")
            record["evidence"]["raw_samples"]["files"] = [raw.relative_to(ROOT).as_posix()]
            path = directory / "live.json"
            path.write_text(json.dumps(record), encoding="utf-8")
            code, _stdout, stderr = self._run(
                [
                    "--phase",
                    "1",
                    "--root",
                    str(ROOT),
                    "--decision-record",
                    str(path),
                    "--mode",
                    "live",
                ]
            )
        self.assertEqual(code, 1)
        self.assertTrue("production path" in stderr or "forbidden live evidence" in stderr)

    def _live_fixture(self) -> dict[str, Any]:
        record = copy.deepcopy(decision.make_offline_record())
        record["evidence_mode"] = "live"
        record["evidence"] = {
            "mode": "live",
            "status": "measured",
            "provenance": {
                "source_class": "production_path",
                "path_id": "direct_existing_chrome",
                "run_id": "run-p1-t7-002",
                "timestamp": "2026-10-04T12:00:00Z",
                "command": ["production-harness", "--run", "run-p1-t7-002"],
                "build_tuple": {
                    "commit": "a" * 40,
                    "build_mode": "release",
                    "os_cpu": "macos-arm64",
                    "chrome_build": "154.0",
                    "extension_host_tuple": "extension-host-v1",
                    "fixture_data_hash": "b" * 64,
                    "tokenizer": "deployed-model-tokenizer-v1",
                    "concurrency": 2,
                    "cache_state": "warm",
                    "statistical_method": "bootstrap-95",
                },
            },
            "raw_samples": {"files": ["artifacts/live-raw.jsonl"], "sha256": "c" * 64, "sample_count": 1000},
            "confidence_intervals": {"method": "bootstrap", "confidence_level": 0.95, "metrics": {}},
            "valid_sample_counts": {},
            "regression_deltas": {},
            "redaction_status": {
                "status": "applied",
                "policy": "central-redaction-v1",
                "raw_browser_ids": False,
                "secrets": False,
                "absolute_paths": False,
                "page_bodies": False,
            },
        }
        for metric_id, metric in record["metric_register"].items():
            metric["evidence_mode"] = "live"
            metric["status"] = "measured"
            metric["value"] = metric["limit"]["value"] + 0.1 if metric["limit"]["kind"] == "absolute_minimum" else metric["limit"]["value"] / 2
            count = 1000 if metric["sample_requirement"] == "p99" else 200
            record["evidence"]["valid_sample_counts"][metric_id] = count
            record["evidence"]["confidence_intervals"]["metrics"][metric_id] = {
                "lower": 0.0,
                "upper": 1.0,
                "confidence_level": 0.95,
                "sample_count": count,
            }
            record["evidence"]["regression_deltas"][metric_id] = 0.0
        for counter in record["safety_counters"].values():
            counter.update({"value": 0, "status": "measured", "evidence_mode": "live"})
        record["chaos"].update({"evidence_mode": "live", "no_replay_assertion": True})
        for fault in record["chaos"]["accounted_faults"].values():
            fault.update({"status": "measured", "value": 1.0, "evidence_mode": "live", "no_replay_assertion": True})
        record["release_eligible"] = True
        return record


if __name__ == "__main__":
    unittest.main()
