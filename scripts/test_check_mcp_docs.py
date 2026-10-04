from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("check_mcp_docs.py")
SPEC = importlib.util.spec_from_file_location("check_mcp_docs", SCRIPT)
checker = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(checker)


class McpDocsAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "docs").mkdir()
        for relative in checker.REQUIRED_DOCS:
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                "# Direct CLI and SDK\n"
                "The direct CLI/SDK is the primary interface. MCP is compatibility-only.\n",
                encoding="utf-8",
            )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write(self, relative: str, text: str) -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def test_reports_each_forbidden_primary_recommendation(self) -> None:
        self.write(
            "docs/overview.md",
            """# Overview
Use the raw tab_id to switch tabs.
Show output as [123] Chrome.
Call browser_close_all when done.
Launch Chrome with agentyc browser.
MCP-first browser automation is the default.
""",
        )
        findings = checker.check(self.root)
        self.assertEqual(
            {finding.split(": ", 1)[1].split(":", 1)[0] for finding in findings},
            {
                "raw_tab_or_target_id_recommendation",
                "id_name_output_format",
                "global_close_recommendation",
                "browser_launch_or_download_recommendation",
                "mcp_as_default_or_primary",
            },
        )

    def test_allows_legacy_adapter_section_and_negated_mentions(self) -> None:
        self.write(
            "docs/overview.md",
            """# Direct CLI and SDK
The direct CLI/SDK is the primary interface. MCP is compatibility-only.
Do not use MCP as the default interface.
Never pass a raw tab_id as authority.
The output is not formatted as [id] name.
Do not call browser_close_all.
The CLI does not launch Chrome or download Chrome.

## Legacy MCP Compatibility Adapter
Use raw tab_id to switch tabs.
Display [123] Chrome.
Call browser_close_all when done.
Launch Chrome with agentyc browser.
""",
        )
        self.assertEqual(checker.check(self.root), [])

    def test_scans_bold_legacy_prefix_in_primary_section(self) -> None:
        self.write(
            "docs/overview.md",
            """# Direct CLI and SDK
The direct CLI/SDK is the primary interface. MCP is compatibility-only.
**Legacy:** Use raw tab_id to switch tabs.
""",
        )
        findings = checker.check(self.root)
        self.assertTrue(
            any("raw_tab_or_target_id_recommendation" in finding for finding in findings)
        )

    def test_allows_explicit_bold_compatibility_prefix(self) -> None:
        self.write(
            "docs/overview.md",
            """# Direct CLI and SDK
The direct CLI/SDK is the primary interface. MCP is compatibility-only.
**Legacy compatibility only:** Use raw tab_id to switch tabs.
""",
        )
        self.assertEqual(checker.check(self.root), [])

    def test_neutral_target_bridge_text_is_not_a_raw_id_recommendation(self) -> None:
        self.write(
            "docs/overview.md",
            """# Direct CLI and SDK
The direct CLI/SDK is the primary interface. MCP is compatibility-only.
The target bridge routes logical pages through the host.
""",
        )
        self.assertEqual(checker.check(self.root), [])

    def test_fails_when_primary_policy_statements_are_missing(self) -> None:
        self.write("docs/cli.md", "# Commands\nUse the CLI.\n")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "docs").mkdir()
            for relative in checker.REQUIRED_DOCS:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("# Guide\n", encoding="utf-8")
            findings = checker.check(root)
        self.assertIn("policy: missing explicit direct CLI/SDK primary statement", findings)
        self.assertIn("policy: missing explicit MCP compatibility-only statement", findings)

    def test_missing_required_docs_fail_closed(self) -> None:
        (self.root / "docs/cli.md").unlink()
        with self.assertRaisesRegex(checker.DocsAuditError, "required primary document is missing"):
            checker.check(self.root)

    def test_symlink_document_fails_closed(self) -> None:
        path = self.root / "docs/api-local.md"
        path.unlink()
        target = self.root / "outside.md"
        target.write_text("# Direct\n", encoding="utf-8")
        path.symlink_to(target)
        with self.assertRaisesRegex(checker.DocsAuditError, "not a regular non-symlink file"):
            checker.check(self.root)

    def test_oversized_document_fails_closed(self) -> None:
        (self.root / "docs/cli.md").write_text("x" * (checker.MAX_FILE_BYTES + 1), encoding="utf-8")
        with self.assertRaisesRegex(checker.DocsAuditError, "exceeds"):
            checker.check(self.root)

    def test_repository_has_no_primary_migration_findings(self) -> None:
        findings = checker.check(checker.ROOT)
        self.assertEqual(findings, [])


if __name__ == "__main__":
    unittest.main()
