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


_install = load_script("rollout_install_drill", "run_install_drill.py")


class InstallationRecordTests(unittest.TestCase):
    def test_offline_record_is_complete_but_not_live(self) -> None:
        record = {
            "evidence_mode": "offline",
            "lifecycle": _install.offline_lifecycle_record(),
            "rollback_safety": _install.offline_rollback_safety(),
        }
        self.assertEqual(_install.validate_lifecycle_record(record, require_live=False), [])
        self.assertTrue(_install.validate_lifecycle_record(record, require_live=True))

    def test_live_record_requires_kill_switch_and_all_lifecycle_phases(self) -> None:
        record = {
            "evidence_mode": "live",
            "lifecycle": {
                "schema_version": 1,
                "evidence_mode": "live",
                "install": "installed",
                "update": "passed",
                "uninstall": "passed",
                "downgrade": "passed",
                "rollback": "rolled_back",
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
        self.assertEqual(_install.validate_lifecycle_record(record, require_live=True), [])
        broken = copy.deepcopy(record)
        broken["lifecycle"]["downgrade"] = "not_measured_offline"
        self.assertTrue(_install.validate_lifecycle_record(broken, require_live=True))
        broken = copy.deepcopy(record)
        broken["rollback_safety"]["kill_switch"]["verified"] = False
        self.assertTrue(_install.validate_lifecycle_record(broken, require_live=True))

    def test_preflight_exposes_non_green_lifecycle_and_rollback_shapes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            artifact_dir = Path(temporary)
            report = _install.build_preflight(
                artifact_dir=artifact_dir,
                profile_dir=None,
                extension_id=None,
                chrome_path=None,
                registration_path=artifact_dir / "native-host.json",
                registration_scope="artifact-test",
            )
        self.assertFalse(report["release_eligible"])
        self.assertEqual(report["lifecycle"]["status"], "not_gateable_offline")
        self.assertEqual(report["rollback_safety"]["kill_switch"]["status"], "not_measured_offline")
        self.assertIsNone(report["rollback_safety"]["pages_retained"])

    def test_install_results_prove_no_browser_state_mutation(self) -> None:
        result = _install._install_result("rolled_back", mutated=True)
        self.assertFalse(result["user_tabs_or_chrome_changed"])
        self.assertFalse(result["chrome_process_terminated"])
        self.assertFalse(result["global_close_used"])


if __name__ == "__main__":
    unittest.main()
