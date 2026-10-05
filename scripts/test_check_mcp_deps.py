from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

try:
    import check_mcp_deps
except ImportError:
    from scripts import check_mcp_deps


class CheckMcpDepsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "crates/agentyc-mcp/src").mkdir(parents=True)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write_manifest(self, text: str) -> None:
        (self.root / "crates/agentyc-mcp/Cargo.toml").write_text(text, encoding="utf-8")

    def write_source(self, relative: str, text: str) -> None:
        path = self.root / "crates/agentyc-mcp/src" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def test_rejects_direct_and_target_dependencies_but_ignores_dev_dependencies(self) -> None:
        self.write_manifest(
            """[dependencies]
            agentyc-cdp = { workspace = true }
            renamed-browser = { package = "agentyc-browser", workspace = true }

            [dev-dependencies]
            agentyc-runtime = { workspace = true }

            [target.'cfg(unix)'.dependencies]
            agentyc_runtime = { workspace = true }
            """
        )
        self.assertEqual(
            check_mcp_deps.check_manifest(self.root),
            [
                "crates/agentyc-mcp/Cargo.toml: forbidden production dependency agentyc-cdp",
                "crates/agentyc-mcp/Cargo.toml: forbidden production dependency agentyc-browser",
                "crates/agentyc-mcp/Cargo.toml: forbidden production dependency agentyc-runtime",
            ],
        )

    def test_rejects_legacy_cdp_feature_even_without_dependency(self) -> None:
        self.write_manifest('[dependencies]\nserde = "1"\n\n[features]\nlegacy-cdp = []\n')
        self.assertEqual(
            check_mcp_deps.check_manifest(self.root),
            ["crates/agentyc-mcp/Cargo.toml: removed feature legacy-cdp is still declared"],
        )

    def test_rejects_removed_legacy_modules_and_any_bypass_symbol(self) -> None:
        self.write_manifest("[dependencies]\nserde = { workspace = true }\n")
        self.write_source("legacy.rs", "use agentyc_runtime::BrowserRuntime;\n")
        self.write_source("host_server.rs", "fn bypass() { close_all(); }\n")
        self.assertEqual(
            check_mcp_deps.check(self.root),
            [
                "crates/agentyc-mcp/src/host_server.rs:1: forbidden browser/CDP authority symbol close_all",
                "crates/agentyc-mcp/src/legacy.rs: removed legacy MCP source remains",
            ],
        )

    def test_ignores_commented_and_cfg_test_only_bypass_tokens(self) -> None:
        self.write_manifest("[dependencies]\nserde = { workspace = true }\n")
        self.write_source(
            "host_server.rs",
            """// CdpClient and target_id in documentation only.
            #[cfg(test)]
            mod tests {
                fn fixture() { close_all(); }
            }
            fn safe() {}
            """,
        )
        self.assertEqual(check_mcp_deps.check(self.root), [])

    def test_reports_strings_that_retain_direct_cdp_commands(self) -> None:
        self.write_manifest("[dependencies]\nserde = { workspace = true }\n")
        self.write_source("host_server.rs", 'let _ = "Runtime.evaluate";\n')
        self.assertEqual(
            check_mcp_deps.check(self.root),
            ["crates/agentyc-mcp/src/host_server.rs:1: forbidden browser/CDP authority symbol Runtime.evaluate"],
        )


if __name__ == "__main__":
    unittest.main()
