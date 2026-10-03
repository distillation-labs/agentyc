from __future__ import annotations

import copy
import importlib.util
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


_gate = load_script("rollout_release_gate", "run_release_gate.py")
_benchmark = load_script("rollout_direct_benchmark", "run_direct_benchmark.py")
_envelope = load_script("rollout_artifact_envelope", "artifact_envelope.py")


class ReleaseGateTests(unittest.TestCase):
    def test_offline_gate_is_never_release_eligible(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / "artifacts") as temporary:
            report, code = _gate.run_gate(artifact_dir=Path(temporary), require_live=False)
        self.assertEqual(code, 0)
        self.assertEqual(report["status"], "offline_passed")
        self.assertFalse(report["release_eligible"])
        self.assertIn("offline-only", {item["code"] for item in report["blockers"]})

    def test_live_gate_missing_sources_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / "artifacts") as temporary:
            report, code = _gate.run_gate(artifact_dir=Path(temporary), require_live=True)
        self.assertEqual(code, 1)
        self.assertEqual(report["status"], "blocked")
        self.assertFalse(report["release_eligible"])
        self.assertEqual(
            {item["gate"] for item in report["blockers"] if item["code"] == "evidence-missing"},
            {"performance", "real_chrome", "installation"},
        )

    def test_skipped_live_status_is_rejected(self) -> None:
        errors = _gate.validate_no_skipped_live(
            {
                "live": {"status": "skipped"},
                "scenario_status": "ignored",
                "ignored_cases": ["user-tab-preservation"],
            }
        )
        self.assertTrue(errors)

    def test_envelope_redaction_tamper_is_rejected(self) -> None:
        report = {
            "schema_version": 1,
            "phase": 0,
            "kind": "test-artifact",
            "result": {"status": "passed"},
        }
        _envelope.envelope(report, kind="test-artifact", command=["scripts/test"])
        report["result"]["token"] = "raw-value"
        errors = _gate.validate_artifact_envelope(report)
        self.assertTrue(any("stable after central redaction" in error for error in errors))

    def test_release_schema_requires_limits_for_every_metric(self) -> None:
        gates = _benchmark.offline_release_gates()
        self.assertEqual(_gate.validate_release_gate_schema({"release_gates": gates}, require_live=False), [])
        persisted = _benchmark.redact_benchmark_report({"release_gates": gates})
        self.assertIsInstance(persisted["release_gates"]["token"], dict)
        broken = copy.deepcopy(gates)
        del broken["resource"]["metrics"]["cpu_p95_percent"]["ceiling"]
        self.assertTrue(_gate.validate_release_gate_schema({"release_gates": broken}, require_live=False))

    def test_deterministic_hooks_have_100_valid_repetitions(self) -> None:
        for hook in ("replay", "chaos", "soak"):
            with self.subTest(hook=hook):
                report = _gate.run_deterministic_hook(hook, seed=17, repetitions=100)
                self.assertEqual(_gate.validate_deterministic_hook(report, hook), [])
                self.assertEqual(report["sample_accounting"]["valid"], 100)
                second = _gate.run_deterministic_hook(hook, seed=17, repetitions=100)
                self.assertEqual(report["outcome_hash"], second["outcome_hash"])

    def test_deterministic_hook_divergence_and_missing_runs_fail(self) -> None:
        report = _gate.run_deterministic_hook("replay", repetitions=100)
        report["sample_accounting"]["divergent"] = 1
        self.assertTrue(_gate.validate_deterministic_hook(report, "replay"))
        short = _gate.run_deterministic_hook("replay", repetitions=100)
        short["repetitions"] = 99
        self.assertTrue(_gate.validate_deterministic_hook(short, "replay"))

    def test_live_source_reports_require_release_eligibility(self) -> None:
        benchmark = {
            "kind": "direct-benchmark-baseline",
            "mode": "managed",
            "evidence_mode": "live",
            "status": "live_passed",
            "release_eligible": False,
        }
        self.assertIn("benchmark is not release eligible", _gate.validate_benchmark(benchmark, require_live=True))

        installation = {
            "evidence_mode": "live",
            "status": "drill_passed",
            "release_eligible": False,
        }
        self.assertIn(
            "installation record is not release eligible",
            _gate.validate_installation_record(installation, require_live=True),
        )

    def test_installation_record_requires_live_lifecycle_and_rollback_safety(self) -> None:
        report = {
            "schema_version": 1,
            "phase": 0,
            "kind": "installation-preflight",
            "evidence_mode": "live",
            "status": "drill_passed",
            "current_run": True,
            "release_eligible": True,
            "evidence": {"platform": {"name": "Darwin"}},
            "installation": {"status": "installed"},
            "rollback": {"status": "rolled_back", "user_tabs_or_chrome_changed": False},
            "lifecycle": {
                "schema_version": 1,
                "evidence_mode": "live",
                "install": "installed",
                "update": "passed",
                "uninstall": "passed",
                "downgrade": "passed",
                "rollback": "rolled_back",
            },
            "safety": {
                "chrome_launch": "never",
                "chrome_download": "never",
                "chrome_profile_mutation": False,
            },
            "rollback_safety": {
                "schema_version": 1,
                "evidence_mode": "live",
                "new_mutations": "paused",
                "pages_retained": True,
                "user_tabs_preserved": True,
                "chrome_process_terminated": False,
                "global_close_used": False,
                "incompatible_ledger_refused": True,
                "kill_switch": {"status": "armed_and_verified", "armed": True, "verified": True},
            },
        }
        _envelope.envelope(report, kind="installation-drill", command=["scripts/run_install_drill.py", "--drill"])
        self.assertEqual(_gate.validate_installation_record(report, require_live=True), [])
        report["rollback_safety"]["kill_switch"]["armed"] = False
        self.assertTrue(_gate.validate_installation_record(report, require_live=True))


if __name__ == "__main__":
    unittest.main()
