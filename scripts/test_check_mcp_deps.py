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
        (self.root / "docs").mkdir()
        (self.root / "docs/mcp-compatibility.md").write_text(
            "Legacy direct-CDP surface: `crates/agentyc-mcp/src/legacy.rs` and `src/tools/mod.rs`.\n",
            encoding="utf-8",
        )
        (self.root / "crates/agentyc-mcp/src/lib.rs").write_text(
            """#[cfg(feature = \"legacy-cdp\")]\nmod state;\n#[cfg(feature = \"legacy-cdp\")]\nmod tools;\n#[cfg(feature = \"legacy-cdp\")]\nmod legacy;\n""",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write_manifest(self, text: str) -> None:
        (self.root / "crates/agentyc-mcp/Cargo.toml").write_text(text, encoding="utf-8")

    def write_source(self, relative: str, text: str) -> None:
        path = self.root / "crates/agentyc-mcp/src" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def test_rejects_direct_and_target_production_dependencies_but_ignores_dev(self) -> None:
        self.write_manifest(
            """[dependencies]
agentyc-cdp = { workspace = true }
renamed-browser = { package = "agentyc-browser", workspace = true }
renamed-runtime = {
    package = "agentyc-runtime",
    workspace = true,
}

[dev-dependencies]
agentyc-runtime = { workspace = true }

[target.'cfg(unix)'.dependencies]
agentyc_runtime = { workspace = true }
"""
        )
        violations = check_mcp_deps.check_manifest(self.root)
        self.assertEqual(
            violations,
            [
                "crates/agentyc-mcp/Cargo.toml:2: forbidden production dependency agentyc-cdp",
                "crates/agentyc-mcp/Cargo.toml:3: forbidden production dependency agentyc-browser",
                "crates/agentyc-mcp/Cargo.toml:4: forbidden production dependency agentyc-runtime",
                "crates/agentyc-mcp/Cargo.toml:13: forbidden production dependency agentyc-runtime",
            ],
        )

    def test_allows_optional_legacy_dependencies_gated_by_non_default_feature(self) -> None:
        self.write_manifest(
            """[dependencies]
agentyc-cdp = { workspace = true, optional = true }
agentyc-browser = { workspace = true, optional = true }
agentyc-runtime = { workspace = true, optional = true }

[features]
default = []
legacy-cdp = [
    "dep:agentyc-cdp",
    "dep:agentyc-browser",
    "dep:agentyc-runtime",
]
"""
        )
        self.assertEqual(check_mcp_deps.check_manifest(self.root), [])

    def test_rejects_optional_dependencies_without_exclusive_non_default_legacy_gate(self) -> None:
        self.write_manifest(
            """[dependencies]
agentyc-cdp = { workspace = true, optional = true }
agentyc-browser = { workspace = true, optional = true }
agentyc-runtime = { workspace = true, optional = true }

[features]
default = ["legacy-cdp"]
legacy-cdp = ["dep:agentyc-cdp"]
other = ["dep:agentyc-browser", "dep:agentyc-runtime"]
"""
        )
        self.assertEqual(
            check_mcp_deps.check_manifest(self.root),
            [
                "crates/agentyc-mcp/Cargo.toml:2: forbidden production dependency agentyc-cdp",
                "crates/agentyc-mcp/Cargo.toml:3: forbidden production dependency agentyc-browser",
                "crates/agentyc-mcp/Cargo.toml:4: forbidden production dependency agentyc-runtime",
            ],
        )

    def test_allows_only_documented_and_feature_gated_legacy_source_references(self) -> None:
        self.write_manifest("[dependencies]\nserde = { workspace = true }\n")
        self.write_source("legacy.rs", "use agentyc_runtime::BrowserRuntime;\n")
        self.write_source("state.rs", "use agentyc_cdp::CdpClient;\n")
        self.write_source("tools/navigation.rs", "use agentyc_browser::BrowserRuntime;\n")
        self.write_source("host_server.rs", "fn bypass() { close_all(); }\n")
        self.assertEqual(
            check_mcp_deps.check(self.root),
            ["crates/agentyc-mcp/src/host_server.rs:1: forbidden bypass symbol close_all"],
        )

    def test_legacy_references_are_not_exempt_without_feature_gate(self) -> None:
        self.write_manifest("[dependencies]\nserde = { workspace = true }\n")
        self.write_source("lib.rs", "mod legacy;\n")
        self.write_source("legacy.rs", "use agentyc_runtime::BrowserRuntime;\n")
        self.assertEqual(
            check_mcp_deps.check(self.root),
            [
                "crates/agentyc-mcp/src/legacy.rs:1: forbidden bypass symbol BrowserRuntime",
                "crates/agentyc-mcp/src/legacy.rs:1: forbidden bypass symbol agentyc_runtime",
            ],
        )

    def test_legacy_references_are_not_exempt_without_compatibility_documentation(self) -> None:
        self.write_manifest("[dependencies]\nserde = { workspace = true }\n")
        (self.root / "docs/mcp-compatibility.md").write_text("No legacy inventory.\n", encoding="utf-8")
        self.write_source("legacy.rs", "use agentyc_runtime::BrowserRuntime;\n")
        self.assertEqual(
            check_mcp_deps.check(self.root),
            [
                "crates/agentyc-mcp/src/legacy.rs:1: forbidden bypass symbol BrowserRuntime",
                "crates/agentyc-mcp/src/legacy.rs:1: forbidden bypass symbol agentyc_runtime",
            ],
        )

    def test_reports_bypass_api_and_protocol_commands_in_sorted_location_order(self) -> None:
        self.write_manifest("[dependencies]\nserde = { workspace = true }\n")
        self.write_source("host_server.rs", 'let _ = "Runtime.evaluate";\n')
        self.write_source("host_adapter.rs", "let _page = active_page;\n")
        self.assertEqual(
            check_mcp_deps.check(self.root),
            [
                "crates/agentyc-mcp/src/host_adapter.rs:1: forbidden bypass symbol active_page",
                "crates/agentyc-mcp/src/host_server.rs:1: forbidden bypass symbol Runtime.evaluate",
            ],
        )


if __name__ == "__main__":
    unittest.main()
