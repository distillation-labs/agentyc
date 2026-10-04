from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("check_mcp_compat.py")
SPEC = importlib.util.spec_from_file_location("check_mcp_compat", SCRIPT)
checker = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(checker)


class McpCompatibilityCheckerTests(unittest.TestCase):
    def test_repository_counts_are_inferred_and_live_chrome_is_not_claimed(self) -> None:
        manifest, report = checker.inspect_repository(checker.ROOT)
        self.assertEqual(manifest["profile_counts"], {"default": 61, "extended": 76})
        self.assertEqual(len(manifest["profiles"]["default"]), 61)
        self.assertEqual(len(manifest["profiles"]["extended"]), 76)
        self.assertEqual(report["live_chrome"], {"status": "not_run", "claim": False})
        self.assertEqual(report["status"], "fail")
        failures = {item["id"] for item in report["checks"] if item["status"] == "fail"}
        self.assertTrue({
            "architecture.no_direct_browser_dependencies",
            "architecture.no_direct_cdp_bypass",
            "policy.raw_ids_adapter_only",
            "errors.canonical_iserror_metadata",
        }.issubset(failures))

    def test_rust_comment_mentions_do_not_count_as_production_bypasses(self) -> None:
        source = """// CdpClient and target_id in a comment are not executable.
/* BrowserRuntime */\nfn safe() { let _ = 1; }\n"""
        stripped = checker._strip_rust_comments(source)
        self.assertIsNone(checker.CDP_BYPASS_RE.search(stripped))
        self.assertIsNone(checker.RAW_ID_RE.search(stripped))

    def test_report_directory_rejects_traversal_and_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "artifacts").mkdir()
            (root / "outside").mkdir()
            with self.assertRaises(checker.CompatibilityError):
                checker.resolve_artifact_dir(root, "artifacts/../outside")
            try:
                (root / "artifacts/link").symlink_to(root / "outside", target_is_directory=True)
            except OSError:
                self.skipTest("symlinks are unavailable")
            with self.assertRaises(checker.CompatibilityError):
                checker.resolve_artifact_dir(root, "artifacts/link/report")

    def test_missing_evidence_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "crates/agentyc-mcp/src").mkdir(parents=True)
            (root / "artifacts").mkdir()
            manifest, report = checker.inspect_repository(root)
            self.assertEqual(report["status"], "fail")
            self.assertEqual(manifest["profile_counts"], {"default": 0, "extended": 0})
            required = next(item for item in report["checks"] if item["id"] == "evidence.required_files")
            self.assertEqual(required["status"], "fail")

    def test_checker_emits_versioned_bounded_artifacts_even_when_gates_fail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "artifacts").mkdir()
            output = root / "artifacts/p8-check"
            manifest = {"schema_version": 1, "manifest_version": "1.0.0"}
            report = {"schema_version": 1, "report_version": "1.0.0", "status": "fail"}
            checker._atomic_write(output / "manifest.v1.json", manifest)
            checker._atomic_write(output / "report.v1.json", report)
            self.assertEqual(json.loads((output / "manifest.v1.json").read_text()), manifest)
            self.assertEqual(json.loads((output / "report.v1.json").read_text()), report)
            self.assertLess((output / "manifest.v1.json").stat().st_size, checker.MAX_OUTPUT_BYTES)


if __name__ == "__main__":
    unittest.main()
