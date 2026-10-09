from __future__ import annotations

import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("check_extension_permissions.py")
SPEC = importlib.util.spec_from_file_location("check_extension_permissions", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("could not load check_extension_permissions.py")
checker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checker)


class ExtensionPermissionCheckerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = checker.load_manifest(checker.ROOT, None)

    def assert_rejected(self, manifest: dict) -> None:
        with self.assertRaises(checker.PermissionError):
            checker.validate_manifest(manifest)

    def test_current_manifest_passes_direct_validation(self) -> None:
        checker.validate_manifest(self.manifest)

    def test_tab_groups_are_required_and_debugger_is_not_allowed(self) -> None:
        missing = copy.deepcopy(self.manifest)
        missing["permissions"].remove("tabGroups")
        self.assert_rejected(missing)

        debugger = copy.deepcopy(self.manifest)
        debugger["permissions"].append("debugger")
        self.assert_rejected(debugger)

    def test_host_scripting_and_optional_permission_keys_are_rejected(self) -> None:
        for key in ("optional_permissions", "host_permissions", "optional_host_permissions"):
            candidate = copy.deepcopy(self.manifest)
            candidate[key] = []
            self.assert_rejected(candidate)

        scripting = copy.deepcopy(self.manifest)
        scripting["permissions"].append("scripting")
        self.assert_rejected(scripting)

    def test_incognito_and_main_world_are_rejected(self) -> None:
        incognito = copy.deepcopy(self.manifest)
        incognito["incognito"] = "spanning"
        self.assert_rejected(incognito)

        main_world = copy.deepcopy(self.manifest)
        main_world["nested_review_fixture"] = {
            "content_scripts": [{"world": "MAIN"}],
        }
        self.assert_rejected(main_world)

    def test_manifest_version_and_permissions_shape_are_required(self) -> None:
        wrong_version = copy.deepcopy(self.manifest)
        wrong_version["manifest_version"] = 2
        self.assert_rejected(wrong_version)

        wrong_permissions = copy.deepcopy(self.manifest)
        wrong_permissions["permissions"] = "debugger"
        self.assert_rejected(wrong_permissions)

    def test_error_registry_requires_typed_denials(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "extension/src/protocol.mjs"
            path.parent.mkdir(parents=True)
            path.write_text('export const PUBLIC_ERROR_CODES = new Set(["unknown"]);', encoding="utf-8")
            with self.assertRaises(checker.PermissionError):
                checker.validate_error_registry(root)

    def test_direct_loader_rejects_missing_and_malformed_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(checker.PermissionError):
                checker.load_manifest(root, "extension/manifest.json")

            manifest_path = root / "manifest.json"
            manifest_path.write_text("{not-json", encoding="utf-8")
            with self.assertRaises(checker.PermissionError):
                checker.load_manifest(root, "manifest.json")

            manifest_path.write_text(json.dumps([]), encoding="utf-8")
            with self.assertRaises(checker.PermissionError):
                checker.load_manifest(root, "manifest.json")


if __name__ == "__main__":
    unittest.main()
