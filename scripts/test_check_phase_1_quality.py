from __future__ import annotations

import copy
import importlib.util
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = Path(__file__).with_name("check_phase_1_quality.py")
SPEC = importlib.util.spec_from_file_location("check_phase_1_quality", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("could not load check_phase_1_quality.py")
checker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checker)

ARTIFACT = ROOT / "artifacts/p1-quality-review.md"


class Phase1QualityCheckerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = ARTIFACT.read_text(encoding="utf-8")
        self.spec = checker.parse_artifact_text(self.text)

    def assert_rejected(self, mutation: Any) -> None:
        candidate = copy.deepcopy(self.spec)
        mutation(candidate)
        with self.assertRaises(checker.QualityError):
            checker.validate_spec(ROOT, candidate, self.text, execute_commands=False)

    def test_valid_repository_fixture_passes_schema_and_evidence(self) -> None:
        checker.validate_spec(ROOT, self.spec, self.text, execute_commands=False)

    def test_allowlisted_executable_evidence_is_static_only(self) -> None:
        checker.validate_spec(ROOT, self.spec, self.text, execute_commands=False)
        commands = self.spec["commands"]
        self.assertEqual({command["id"] for command in commands}, set(checker.COMMANDS_BY_ID))
        self.assertTrue(all(command["network"] == "forbidden" for command in commands))
        self.assertTrue(all(command["browser_launch"] is False for command in commands))
        self.assertTrue(all(command["browser_attach"] is False for command in commands))

    def test_executable_evidence_commands_pass_without_live_launches(self) -> None:
        checker.validate(ROOT, execute_commands=True)

    def test_profile_disclosure_contract_schema(self) -> None:
        self.assertEqual(
            self.spec["profile_sharing"]["pre_create_disclosure"]["phase"],
            "before_ledger_commit",
        )
        self.assertFalse(self.spec["profile_sharing"]["isolation_claim"])
        self.assertFalse(self.spec["profile_sharing"]["pre_create_disclosure"]["isolation_claim"])
        self.assert_rejected(
            lambda candidate: candidate["profile_sharing"]["pre_create_disclosure"].update(
                {"phase": "after_ledger_commit"}
            )
        )

    def test_public_raw_id_boundary_requires_controls(self) -> None:
        self.assert_rejected(
            lambda candidate: candidate["public_identity"].update(
                {"public_raw_ids_forbidden": False}
            )
        )
        self.assert_rejected(
            lambda candidate: candidate["public_identity"]["legacy_allowlist"].pop()
        )

    def test_mutation_inventory_requires_controls(self) -> None:
        mutation = self.spec["mutation_inventory"]["mutations"][0]
        self.assertTrue(mutation["lease"]["required"])
        self.assertTrue(mutation["epoch"]["required"])
        self.assertTrue(mutation["policy"]["required"])
        self.assertGreaterEqual(len(mutation["test_refs"]), 2)
        self.assert_rejected(
            lambda candidate: candidate["mutation_inventory"]["mutations"][0]["lease"].update(
                {"required": False}
            )
        )
        self.assert_rejected(
            lambda candidate: candidate["mutation_inventory"]["mutations"][0].update(
                {"test_refs": []}
            )
        )

    def test_epoch_names_are_non_interchangeable(self) -> None:
        names = [epoch["name"] for epoch in self.spec["runtime_epochs"]["epochs"]]
        self.assertEqual(set(names), set(checker.REQUIRED_EPOCHS))
        self.assertTrue(self.spec["runtime_epochs"]["same_value_is_not_substitution"])
        self.assert_rejected(
            lambda candidate: candidate["runtime_epochs"]["epochs"][0].update(
                {"distinct_from": []}
            )
        )
        self.assert_rejected(
            lambda candidate: candidate["runtime_epochs"]["epochs"][1].update(
                {"name": "broker_epoch"}
            )
        )

    def test_missing_fence_ack_fails_closed(self) -> None:
        missing_ack = self.spec["fence"]["missing_ack"]
        self.assertEqual(missing_ack["result_state"], "fence_pending")
        self.assertFalse(missing_ack["admit_mutations"])
        self.assertTrue(missing_ack["fail_closed"])
        self.assert_rejected(
            lambda candidate: candidate["fence"]["missing_ack"].update(
                {"admit_mutations": True}
            )
        )
        self.assert_rejected(
            lambda candidate: candidate["fence"]["transition"].update(
                {"durable_record": "memory_only"}
            )
        )

    def test_security_privacy_distribution_and_rollback_constraints(self) -> None:
        constraints = self.spec["constraints"]
        self.assertEqual(constraints["security"]["remote_tcp"], "disabled_by_default")
        self.assertFalse(constraints["privacy"]["ledger_secrets"])
        self.assertFalse(constraints["distribution"]["unpacked_is_production_proof"])
        self.assertTrue(constraints["rollback"]["user_tabs_preserved"])
        self.assert_rejected(
            lambda candidate: candidate["constraints"]["rollback"].update(
                {"user_tabs_preserved": False}
            )
        )
        self.assert_rejected(
            lambda candidate: candidate["constraints"]["privacy"].update(
                {"ledger_page_bodies": True}
            )
        )

    def test_rollback_constraints_are_retention_only(self) -> None:
        rollback = self.spec["constraints"]["rollback"]
        self.assertEqual(rollback["new_mutations"], "disabled")
        self.assertTrue(rollback["pages_retained"])
        self.assertFalse(rollback["chrome_process_killed"])
        self.assertFalse(rollback["implicit_cleanup"])

    def test_residual_live_evidence_limits_are_explicit(self) -> None:
        boundary = self.spec["evidence_boundary"]
        self.assertEqual(boundary["live_behavior"], "not_run")
        self.assertEqual(boundary["production_path"], "not_proven")
        self.assertFalse(boundary["release_eligible"])
        self.assertEqual(
            {item["id"] for item in boundary["residuals"]},
            checker.REQUIRED_RESIDUALS,
        )
        self.assert_rejected(lambda candidate: candidate.update({"live_claims": True}))
        self.assert_rejected(
            lambda candidate: candidate["evidence_boundary"]["residuals"][0].update(
                {"status": "proven"}
            )
        )

    def test_command_manifest_rejects_network_or_arbitrary_commands(self) -> None:
        self.assert_rejected(
            lambda candidate: candidate["commands"][0].update(
                {"argv": ["curl", "https://example.invalid"]}
            )
        )
        self.assert_rejected(
            lambda candidate: candidate["commands"].pop()
        )


if __name__ == "__main__":
    unittest.main()
