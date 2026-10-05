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
        "profile": {
            "enrolled": True,
            "observed": True,
            "current_run": True,
            "source": "existing_chrome_current_run",
            "status": "bound",
        },
        "host": {
            "enrolled": True,
            "observed": True,
            "current_run": True,
            "source": "existing_chrome_current_run",
            "status": "connected",
        },
        "extension": {
            "enrolled": True,
            "observed": True,
            "current_run": True,
            "source": "existing_chrome_current_run",
            "status": "installed",
        },
    }


def _coexistence_report(
    *,
    scenario_names: list[str] | None = None,
    safety: dict[str, Any] | None = None,
    enrollment: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    names = scenario_names if scenario_names is not None else list(REQUIRED_SCENARIOS)
    enrollment_evidence = copy.deepcopy(enrollment or _enrollment())
    receipt_prefixes = (
        "browser.inventory.initial.",
        "browser.inventory.isolation.",
        "browser.inventory.post-action.",
        "browser.inventory.takeover.",
        "browser.inventory.return-control.",
        "browser.inventory.cleanup.",
        "browser.inventory.checkpoint-worker-restart-recovery.",
        "browser.inventory.checkpoint-host-restart-recovery.",
        "browser.inventory.checkpoint-chrome-restart-recovery.",
        "browser.inventory.checkpoint-extension-update-recovery.",
    )
    receipt_operations = [
        f"{receipt_prefixes[index]}research.1"
        if index < len(receipt_prefixes)
        else f"browser.inventory.extra.{index}"
        for index, _name in enumerate(names)
    ]
    receipts = [
        {
            "operation": operation,
            "source": "browser",
            "current_run": True,
            "observed": True,
            "browser_observed": True,
        }
        for operation in receipt_operations
    ]
    transport_receipts = [
        {
            "operation": "action.execute.cross_space_rejection",
            "current_run": True,
            "observed": True,
            "ok": False,
            "expected_rejection": True,
            "failure_code": "space_forbidden",
        },
        {
            "operation": "action.execute.stale_lease_rejection",
            "current_run": True,
            "observed": True,
            "ok": False,
            "expected_rejection": True,
            "failure_code": "stale_lease",
        },
    ]
    report = {
        "phase": 0,
        "kind": "existing-chrome-coexistence",
        "evidence_mode": "live",
        "status": "live_passed",
        "current_run": True,
        "release_eligible": True,
        "mode": "headed",
        "spaces": 2,
        "agents": 2,
        "enrollment": enrollment_evidence,
        "execution_policy": {
            "attached": True,
            "current_run": True,
            "browser_launch": False,
            "browser_download": False,
            "cdp_url_used": False,
        },
        "live": {
            "requested": True,
            "required": True,
            "executed": True,
            "current_run": True,
            "status": "live_passed",
            "evidence_status": "live_passed",
            "provenance": "existing_chrome_current_run",
            "descriptor_policy": "descriptors_not_accepted_as_live_evidence",
            "operator_claims_used": False,
            "host_probe_used": False,
            "browser_observed": True,
            "profile_scope": "existing_user_profile",
            "browser": {
                "observed": True,
                "current_run": True,
                "attached": True,
                "launch": False,
                "download": False,
                "cdp_url_used": False,
            },
            "enrollment": copy.deepcopy(enrollment_evidence),
            "receipts": receipts,
            "transport_receipts": transport_receipts,
        },
        "scenarios": [
            {
                "name": name,
                "status": "live_passed",
                "observation": {
                    "source": "browser_current_run",
                    "observed": True,
                    "current_run": True,
                    "browser_observed": True,
                    "receipt_refs": [receipt_operations[index]],
                },
            }
            for index, name in enumerate(names)
        ],
        "safety": safety
        or {
            "user_tab_closes": 0,
            "focus_theft": 0,
            "cross_space_mutations": 0,
            "stale_agent_mutations": 0,
            "measurement_status": "measured_live",
            "current_run": True,
        },
    }
    return _envelope.envelope(
        report,
        kind="existing-chrome-coexistence",
        command=["tests/rollout/test_phase0_gate_contracts.py"],
    )


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
    receipts = [
        {
            "operation": f"browser.scenario.{index}",
            "source": "browser",
            "current_run": True,
            "observed": True,
            "browser_observed": True,
        }
        for index, _name in enumerate(REQUIRED_SCENARIOS)
    ]
    enrollment = _enrollment()
    report: dict[str, Any] = {
        "schema_version": 1,
        "phase": 0,
        "kind": "existing-chrome-coexistence",
        "evidence_mode": "live",
        "status": "live_passed",
        "current_run": True,
        "release_eligible": True,
        "mode": "headed",
        "spaces": 2,
        "agents": 2,
        "live": {
            "requested": True,
            "required": True,
            "executed": True,
            "current_run": True,
            "status": "live_passed",
            "evidence_status": "live_passed",
            "provenance": "existing_chrome_current_run",
            "descriptor_policy": "descriptors_not_accepted_as_live_evidence",
            "operator_claims_used": False,
            "host_probe_used": False,
            "browser_observed": True,
            "profile_scope": "existing_user_profile",
            "browser": {
                "observed": True,
                "current_run": True,
                "attached": True,
                "launch": False,
                "download": False,
                "cdp_url_used": False,
            },
            "enrollment": copy.deepcopy(enrollment),
            "receipts": receipts,
        },
        "enrollment": enrollment,
        "execution_policy": {
            "attached": True,
            "current_run": True,
            "browser_launch": False,
            "browser_download": False,
            "cdp_url_used": False,
        },
        "scenarios": [
            {
                "name": name,
                "status": "live_passed",
                "observation": {
                    "source": "browser_current_run",
                    "observed": True,
                    "current_run": True,
                    "browser_observed": True,
                    "receipt_refs": [f"browser.scenario.{index}"],
                },
            }
            for index, name in enumerate(REQUIRED_SCENARIOS)
        ],
        "safety": {
            **{counter: 0 for counter in SAFETY_COUNTERS},
            "measurement_status": "measured_live",
            "current_run": True,
        },
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
            "reordered": [REQUIRED_SCENARIOS[1], REQUIRED_SCENARIOS[0], *REQUIRED_SCENARIOS[2:]],
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

    def test_coexistence_receipts_are_unique_current_browser_observations(self) -> None:
        cases = {
            "missing_refs": lambda report: report["scenarios"][0]["observation"].pop("receipt_refs"),
            "unknown_ref": lambda report: report["scenarios"][0]["observation"].update({"receipt_refs": ["browser.missing"]}),
            "duplicate_ref": lambda report: report["scenarios"][1]["observation"].update(
                {"receipt_refs": report["scenarios"][0]["observation"]["receipt_refs"]}
            ),
            "duplicate_receipt": lambda report: report["live"]["receipts"].append(
                copy.deepcopy(report["live"]["receipts"][0])
            ),
            "direct_cli": lambda report: report["live"]["receipts"][0].update({"source": "direct_cli"}),
            "host_probe": lambda report: report["live"]["receipts"][0].update({"source": "host_probe"}),
        }
        for name, mutate in cases.items():
            with self.subTest(case=name):
                report = _coexistence_report()
                mutate(report)
                status, _ = _validate_coexistence(report)
                self.assertNotEqual(status, "passed")

    def test_coexistence_rejects_stale_non_current_or_ineligible_reports(self) -> None:
        cases = {
            "stale": lambda report: report.update({"timestamp": "2020-01-01T00:00:00Z"}),
            "stale_provenance": lambda report: report["provenance"].update({"timestamp": "2020-01-01T00:00:00Z"}),
            "not_current": lambda report: report.update({"current_run": False}),
            "not_release_eligible": lambda report: report.update({"release_eligible": False}),
        }
        for name, mutate in cases.items():
            with self.subTest(case=name):
                report = _coexistence_report()
                mutate(report)
                status, _ = _validate_coexistence(report)
                self.assertNotEqual(status, "passed")

    def test_coexistence_rejects_attached_cdp_policy_contradictions(self) -> None:
        cases = {
            "execution_not_attached": lambda report: report["execution_policy"].update({"attached": False}),
            "execution_cdp_url": lambda report: report["execution_policy"].update({"cdp_url_used": True}),
            "browser_not_attached": lambda report: report["live"]["browser"].update({"attached": False}),
            "browser_cdp_url": lambda report: report["live"]["browser"].update({"cdp_url_used": True}),
        }
        for name, mutate in cases.items():
            with self.subTest(case=name):
                report = _coexistence_report()
                mutate(report)
                status, _ = _validate_coexistence(report)
                self.assertNotEqual(status, "passed")

    def test_coexistence_rejects_offline_descriptor_and_operator_claims(self) -> None:
        cases = {
            "offline": {"evidence_mode": "offline", "status": "offline_passed"},
            "descriptor": {
                "evidence_mode": "live",
                "live": {
                    "executed": False,
                    "provenance": "descriptor",
                    "operator_claims_used": False,
                    "host_probe_used": False,
                    "browser_observed": False,
                },
            },
            "operator": {"live": {"operator_claims_used": True}},
        }
        for name, updates in cases.items():
            with self.subTest(case=name):
                report = _coexistence_report()
                for key, value in updates.items():
                    if key == "live":
                        report["live"].update(value)
                    else:
                        report[key] = value
                status, _ = _validate_coexistence(report)
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

    def test_release_gate_requires_profile_host_and_extension_enrollment(self) -> None:
        report = _release_existing_chrome_report()
        self.assertEqual(_release.validate_existing_chrome(report, require_live=True), [])
        for component in ("profile", "host", "extension"):
            with self.subTest(component=component):
                candidate = copy.deepcopy(report)
                del candidate["enrollment"][component]
                self.assertTrue(_release.validate_existing_chrome(candidate, require_live=True))

    def test_release_gate_requires_exact_current_scenarios_and_receipts(self) -> None:
        scenarios = copy.deepcopy(_release_existing_chrome_report()["scenarios"])
        cases = {
            "duplicate": scenarios + [copy.deepcopy(scenarios[0])],
            "missing": scenarios[:-1],
            "extra": scenarios + [{"name": "unapproved-scenario", "status": "live_passed"}],
            "reordered": [scenarios[1], scenarios[0], *scenarios[2:]],
        }
        for name, candidate_scenarios in cases.items():
            with self.subTest(case=name):
                report = _release_existing_chrome_report()
                report["scenarios"] = candidate_scenarios
                self.assertTrue(_release.validate_existing_chrome(report, require_live=True))

        for name, mutate in {
            "unknown_ref": lambda report: report["scenarios"][0]["observation"].update({"receipt_refs": ["browser.missing"]}),
            "duplicate_ref": lambda report: report["scenarios"][1]["observation"].update(
                {"receipt_refs": report["scenarios"][0]["observation"]["receipt_refs"]}
            ),
            "duplicate_receipt": lambda report: report["live"]["receipts"].append(
                copy.deepcopy(report["live"]["receipts"][0])
            ),
            "direct_cli": lambda report: report["live"]["receipts"][0].update({"source": "direct_cli"}),
            "host_probe": lambda report: report["live"]["receipts"][0].update({"source": "host_probe"}),
        }.items():
            with self.subTest(case=name):
                report = _release_existing_chrome_report()
                mutate(report)
                self.assertTrue(_release.validate_existing_chrome(report, require_live=True))

    def test_release_gate_rejects_stale_non_current_ineligible_and_missing_live_evidence(self) -> None:
        cases = {
            "stale": lambda report: report.update({"timestamp": "2020-01-01T00:00:00Z"}),
            "stale_provenance": lambda report: report["provenance"].update({"timestamp": "2020-01-01T00:00:00Z"}),
            "not_current": lambda report: report.update({"current_run": False}),
            "not_release_eligible": lambda report: report.update({"release_eligible": False}),
            "offline": lambda report: report.update({"evidence_mode": "offline", "status": "offline_passed"}),
            "missing_live_evidence": lambda report: report["live"].update({"browser_observed": False}),
        }
        for name, mutate in cases.items():
            with self.subTest(case=name):
                report = _release_existing_chrome_report()
                mutate(report)
                self.assertTrue(_release.validate_existing_chrome(report, require_live=True))

    def test_release_gate_rejects_attached_cdp_policy_contradictions(self) -> None:
        cases = {
            "execution_not_attached": lambda report: report["execution_policy"].update({"attached": False}),
            "execution_cdp_url": lambda report: report["execution_policy"].update({"cdp_url_used": True}),
            "browser_not_attached": lambda report: report["live"]["browser"].update({"attached": False}),
            "browser_cdp_url": lambda report: report["live"]["browser"].update({"cdp_url_used": True}),
        }
        for name, mutate in cases.items():
            with self.subTest(case=name):
                report = _release_existing_chrome_report()
                mutate(report)
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

    def test_phase_zero_envelopes_are_scoped_to_phase_zero_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            p0_directory = root / "artifacts" / "p0-test"
            p5_directory = root / "artifacts" / "p5-test"
            p0_directory.mkdir(parents=True)
            p5_directory.mkdir(parents=True)
            (p0_directory / "report.json").write_text("{}", encoding="utf-8")
            (p5_directory / "report.json").write_text("{}", encoding="utf-8")

            checker = _checker.Checker(root)
            _checker.validate_artifact_envelope(checker)
            self.assertEqual(
                [issue.path for issue in checker.issues if issue.code == "artifact-envelope-missing"],
                ["artifacts/p0-test/report.json"],
            )

            report = _envelope.envelope({"phase": 0, "status": "passed"}, kind="p0-test")
            (p0_directory / "report.json").write_text(
                json.dumps(report), encoding="utf-8"
            )
            checker = _checker.Checker(root)
            _checker.validate_artifact_envelope(checker)
            self.assertEqual(checker.issues, [])

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
