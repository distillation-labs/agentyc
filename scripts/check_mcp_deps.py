#!/usr/bin/env python3
"""Check the MCP crate for direct browser dependencies and authority bypasses."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MCP_REL = Path("crates/agentyc-mcp")
MANIFEST_REL = MCP_REL / "Cargo.toml"
SOURCE_REL = MCP_REL / "src"
COMPAT_DOC_REL = Path("docs/mcp-compatibility.md")
FORBIDDEN_CRATES = {"agentyc-cdp", "agentyc-browser", "agentyc-runtime"}
FORBIDDEN_SYMBOLS = (
    ("agentyc_cdp", re.compile(r"\bagentyc_cdp\b")),
    ("agentyc_browser", re.compile(r"\bagentyc_browser\b")),
    ("agentyc_runtime", re.compile(r"\bagentyc_runtime\b")),
    ("CdpClient", re.compile(r"\bCdpClient\b")),
    ("BrowserRuntime", re.compile(r"\bBrowserRuntime\b")),
    ("active_page", re.compile(r"\bactive_page\b")),
    ("close_all", re.compile(r"\bclose_all\b")),
    ("Target.*", re.compile(r"\bTarget\s*\.")),
    ("Runtime.evaluate", re.compile(r"\bRuntime\s*\.\s*evaluate\b")),
)
LEGACY_SOURCE_PREFIXES = (Path("lib.rs"), Path("state.rs"), Path("tools"))
TEST_ONLY_SOURCE = Path("host_adapter_audit.rs")


def _is_production_dependency_section(section: str) -> bool:
    """Match Cargo normal/target dependency tables, not dev or build deps."""
    return (
        section == "dependencies"
        or section.startswith("dependencies.")
        or section.endswith(".dependencies")
        or ".dependencies." in section
    )


def _dependency_name(key: str, declaration: str) -> str | None:
    package = re.search(r"\bpackage\s*=\s*['\"]([^'\"]+)['\"]", declaration)
    return package.group(1) if package else key.replace("_", "-")


def _inline_table_end(lines: list[str], start: int, first_value: str) -> int:
    """Return the last line of a possibly multiline Cargo inline table."""
    depth = first_value.count("{") - first_value.count("}")
    end = start
    while depth > 0 and end + 1 < len(lines):
        end += 1
        depth += lines[end].count("{") - lines[end].count("}")
    return end


def check_manifest(root: Path) -> list[str]:
    path = root / MANIFEST_REL
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        return [f"{MANIFEST_REL.as_posix()}: {exc}"]

    violations: list[str] = []
    section = ""
    for index, line in enumerate(lines):
        header = re.match(r"^\s*\[([^]]+)\]\s*(?:#.*)?$", line)
        if header:
            section = header.group(1).strip().strip("\"'")
            # Cargo also accepts dependency-specific tables such as
            # [dependencies.alias] and [target.'cfg(...)'.dependencies.alias].
            for crate in sorted(FORBIDDEN_CRATES):
                if _is_production_dependency_section(section) and (
                    section == f"dependencies.{crate}"
                    or section.endswith(f".dependencies.{crate}")
                    or section == f"dependencies.{crate.replace('-', '_')}"
                    or section.endswith(f".dependencies.{crate.replace('-', '_')}")
                ):
                    violations.append(
                        f"{MANIFEST_REL.as_posix()}:{index + 1}: forbidden production dependency {crate}"
                    )
            continue

        if not _is_production_dependency_section(section):
            continue
        declaration = line.split("#", 1)[0]
        entry = re.match(r"^\s*([A-Za-z0-9_-]+)\s*=\s*(.*)$", declaration)
        if not entry:
            continue
        key, value = entry.groups()
        end = _inline_table_end(lines, index, value)
        full_declaration = "\n".join([value, *lines[index + 1 : end + 1]])
        crate = _dependency_name(key, full_declaration)
        if crate in FORBIDDEN_CRATES:
            violations.append(
                f"{MANIFEST_REL.as_posix()}:{index + 1}: forbidden production dependency {crate}"
            )

    return violations


def _legacy_compatibility_is_documented(root: Path) -> bool:
    try:
        text = (root / COMPAT_DOC_REL).read_text(encoding="utf-8").lower()
    except (OSError, UnicodeError):
        return False
    return (
        "legacy direct-cdp" in text
        and "crates/agentyc-mcp/src/lib.rs" in text
        and "src/tools/mod.rs" in text
    )


def _is_documented_legacy_source(relative: Path, documentation_present: bool) -> bool:
    return documentation_present and any(
        relative == prefix or prefix in relative.parents for prefix in LEGACY_SOURCE_PREFIXES
    )


def check_sources(root: Path) -> list[str]:
    source_root = root / SOURCE_REL
    if not source_root.is_dir():
        return [f"{SOURCE_REL.as_posix()}: missing MCP source directory"]
    legacy_documented = _legacy_compatibility_is_documented(root)
    violations: list[str] = []
    files = sorted(source_root.rglob("*.rs"), key=lambda path: path.relative_to(root).as_posix())
    for path in files:
        relative = path.relative_to(source_root)
        if relative == TEST_ONLY_SOURCE or _is_documented_legacy_source(relative, legacy_documented):
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError) as exc:
            violations.append(f"{path.relative_to(root).as_posix()}: {exc}")
            continue
        for line_number, line in enumerate(lines, 1):
            for name, pattern in FORBIDDEN_SYMBOLS:
                if pattern.search(line):
                    violations.append(
                        f"{path.relative_to(root).as_posix()}:{line_number}: forbidden bypass symbol {name}"
                    )
    return violations


def check(root: Path) -> list[str]:
    return sorted(check_manifest(root) + check_sources(root))


def main() -> int:
    violations = check(ROOT)
    if violations:
        print("check_mcp_deps: FAIL")
        for violation in violations:
            print(f"- {violation}")
        return 1
    print("check_mcp_deps: PASS (no forbidden production dependencies or bypass symbols)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
