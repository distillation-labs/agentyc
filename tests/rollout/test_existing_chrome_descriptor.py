from __future__ import annotations

import importlib.util
import sys
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


_chrome = load_script("rollout_existing_chrome", "run_existing_chrome.py")


class ExistingChromeDescriptorTests(unittest.TestCase):
    def descriptor(self) -> dict[str, Any]:
        return {
            "schema_version": 2,
            "kind": "existing-chrome-enrollment",
            "mode": "existing-chrome",
            "profile_scope": "existing_user_profile",
            "enrollment": {
                "profile": {"enrolled": True, "status": "bound", "binding_verified": True},
                "host": {"enrolled": True, "status": "connected", "origin_match_verified": True},
                "extension": {
                    "enrolled": True,
                    "status": "installed",
                    "identity_verified": True,
                    "host_origin_matches": True,
                    "distribution": "stable_unpacked",
                },
            },
            "browser": {
                "status": "already-running",
                "launch": False,
                "download": False,
                "cdp_url_used": False,
            },
            "safety": {"user_tab_preserved": True, "focus_theft": False},
        }

    def test_descriptor_requires_all_enrolled_components(self) -> None:
        self.assertEqual(_chrome._descriptor_errors(self.descriptor()), [])
        for component in ("profile", "host", "extension"):
            with self.subTest(component=component):
                descriptor = self.descriptor()
                del descriptor["enrollment"][component]
                self.assertTrue(_chrome._descriptor_errors(descriptor))

                descriptor = self.descriptor()
                descriptor["enrollment"][component]["enrolled"] = False
                self.assertTrue(_chrome._descriptor_errors(descriptor))

    def test_descriptor_rejects_ids_paths_and_debugger_endpoints(self) -> None:
        for field, value in (
            ("cdp_url", "ws://127.0.0.1:9222/devtools/page/one"),
            ("profilePath", "/Users/test/Chrome/Profile"),
            ("extensionId", "a" * 32),
        ):
            with self.subTest(field=field):
                descriptor = self.descriptor()
                descriptor[field] = value
                self.assertTrue(_chrome._descriptor_errors(descriptor))

    def test_live_evidence_requires_exact_non_skipped_scenario_set(self) -> None:
        exact = [
            {"name": name, "status": "live_passed"}
            for name in _chrome.REQUIRED_LIVE_SCENARIOS
        ]
        cases = {
            "exact": exact,
            "duplicate": exact + [exact[0]],
            "missing": exact[:-1],
            "extra": exact + [{"name": "unapproved-scenario", "status": "live_passed"}],
            "skipped": [{**item} for item in exact],
        }
        cases["skipped"][0]["status"] = "skipped"
        for name, scenarios in cases.items():
            with self.subTest(case=name):
                descriptor = self.descriptor()
                descriptor["evidence"] = {
                    "executed": True,
                    "status": "live_passed",
                    "scenarios": scenarios,
                }
                errors = _chrome._descriptor_errors(descriptor)
                if name == "exact":
                    self.assertEqual(errors, [])
                else:
                    self.assertTrue(errors)

    def test_descriptor_only_is_not_executed_evidence(self) -> None:
        descriptor = self.descriptor()
        loaded = _chrome.redact_for_persistence(
            {
                "executed": False,
                "evidence_status": "descriptor_only",
                "enrollment": descriptor["enrollment"],
            }
        )
        self.assertFalse(loaded["executed"])
        self.assertEqual(loaded["evidence_status"], "descriptor_only")


if __name__ == "__main__":
    unittest.main()
