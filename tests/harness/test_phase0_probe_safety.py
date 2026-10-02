"""Executable failure-path checks for the Phase 0 installation probe."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
SCRIPT = ROOT / "scripts" / "run_install_drill.py"
_spec = importlib.util.spec_from_file_location("phase0_install_drill", SCRIPT)
if _spec is None or _spec.loader is None:
    raise RuntimeError("could not load installation drill")
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)


class InstallationProbeSafetyTests(unittest.TestCase):
    extension_id = "a" * 32

    def test_install_is_owned_and_already_installed_is_not_a_new_pass(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifact_dir = root / "artifacts"
            registration = root / "native-host.json"
            first = _module.install_registration(registration, self.extension_id, artifact_dir)
            self.assertEqual(first["status"], "installed")
            second = _module.install_registration(registration, self.extension_id, artifact_dir)
            self.assertEqual(second["status"], "already_installed")
            self.assertFalse(second["mutated"])
            rollback = _module.rollback_registration(registration, self.extension_id, artifact_dir)
            self.assertEqual(rollback["status"], "rolled_back")
            self.assertFalse(registration.exists())

    def test_rollback_refuses_a_changed_registration(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifact_dir = root / "artifacts"
            registration = root / "native-host.json"
            self.assertEqual(
                _module.install_registration(registration, self.extension_id, artifact_dir)["status"],
                "installed",
            )
            registration.write_text("{}\n", encoding="utf-8")
            result = _module.rollback_registration(registration, self.extension_id, artifact_dir)
            self.assertEqual(result["status"], "refusing_changed_registration")
            self.assertTrue(registration.exists())

    def test_invalid_extension_ids_fail_before_registration(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaises(ValueError):
                _module.expected_manifest("not-a-chrome-id")
            self.assertFalse((root / "native-host.json").exists())


if __name__ == "__main__":
    unittest.main()
