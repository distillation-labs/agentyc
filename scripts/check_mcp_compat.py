#!/usr/bin/env python3
"""Build a static, fail-closed Phase 8 MCP compatibility inventory.

This checker inspects repository declarations only. It does not start MCP, a
host, an extension, or Chrome, and cannot establish live-browser compatibility.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT = "artifacts/p8-mcp-compatibility"
MAX_INPUT_BYTES = 5 * 1024 * 1024
MAX_OUTPUT_BYTES = 2 * 1024 * 1024
DEFAULT_TOOL_COUNT = 61
EXTENDED_TOOL_COUNT = 76
EXTENDED_MARKER = "Observability tools (extended profile)"
ADAPTER_FILES = {"adapter.rs", "compat.rs", "connection.rs"}
RAW_ID_RE = re.compile(
    r"\b(?:tab_id|target_id|debugger_id|cdp_url|websocket_url|"
    r"tabId|targetId|debuggerId|cdpUrl|webSocketDebuggerUrl)\b"
)
CDP_BYPASS_RE = re.compile(
    r"\b(?:agentyc_cdp|agentyc-cdp|CdpClient|BrowserRuntime|"
    r"cdp_root|cdp_session)\b|\bcdp\s*\(|"
    r"\b(?:Runtime\.evaluate|Target\.(?:getTargets|createTarget|closeTarget))\b"
)


class CompatibilityError(ValueError):
    """Repository evidence or artifact bounds are invalid."""


def _read_text(path: Path) -> str:
    if not path.is_file() or path.is_symlink():
        raise CompatibilityError(f"required evidence is missing or not a regular file: {path}")
    if path.stat().st_size > MAX_INPUT_BYTES:
        raise CompatibilityError(f"evidence exceeds {MAX_INPUT_BYTES} bytes: {path}")
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise CompatibilityError(f"evidence is unreadable: {path}") from exc


def _source_digest(path: Path, root: Path) -> dict[str, str]:
    data = path.read_bytes()
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def _strip_rust_comments(source: str) -> str:
    """Remove Rust comments while preserving line numbers and string contents."""
    output: list[str] = []
    i = 0
    block_depth = 0
    in_string = False
    in_char = False
    escaped = False
    while i < len(source):
        char = source[i]
        nxt = source[i + 1] if i + 1 < len(source) else ""
        if block_depth:
            if char == "/" and nxt == "*":
                block_depth += 1
                output.extend("  ")
                i += 2
            elif char == "*" and nxt == "/":
                block_depth -= 1
                output.extend("  ")
                i += 2
            else:
                output.append("\n" if char == "\n" else " ")
                i += 1
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
            i += 1
            continue
        if char == "/" and nxt == "/":
            while i < len(source) and source[i] != "\n":
                output.append(" ")
                i += 1
            continue
        if char == "/" and nxt == "*":
            block_depth = 1
            output.extend("  ")
            i += 2
            continue
        if char == '"':
            in_string = True
        elif char == "'" and nxt and (nxt.isalpha() or nxt == "_"):
            # Rust lifetimes are not character literals.
            output.append(char)
            i += 1
            continue
        elif char == "'":
            in_char = True
        output.append(char)
        i += 1
    return "".join(output)


def _without_test_modules(source: str) -> str:
    """Exclude cfg(test) modules from production-only source audits."""
    lines = source.splitlines(keepends=True)
    kept: list[str] = []
    i = 0
    while i < len(lines):
        if re.match(r"\s*#\[cfg\(test\)\]\s*$", lines[i]):
            j = i + 1
            while j < len(lines) and not re.search(r"\bmod\s+\w+\s*\{", lines[j]):
                j += 1
            if j < len(lines):
                depth = 0
                started = False
                while j < len(lines):
                    text = lines[j]
                    depth += text.count("{") - text.count("}")
                    started = started or "{" in text
                    j += 1
                    if started and depth <= 0:
                        break
                i = j
                continue
        kept.append(lines[i])
        i += 1
    return "".join(kept)


def _tool_declarations(source: str) -> tuple[list[str], list[str]]:
    """Return declared tool names and names declared after the extended marker."""
    marker_at = source.find(EXTENDED_MARKER)
    extended_source = source[marker_at:] if marker_at >= 0 else ""
    pattern = re.compile(r"#\[rmcp::tool\b[\s\S]*?\basync\s+fn\s+(\w+)\s*\(")
    all_names = pattern.findall(source)
    extended_names = pattern.findall(extended_source)
    return all_names, extended_names


def _add_check(checks: list[dict[str, Any]], check_id: str, passed: bool, detail: Any) -> None:
    checks.append({"id": check_id, "status": "pass" if passed else "fail", "detail": detail})


def inspect_repository(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Inspect source evidence and return a versioned manifest and report."""
    root = root.resolve()
    checks: list[dict[str, Any]] = []
    evidence_paths = {
        "package": root / "crates/agentyc-mcp/Cargo.toml",
        "tools": root / "crates/agentyc-mcp/src/lib.rs",
        "compatibility_docs": root / "docs/mcp-compatibility.md",
        "phase_plan": root / "docs/exec-plans/active/agentyc-browser-task-spaces/plans/phase-8-mcp-compatibility.md",
        "catalog": root / "tests/fixtures/mcp/tool_catalog.json",
        "host_adapter": root / "crates/agentyc-mcp/src/host_adapter.rs",
        "host_server": root / "crates/agentyc-mcp/src/host_server.rs",
        "legacy_errors": root / "crates/agentyc-mcp/src/tools/mod.rs",
    }
    loaded: dict[str, str] = {}
    for key, path in evidence_paths.items():
        try:
            loaded[key] = _read_text(path)
        except CompatibilityError as exc:
            _add_check(checks, f"evidence.{key}", False, str(exc))
    _add_check(
        checks,
        "evidence.required_files",
        len(loaded) == len(evidence_paths),
        {"present": sorted(loaded), "required": sorted(evidence_paths)},
    )

    hashes = [
        _source_digest(path, root)
        for key, path in evidence_paths.items()
        if key in loaded
    ]
    package_data: dict[str, Any] = {}
    try:
        package_data = tomllib.loads(loaded["package"])
    except (KeyError, tomllib.TOMLDecodeError) as exc:
        _add_check(checks, "package.toml", False, f"missing or invalid package manifest: {exc}")
    else:
        dependencies = package_data.get("dependencies")
        forbidden = {"agentyc-cdp", "agentyc-browser", "agentyc-runtime"}
        direct = sorted(forbidden.intersection(dependencies if isinstance(dependencies, dict) else {}))
        _add_check(
            checks,
            "architecture.no_direct_browser_dependencies",
            isinstance(dependencies, dict) and not direct,
            {"forbidden_production_dependencies": direct},
        )

    source_files = sorted((root / "crates/agentyc-mcp/src").rglob("*.rs"))
    production_sources: list[tuple[Path, str]] = []
    for path in source_files:
        try:
            text = _read_text(path)
        except CompatibilityError as exc:
            _add_check(checks, f"evidence.source.{path.name}", False, str(exc))
            continue
        if re.match(r"\s*#\[cfg\(test\)\]", text):
            continue
        production_sources.append((path, _without_test_modules(_strip_rust_comments(text))))
    bypasses = []
    raw_id_uses = []
    for path, source in production_sources:
        relative = path.relative_to(root).as_posix()
        for match in CDP_BYPASS_RE.finditer(source):
            line = source.count("\n", 0, match.start()) + 1
            bypasses.append({"path": relative, "line": line, "evidence": match.group(0)})
        for match in RAW_ID_RE.finditer(source):
            line = source.count("\n", 0, match.start()) + 1
            if path.name not in ADAPTER_FILES:
                raw_id_uses.append({"path": relative, "line": line, "field": match.group(0)})
    _add_check(
        checks,
        "architecture.no_direct_cdp_bypass",
        bool(source_files) and not bypasses,
        {"production_source_files": len(production_sources), "violations": bypasses},
    )
    _add_check(
        checks,
        "policy.raw_ids_adapter_only",
        bool(source_files) and not raw_id_uses,
        {"adapter_files": sorted(ADAPTER_FILES), "violations": raw_id_uses},
    )

    all_tools: list[str] = []
    extended_tools: list[str] = []
    catalog: dict[str, Any] = {}
    if "tools" in loaded:
        all_tools, extended_tools = _tool_declarations(loaded["tools"])
    try:
        catalog = json.loads(loaded["catalog"])
        catalog_tools = catalog.get("tools")
        if not isinstance(catalog_tools, list) or any(not isinstance(item, dict) for item in catalog_tools):
            raise ValueError("tools must be an array of objects")
        catalog_names = [item.get("name") for item in catalog_tools]
        if any(not isinstance(name, str) or not name for name in catalog_names):
            raise ValueError("every catalog tool requires a non-empty name")
        if len(set(catalog_names)) != len(catalog_names):
            raise ValueError("catalog contains duplicate tool names")
        statuses = {item.get("status") for item in catalog_tools}
        if not statuses.issubset({"supported", "partial", "legacy-only", "unsupported"}):
            raise ValueError("catalog contains an unknown tool status")
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        catalog_tools = []
        _add_check(checks, "catalog.valid", False, str(exc))
    else:
        _add_check(checks, "catalog.valid", True, {"tool_count": len(catalog_tools)})

    duplicates = sorted({name for name in all_tools if all_tools.count(name) > 1})
    extended_set = set(extended_tools)
    default_tools = [name for name in all_tools if name not in extended_set]
    _add_check(
        checks,
        "profiles.exact_counts",
        len(default_tools) == DEFAULT_TOOL_COUNT
        and len(all_tools) == EXTENDED_TOOL_COUNT
        and len(extended_tools) == EXTENDED_TOOL_COUNT - DEFAULT_TOOL_COUNT
        and not duplicates,
        {
            "default": len(default_tools),
            "extended": len(all_tools),
            "extended_only": len(extended_tools),
            "duplicate_declarations": duplicates,
            "expected": {"default": DEFAULT_TOOL_COUNT, "extended": EXTENDED_TOOL_COUNT},
        },
    )
    catalog_name_set = {item.get("name") for item in catalog_tools}
    declared_name_set = set(all_tools)
    _add_check(
        checks,
        "profiles.catalog_matches_declarations",
        bool(catalog_tools) and catalog_name_set == declared_name_set,
        {
            "catalog_only": sorted(catalog_name_set - declared_name_set),
            "declarations_only": sorted(declared_name_set - catalog_name_set),
        },
    )
    docs_text = (loaded.get("compatibility_docs", "") + "\n" + loaded.get("phase_plan", "")).lower()
    _add_check(
        checks,
        "profiles.documented_baseline",
        "61" in docs_text and "76" in docs_text and "default" in docs_text and "extended" in docs_text,
        {"sources": [
            "docs/mcp-compatibility.md",
            "docs/exec-plans/active/agentyc-browser-task-spaces/plans/phase-8-mcp-compatibility.md",
        ]},
    )

    adapter_text = loaded.get("host_adapter", "")
    canonical_markers = [
        "CallToolResult::structured_error",
        '"code"',
        '"retryable"',
        '"guidance"',
        '"message"',
        '"action_id"',
        '"reconcile_token"',
        '"next_action"',
    ]
    missing_metadata = [marker for marker in canonical_markers if marker not in adapter_text]
    has_is_error_test = 'wire["isError"]' in loaded.get("host_server", "")
    legacy_errors = loaded.get("legacy_errors", "")
    legacy_text_error = "CallToolResult::error" in legacy_errors
    substring_classification = bool(re.search(r"\bmsg\s*\.\s*contains\s*\(", legacy_errors))
    _add_check(
        checks,
        "errors.canonical_iserror_metadata",
        not missing_metadata and has_is_error_test and not legacy_text_error and not substring_classification,
        {
            "missing_host_metadata": missing_metadata,
            "host_isError_test_present": has_is_error_test,
            "legacy_text_error_result": legacy_text_error,
            "legacy_substring_classification": substring_classification,
        },
    )

    catalog_by_name = {
        item["name"]: {"category": item.get("category"), "status": item.get("status")}
        for item in catalog_tools
        if isinstance(item.get("name"), str)
    }
    manifest_tools = []
    required_tool_evidence = (
        "schema",
        "output",
        "side_effects",
        "authority",
        "deprecation",
        "error_mapping",
    )
    for name in all_tools:
        metadata = catalog_by_name.get(name)
        manifest_tools.append({
            "name": name,
            "profiles": ["default", "extended"] if name not in extended_set else ["extended"],
            "category": metadata.get("category") if metadata else None,
            "status": metadata.get("status") if metadata else None,
            "schema": None,
            "output": None,
            "side_effects": None,
            "authority": None,
            "deprecation": None,
            "error_mapping": None,
            "raw_id_policy": "adapter-only",
        })
    metadata_missing = [
        {"tool": item["name"], "fields": list(required_tool_evidence)}
        for item in manifest_tools
    ]
    _add_check(
        checks,
        "manifest.required_tool_evidence",
        bool(manifest_tools) and not metadata_missing,
        {
            "required_fields": list(required_tool_evidence),
            "missing_by_tool": metadata_missing,
            "note": "Declarations and the capability catalog do not provide versioned schemas or full policy metadata.",
        },
    )
    manifest = {
        "schema_version": 1,
        "manifest_version": "1.0.0",
        "kind": "static-mcp-tool-compatibility-inventory",
        "scope": "repository declarations only; not protocol execution or live-browser evidence",
        "profile_counts": {"default": len(default_tools), "extended": len(all_tools)},
        "profiles": {
            "default": default_tools,
            "extended": all_tools,
        },
        "tools": manifest_tools,
        "evidence": hashes,
    }
    passed = all(check["status"] == "pass" for check in checks)
    report = {
        "schema_version": 1,
        "report_version": "1.0.0",
        "kind": "static-mcp-compatibility-check",
        "status": "pass" if passed else "fail",
        "checks": checks,
        "live_chrome": {"status": "not_run", "claim": False},
        "artifacts": {"manifest": "manifest.v1.json", "report": "report.v1.json"},
    }
    return manifest, report


def resolve_artifact_dir(root: Path, requested: str) -> Path:
    """Require a repository-local artifacts directory and reject symlinks."""
    root = root.resolve()
    artifact_root = root / "artifacts"
    candidate = Path(requested).expanduser()
    if ".." in candidate.parts:
        raise CompatibilityError("report path must not contain parent traversal")
    if candidate.is_absolute():
        lexical_target = Path(os.path.abspath(candidate))
    else:
        lexical_target = Path(os.path.abspath(root / candidate))
    try:
        lexical_parts = lexical_target.relative_to(root).parts
    except ValueError as exc:
        raise CompatibilityError("report directory must remain inside repository artifacts/") from exc
    current = root
    for part in lexical_parts:
        current = current / part
        if current.is_symlink():
            raise CompatibilityError("report path components must not be symlinks")
    try:
        resolved = lexical_target.resolve(strict=False)
        resolved.relative_to(artifact_root.resolve(strict=False))
    except (OSError, ValueError) as exc:
        raise CompatibilityError("report directory must remain inside repository artifacts/") from exc
    try:
        parts = resolved.relative_to(root).parts
    except ValueError as exc:
        raise CompatibilityError("report directory must remain inside repository artifacts/") from exc
    if not resolved.is_relative_to(artifact_root):
        raise CompatibilityError("report directory must remain inside repository artifacts/")
    if resolved.exists() and not resolved.is_dir():
        raise CompatibilityError("report path exists and is not a directory")
    return resolved


def _atomic_write(path: Path, value: dict[str, Any]) -> None:
    rendered = (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n").encode("utf-8")
    if len(rendered) > MAX_OUTPUT_BYTES:
        raise CompatibilityError(f"artifact exceeds bounded output size of {MAX_OUTPUT_BYTES} bytes")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise CompatibilityError("artifact output file must not be a symlink")
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile("wb", dir=path.parent, prefix=f".{path.name}.", delete=False) as handle:
            temporary = handle.name
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", default=DEFAULT_REPORT, help="versioned output directory under artifacts/")
    parser.add_argument("--root", default=str(ROOT), help="repository root")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        root = Path(args.root).expanduser().resolve()
        if not root.is_dir():
            raise CompatibilityError("repository root is not a directory")
        artifact_dir = resolve_artifact_dir(root, args.report)
        manifest, report = inspect_repository(root)
        _atomic_write(artifact_dir / "manifest.v1.json", manifest)
        _atomic_write(artifact_dir / "report.v1.json", report)
    except (CompatibilityError, OSError) as exc:
        print(f"check_mcp_compat: FAIL: {exc}", file=sys.stderr)
        return 1
    print(f"check_mcp_compat: {report['status'].upper()} (static MCP declarations; live Chrome not run)")
    if report["status"] != "pass":
        failed = [check["id"] for check in report["checks"] if check["status"] != "pass"]
        print("failed checks: " + ", ".join(failed), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
