#!/usr/bin/env python3
"""Build a static, fail-closed inventory of the shipped host-backed MCP surface.

This checker inspects repository declarations only. It does not start MCP, a
host, an extension, or Chrome, and cannot establish live-browser compatibility.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import tomllib

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT = "artifacts/p8-mcp-compatibility"
MAX_INPUT_BYTES = 5 * 1024 * 1024
MAX_OUTPUT_BYTES = 2 * 1024 * 1024
EXPECTED_OFFLINE_TOOLS = 29
EXPECTED_REMOTE_TOOLS = 30
FORBIDDEN_DEPENDENCIES = {"agentyc-cdp", "agentyc-browser", "agentyc-runtime"}
FORBIDDEN_SOURCE = re.compile(
    r"\bagentyc_cdp\b|\bCdpClient\b|\bBrowserRuntime\b|"
    r"\bactive_page\b|\bclose_all\b|\bTarget\s*\.|"
    r"\bRuntime\s*\.\s*evaluate\b|\b(?:tab_id|target_id|debugger_id|"
    r"cdp_url|websocket_url|tabId|targetId|debuggerId|cdpUrl)\b"
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
            output.append(char)
            i += 1
            continue
        elif char == "'":
            in_char = True
        output.append(char)
        i += 1
    return "".join(output)


def _source_digest(path: Path, root: Path) -> dict[str, str]:
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def _without_test_modules(source: str) -> str:
    lines = source.splitlines(keepends=True)
    kept: list[str] = []
    index = 0
    while index < len(lines):
        if re.match(r"\\s*#\\[cfg\\(test\\)\\]\\s*$", lines[index]):
            module_index = index + 1
            while module_index < len(lines) and not re.search(r"\\bmod\\s+\\w+\\s*\\{", lines[module_index]):
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


def _add_check(checks: list[dict[str, Any]], check_id: str, passed: bool, detail: Any) -> None:
    checks.append({"id": check_id, "status": "pass" if passed else "fail", "detail": detail})


def _tool_declarations(source: str) -> list[str]:
    pattern = re.compile(
        r"#\[rmcp::tool\([\s\S]*?\bname\s*=\s*\"([^\"]+)\"[\s\S]*?\)\]\s*"
        r"async\s+fn\s+(\w+)\s*\("
    )
    pairs = pattern.findall(source)
    return [name for name, function in pairs if name == function]


def _remote_tool_declarations(source: str) -> list[dict[str, Any]]:
    start = source.find("const REMOTE_TOOL_SPECS")
    if start < 0:
        return []
    end = source.find("\n];", start)
    if end < 0:
        return []
    block = source[start:end]
    pattern = re.compile(
        r"RemoteToolSpec\s*\{\s*name:\s*\"([^\"]+)\"[\s\S]*?"
        r"supported_by_local_protocol:\s*(true|false),\s*\}"
    )
    return [
        {"name": name, "local_protocol": supported == "true"}
        for name, supported in pattern.findall(block)
    ]


def _report_reference(root: Path, requested: str | Path) -> str:
    candidate = Path(requested).expanduser()
    if not candidate.is_absolute():
        return candidate.as_posix()
    try:
        return candidate.resolve().relative_to(root.resolve()).as_posix()
    except (OSError, ValueError):
        return DEFAULT_REPORT


def _add_common_artifact_envelope(
    manifest: dict[str, Any],
    report: dict[str, Any],
    *,
    root: Path,
    report_reference: str | Path,
) -> None:
    timestamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    nonce = secrets.token_hex(16)
    command = ["python3", "scripts/check_mcp_compat.py", "--report", _report_reference(root, report_reference)]
    build_tuple = {
        "phase": 0,
        "artifact_kind": "mcp-compatibility",
        "producer": "scripts/check_mcp_compat.py",
        "producer_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    environment = {
        "platform": sys.platform,
        "python": sys.version.split()[0],
        "cwd": "repository-relative",
        "network": "forbidden",
        "browser_launch": False,
        "browser_download": False,
        "browser_attach": False,
    }
    redaction_status = {
        "status": "applied",
        "policy": "bounded-static-source-envelope; logical tool names preserved",
        "raw_browser_ids": False,
        "secrets": False,
        "absolute_paths": False,
        "page_bodies": False,
        "errors": False,
        "recursive_identifier_redaction": False,
    }
    common = {
        "schema_version": 1,
        "build_tuple": build_tuple,
        "environment": environment,
        "timestamp": timestamp,
        "nonce": nonce,
        "command": command,
        "provenance": {"nonce": nonce, "timestamp": timestamp, "command": command, "build_tuple": build_tuple},
        "redaction_status": redaction_status,
    }
    manifest.update(common)
    manifest["result"] = {"status": report["status"], "kind": "manifest"}
    report.update(common)
    report["result"] = {"status": report["status"], "kind": "report"}


def inspect_repository(
    root: Path,
    report_reference: str | Path = DEFAULT_REPORT,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Inspect source evidence and return a versioned manifest and report."""
    root = root.resolve()
    evidence_paths = {
        "package": root / "crates/agentyc-mcp/Cargo.toml",
        "library": root / "crates/agentyc-mcp/src/lib.rs",
        "host_server": root / "crates/agentyc-mcp/src/host_server.rs",
        "remote_server": root / "crates/agentyc-mcp/src/remote_host_server.rs",
        "host_adapter": root / "crates/agentyc-mcp/src/host_adapter.rs",
        "compatibility_docs": root / "docs/mcp-compatibility.md",
        "phase_plan": root / "docs/exec-plans/active/agentyc-browser-task-spaces/plans/phase-8-mcp-compatibility.md",
        "protocol_test": root / "tests/mcp_protocol.rs",
    }
    checks: list[dict[str, Any]] = []
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

    package: dict[str, Any] = {}
    try:
        package = tomllib.loads(loaded["package"])
    except (KeyError, tomllib.TOMLDecodeError) as exc:
        _add_check(checks, "package.toml", False, f"missing or invalid package manifest: {exc}")
    else:
        dependencies = package.get("dependencies", {})
        direct = sorted(FORBIDDEN_DEPENDENCIES.intersection(dependencies if isinstance(dependencies, dict) else {}))
        features = package.get("features", {})
        legacy_feature = isinstance(features, dict) and "legacy-cdp" in features
        _add_check(
            checks,
            "architecture.host_only_dependencies",
            isinstance(dependencies, dict) and not direct and not legacy_feature,
            {"forbidden_production_dependencies": direct, "legacy_cdp_feature": legacy_feature},
        )

    mcp_source_root = root / "crates/agentyc-mcp/src"
    source_files = sorted(mcp_source_root.rglob("*.rs")) if mcp_source_root.is_dir() else []
    legacy_paths = [
        path.relative_to(root).as_posix()
        for path in source_files
        if path.name in {"legacy.rs", "state.rs"} or "tools" in path.relative_to(mcp_source_root).parts
    ]
    _add_check(checks, "architecture.legacy_modules_removed", not legacy_paths, {"legacy_paths": legacy_paths})

    bypasses: list[dict[str, Any]] = []
    for path in source_files:
        if path.name == "host_adapter_audit.rs":
            continue
        try:
            source = _without_test_modules(_strip_rust_comments(_read_text(path)))
        except CompatibilityError as exc:
            bypasses.append({"path": path.relative_to(root).as_posix(), "error": str(exc)})
            continue
        for match in FORBIDDEN_SOURCE.finditer(source):
            bypasses.append({
                "path": path.relative_to(root).as_posix(),
                "line": source.count("\n", 0, match.start()) + 1,
                "evidence": match.group(0),
            })
    _add_check(checks, "architecture.no_direct_browser_or_raw_id_authority", bool(source_files) and not bypasses, bypasses)

    offline_tools = _tool_declarations(loaded.get("host_server", ""))
    remote_tools = _remote_tool_declarations(loaded.get("remote_server", ""))
    offline_duplicates = sorted({name for name in offline_tools if offline_tools.count(name) > 1})
    remote_names = [item["name"] for item in remote_tools]
    remote_duplicates = sorted({name for name in remote_names if remote_names.count(name) > 1})
    all_host_names = offline_tools + remote_names
    non_host_names = sorted({name for name in all_host_names if not name.startswith("host_")})
    _add_check(
        checks,
        "tools.host_only_inventory",
        len(offline_tools) == EXPECTED_OFFLINE_TOOLS
        and len(remote_tools) == EXPECTED_REMOTE_TOOLS
        and not offline_duplicates
        and not remote_duplicates
        and not non_host_names,
        {
            "offline_count": len(offline_tools),
            "remote_count": len(remote_tools),
            "offline_duplicates": offline_duplicates,
            "remote_duplicates": remote_duplicates,
            "non_host_names": non_host_names,
            "expected": {"offline": EXPECTED_OFFLINE_TOOLS, "remote": EXPECTED_REMOTE_TOOLS},
            "offline_tools": offline_tools,
            "remote_tools": remote_tools,
        },
    )
    unavailable = sorted(item["name"] for item in remote_tools if not item["local_protocol"])
    _add_check(
        checks,
        "tools.unsupported_routes_are_explicit",
        len(unavailable) == 12 and all(name.startswith("host_") for name in unavailable),
        {"capability_unavailable_count": len(unavailable), "tools": unavailable},
    )

    protocol_text = loaded.get("protocol_test", "")
    _add_check(
        checks,
        "protocol.test_covers_host_only_tools",
        'assert_eq!(tools.len(), 29)' in protocol_text
        and 'name.starts_with("host_")' in protocol_text
        and 'name.starts_with("browser_")' in protocol_text,
        {"test": "tests/mcp_protocol.rs", "expected_tools": EXPECTED_OFFLINE_TOOLS},
    )
    docs_text = loaded.get("compatibility_docs", "").lower()
    plan_text = loaded.get("phase_plan", "").lower()
    _add_check(
        checks,
        "docs.legacy_removal_and_live_gate_are_explicit",
        "have been removed" in docs_text
        and "not distribution-ready" in docs_text
        and "removed" in plan_text
        and "headed chrome" in plan_text,
        {"docs": "docs/mcp-compatibility.md", "plan": "Phase 8"},
    )

    adapter = loaded.get("host_adapter", "")
    required_error_markers = [
        "CallToolResult::structured_error",
        '"code"',
        '"retryable"',
        '"message"',
        '"action_id"',
        '"reconcile_token"',
        '"next_action"',
    ]
    missing_error_markers = [marker for marker in required_error_markers if marker not in adapter]
    _add_check(
        checks,
        "errors.structured_host_metadata",
        not missing_error_markers,
        {"missing_markers": missing_error_markers},
    )

    hashes = [_source_digest(path, root) for key, path in evidence_paths.items() if key in loaded]
    manifest = {
        "schema_version": 1,
        "manifest_version": "2.0.0",
        "kind": "static-host-backed-mcp-inventory",
        "scope": "repository declarations only; not protocol execution or live-browser evidence",
        "profile_counts": {"offline": len(offline_tools), "remote": len(remote_names)},
        "tools": {"offline": offline_tools, "remote": remote_tools},
        "evidence": hashes,
    }
    passed = bool(checks) and all(check["status"] == "pass" for check in checks)
    report = {
        "schema_version": 1,
        "report_version": "2.0.0",
        "kind": "static-host-backed-mcp-check",
        "status": "pass" if passed else "fail",
        "checks": checks,
        "live_chrome": {"status": "not_run", "claim": False},
        "artifacts": {"manifest": "manifest.v1.json", "report": "report.v1.json"},
    }
    _add_common_artifact_envelope(manifest, report, root=root, report_reference=report_reference)
    return manifest, report


def resolve_artifact_dir(root: Path, requested: str) -> Path:
    """Require a repository-local artifacts directory and reject symlinks."""
    root = root.resolve()
    artifact_root = root / "artifacts"
    candidate = Path(requested).expanduser()
    if ".." in candidate.parts:
        raise CompatibilityError("report path must not contain parent traversal")
    lexical_target = Path(os.path.abspath(candidate if candidate.is_absolute() else root / candidate))
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
    if not resolved.is_relative_to(artifact_root) or (resolved.exists() and not resolved.is_dir()):
        raise CompatibilityError("report directory must remain inside repository artifacts/")
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
        manifest, report = inspect_repository(root, artifact_dir.relative_to(root).as_posix())
        _atomic_write(artifact_dir / "manifest.v1.json", manifest)
        _atomic_write(artifact_dir / "report.v1.json", report)
    except (CompatibilityError, OSError) as exc:
        print(f"check_mcp_compat: FAIL: {exc}", file=sys.stderr)
        return 1
    print(f"check_mcp_compat: {report['status'].upper()} (static host-backed MCP declarations; live Chrome not run)")
    if report["status"] != "pass":
        failed = [check["id"] for check in report["checks"] if check["status"] != "pass"]
        print("failed checks: " + ", ".join(failed), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
