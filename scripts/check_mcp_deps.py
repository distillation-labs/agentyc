#!/usr/bin/env python3
"""Check the MCP crate for direct browser dependencies and authority bypasses."""

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
LEGACY_FEATURE = "legacy-cdp"
LEGACY_SOURCE_PREFIXES = (Path("legacy.rs"), Path("state.rs"), Path("tools"))
LEGACY_MODULES = {
    Path("legacy.rs"): "legacy",
    Path("state.rs"): "state",
    Path("tools"): "tools",
}
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


def _dependency_alias_from_section(section: str) -> str | None:
    if not _is_production_dependency_section(section) or section == "dependencies":
        return None
    if section.endswith(".dependencies"):
        return None
    if section.startswith("dependencies.") or ".dependencies." in section:
        return section.rsplit(".", 1)[-1].strip("\"'")
    return None


def _has_optional_true(declaration: str) -> bool:
    return re.search(
        r"(?:^|[,\n{])\s*optional\s*=\s*true\s*(?=\s*(?:[,}]|$|#))",
        declaration,
        flags=re.IGNORECASE,
    ) is not None


def _dependency_declarations(lines: list[str]) -> list[dict[str, Any]]:
    """Extract production dependency declarations with their source lines."""
    headers: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        header = re.match(r"^\s*\[([^]]+)\]\s*(?:#.*)?$", line)
        if header:
            headers.append((index, header.group(1).strip().strip("\"'")))

    declarations: list[dict[str, Any]] = []
    for header_index, (index, section) in enumerate(headers):
        alias = _dependency_alias_from_section(section)
        if alias is None:
            continue
        end = headers[header_index + 1][0] if header_index + 1 < len(headers) else len(lines)
        body = "\n".join(line.split("#", 1)[0] for line in lines[index + 1 : end])
        declarations.append(
            {
                "crate": _dependency_name(alias, body),
                "line": index + 1,
                "optional": _has_optional_true(body),
                "key": alias,
            }
        )

    section = ""
    table_alias: str | None = None
    for index, line in enumerate(lines):
        header = re.match(r"^\s*\[([^]]+)\]\s*(?:#.*)?$", line)
        if header:
            section = header.group(1).strip().strip("\"'")
            table_alias = _dependency_alias_from_section(section)
            continue
        if table_alias is not None or not _is_production_dependency_section(section):
            continue
        declaration = line.split("#", 1)[0]
        entry = re.match(r"^\s*([A-Za-z0-9_-]+)\s*=\s*(.*)$", declaration)
        if not entry:
            continue
        key, value = entry.groups()
        end = _inline_table_end(lines, index, value)
        full_declaration = "\n".join(
            [value, *[line.split("#", 1)[0] for line in lines[index + 1 : end + 1]]]
        )
        declarations.append(
            {
                "crate": _dependency_name(key, full_declaration),
                "line": index + 1,
                "optional": _has_optional_true(full_declaration),
                "key": key,
            }
        )
    return declarations


def _feature_data(text: str) -> dict[str, Any]:
    try:
        parsed = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return {}
    features = parsed.get("features")
    return features if isinstance(features, dict) else {}


def _feature_reference_matches(value: Any, dependency_key: str) -> bool:
    if not isinstance(value, str) or not value.startswith("dep:"):
        return False
    reference = value[4:]
    aliases = {
        dependency_key,
        dependency_key.replace("_", "-"),
        dependency_key.replace("-", "_"),
    }
    return reference in aliases


def _feature_reaches(
    features: dict[str, Any],
    start: str,
    target: str,
    visited: set[str] | None = None,
) -> bool:
    if start == target:
        return True
    seen = set() if visited is None else visited
    if start in seen:
        return False
    seen.add(start)
    values = features.get(start)
    if not isinstance(values, list):
        return False
    return any(
        isinstance(value, str)
        and not value.startswith("dep:")
        and value in features
        and _feature_reaches(features, value, target, seen)
        for value in values
    )


def _feature_reaches_dependency(
    features: dict[str, Any],
    start: str,
    dependency_key: str,
    visited: set[str] | None = None,
) -> bool:
    seen = set() if visited is None else visited
    if start in seen:
        return False
    seen.add(start)
    values = features.get(start)
    if not isinstance(values, list):
        return False
    return any(
        _feature_reference_matches(value, dependency_key)
        or (
            isinstance(value, str)
            and not value.startswith("dep:")
            and value in features
            and _feature_reaches_dependency(features, value, dependency_key, seen)
        )
        for value in values
    )


def _dependency_is_legacy_only(features: dict[str, Any], dependency_key: str) -> bool:
    """Require an optional dependency to be directly and exclusively feature-gated."""
    legacy_values = features.get(LEGACY_FEATURE)
    if not isinstance(legacy_values, list) or any(not isinstance(value, str) for value in legacy_values):
        return False
    if not any(_feature_reference_matches(value, dependency_key) for value in legacy_values):
        return False

    direct_gate_features = {
        name
        for name, values in features.items()
        if isinstance(values, list)
        and any(_feature_reference_matches(value, dependency_key) for value in values)
    }
    if direct_gate_features != {LEGACY_FEATURE}:
        return False

    default_values = features.get("default", [])
    if not isinstance(default_values, list) or any(not isinstance(value, str) for value in default_values):
        return False
    if any(
        isinstance(value, str)
        and (
            _feature_reference_matches(value, dependency_key)
            or (value in features and _feature_reaches_dependency(features, value, dependency_key))
            or (value in features and _feature_reaches(features, value, LEGACY_FEATURE))
        )
        for value in default_values
    ):
        return False

    # Any other public feature that aliases legacy-cdp would make the
    # dependency available through a non-legacy feature as well.
    return not any(
        name != LEGACY_FEATURE
        and isinstance(values, list)
        and _feature_reaches(features, name, LEGACY_FEATURE)
        for name, values in features.items()
    )


def check_manifest(root: Path) -> list[str]:
    path = root / MANIFEST_REL
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        return [f"{MANIFEST_REL.as_posix()}: {exc}"]

    features = _feature_data(text)
    violations: list[str] = []
    for declaration in _dependency_declarations(text.splitlines()):
        crate = declaration["crate"]
        if crate not in FORBIDDEN_CRATES:
            continue
        allowed = declaration["optional"] and _dependency_is_legacy_only(features, declaration["key"])
        if not allowed:
            violations.append(
                f"{MANIFEST_REL.as_posix()}:{declaration['line']}: forbidden production dependency {crate}"
            )
    return violations


def _legacy_compatibility_is_documented(root: Path) -> bool:
    try:
        text = (root / COMPAT_DOC_REL).read_text(encoding="utf-8").lower()
    except (OSError, UnicodeError):
        return False
    return (
        "legacy direct-cdp" in text
        and "crates/agentyc-mcp/src/legacy.rs" in text
        and "src/tools/mod.rs" in text
    )


def _feature_gated_legacy_modules(root: Path) -> set[str]:
    """Return legacy modules explicitly gated by cfg(feature = \"legacy-cdp\")."""
    path = root / SOURCE_REL / "lib.rs"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return set()

    cfg_line = re.compile(
        r'^\s*#\[\s*cfg\s*\(\s*feature\s*=\s*["\']legacy-cdp["\']\s*\)\s*\]\s*$'
    )
    modules: set[str] = set()
    for index, line in enumerate(lines[:-1]):
        if not cfg_line.fullmatch(line):
            continue
        next_line = lines[index + 1]
        match = re.fullmatch(r"\s*(?:pub\s+)?mod\s+(legacy|state|tools)\s*;\s*", next_line)
        if match:
            modules.add(match.group(1))
    return modules


def _is_documented_legacy_source(
    relative: Path,
    documentation_present: bool,
    gated_modules: set[str],
) -> bool:
    if not documentation_present:
        return False
    for prefix, module in LEGACY_MODULES.items():
        if relative == prefix or prefix in relative.parents:
            return module in gated_modules
    return False


def check_sources(root: Path) -> list[str]:
    source_root = root / SOURCE_REL
    if not source_root.is_dir():
        return [f"{SOURCE_REL.as_posix()}: missing MCP source directory"]
    legacy_documented = _legacy_compatibility_is_documented(root)
    gated_modules = _feature_gated_legacy_modules(root)
    violations: list[str] = []
    files = sorted(source_root.rglob("*.rs"), key=lambda path: path.relative_to(root).as_posix())
    for path in files:
        relative = path.relative_to(source_root)
        if relative == TEST_ONLY_SOURCE or _is_documented_legacy_source(relative, legacy_documented, gated_modules):
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
