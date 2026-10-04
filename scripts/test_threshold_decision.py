from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path
from typing import Any

try:
    import threshold_decision as decision
except ModuleNotFoundError:
    from scripts import threshold_decision as decision

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / "artifacts/p1-t7-threshold-decision.json"


class ThresholdDecisionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.record = decision.load_decision_record(ROOT, "artifacts/p1-t7-threshold-decision.json")

    def assert_rejected(self, record: dict[str, Any], **kwargs: Any) -> None:
        with self.assertRaises(decision.DecisionError):
            decision.validate_decision_record(record, **kwargs)

    def test_canonical_offline_record_passes_and_is_null(self) -> None:
        decision.validate_decision_record(self.record, mode="offline", root=ROOT)
        self.assertEqual(self.record["record_version"], "p1-t7-threshold-decision-v1")
        self.assertFalse(self.record["release_eligible"])
        for metric in self.record["metric_register"].values():
            self.assertIsNone(metric["value"])
            self.assertEqual(metric["status"], "not_measured_offline")
        for counter in self.record["safety_counters"].values():
            self.assertIsNone(counter["value"])
            self.assertEqual(counter["status"], "not_measured_offline")

    def test_strict_schema_rejects_missing_and_unexpected_fields(self) -> None:
        missing = copy.deepcopy(self.record)
        del missing["signoff"]
        self.assert_rejected(missing, mode="offline")

        extra = copy.deepcopy(self.record)
        extra["unreviewed_claim"] = False
        self.assert_rejected(extra, mode="offline")

    def test_register_requires_all_metrics_and_explicit_threshold_fields(self) -> None:
        missing_metric = copy.deepcopy(self.record)
        missing_metric["metric_register"].pop("fault_recovery_lag")
        self.assert_rejected(missing_metric, mode="offline")

        missing_budget = copy.deepcopy(self.record)
        del missing_budget["metric_register"]["end_to_end_first_action"]["regression_budget"]
        self.assert_rejected(missing_budget, mode="offline")

        missing_owner = copy.deepcopy(self.record)
        missing_owner["metric_register"]["batch_round_trips"]["owner"] = ""
        self.assert_rejected(missing_owner, mode="offline")

    def test_offline_live_claims_are_rejected(self) -> None:
        for metric in self.record["metric_register"].values():
            metric["evidence_mode"] = "live"
        self.record["evidence_mode"] = "live"
        self.record["evidence"]["mode"] = "live"
        self.assert_rejected(self.record, mode="live")

    def test_live_fixture_requires_production_evidence(self) -> None:
        live = self._live_record()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw_path = root / "artifacts" / "raw.jsonl"
            raw_path.parent.mkdir(parents=True)
            raw_path.write_text('{"sample": 1}\n', encoding="utf-8")
            decision.validate_decision_record(live, mode="live", root=root)

        forbidden = copy.deepcopy(live)
        forbidden["evidence"]["provenance"]["source_class"] = "disposable_cdp"
        self.assert_rejected(forbidden, mode="live")

        missing_raw = copy.deepcopy(live)
        missing_raw["evidence"]["raw_samples"] = None
        self.assert_rejected(missing_raw, mode="live")

    def test_live_evidence_requires_provenance_samples_ci_counts_and_redaction(self) -> None:
        live = self._live_record()
        for field in ("provenance", "raw_samples", "confidence_intervals", "valid_sample_counts", "redaction_status"):
            broken = copy.deepcopy(live)
            broken["evidence"][field] = None
            with self.subTest(field=field):
                self.assert_rejected(broken, mode="live")

    def test_nonzero_safety_and_unaccounted_chaos_faults_fail_closed(self) -> None:
        live = self._live_record()
        unsafe = copy.deepcopy(live)
        unsafe["safety_counters"]["cross_space_mutations"]["value"] = 1
        self.assert_rejected(unsafe, mode="live")

        missing_fault = copy.deepcopy(live)
        missing_fault["chaos"]["accounted_faults"].pop("chrome_restart")
        self.assert_rejected(missing_fault, mode="live")

        unaccounted = copy.deepcopy(live)
        unaccounted["chaos"]["unaccounted_faults"] = ["chrome_restart"]
        self.assert_rejected(unaccounted, mode="live")

    def test_threshold_changes_need_new_decision_id(self) -> None:
        changed = copy.deepcopy(self.record)
        changed["metric_register"]["end_to_end_first_action"]["limit"]["value"] = 900.0
        changed["metric_register"]["end_to_end_first_action"]["provisional_limits"][0]["value"] = 900.0
        self.assert_rejected(changed, mode="offline")

        changed_sample_policy = copy.deepcopy(self.record)
        changed_sample_policy["sample_policy"]["p95_min_valid_samples"] = 250
        self.assert_rejected(changed_sample_policy, mode="offline")

        same_id = copy.deepcopy(changed)
        same_id["threshold_change"] = {
            "changed": True,
            "previous_decision_id": self.record["decision_id"],
            "new_decision_id": self.record["decision_id"],
            "reason": "tightened first-action ceiling",
        }
        self.assert_rejected(same_id, mode="offline")

        new_id = copy.deepcopy(changed)
        new_id["decision_id"] = "p1-t7-2026-10-05-v2"
        new_id["threshold_change"] = {
            "changed": True,
            "previous_decision_id": self.record["decision_id"],
            "new_decision_id": new_id["decision_id"],
            "reason": "tightened first-action ceiling",
        }
        decision.validate_decision_record(
            new_id,
            mode="offline",
            previous_record=self.record,
        )

    def _live_record(self) -> dict[str, Any]:
        live = copy.deepcopy(self.record)
        live["evidence_mode"] = "live"
        live["evidence"] = {
            "mode": "live",
            "status": "measured",
            "provenance": {
                "source_class": "production_path",
                "path_id": "direct_existing_chrome",
                "run_id": "run-p1-t7-001",
                "timestamp": "2026-10-04T12:00:00Z",
                "command": ["production-harness", "--run", "p1-t7-001"],
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
            "raw_samples": {
                "files": ["artifacts/raw.jsonl"],
                "sha256": "c" * 64,
                "sample_count": 1000,
            },
            "confidence_intervals": {
                "method": "bootstrap",
                "confidence_level": 0.95,
                "metrics": {},
            },
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
        for metric_id, metric in live["metric_register"].items():
            metric["evidence_mode"] = "live"
            metric["status"] = "measured"
            if metric["limit"]["kind"] == "absolute_minimum":
                metric["value"] = metric["limit"]["value"] + 0.1
            else:
                metric["value"] = metric["limit"]["value"] / 2
            count = 1000 if metric["sample_requirement"] == "p99" else 200
            live["evidence"]["valid_sample_counts"][metric_id] = count
            live["evidence"]["confidence_intervals"]["metrics"][metric_id] = {
                "lower": 0.0,
                "upper": 1.0,
                "confidence_level": 0.95,
                "sample_count": count,
            }
            live["evidence"]["regression_deltas"][metric_id] = 0.0
        for counter in live["safety_counters"].values():
            counter["value"] = 0
            counter["status"] = "measured"
            counter["evidence_mode"] = "live"
        live["chaos"]["evidence_mode"] = "live"
        live["chaos"]["no_replay_assertion"] = True
        for entry in live["chaos"]["accounted_faults"].values():
            entry["status"] = "measured"
            entry["value"] = 1.0
            entry["evidence_mode"] = "live"
            entry["no_replay_assertion"] = True
        live["release_eligible"] = True
        return live


if __name__ == "__main__":
    unittest.main()
