from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import shutil
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


_checker = load_script("phase0_gate_checker_contracts", "check_phase_0_baseline.py")
_chrome = load_script("phase0_gate_chrome_contracts", "run_existing_chrome.py")
_release = load_script("phase0_gate_release_contracts", "run_release_gate.py")
_envelope = load_script("phase0_gate_envelope_contracts", "artifact_envelope.py")

REQUIRED_SCENARIOS = (
    "user-tab-preservation",
    "two-space-isolation",
    "focus-stability",
    "takeover-fence",
    "return-control-fresh-lease",
    "agent-page-cleanup",
    "worker-restart-recovery",
    "host-restart-recovery",
    "chrome-restart-recovery",
    "extension-update-recovery",
)
SAFETY_COUNTERS = (
    "user_tab_closes",
    "focus_theft",
    "cross_space_mutations",
    "stale_agent_mutations",
)


def _enrollment() -> dict[str, dict[str, Any]]:
    return {
        "profile": {"enrolled": True, "status": "bound"},
        "host": {"enrolled": True, "status": "connected"},
        "extension": {"enrolled": True, "status": "installed"},
    }


def _coexistence_report(
    *,
    scenario_names: list[str] | None = None,
    safety: dict[str, Any] | None = None,
    enrollment: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    names = scenario_names if scenario_names is not None else list(REQUIRED_SCENARIOS)
    return {
        "status": "live_passed",
        "mode": "headed",
        "spaces": 2,
        "agents": 2,
        "live": {
            "requested": True,
            "required": True,
            "status": "live_passed",
            "evidence_status": "live_passed",
            "profile_scope": "existing_user_profile",
            "enrollment": copy.deepcopy(enrollment or _enrollment()),
        },
        "scenarios": [{"name": name, "status": "live_passed"} for name in names],
        "safety": safety
        or {
            "user_tab_closes": 0,
            "focus_theft": 0,
            "cross_space_mutations": 0,
            "stale_agent_mutations": 0,
        },
    }


def _validate_coexistence(report: dict[str, Any]) -> tuple[str, set[str]]:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        directory = root / "artifacts" / "p0-coexistence"
        directory.mkdir(parents=True)
        (directory / "report.json").write_text(json.dumps(report), encoding="utf-8")
        checker = _checker.Checker(root)
        status = _checker.validate_coexistence_gate(checker)
        return status, {issue.code for issue in checker.issues}


def _prepare_extension_root(root: Path) -> None:
    (root / "scripts").mkdir(parents=True)
    shutil.copy2(SCRIPTS / "run_chrome_probe.py", root / "scripts" / "run_chrome_probe.py")
    shutil.copytree(ROOT / "extension" / "probes", root / "extension" / "probes")
    (root / "artifacts" / "p0-extension").mkdir(parents=True)


def _extension_report(root: Path) -> dict[str, Any]:
    runner_hash = hashlib.sha256((root / "scripts" / "run_chrome_probe.py").read_bytes()).hexdigest()
    source_hash = _checker.extension_tree_sha256(root / "extension" / "probes")
    live: dict[str, Any] = {
        "requested": True,
        "required": True,
        "status": "live_passed",
        "binding_status": "observed",
        "binding_nonce_observed": True,
        "binding_source_tree_hash_observed": True,
        "binding_nonce_matches": True,
        "binding_source_tree_hash_matches": True,
        "launched_by_probe": True,
        "extension_loaded": True,
        "extension_identity_passed": True,
        "extension_build_binding_passed": True,
        "fixture_identity_passed": True,
        "debugger_command_passed": True,
        "debugger_event_received": True,
        "tab_group_created": True,
        "native_messaging_passed": True,
        "chrome_mediated_native_messaging": True,
        "debugger_cleanup_passed": True,
        "cleanup_passed": True,
        "screenshot_captured": True,
        "extension_unload_passed": True,
        "screenshots": [{"captured": True}],
        "handshake_transcript": ["hello_accepted", "probe_accepted"],
        "permission_prompts": {"status": "not_requested", "required_manual_review": False},
        "operator_assisted": False,
        "load_method": "cdp_extensions_load_unpacked",
        "load_extension_flag_used": False,
        "browser_target_cdp": True,
        "developer_private_used": False,
        "extensions_ui_dom_access": False,
        "extension_load_evidence": {
            "status": "passed",
            "method": "cdp_extensions_load_unpacked",
            "browser_target_cdp": True,
            "load_command_passed": True,
            "returned_id_matches_expected": True,
            "inventory_checked": True,
            "inventory_identity_passed": True,
            "inventory_path_matches": True,
            "inventory_enabled": True,
        },
        "extension_unload_evidence": {
            "status": "passed",
            "uninstall_command_passed": True,
            "inventory_checked": True,
            "absent_after_uninstall": True,
        },
        "runner_sha256": runner_hash,
        "source_extension_tree_sha256": source_hash,
        "staged_extension_tree_sha256": "c" * 64,
    }
    return {
        "probe": "P0-T2",
        "mode": "headed",
        "status": "live_passed",
        "handshake_transcript": ["hello_accepted", "probe_accepted"],
        "live": live,
        "safety": {
            "default_chrome_launch": False,
            "default_profile_mutation": False,
            "raw_ids_logged": False,
            "secrets_logged": False,
            "fixture_only_mutation": True,
        },
    }


def _write_extension_report(root: Path, report: dict[str, Any]) -> None:
    (root / "artifacts" / "p0-extension" / "report.json").write_text(
        json.dumps(report), encoding="utf-8"
    )


def _release_existing_chrome_report() -> dict[str, Any]:
    report: dict[str, Any] = {
        "schema_version": 1,
        "phase": 0,
        "kind": "existing-chrome-coexistence",
        "evidence_mode": "live",
        "status": "live_passed",
        "live": {"executed": True, "status": "live_passed"},
        "enrollment": _enrollment(),
        "execution_policy": {
            "attached": True,
            "browser_launch": False,
            "browser_download": False,
            "cdp_url_used": False,
        },
        "scenarios": [{"name": name, "status": "live_passed"} for name in REQUIRED_SCENARIOS],
        "safety": {counter: 0 for counter in SAFETY_COUNTERS},
    }
    return _envelope.envelope(
        report,
        kind="existing-chrome-coexistence",
        command=["tests/rollout/test_phase0_gate_contracts.py"],
    )


class Phase0GateContractTests(unittest.TestCase):
    def test_coexistence_requires_exact_scenario_list(self) -> None:
        self.assertEqual(tuple(_chrome.REQUIRED_LIVE_SCENARIOS), REQUIRED_SCENARIOS)
        self.assertEqual(_checker.REQUIRED_COEXISTENCE_SCENARIOS, set(REQUIRED_SCENARIOS))
        cases = {
            "exact": list(REQUIRED_SCENARIOS),
            "duplicate": list(REQUIRED_SCENARIOS) + [REQUIRED_SCENARIOS[0]],
            "missing": list(REQUIRED_SCENARIOS[:-1]),
            "extra": list(REQUIRED_SCENARIOS) + ["unapproved-scenario"],
            "skipped": list(REQUIRED_SCENARIOS),
        }
        for name, scenario_names in cases.items():
            with self.subTest(case=name):
                report = _coexistence_report(scenario_names=scenario_names)
                if name == "skipped":
                    report["scenarios"][0]["status"] = "skipped"
                status, _ = _validate_coexistence(report)
                if name == "exact":
                    self.assertEqual(status, "passed")
                else:
                    self.assertNotEqual(status, "passed")

    def test_coexistence_requires_zero_safety_counters(self) -> None:
        for counter in SAFETY_COUNTERS:
            with self.subTest(counter=counter):
                safety = {name: 0 for name in SAFETY_COUNTERS}
                safety[counter] = 1
                status, _ = _validate_coexistence(_coexistence_report(safety=safety))
                self.assertNotEqual(status, "passed")

    def test_coexistence_requires_profile_host_and_extension_enrollment(self) -> None:
        for component in ("profile", "host", "extension"):
            with self.subTest(component=component):
                enrollment = _enrollment()
                del enrollment[component]
                status, _ = _validate_coexistence(_coexistence_report(enrollment=enrollment))
                self.assertNotEqual(status, "passed")

                enrollment = _enrollment()
                enrollment[component]["enrolled"] = False
                status, _ = _validate_coexistence(_coexistence_report(enrollment=enrollment))
                self.assertNotEqual(status, "passed")

    def test_release_gate_requires_profile_enrollment(self) -> None:
        report = _release_existing_chrome_report()
        self.assertEqual(_release.validate_existing_chrome(report, require_live=True), [])
        del report["enrollment"]["profile"]
        self.assertTrue(_release.validate_existing_chrome(report, require_live=True))

    def test_release_gate_requires_zero_safety_counters(self) -> None:
        for counter in SAFETY_COUNTERS:
            with self.subTest(counter=counter):
                report = _release_existing_chrome_report()
                report["safety"][counter] = 1
                self.assertTrue(_release.validate_existing_chrome(report, require_live=True))

    def test_extension_gate_requires_observed_binding_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _prepare_extension_root(root)
            valid = _extension_report(root)
            _write_extension_report(root, valid)
            self.assertEqual(_checker.validate_extension_gate(_checker.Checker(root)), "passed")

            for field, invalid in (
                ("binding_status", "reported"),
                ("binding_nonce_observed", False),
                ("binding_source_tree_hash_observed", False),
            ):
                with self.subTest(field=field):
                    candidate = copy.deepcopy(valid)
                    candidate["live"][field] = invalid
                    _write_extension_report(root, candidate)
                    checker = _checker.Checker(root)
                    self.assertNotEqual(_checker.validate_extension_gate(checker), "passed")

    def test_real_p0_t2_artifact_is_accepted_only_when_observed(self) -> None:
        path = ROOT / "artifacts" / "p0-extension" / "report.json"
        self.assertTrue(path.is_file(), "the real P0-T2 artifact is required for this contract")
        report = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(_checker.validate_extension_gate(_checker.Checker(ROOT)), "passed")
        self.assertEqual(report["live"]["binding_status"], "observed")
        self.assertIs(report["live"]["binding_nonce_observed"], True)
        self.assertIs(report["live"]["binding_source_tree_hash_observed"], True)

        for field, invalid in (
            ("binding_status", "reported"),
            ("binding_nonce_observed", False),
            ("binding_source_tree_hash_observed", False),
        ):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                _prepare_extension_root(root)
                candidate = copy.deepcopy(report)
                candidate["live"][field] = invalid
                _write_extension_report(root, candidate)
                checker = _checker.Checker(root)
                self.assertNotEqual(_checker.validate_extension_gate(checker), "passed")


if __name__ == "__main__":
    unittest.main()
