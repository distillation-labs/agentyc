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
    def test_repository_inventory_is_host_only_and_does_not_claim_live_chrome(self) -> None:
        manifest, report = checker.inspect_repository(checker.ROOT)
        self.assertEqual(manifest["profile_counts"], {"offline": 29, "remote": 30})
        self.assertEqual(len(manifest["tools"]["offline"]), 29)
        self.assertEqual(len(manifest["tools"]["remote"]), 30)
        self.assertEqual(report["live_chrome"], {"status": "not_run", "claim": False})
        self.assertEqual(report["status"], "pass")
        failed = [item["id"] for item in report["checks"] if item["status"] == "fail"]
        self.assertEqual(failed, [])
        self.assertTrue(all(name.startswith("host_") for name in manifest["tools"]["offline"]))
        self.assertTrue(all(item["name"].startswith("host_") for item in manifest["tools"]["remote"]))
        self.assertEqual(
            sum(not item["local_protocol"] for item in manifest["tools"]["remote"]),
            12,
        )
        tool_names = manifest["tools"]["offline"] + [item["name"] for item in manifest["tools"]["remote"]]
        self.assertFalse(any(name.startswith("browser_") for name in tool_names))

        envelope_fields = {
            "schema_version", "build_tuple", "environment", "timestamp", "nonce",
            "command", "result", "redaction_status",
        }
        self.assertTrue(envelope_fields.issubset(manifest))
        self.assertTrue(envelope_fields.issubset(report))
        self.assertEqual(manifest["timestamp"], report["timestamp"])
        self.assertEqual(manifest["nonce"], report["nonce"])
        self.assertEqual(
            [item["id"] for item in report["checks"]],
            [
                "evidence.required_files",
                "architecture.host_only_dependencies",
                "architecture.legacy_modules_removed",
                "architecture.no_direct_browser_or_raw_id_authority",
                "tools.host_only_inventory",
                "tools.unsupported_routes_are_explicit",
                "protocol.test_covers_host_only_tools",
                "docs.legacy_removal_and_live_gate_are_explicit",
                "errors.structured_host_metadata",
            ],
        )

    def test_parsers_extract_only_declared_host_tools(self) -> None:
        offline = '''
#[rmcp::tool(name = "host_space_list", description = "List spaces")]
async fn host_space_list(&self) -> Result<(), Error> { todo!() }
'''
        remote = '''
const REMOTE_TOOL_SPECS: &[RemoteToolSpec] = &[
    RemoteToolSpec { name: "host_space_list", method: "space.list", description: "List.", fields: NO_FIELDS, supported_by_local_protocol: true, },
];
'''
        self.assertEqual(checker._tool_declarations(offline), ["host_space_list"])
        self.assertEqual(
            checker._remote_tool_declarations(remote),
            [{"name": "host_space_list", "local_protocol": True}],
        )

    def test_rust_comment_mentions_do_not_count_as_bypasses(self) -> None:
        source = "// CdpClient and target_id in a comment are not executable.\n/* BrowserRuntime */\nfn safe() {}\n"
        stripped = checker._strip_rust_comments(source)
        self.assertIsNone(checker.FORBIDDEN_SOURCE.search(stripped))

    def test_missing_evidence_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest, report = checker.inspect_repository(Path(directory))
            self.assertEqual(report["status"], "fail")
            self.assertEqual(manifest["profile_counts"], {"offline": 0, "remote": 0})
            required = next(item for item in report["checks"] if item["id"] == "evidence.required_files")
            self.assertEqual(required["status"], "fail")

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

    def test_checker_writes_bounded_artifacts_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "artifacts").mkdir()
            output = root / "artifacts/report/manifest.v1.json"
            checker._atomic_write(output, {"status": "pass"})
            self.assertEqual(json.loads(output.read_text()), {"status": "pass"})
            self.assertLess(output.stat().st_size, checker.MAX_OUTPUT_BYTES)


if __name__ == "__main__":
    unittest.main()
