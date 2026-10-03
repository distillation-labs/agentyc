"""Focused contract tests for the P0-T7 lifecycle executor."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
SCRIPT = ROOT / "scripts" / "run_install_lifecycle.py"
spec = importlib.util.spec_from_file_location("phase0_install_lifecycle", SCRIPT)
if spec is None or spec.loader is None:
    raise RuntimeError("could not load lifecycle executor")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

DRILL_SCRIPT = ROOT / "scripts" / "run_install_drill.py"
drill_spec = importlib.util.spec_from_file_location("phase0_install_drill", DRILL_SCRIPT)
if drill_spec is None or drill_spec.loader is None:
    raise RuntimeError("could not load install drill")
drill_module = importlib.util.module_from_spec(drill_spec)
drill_spec.loader.exec_module(drill_module)
validate_lifecycle_record = drill_module.validate_lifecycle_record


class InstallLifecycleContractTests(unittest.TestCase):
    def test_offline_record_is_explicitly_not_gateable(self) -> None:
        record = module._offline_record()
        self.assertEqual(record["evidence_mode"], "offline")
        self.assertEqual(record["lifecycle"]["update"], "not_measured_offline")
        self.assertEqual(validate_lifecycle_record(record, require_live=False), [])
        self.assertNotEqual(validate_lifecycle_record(record, require_live=True), [])

    def test_live_shape_requires_every_phase_and_rollback_safety(self) -> None:
        record = module._live_record_base()
        record["status"] = "live_passed"
        record["lifecycle"].update(
            {
                "status": "passed",
                "install": "installed",
                "update": "passed",
                "uninstall": "passed",
                "downgrade": "passed",
                "rollback": "rolled_back",
            }
        )
        record["rollback_safety"].update(
            {
                "new_mutations": "paused",
                "pages_retained": True,
                "user_tabs_preserved": True,
                "chrome_process_terminated": False,
                "global_close_used": False,
                "incompatible_ledger_refused": True,
                "kill_switch": {"status": "armed_and_verified", "armed": True, "verified": True},
            }
        )
        self.assertEqual(validate_lifecycle_record(record, require_live=True), [])

    def test_ledger_refuses_another_extension_identity(self) -> None:
        staged = ROOT / "extension" / "probes"
        ledger = module.LifecycleLedger("a" * 32, staged, "0.0.1")
        self.assertTrue(ledger.refuse_incompatible())

    def test_version_order_is_strict_and_bounded(self) -> None:
        self.assertEqual(module._next_version("0.0.1"), "0.0.2")
        self.assertEqual(module._default_older_version("0.0.1"), "0.0.0")
        self.assertLess(module._version("0.0.0"), module._version("0.0.1"))
        with self.assertRaises(ValueError):
            module._version("0.01.0")
        with self.assertRaises(ValueError):
            module._version("1.2.3.4.5")

    def test_non_macos_live_execution_fails_closed_before_launch(self) -> None:
        with patch.object(module.platform, "system", return_value="Linux"):
            record = module.execute_lifecycle()
        self.assertEqual(record["failure_code"], "unsupported_platform")
        self.assertNotEqual(record["lifecycle"]["update"], "passed")


if __name__ == "__main__":
    unittest.main()
