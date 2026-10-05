#!/usr/bin/env python3
"""Fail if the MCP crate regains direct browser/CDP authority or legacy modules."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

import tomllib

ROOT = Path(__file__).resolve().parents[1]
MCP_REL = Path("crates/agentyc-mcp")
MANIFEST_REL = MCP_REL / "Cargo.toml"
SOURCE_REL = MCP_REL / "src"
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
LEGACY_SOURCE_NAMES = {"legacy.rs", "state.rs"}
TEST_ONLY_SOURCE = Path("host_adapter_audit.rs")


def _production_dependency_tables(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    tables: list[dict[str, Any]] = []
    dependencies = manifest.get("dependencies")
    if isinstance(dependencies, dict):
        tables.append(dependencies)
    targets = manifest.get("target")
    if isinstance(targets, dict):
        for target in targets.values():
            if isinstance(target, dict) and isinstance(target.get("dependencies"), dict):
                tables.append(target["dependencies"])
    return tables


def check_manifest(root: Path) -> list[str]:
    path = root / MANIFEST_REL
    try:
        manifest = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        return [f"{MANIFEST_REL.as_posix()}: {exc}"]

    violations: list[str] = []
    for table in _production_dependency_tables(manifest):
        for key, declaration in table.items():
            if not isinstance(declaration, dict):
                package = key.replace("_", "-")
            else:
                package = declaration.get("package", key.replace("_", "-"))
            if package in FORBIDDEN_CRATES:
                violations.append(f"{MANIFEST_REL.as_posix()}: forbidden production dependency {package}")

    features = manifest.get("features", {})
    if isinstance(features, dict) and "legacy-cdp" in features:
        violations.append(f"{MANIFEST_REL.as_posix()}: removed feature legacy-cdp is still declared")
    return violations


def _remove_rust_comments(source: str) -> str:
    """Remove line and nested block comments without masking string contents."""
    output: list[str] = []
    index = 0
    depth = 0
    in_string = False
    in_char = False
    escaped = False
    while index < len(source):
        char = source[index]
        next_char = source[index + 1] if index + 1 < len(source) else ""
        if depth:
            if char == "/" and next_char == "*":
                depth += 1
                output.extend("  ")
                index += 2
            elif char == "*" and next_char == "/":
                depth -= 1
                output.extend("  ")
                index += 2
            else:
                output.append("\n" if char == "\n" else " ")
                index += 1
            continue
        if in_string or in_char:
            output.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif (in_string and char == '"') or (in_char and char == "'"):
                in_string = False
                in_char = False
            index += 1
            continue
        if char == "/" and next_char == "/":
            while index < len(source) and source[index] != "\n":
                output.append(" ")
                index += 1
            continue
        if char == "/" and next_char == "*":
            depth = 1
            output.extend("  ")
            index += 2
            continue
        if char == '"':
            in_string = True
        elif char == "'" and next_char and (next_char.isalpha() or next_char == "_"):
            output.append(char)
            index += 1
            continue
        elif char == "'":
            in_char = True
        output.append(char)
        index += 1
    return "".join(output)


def _without_test_modules(source: str) -> str:
    lines = source.splitlines(keepends=True)
    kept: list[str] = []
    index = 0
    while index < len(lines):
        if re.match(r"\s*#\[cfg\(test\)\]\s*$", lines[index]):
            module_index = index + 1
            while module_index < len(lines) and not re.search(r"\bmod\s+\w+\s*\{", lines[module_index]):
                module_index += 1
            if module_index < len(lines):
                depth = 0
                started = False
                while module_index < len(lines):
                    line = lines[module_index]
                    depth += line.count("{") - line.count("}")
                    started = started or "{" in line
                    module_index += 1
                    if started and depth <= 0:
                        break
                index = module_index
                continue
        kept.append(lines[index])
        index += 1
    return "".join(kept)


def check_sources(root: Path) -> list[str]:
    source_root = root / SOURCE_REL
    if not source_root.is_dir():
        return [f"{SOURCE_REL.as_posix()}: missing MCP source directory"]

    violations: list[str] = []
    files = sorted(source_root.rglob("*.rs"), key=lambda path: path.relative_to(root).as_posix())
    for path in files:
        relative = path.relative_to(source_root)
        if relative.name in LEGACY_SOURCE_NAMES or "tools" in relative.parts:
            violations.append(f"{path.relative_to(root).as_posix()}: removed legacy MCP source remains")
            continue
        if relative == TEST_ONLY_SOURCE:
            continue
        try:
            source = _remove_rust_comments(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError) as exc:
            violations.append(f"{path.relative_to(root).as_posix()}: {exc}")
            continue
        source = _without_test_modules(source)
        for line_number, line in enumerate(source.splitlines(), 1):
            for name, pattern in FORBIDDEN_SYMBOLS:
                if pattern.search(line):
                    violations.append(
                        f"{path.relative_to(root).as_posix()}:{line_number}: forbidden browser/CDP authority symbol {name}"
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
    print("check_mcp_deps: PASS (host-only MCP dependencies and source boundary)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
