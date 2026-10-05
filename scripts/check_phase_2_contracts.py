#!/usr/bin/env python3
"""Check the deterministic Phase 2 contract/evidence slice.

This gate is deliberately read-only and stdlib-only.  It validates versioned
repository declarations, source/test references, sanitized MCP fixtures, and a
bounded Markdown artifact.  It never invokes cargo, a browser, a host, an
extension, an MCP server, a network client, or a subprocess.  A pass proves
repository-contract evidence only; it is not live Chrome, integration, or
release evidence.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MAX_INPUT_BYTES = 4 * 1024 * 1024
MAX_MANIFEST_BYTES = 2 * 1024 * 1024
MAX_ARTIFACT_BYTES = 4 * 1024 * 1024

MANIFEST_PATH = Path("tests/phase-2-manifest.yaml")
ARTIFACT_PATH = Path("artifacts/p2-contracts-review.md")
TRACEABILITY_PATH = Path("docs/contract-traceability-phase-2.md")
PHASE_PLAN_PATH = Path(
    "docs/exec-plans/active/agentyc-browser-task-spaces/plans/phase-2-contracts.md"
)
PHASE_1_PLAN_PATH = Path(
    "docs/exec-plans/active/agentyc-browser-task-spaces/plans/phase-1-architecture.md"
)
PLAN_INDEX_PATH = Path(
    "docs/exec-plans/active/agentyc-browser-task-spaces/plans/PLAN_INDEX.md"
)
README_PATH = Path("docs/exec-plans/active/agentyc-browser-task-spaces/README.md")
TOOL_CATALOG_PATH = Path("tests/fixtures/mcp/tool_catalog.json")
OPERATIONS_PATH = Path("packages/agentyc-browser/src/operations.mjs")

CANONICAL_ERRORS = (
    "extension_not_connected",
    "profile_not_found",
    "space_required",
    "space_not_found",
    "space_forbidden",
    "user_control_required",
    "lease_expired",
    "stale_lease",
    "page_not_found",
    "page_not_owned",
    "unmanaged_page",
    "stale_ref",
    "target_replaced",
    "event_lagged",
    "unknown_outcome",
    "reconciliation_required",
    "capability_unavailable",
    "permission_denied",
    "native_host_unavailable",
    "protocol_mismatch",
    "message_too_large",
    "invalid_argument",
    "timeout",
    "cancelled",
    "host_draining",
    "ledger_incompatible",
    "truncated_frame",
    "invalid_utf8",
    "invalid_json",
)

RETRYABLE_ERRORS = {
    "extension_not_connected",
    "native_host_unavailable",
    "event_lagged",
    "timeout",
    "host_draining",
}
GUIDANCE_BY_ERROR = {
    "lease_expired": "refresh_lease",
    "stale_lease": "refresh_lease",
    "stale_ref": "resync",
    "target_replaced": "resync",
    "event_lagged": "resync",
    "unknown_outcome": "reconcile",
    "reconciliation_required": "reconcile",
    "user_control_required": "await_user_control",
    "unmanaged_page": "claim",
    "page_not_owned": "claim",
    "extension_not_connected": "retry",
    "native_host_unavailable": "retry",
    "timeout": "retry",
    "host_draining": "retry",
}
for _error in CANONICAL_ERRORS:
    GUIDANCE_BY_ERROR.setdefault(_error, "none")

PHASE_TASK_IDS = tuple(f"P2-T{index}" for index in range(1, 9))
QUALITY_IDS = tuple(f"Q2-{index:02d}" for index in range(1, 12))

SAFE_REDACTIONS = {
    "<redacted>",
    "<redacted-id>",
    "<redacted id>",
    "<redacted browser id>",
    "<redacted session id>",
    "<redacted token>",
    "[redacted]",
    "[redacted-id]",
    "not-authoritative",
    "untrusted",
    "internal",
    "null",
    "none",
    "false",
    "true",
}

RAW_ASSIGNMENT = re.compile(
    r"(?im)(?:[\"'`]?)"
    r"(tab[_-]?id|target[_-]?id|session[_-]?id|group[_-]?id|"
    r"current[_-]?tab[_-]?id|debugger[_-]?endpoint|websocket[_-]?url|"
    r"cdp[_-]?url|debugger[_-]?id)"
    r"(?:[\"'`]?)\s*[:=]\s*[\"']?([^,\s\"'}\]]+)"
)
BRACKETED_NAME = re.compile(r"\[[^\]]*id[^\]]*\]\s+name", re.IGNORECASE)
SECRET_ASSIGNMENT = re.compile(
    r"(?i)(?:token|secret|password|passwd|authorization|credential)"
    r"\s*[:=]\s*(?!null\b|false\b|true\b|<redacted|\[redacted)[^\s,}]+"
)
EXTERNAL_URL = re.compile(r"(?i)\b(?:https?|wss?|ftp)://")

MANIFEST_KEYS = {
    "schema_version",
    "phase",
    "kind",
    "status",
    "evidence_mode",
    "release_eligible",
    "live_claims",
    "phase_plan",
    "artifact",
    "traceability",
    "fixture_index",
    "framing",
    "phase_1",
    "tasks",
    "quality_checklist",
    "identity",
    "mappings",
    "error_fixture",
    "mcp_manifests",
    "commands",
    "nonclaims",
}


class ContractError(ValueError):
    """A required Phase 2 evidence invariant is missing or unsafe."""


def safe_root(value: str | Path | None = None) -> Path:
    """Resolve a repository root without following a root symlink."""
    candidate = Path(value).expanduser() if value is not None else ROOT
    if candidate.is_symlink() or not candidate.is_dir():
        raise ContractError("repository root is not a non-symlink directory")
    return candidate.resolve()


def _relative_path(value: str | Path, name: str) -> Path:
    path = Path(value)
    if not str(value) or path.is_absolute() or ".." in path.parts:
        raise ContractError(f"{name} must be a repository-relative path")
    if path == Path(".") or any(part in {"", "."} for part in path.parts):
        raise ContractError(f"{name} is not a normalized path")
    if any(char in str(path) for char in "*?[]"):
        raise ContractError(f"{name} may not contain glob characters")
    return path


def read_bounded(root: Path, relative: str | Path, *, limit: int = MAX_INPUT_BYTES) -> str:
    """Read one regular, non-symlink repository file within a byte bound."""
    root = safe_root(root)
    path = _relative_path(relative, "input path")
    candidate = root.joinpath(path)
    current = root
    for component in path.parts:
        current = current / component
        if current.is_symlink():
            raise ContractError(f"{path.as_posix()} contains a symlink component")
    try:
        resolved = candidate.resolve()
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise ContractError(f"{path.as_posix()} escapes the repository root") from exc
    if resolved.is_symlink() or not resolved.is_file():
        raise ContractError(f"{path.as_posix()} is missing or not a regular file")
    try:
        if resolved.stat().st_size > limit:
            raise ContractError(f"{path.as_posix()} exceeds the bounded read limit")
        return resolved.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ContractError(f"{path.as_posix()} is unreadable") from exc


def _read_external_bounded(path: Path, *, limit: int) -> str:
    """Read a parser input when tests call parse_* directly."""
    if path.is_symlink() or not path.is_file():
        raise ContractError(f"{path} is missing or is a symlink")
    try:
        if path.stat().st_size > limit:
            raise ContractError(f"{path} exceeds the bounded read limit")
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ContractError(f"{path} is unreadable") from exc


def _parse_yaml_subset(text: str, label: str) -> Any:
    """Parse the small YAML subset used by the manifest.

    JSON is valid YAML and is preferred for the checked-in manifest.  This
    fallback accepts indentation-based mappings/lists and JSON-style flow
    values, without importing a YAML implementation or accepting aliases,
    tags, anchors, or implicit executable content.
    """

    lines: list[tuple[int, str]] = []
    raw_lines = text.splitlines()
    index = 0
    while index < len(raw_lines):
        number = index + 1
        raw = raw_lines[index]
        if "\t" in raw:
            raise ContractError(f"{label} line {number} uses tabs")
        content = raw.lstrip(" ")
        if not content or content.startswith("#"):
            index += 1
            continue
        indent = len(raw) - len(content)
        if content.startswith(("[", "{")):
            parts = [content]
            depth = (
                content.count("[")
                - content.count("]")
                + content.count("{")
                - content.count("}")
            )
            while depth > 0:
                index += 1
                if index >= len(raw_lines):
                    raise ContractError(f"{label} has an unterminated flow collection")
                continuation = raw_lines[index]
                if "\t" in continuation:
                    raise ContractError(f"{label} line {index + 1} uses tabs")
                part = continuation.strip()
                if part and not part.startswith("#"):
                    parts.append(part)
                depth += part.count("[") - part.count("]")
                depth += part.count("{") - part.count("}")
            joined = " ".join(parts)
            if lines and lines[-1][1].endswith(":"):
                previous_indent, previous_content = lines[-1]
                lines[-1] = (previous_indent, f"{previous_content} {joined}")
            else:
                lines.append((indent, joined))
        else:
            lines.append((indent, content))
        index += 1

    if not lines:
        raise ContractError(f"{label} is empty")

    def scalar(value: str) -> Any:
        value = value.strip()
        if not value:
            return None
        if value.startswith(("[", "{", '"')):
            try:
                return json.loads(re.sub(r",\s*([}\]])", r"\1", value))
            except json.JSONDecodeError as exc:
                raise ContractError(f"{label} has an invalid flow scalar") from exc
        if value.startswith("'") and value.endswith("'"):
            return value[1:-1].replace("''", "'")
        if value in {"true", "True"}:
            return True
        if value in {"false", "False"}:
            return False
        if value in {"null", "Null", "NULL", "~"}:
            return None
        if re.fullmatch(r"-?\d+", value):
            return int(value)
        if re.fullmatch(r"-?(?:\d+\.\d*|\d*\.\d+)(?:[eE][+-]?\d+)?", value):
            return float(value)
        return value

    def pair(content: str) -> tuple[str, str]:
        if ":" not in content:
            raise ContractError(f"{label} mapping entry has no colon")
        key, value = content.split(":", 1)
        key = key.strip()
        if not key or any(char in key for char in "{}[]"):
            raise ContractError(f"{label} has an invalid mapping key: {key!r}")
        return key, value.strip()

    def node(position: int, indent: int) -> tuple[Any, int]:
        if position >= len(lines) or lines[position][0] != indent:
            raise ContractError(f"{label} has invalid indentation")
        if lines[position][1] == "-" or lines[position][1].startswith("- "):
            return list_node(position, indent)
        return mapping(position, indent)

    def nested(position: int, parent_indent: int) -> tuple[Any, int]:
        if position >= len(lines) or lines[position][0] <= parent_indent:
            return {}, position
        return node(position, lines[position][0])

    def mapping(position: int, indent: int) -> tuple[dict[str, Any], int]:
        result: dict[str, Any] = {}
        while (
            position < len(lines)
            and lines[position][0] == indent
            and not lines[position][1].startswith("-")
        ):
            key, value = pair(lines[position][1])
            if key in result:
                raise ContractError(f"{label} has duplicate key {key}")
            position += 1
            if value:
                result[key] = scalar(value)
            else:
                result[key], position = nested(position, indent)
        return result, position

    def list_node(position: int, indent: int) -> tuple[list[Any], int]:
        result: list[Any] = []
        while position < len(lines) and lines[position][0] == indent and lines[position][1].startswith("-"):
            content = lines[position][1][1:].strip()
            position += 1
            if not content:
                item, position = nested(position, indent)
                result.append(item)
                continue
            if ":" not in content:
                result.append(scalar(content))
                continue
            key, value = pair(content)
            item: dict[str, Any] = {}
            if value:
                item[key] = scalar(value)
            else:
                item[key], position = nested(position, indent)
            if position < len(lines) and lines[position][0] > indent:
                continuation, position = mapping(position, lines[position][0])
                for continuation_key, continuation_value in continuation.items():
                    if continuation_key in item:
                        raise ContractError(f"{label} has duplicate list-item key")
                    item[continuation_key] = continuation_value
            result.append(item)
        return result, position

    value, position = node(0, lines[0][0])
    if position != len(lines):
        raise ContractError(f"{label} has trailing or mis-indented content")
    return value


def _remove_json_trailing_commas(text: str) -> str:
    """Accept the checked-in JSON-with-trailing-commas subset safely."""
    output: list[str] = []
    index = 0
    in_string = False
    escaped = False
    while index < len(text):
        char = text[index]
        if in_string:
            output.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
            output.append(char)
            index += 1
            continue
        if char == ",":
            lookahead = index + 1
            while lookahead < len(text) and text[lookahead].isspace():
                lookahead += 1
            if lookahead < len(text) and text[lookahead] in "}]":
                index += 1
                continue
        output.append(char)
        index += 1
    return "".join(output)


def _parse_document(text: str, label: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        try:
            return json.loads(_remove_json_trailing_commas(text))
        except json.JSONDecodeError:
            return _parse_yaml_subset(text, label)


def parse_manifest(path: str | Path) -> dict[str, Any]:
    """Parse a Phase 2 YAML/JSON manifest without writing or executing anything."""
    path = Path(path)
    if ".." in path.parts:
        raise ContractError("manifest path traversal is forbidden")
    value = _parse_document(
        _read_external_bounded(path, limit=MAX_MANIFEST_BYTES), str(path)
    )
    if not isinstance(value, dict):
        raise ContractError("Phase 2 manifest root must be an object")
    return value


def parse_artifact(path: str | Path) -> dict[str, Any]:
    """Extract exactly one versioned JSON artifact block."""
    path = Path(path)
    if ".." in path.parts:
        raise ContractError("artifact path traversal is forbidden")
    text = _read_external_bounded(path, limit=MAX_ARTIFACT_BYTES)
    matches = re.findall(
        r"(?ms)^```json[ \t]+phase-2-contracts-v1[ \t]*\n(.*?)^```[ \t]*$",
        text,
    )
    if len(matches) != 1:
        raise ContractError("artifact must contain exactly one phase-2 JSON fence")
    try:
        value = json.loads(matches[0])
    except json.JSONDecodeError as exc:
        raise ContractError("artifact JSON block is invalid") from exc
    if not isinstance(value, dict):
        raise ContractError("artifact JSON block must be an object")
    return value


def _mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError(f"{name} must be an object")
    return value


def _list(value: Any, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise ContractError(f"{name} must be a list")
    return value


def _string(value: Any, name: str, *, nonempty: bool = True) -> str:
    if not isinstance(value, str) or (nonempty and not value):
        raise ContractError(f"{name} must be a non-empty string")
    if "\x00" in value:
        raise ContractError(f"{name} contains NUL")
    return value


def _path_list(value: Any, name: str) -> list[str]:
    values = _list(value, name)
    result: list[str] = []
    for index, item in enumerate(values):
        text = _string(item, f"{name}[{index}]")
        _relative_path(text, f"{name}[{index}]")
        result.append(text)
    if not result:
        raise ContractError(f"{name} must not be empty")
    return result


def _test_ref_parts(reference: Any, name: str) -> tuple[str, str]:
    text = _string(reference, name)
    if text.count("::") != 1:
        raise ContractError(f"{name} must use path::symbol form")
    path, symbol = text.split("::", 1)
    _relative_path(path, name)
    if not symbol or any(char in symbol for char in "\n\r"):
        raise ContractError(f"{name} has an invalid symbol")
    return path, symbol


def _require_exact_keys(value: dict[str, Any], expected: set[str], name: str) -> None:
    actual = set(value)
    missing = expected - actual
    extra = actual - expected
    if missing or extra:
        details = []
        if missing:
            details.append("missing " + ", ".join(sorted(missing)))
        if extra:
            details.append("unexpected " + ", ".join(sorted(extra)))
        raise ContractError(f"{name} structure mismatch: {'; '.join(details)}")


def _assert_file_paths(root: Path, paths: Iterable[str], name: str) -> None:
    for path in paths:
        read_bounded(root, path)


def _symbol_exists(root: Path, reference: str) -> None:
    path_text, symbol = _test_ref_parts(reference, "test reference")
    source = read_bounded(root, path_text)
    path = Path(path_text)
    if path.suffix == ".rs":
        pattern = rf"\b(?:async\s+)?fn\s+{re.escape(symbol)}\b"
        if not re.search(pattern, source):
            raise ContractError(f"test symbol is missing: {reference}")
        return
    if path.suffix in {".mjs", ".js", ".ts", ".tsx"}:
        patterns = (
            rf"\b(?:test|it)\(\s*\"{re.escape(symbol)}\"",
            rf"\b(?:test|it)\(\s*'{re.escape(symbol)}'",
        )
        if not any(re.search(pattern, source) for pattern in patterns):
            raise ContractError(f"test title is missing: {reference}")
        return
    if re.search(rf"\b{re.escape(symbol)}\b", source) is None:
        raise ContractError(f"test symbol is missing: {reference}")


def _validate_manifest_shape(manifest: dict[str, Any]) -> None:
    _require_exact_keys(manifest, MANIFEST_KEYS, "manifest")
    if manifest.get("schema_version") != 1 or manifest.get("phase") != 2:
        raise ContractError("manifest schema_version 1 and phase 2 are required")
    if manifest.get("kind") != "phase-2-contract-manifest":
        raise ContractError("manifest kind is invalid")
    if manifest.get("status") not in {"active", "complete"}:
        raise ContractError("Phase 2 manifest status must be active or complete")
    if manifest.get("evidence_mode") != "deterministic_repository_contracts":
        raise ContractError("manifest evidence mode must be deterministic repository contracts")
    if manifest.get("release_eligible") is not False or manifest.get("live_claims") is not False:
        raise ContractError("Phase 2 manifest cannot claim release or live evidence")
    for key, expected in {
        "phase_plan": PHASE_PLAN_PATH.as_posix(),
        "artifact": ARTIFACT_PATH.as_posix(),
        "traceability": TRACEABILITY_PATH.as_posix(),
        "fixture_index": "tests/fixtures/mcp/index.v1.json",
        "error_fixture": "tests/fixtures/mcp/errors/v1.json",
    }.items():
        if manifest.get(key) != expected:
            raise ContractError(f"manifest.{key} must be {expected}")
    commands = _list(manifest.get("commands"), "manifest.commands")
    required_commands = {
        "python3 scripts/check_exec_plan.py docs/exec-plans/active/agentyc-browser-task-spaces",
        "python3 scripts/check_phase_0_baseline.py",
        "python3 scripts/check_phase_1_quality.py",
        "python3 scripts/check_phase_2_contracts.py",
        "cargo test -p agentyc-core --locked",
        "cargo test -p agentyc-host --locked",
        "cargo test -p agentyc --locked",
        "cargo test -p agentyc-mcp --locked",
        "npm test --prefix extension",
        "npm test --prefix packages/agentyc-browser",
        "npm run check --prefix packages/agentyc-browser",
    }
    seen_commands: set[str] = set()
    for index, entry in enumerate(commands):
        entry = _mapping(entry, f"manifest.commands[{index}]")
        _require_exact_keys(entry, {"command", "result"}, f"manifest.commands[{index}]")
        command = _string(entry.get("command"), f"manifest.commands[{index}].command")
        result = _string(entry.get("result"), f"manifest.commands[{index}].result")
        if command in seen_commands:
            raise ContractError(f"manifest.commands contains a duplicate: {command}")
        seen_commands.add(command)
        if not (result == "pass" or result.startswith("unavailable:")):
            raise ContractError(f"manifest.commands[{index}].result is not an accepted bounded result")
        if any(marker in command for marker in ("--cdp-url", "serve", "repl", "http://", "https://")):
            raise ContractError(f"manifest.commands[{index}] is outside deterministic Phase 2 scope")
    if not required_commands.issubset(seen_commands):
        missing = sorted(required_commands - seen_commands)
        raise ContractError(f"manifest.commands is missing required validation commands: {missing}")
    if not isinstance(manifest.get("nonclaims"), list) or len(manifest["nonclaims"]) < 5:
        raise ContractError("manifest must state Phase 1, Phase 2, live, integration, and later-phase nonclaims")
    for index, nonclaim in enumerate(manifest["nonclaims"]):
        _string(nonclaim, f"manifest.nonclaims[{index}]")

    phase_1 = _mapping(manifest["phase_1"], "manifest.phase_1")
    _require_exact_keys(phase_1, {"status", "preserved", "plan", "nonclaims"}, "manifest.phase_1")
    if phase_1.get("status") != "complete" or phase_1.get("preserved") is not True:
        raise ContractError("Phase 1 must remain complete and preserved")
    if phase_1.get("plan") != PHASE_1_PLAN_PATH.as_posix():
        raise ContractError("manifest.phase_1.plan is invalid")
    if not _list(phase_1.get("nonclaims"), "manifest.phase_1.nonclaims"):
        raise ContractError("Phase 1 nonclaims are required")


def validate_framing(root: Path, framing: Any) -> None:
    framing = _mapping(framing, "manifest.framing")
    _require_exact_keys(framing, {"distinct", "local", "native"}, "manifest.framing")
    if framing.get("distinct") is not True:
        raise ContractError("local and Native Messaging framing must be explicitly distinct")
    expected = {
        "local": ("big_endian", "local IPC", ("crates/agentyc-core/src/protocol.rs", "crates/agentyc-host/src/local_ipc.rs")),
        "native": ("little_endian", "Native Messaging", ("crates/agentyc-host/src/native_messaging.rs", "extension/src/native-messaging.mjs")),
    }
    for name, (byte_order, label, required_paths) in expected.items():
        entry = _mapping(framing[name], f"manifest.framing.{name}")
        _require_exact_keys(entry, {"byte_order", "prefix_bytes", "source_paths", "markers"}, f"manifest.framing.{name}")
        if entry.get("byte_order") != byte_order or entry.get("prefix_bytes") != 4:
            raise ContractError(f"{label} framing declaration is invalid")
        paths = _path_list(entry.get("source_paths"), f"manifest.framing.{name}.source_paths")
        for required in required_paths:
            if required not in paths:
                raise ContractError(f"{label} framing source is missing: {required}")
        markers = _list(entry.get("markers"), f"manifest.framing.{name}.markers")
        if not markers or any(not isinstance(marker, str) or not marker for marker in markers):
            raise ContractError(f"{label} framing markers are required")
        combined = "\n".join(read_bounded(root, path) for path in paths)
        lower = combined.lower()
        if name == "local":
            if "big-endian" not in lower and "big_endian" not in lower and "to_be_bytes" not in lower:
                raise ContractError("local IPC source does not prove big-endian framing")
            if "little-endian" in lower and "separate" not in lower:
                raise ContractError("local IPC source conflates Native Messaging framing")
        else:
            if "little-endian" not in lower and "little_endian" not in lower and "to_ne_bytes" not in lower:
                raise ContractError("Native Messaging source does not prove little-endian/native framing")
            if "big-endian" not in lower and "big_endian" not in lower:
                raise ContractError("Native Messaging source does not distinguish local big-endian framing")
            if "allowed_extension_origins" not in lower or "wildcards are forbidden" not in lower:
                raise ContractError("Native Messaging exact-origin allowlist evidence is missing")
            origin_match = re.search(r"ALLOWED_EXTENSION_ORIGINS[^=]*=\s*&?\[([^\]]*)\]", combined)
            if origin_match and "*" in origin_match.group(1):
                raise ContractError("Native Messaging extension-origin allowlist contains a wildcard")
        for marker in markers:
            if marker.lower() not in lower:
                raise ContractError(f"framing marker is absent from declared source: {marker}")


def _validate_task(root: Path, task: Any, index: int) -> None:
    name = f"manifest.tasks[{index}]"
    task = _mapping(task, name)
    _require_exact_keys(task, {"id", "checked", "required_modules", "fixtures", "tests", "evidence_paths", "notes"}, name)
    task_id = task.get("id")
    if task_id not in PHASE_TASK_IDS:
        raise ContractError(f"unexpected task id: {task_id}")
    if task.get("checked") is not True:
        raise ContractError(f"{task_id} is not checked")
    modules = _path_list(task.get("required_modules"), f"{name}.required_modules")
    fixtures = _path_list(task.get("fixtures"), f"{name}.fixtures")
    evidence = _path_list(task.get("evidence_paths"), f"{name}.evidence_paths")
    tests = _list(task.get("tests"), f"{name}.tests")
    if not tests:
        raise ContractError(f"{task_id} has no exact test references")
    for test_index, reference in enumerate(tests):
        _test_ref_parts(reference, f"{name}.tests[{test_index}]")
    _string(task.get("notes"), f"{name}.notes")
    _assert_file_paths(root, modules, f"{task_id} modules")
    _assert_file_paths(root, fixtures, f"{task_id} fixtures")
    _assert_file_paths(root, evidence, f"{task_id} evidence")
    for reference in tests:
        _symbol_exists(root, reference)


def validate_tasks(root: Path, tasks: Any) -> None:
    tasks = _list(tasks, "manifest.tasks")
    if len(tasks) != len(PHASE_TASK_IDS):
        raise ContractError("manifest must contain exactly P2-T1 through P2-T8")
    ids = [(_mapping(task, "task").get("id")) for task in tasks]
    if tuple(ids) != PHASE_TASK_IDS:
        raise ContractError("Phase 2 task order or IDs are invalid")
    for index, task in enumerate(tasks):
        _validate_task(root, task, index)


def validate_core_boundary(root: Path) -> None:
    """Keep the transport-neutral core free of browser/adapter dependencies."""
    cargo = read_bounded(root, "crates/agentyc-core/Cargo.toml").lower()
    if re.search(r"(?m)^\s*(?:[a-z0-9_-]*(?:chrome|cdp|mcp)[a-z0-9_-]*)\s*=", cargo):
        raise ContractError("agentyc-core declares a Chrome/CDP/MCP dependency")
    source_paths = (
        "crates/agentyc-core/src/lib.rs",
        "crates/agentyc-core/src/ids.rs",
        "crates/agentyc-core/src/records.rs",
        "crates/agentyc-core/src/states.rs",
        "crates/agentyc-core/src/errors.rs",
        "crates/agentyc-core/src/protocol.rs",
        "crates/agentyc-core/src/snapshots.rs",
        "crates/agentyc-core/src/actions.rs",
        "crates/agentyc-core/src/events.rs",
    )
    for path in source_paths:
        source = read_bounded(root, path)
        if re.search(r"(?im)^\\s*(?:use|extern\\s+crate|mod)\\s+[^;]*(?:chrome|cdp|mcp)", source):
            raise ContractError(f"agentyc-core source imports a browser or adapter dependency: {path}")


def validate_quality(root: Path, checklist: Any) -> None:
    checklist = _list(checklist, "manifest.quality_checklist")
    if len(checklist) != len(QUALITY_IDS):
        raise ContractError("manifest quality checklist is incomplete")
    ids = []
    for index, item in enumerate(checklist):
        name = f"manifest.quality_checklist[{index}]"
        item = _mapping(item, name)
        _require_exact_keys(item, {"id", "checked", "evidence_paths", "tests"}, name)
        ids.append(item.get("id"))
        if item.get("checked") is not True:
            raise ContractError(f"quality checklist item {item.get('id')} is not checked")
        evidence = _path_list(item.get("evidence_paths"), f"{name}.evidence_paths")
        tests = _list(item.get("tests"), f"{name}.tests")
        if not tests:
            raise ContractError(f"{name}.tests is empty")
        _assert_file_paths(root, evidence, f"{name}.evidence_paths")
        for test_index, reference in enumerate(tests):
            _test_ref_parts(reference, f"{name}.tests[{test_index}]")
            _symbol_exists(root, reference)
    if tuple(ids) != QUALITY_IDS:
        raise ContractError("quality checklist IDs are invalid or reordered")


def validate_identity(root: Path, identity_value: Any) -> list[str]:
    identity = _mapping(identity_value, "manifest.identity")
    _require_exact_keys(
        identity,
        {"canonical_fields", "allowlist", "primary_paths", "forbidden_markers", "safe_values"},
        "manifest.identity",
    )
    fields = _list(identity.get("canonical_fields"), "manifest.identity.canonical_fields")
    required_fields = {"space_id", "page_id", "frame_id", "document_id", "navigation_id", "action_id", "event_id", "ref_id", "snapshot_id", "profile_instance_id", "broker_epoch", "connection_epoch", "browser_session_epoch", "worker_instance_epoch"}
    if set(fields) != required_fields:
        raise ContractError("canonical logical identity field set is incomplete or changed")
    primary_paths = _path_list(identity.get("primary_paths"), "manifest.identity.primary_paths")
    allowlist = _list(identity.get("allowlist"), "manifest.identity.allowlist")
    allowlisted_paths: list[str] = []
    for index, item in enumerate(allowlist):
        name = f"manifest.identity.allowlist[{index}]"
        item = _mapping(item, name)
        _require_exact_keys(item, {"path", "scope", "reason"}, name)
        path = _string(item.get("path"), f"{name}.path")
        _relative_path(path, f"{name}.path")
        if "*" in path:
            raise ContractError("identity allowlist may not use wildcard paths")
        if path.endswith("/legacy.rs") or "/tools/" in path:
            raise ContractError("removed legacy MCP paths may not be allowlisted")
        if item.get("scope") not in {"compatibility_only", "sanitized_fixture", "negative_fixture"}:
            raise ContractError(f"{name}.scope is not explicit")
        _string(item.get("reason"), f"{name}.reason")
        allowlisted_paths.append(path)
    _assert_file_paths(root, allowlisted_paths, "identity allowlist")
    for item in allowlist:
        if item["scope"] == "compatibility_only":
            text = read_bounded(root, item["path"]).lower()
            if not any(marker in text for marker in ("legacy", "deprecated", "compatibility")):
                raise ContractError(f"compatibility allowlist lacks a deprecation boundary: {item['path']}")
    _assert_file_paths(root, primary_paths, "identity primary paths")
    markers = _list(identity.get("forbidden_markers"), "manifest.identity.forbidden_markers")
    safe_values = _list(identity.get("safe_values"), "manifest.identity.safe_values")
    if not markers or not safe_values:
        raise ContractError("identity forbidden markers and safe values are required")
    for index, marker in enumerate(markers):
        _string(marker, f"manifest.identity.forbidden_markers[{index}]")
    safe = {str(value).lower() for value in safe_values}
    violations: list[str] = []
    for path in primary_paths:
        text = read_bounded(root, path)
        for line in text.splitlines():
            if BRACKETED_NAME.search(line):
                lowered = line.lower()
                explanatory = any(
                    marker in lowered
                    for marker in ("never", "not", "no ", "forbidden", "avoid", "without", "must not", "does not")
                )
                if not explanatory:
                    violations.append(f"{path}: bracketed id-plus-name presentation")
        for match in RAW_ASSIGNMENT.finditer(text):
            value = match.group(2).strip().lower().rstrip(".;")
            if value not in safe and not value.startswith("<redacted") and not value.startswith("[redacted"):
                violations.append(f"{path}: concrete browser identifier assigned to {match.group(1)}")
    if violations:
        raise ContractError("identity leakage: " + "; ".join(sorted(set(violations))))
    return allowlisted_paths


def _registry_entries(source: str) -> dict[str, bool]:
    entries: dict[str, bool] = {}
    for match in re.finditer(r"(?ms)^\s*\{\s*\n\s*key:\s*\"([^\"]+)\"\s*,(.*?)(?=^\s*\{\s*\n\s*key:|^\];)", source):
        block = match.group(0)
        key = match.group(1)
        supported_match = re.search(r"\bsupported:\s*(true|false)", block)
        if not supported_match:
            raise ContractError(f"operation registry entry has no support flag: {key}")
        if key in entries:
            raise ContractError(f"duplicate operation registry key: {key}")
        entries[key] = supported_match.group(1) == "true"
    action_section = source.split("const actionEntries = [", 1)
    if len(action_section) == 2:
        action_text = action_section[1].split("].map", 1)[0]
        for match in re.finditer(r'\[\s*"([a-z_]+)"\s*,\s*(true|false)\s*\]', action_text):
            key = f"action.{match.group(1)}"
            if key in entries:
                raise ContractError(f"duplicate operation registry key: {key}")
            entries[key] = match.group(2) == "true"
    if not entries:
        raise ContractError("operation registry could not be statically read")
    return entries


def validate_mappings(root: Path, mappings: Any) -> None:
    source = read_bounded(root, OPERATIONS_PATH)
    registry = _registry_entries(source)
    mcp_source = "\n".join(
        read_bounded(root, path)
        for path in (
            "crates/agentyc-mcp/src/host_server.rs",
            "crates/agentyc-mcp/src/remote_host_server.rs",
            "crates/agentyc-mcp/src/host_adapter.rs",
        )
    )
    mappings = _list(mappings, "manifest.mappings")
    by_key: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(mappings):
        name = f"manifest.mappings[{index}]"
        item = _mapping(raw, name)
        _require_exact_keys(item, {"key", "wire", "sdk", "cli", "mcp", "supported", "status", "gap_layers", "reason", "evidence_paths"}, name)
        key = _string(item.get("key"), f"{name}.key")
        if key in by_key:
            raise ContractError(f"duplicate mapping: {key}")
        by_key[key] = item
        if key not in registry:
            raise ContractError(f"mapping is not in the central operation registry: {key}")
        if item.get("supported") is not registry[key]:
            raise ContractError(f"mapping support disagrees with operation registry: {key}")
        if item.get("status") not in {"supported", "integration_gap", "unsupported"}:
            raise ContractError(f"mapping status is invalid: {key}")
        gap_layers = _list(item.get("gap_layers"), f"{name}.gap_layers")
        if any(layer not in {"cli", "sdk", "mcp", "wire"} for layer in gap_layers):
            raise ContractError(f"mapping gap layer is invalid: {key}")
        reason = item.get("reason")
        if item["status"] == "supported":
            if item["supported"] is not True or gap_layers or reason is not None:
                raise ContractError(f"supported mapping has an unexplained gap: {key}")
            for field in ("wire", "sdk", "cli", "mcp"):
                _string(item.get(field), f"{name}.{field}")
        elif item["status"] == "integration_gap":
            if item["supported"] is not True or not gap_layers:
                raise ContractError(f"integration gap must identify a supported operation and layer: {key}")
            _string(reason, f"{name}.reason")
            _string(item.get("wire"), f"{name}.wire")
        else:
            if item["supported"] is not False or not reason or gap_layers:
                raise ContractError(f"unsupported mapping needs an explicit reason: {key}")
            for field in ("sdk", "cli", "mcp"):
                if item.get(field) is not None:
                    raise ContractError(f"unsupported mapping must not claim a {field} implementation: {key}")
        wire = item.get("wire")
        if isinstance(wire, str) and wire not in source and item["status"] != "unsupported":
            raise ContractError(f"mapping wire method is absent from the central registry: {key}")
        mcp = item.get("mcp")
        if isinstance(mcp, str) and mcp not in mcp_source:
            raise ContractError(f"mapping MCP route is absent from MCP source evidence: {key}")
        evidence = _path_list(item.get("evidence_paths"), f"{name}.evidence_paths")
        _assert_file_paths(root, evidence, f"{name}.evidence_paths")
    if set(by_key) != set(registry):
        missing = sorted(set(registry) - set(by_key))
        extra = sorted(set(by_key) - set(registry))
        raise ContractError(f"operation mapping coverage mismatch; missing={missing}, extra={extra}")


def _fixture_json(root: Path, path: str) -> dict[str, Any]:
    value = _parse_document(read_bounded(root, path), path)
    if not isinstance(value, dict):
        raise ContractError(f"fixture {path} root must be an object")
    return value


def _sanitized_fixture_text(path: str, text: str) -> None:
    if SECRET_ASSIGNMENT.search(text):
        raise ContractError(f"fixture {path} contains a secret assignment")
    if EXTERNAL_URL.search(text):
        raise ContractError(f"fixture {path} contains an external URL")
    for match in RAW_ASSIGNMENT.finditer(text):
        value = match.group(2).strip().lower().rstrip(".;")
        if value not in SAFE_REDACTIONS and value not in {"{", "["} and not value.startswith("<redacted") and not value.startswith("[redacted"):
            raise ContractError(f"fixture {path} contains a concrete browser identifier")


def validate_errors(root: Path, path: str) -> None:
    text = read_bounded(root, path)
    _sanitized_fixture_text(path, text)
    fixture = _parse_document(text, path)
    if not isinstance(fixture, dict):
        raise ContractError("error fixture root must be an object")
    _require_exact_keys(fixture, {"schema_version", "kind", "status", "sanitized", "live_claims", "errors"}, path)
    if fixture.get("schema_version") != 1 or fixture.get("kind") != "mcp-error-fixtures":
        raise ContractError("error fixture version/kind is invalid")
    if fixture.get("status") != "contract-only" or fixture.get("sanitized") is not True or fixture.get("live_claims") is not False:
        raise ContractError("error fixture must be sanitized contract-only evidence")
    errors = _list(fixture.get("errors"), f"{path}.errors")
    if len(errors) != len(CANONICAL_ERRORS):
        raise ContractError("error fixture must cover every canonical error exactly once")
    codes: set[str] = set()
    for index, raw in enumerate(errors):
        name = f"{path}.errors[{index}]"
        item = _mapping(raw, name)
        _require_exact_keys(item, {"code", "retryable", "guidance", "next_step", "cli", "sdk", "mcp"}, name)
        code = _string(item.get("code"), f"{name}.code")
        if code in codes or code not in CANONICAL_ERRORS:
            raise ContractError(f"unknown or duplicate canonical error: {code}")
        codes.add(code)
        if item.get("retryable") is not (code in RETRYABLE_ERRORS):
            raise ContractError(f"retryability policy is wrong for {code}")
        guidance = GUIDANCE_BY_ERROR[code]
        if item.get("guidance") != guidance or item.get("next_step") != guidance:
            raise ContractError(f"guidance/next-step policy is wrong for {code}")
        for layer in ("cli", "sdk", "mcp"):
            layer_value = _mapping(item.get(layer), f"{name}.{layer}")
            if not layer_value:
                raise ContractError(f"{name}.{layer} mapping is empty")
            for key, value in layer_value.items():
                _string(key, f"{name}.{layer} key")
                if isinstance(value, str):
                    _string(value, f"{name}.{layer}.{key}")
        mcp = item["mcp"]
        if "classification" not in mcp:
            raise ContractError(f"MCP classification is missing for {code}")
        if mcp["classification"] not in {"tool_error", "protocol_error", "transport_error"}:
            raise ContractError(f"MCP classification is invalid for {code}")
    if codes != set(CANONICAL_ERRORS):
        raise ContractError("canonical error coverage is incomplete")


def validate_mcp_fixtures(root: Path, manifest: dict[str, Any]) -> None:
    index_path = manifest["fixture_index"]
    index_text = read_bounded(root, index_path)
    _sanitized_fixture_text(index_path, index_text)
    index = _fixture_json(root, index_path)
    _require_exact_keys(index, {"schema_version", "kind", "status", "sanitized", "live_claims", "profiles", "schemas", "errors", "workflows", "transcripts", "nonclaims"}, index_path)
    if index.get("schema_version") != 1 or index.get("kind") != "mcp-fixture-index":
        raise ContractError("MCP fixture index version/kind is invalid")
    if index.get("status") != "contract-only" or index.get("sanitized") is not True or index.get("live_claims") is not False:
        raise ContractError("MCP fixture index must be sanitized contract-only evidence")
    profiles = _mapping(index.get("profiles"), f"{index_path}.profiles")
    expected_profiles = {"default": "tests/fixtures/mcp/manifests/default.v1.json", "extended": "tests/fixtures/mcp/manifests/extended.v1.json"}
    if profiles != expected_profiles:
        raise ContractError("MCP fixture index profiles are not exact")
    schemas = _list(index.get("schemas"), f"{index_path}.schemas")
    workflows = _list(index.get("workflows"), f"{index_path}.workflows")
    transcripts = _list(index.get("transcripts"), f"{index_path}.transcripts")
    if schemas != ["tests/fixtures/mcp/schemas/tools.v1.json"]:
        raise ContractError("MCP schema fixture index is not exact")
    if workflows != [
        "tests/fixtures/mcp/workflows/stdio.v1.json",
        "tests/fixtures/mcp/workflows/http.v1.json",
        "tests/fixtures/mcp/workflows/host-backed.v1.json",
    ]:
        raise ContractError("MCP workflow fixture index is not exact")
    if transcripts != [
        "tests/fixtures/mcp/transcripts/stdio-initialize.v1.jsonl",
        "tests/fixtures/mcp/transcripts/tool-error.v1.jsonl",
        "tests/fixtures/mcp/transcripts/http-session.v1.jsonl",
    ]:
        raise ContractError("MCP transcript fixture index is not exact")
    for name, values in (("schemas", schemas), ("workflows", workflows), ("transcripts", transcripts)):
        _path_list(values, f"{index_path}.{name}")
        _assert_file_paths(root, values, f"{index_path}.{name}")
    if index.get("errors") != manifest["error_fixture"]:
        raise ContractError("MCP fixture index error path disagrees with manifest")
    if set(manifest["mcp_manifests"]) != set(expected_profiles.values()):
        raise ContractError("manifest MCP profile paths are incomplete")
    _assert_file_paths(root, manifest["mcp_manifests"], "manifest.mcp_manifests")
    validate_errors(root, manifest["error_fixture"])

    catalog_path = TOOL_CATALOG_PATH.as_posix()
    catalog_text = read_bounded(root, catalog_path)
    _sanitized_fixture_text(catalog_path, catalog_text)
    catalog = _fixture_json(root, catalog_path)
    catalog_tools = _list(catalog.get("tools"), "tool catalog.tools")
    catalog_names = [
        _string(_mapping(item, "tool catalog item").get("name"), "tool catalog name")
        for item in catalog_tools
    ]
    if len(catalog_names) != 76 or len(set(catalog_names)) != 76:
        raise ContractError("source MCP tool catalog must contain exactly 76 unique tools")
    for profile, expected_count in (("default", 61), ("extended", 76)):
        path = expected_profiles[profile]
        fixture_text = read_bounded(root, path)
        _sanitized_fixture_text(path, fixture_text)
        fixture = _fixture_json(root, path)
        _require_exact_keys(fixture, {"schema_version", "kind", "profile", "status", "sanitized", "live_claims", "rmcp", "transports", "tool_count", "tools", "nonclaims"}, path)
        if fixture.get("schema_version") != 1 or fixture.get("kind") != "mcp-tool-manifest" or fixture.get("profile") != profile:
            raise ContractError(f"MCP {profile} manifest identity is invalid")
        if fixture.get("status") != "contract-only" or fixture.get("sanitized") is not True or fixture.get("live_claims") is not False:
            raise ContractError(f"MCP {profile} manifest makes a live claim")
        rmcp = _mapping(fixture.get("rmcp"), f"{path}.rmcp")
        if rmcp.get("version") != "1.7" or rmcp.get("accepted_protocol") != "2024-11-05" or set(rmcp.get("not_claimed_protocols", [])) != {"2025-11-25", "2026-07-28"}:
            raise ContractError(f"MCP {profile} protocol compatibility declaration is invalid")
        transports = _list(fixture.get("transports"), f"{path}.transports")
        if set(transports) != {"stdio", "http"}:
            raise ContractError(f"MCP {profile} transports are incomplete")
        tools = _list(fixture.get("tools"), f"{path}.tools")
        if fixture.get("tool_count") != expected_count or len(tools) != expected_count:
            raise ContractError(f"MCP {profile} tool count is not {expected_count}")
        names: list[str] = []
        for index, raw in enumerate(tools):
            item = _mapping(raw, f"{path}.tools[{index}]")
            _require_exact_keys(item, {"name", "status", "side_effect", "authority", "deprecation", "mapping", "profile"}, f"{path}.tools[{index}]")
            names.append(_string(item.get("name"), f"{path}.tools[{index}].name"))
            if item.get("status") not in {"supported", "partial", "legacy-only"}:
                raise ContractError(f"MCP tool status is invalid in {path}")
            if item.get("side_effect") not in {"read", "mutation", "mixed"}:
                raise ContractError(f"MCP tool side-effect classification is invalid in {path}")
            _string(item.get("authority"), f"{path}.tools[{index}].authority")
            _string(item.get("deprecation"), f"{path}.tools[{index}].deprecation")
            _string(item.get("mapping"), f"{path}.tools[{index}].mapping")
            if item.get("profile") != profile:
                raise ContractError(f"MCP tool profile marker is wrong in {path}")
        expected_names = catalog_names if profile == "extended" else catalog_names[:61]
        if names != expected_names:
            raise ContractError(f"MCP {profile} manifest does not match the measured catalog order")
        if not _list(fixture.get("nonclaims"), f"{path}.nonclaims"):
            raise ContractError(f"MCP {profile} nonclaims are missing")

    schema_path = schemas[0]
    schema_text = read_bounded(root, schema_path)
    _sanitized_fixture_text(schema_path, schema_text)
    schema = _fixture_json(root, schema_path)
    _require_exact_keys(schema, {"schema_version", "kind", "status", "sanitized", "live_claims", "entries", "nonclaims"}, schema_path)
    if schema.get("schema_version") != 1 or schema.get("kind") != "mcp-tool-schemas" or schema.get("status") != "contract-only" or schema.get("sanitized") is not True or schema.get("live_claims") is not False:
        raise ContractError("MCP schema fixture envelope is invalid")
    entries = _list(schema.get("entries"), f"{schema_path}.entries")
    required_schema_names = {"host_space_create", "host_page_close", "host_action_execute", "browser_get_state", "browser_switch_tab"}
    schema_names: set[str] = set()
    for index, raw in enumerate(entries):
        item = _mapping(raw, f"{schema_path}.entries[{index}]")
        _require_exact_keys(item, {"name", "scope", "deprecated", "schema"}, f"{schema_path}.entries[{index}]")
        name = _string(item.get("name"), f"{schema_path}.entries[{index}].name")
        schema_names.add(name)
        _string(item.get("scope"), f"{schema_path}.entries[{index}].scope")
        schema_value = _mapping(item.get("schema"), f"{schema_path}.entries[{index}].schema")
        if schema_value.get("type") != "object" or schema_value.get("additionalProperties") is not False or not isinstance(schema_value.get("properties"), dict):
            raise ContractError(f"MCP schema is not bounded for {name}")
        if name == "browser_switch_tab":
            if item.get("deprecated") is not True or item.get("scope") != "adapter-only":
                raise ContractError("legacy tab_id schema is not explicitly adapter-only/deprecated")
            tab = schema_value["properties"].get("tab_id")
            if not isinstance(tab, dict) or tab.get("example") != "<redacted browser id>":
                raise ContractError("legacy tab_id schema must use a redacted example")
    if not required_schema_names.issubset(schema_names):
        raise ContractError("MCP schema fixture is missing required logical/legacy entries")

    for path in workflows:
        workflow_text = read_bounded(root, path)
        _sanitized_fixture_text(path, workflow_text)
        workflow = _fixture_json(root, path)
        _require_exact_keys(workflow, {"schema_version", "kind", "name", "transport", "status", "sanitized", "live_claims", "steps", "nonclaims"}, path)
        if workflow.get("schema_version") != 1 or workflow.get("kind") != "mcp-workflow" or workflow.get("status") != "contract-only" or workflow.get("sanitized") is not True or workflow.get("live_claims") is not False:
            raise ContractError(f"MCP workflow envelope is invalid: {path}")
        if workflow.get("transport") not in {"stdio", "http", "host-backed"}:
            raise ContractError(f"MCP workflow transport is invalid: {path}")
        steps = _list(workflow.get("steps"), f"{path}.steps")
        if not steps:
            raise ContractError(f"MCP workflow has no steps: {path}")
        joined = json.dumps(workflow, sort_keys=True)
        if workflow["transport"] == "stdio" and "2024-11-05" not in joined:
            raise ContractError("stdio workflow does not record accepted protocol")
        if workflow["transport"] == "http" and not all(marker in joined for marker in ("GET", "POST", "DELETE", "SSE", "Mcp-Session-Id")):
            raise ContractError("HTTP workflow does not cover session/GET/SSE/DELETE")
        if workflow["transport"] == "host-backed" and not all(marker in joined for marker in ("local IPC", "space_id", "host_space_create")):
            raise ContractError("host-backed workflow does not show the logical host boundary")
        if not _list(workflow.get("nonclaims"), f"{path}.nonclaims"):
            raise ContractError(f"MCP workflow nonclaims are missing: {path}")

    for path in transcripts:
        text = read_bounded(root, path)
        _sanitized_fixture_text(path, text)
        lines = [line for line in text.splitlines() if line.strip()]
        if not lines:
            raise ContractError(f"MCP transcript is empty: {path}")
        for index, line in enumerate(lines):
            value = _parse_document(line, f"{path}:{index + 1}")
            if not isinstance(value, dict) or value.get("schema_version") != 1 or value.get("sanitized") is not True or value.get("live_claims") is not False:
                raise ContractError(f"MCP transcript line is not a sanitized v1 record: {path}:{index + 1}")
        joined = text
        if path.endswith("stdio-initialize.v1.jsonl") and "2024-11-05" not in joined:
            raise ContractError("stdio transcript lacks accepted protocol")
        if path.endswith("tool-error.v1.jsonl") and not all(marker in joined for marker in ("isError", "structuredContent", "stale_lease")):
            raise ContractError("tool-error transcript lacks structured MCP error evidence")
        if path.endswith("http-session.v1.jsonl") and not all(marker in joined for marker in ("GET", "POST", "DELETE", "SSE", "<redacted session id>")):
            raise ContractError("HTTP transcript lacks session lifecycle evidence")


def _front_matter_status(text: str, phase: int) -> str:
    match = re.search(r"(?ms)^---\s*\n(.*?)^---\s*$", text)
    if not match:
        raise ContractError(f"phase {phase} plan has no front matter")
    status = re.search(r"(?m)^status:\s*([^\s#]+)\s*$", match.group(1))
    if not status:
        raise ContractError(f"phase {phase} plan has no status")
    return status.group(1)


def validate_plan_and_phase_history(root: Path) -> None:
    plan = read_bounded(root, PHASE_PLAN_PATH)
    phase2_status = _front_matter_status(plan, 2)
    if phase2_status not in {"active", "complete"}:
        raise ContractError("Phase 2 plan status must be active or complete")
    for task_id in PHASE_TASK_IDS:
        if not re.search(rf"(?m)^- \[x\] {re.escape(task_id)}\b", plan):
            raise ContractError(f"Phase 2 plan task is not checked: {task_id}")
    for index in range(1, 12):
        if not re.search(r"(?m)^- \[x\] .*", plan.split("## Quality checklist", 1)[-1]):
            raise ContractError("Phase 2 quality checklist contains an unchecked item")
    checklist = plan.split("## Quality checklist", 1)[-1].split("## Handoff out", 1)[0]
    unchecked = re.findall(r"(?m)^- \[ \]", checklist)
    if unchecked:
        raise ContractError("Phase 2 quality checklist contains unchecked items")
    if _front_matter_status(root and read_bounded(root, PHASE_1_PLAN_PATH), 1) != "complete":
        raise ContractError("Phase 1 is no longer complete")
    index = read_bounded(root, PLAN_INDEX_PATH)
    if not re.search(r"(?m)^\|\s*1\s*\|.*\|\s*complete\s*\|", index):
        raise ContractError("PLAN_INDEX no longer preserves Phase 1 complete")
    readme = read_bounded(root, README_PATH)
    if phase2_status == "active":
        if not re.search(r"(?m)^\|\s*2\s*\|.*\|\s*active\s*\|", index):
            raise ContractError("PLAN_INDEX does not keep Phase 2 active")
        if not re.search(r"(?m)^\|\s*3\s*\|.*\|\s*pending\s*\|", index):
            raise ContractError("PLAN_INDEX must keep Phase 3 pending while Phase 2 is active")
        if "Phase 2 is now the sole active phase" not in readme or "Phase 1 is complete" not in readme:
            raise ContractError("execution README does not preserve Phase 1 and Phase 2 active status")
    else:
        phase3_path = Path("docs/exec-plans/active/agentyc-browser-task-spaces/plans/phase-3-core-implementation.md")
        phase4_path = Path("docs/exec-plans/active/agentyc-browser-task-spaces/plans/phase-4-extension.md")
        phase3_plan = read_bounded(root, phase3_path)
        phase4_plan = read_bounded(root, phase4_path)
        if not re.search(r"(?m)^\|\s*2\s*\|.*\|\s*complete\s*\|", index):
            raise ContractError("PLAN_INDEX does not preserve Phase 2 complete")
        phase3_status = _front_matter_status(phase3_plan, 3)
        if phase3_status == "active":
            if not re.search(r"(?m)^\|\s*3\s*\|.*\|\s*active\s*\|", index):
                raise ContractError("PLAN_INDEX does not activate Phase 3")
            if "Phase 2 complete" not in readme or "Phase 3" not in readme:
                raise ContractError("execution README does not preserve Phase 2 completion and Phase 3 activation")
        elif phase3_status == "complete":
            if not re.search(r"(?m)^\|\s*3\s*\|.*\|\s*complete\s*\|", index):
                raise ContractError("PLAN_INDEX does not preserve Phase 3 complete")
            if not re.search(r"(?m)^\|\s*4\s*\|.*\|\s*active\s*\|", index):
                raise ContractError("PLAN_INDEX does not activate Phase 4")
            if _front_matter_status(phase4_plan, 4) != "active":
                raise ContractError("Phase 4 plan must be active after Phase 3 completion")
            if "Phase 3 is complete" not in readme or "Phase 4" not in readme:
                raise ContractError("execution README does not preserve Phase 3 completion and Phase 4 activation")
        else:
            raise ContractError("Phase 3 plan must be active or complete after Phase 2 completion")


def validate_traceability(root: Path, manifest: dict[str, Any]) -> None:
    text = read_bounded(root, manifest["traceability"])
    required_strings = [
        "P2-T1",
        "P2-T2",
        "P2-T3",
        "P2-T4",
        "P2-T5",
        "P2-T6",
        "P2-T7",
        "P2-T8",
        manifest["fixture_index"],
        manifest["artifact"],
        "Phase 1",
        "Phase 2",
        "later phases",
        "deterministic repository-contract evidence",
        "live Chrome",
        "integration gap",
    ]
    for marker in required_strings:
        if marker.lower() not in text.lower():
            raise ContractError(f"traceability is missing required marker: {marker}")
    for task in manifest["tasks"]:
        for field in ("required_modules", "fixtures", "evidence_paths", "tests"):
            for value in task[field]:
                if value not in text:
                    raise ContractError(f"traceability omits {value}")
    for item in manifest["quality_checklist"]:
        for value in item["evidence_paths"] + item["tests"]:
            if value not in text:
                raise ContractError(f"traceability omits quality evidence {value}")
    for item in manifest["mappings"]:
        if item["key"] not in text:
            raise ContractError(f"traceability omits operation mapping {item['key']}")
    for path in manifest["mcp_manifests"] + [manifest["error_fixture"]]:
        if path not in text:
            raise ContractError(f"traceability omits fixture {path}")


def validate_artifact(root: Path, artifact_path: str | Path = ARTIFACT_PATH) -> dict[str, Any]:
    path = Path(artifact_path)
    if path.is_absolute():
        try:
            path = path.resolve().relative_to(safe_root(root))
        except (OSError, ValueError) as exc:
            raise ContractError("artifact path escapes repository root") from exc
    artifact = parse_artifact(safe_root(root) / _relative_path(path, "artifact"))
    _require_exact_keys(
        artifact,
        {"schema_version", "kind", "phase", "phase_status", "result", "evidence_mode", "release_eligible", "live_claims", "manifest", "traceability", "fixture_index", "phase_1", "commands", "checks", "nonclaims", "integration_gaps", "redaction_status"},
        "artifact",
    )
    if artifact.get("schema_version") != 1 or artifact.get("kind") != "phase-2-contracts-review" or artifact.get("phase") != 2:
        raise ContractError("artifact version/kind/phase is invalid")
    if artifact.get("phase_status") not in {"active", "complete"} or artifact.get("result") != "pass":
        raise ContractError("artifact must pass while Phase 2 is active or complete")
    if artifact.get("evidence_mode") != "deterministic_repository_contracts" or artifact.get("release_eligible") is not False or artifact.get("live_claims") is not False:
        raise ContractError("artifact makes an unbounded/live/release claim")
    if artifact.get("manifest") != MANIFEST_PATH.as_posix() or artifact.get("traceability") != TRACEABILITY_PATH.as_posix() or artifact.get("fixture_index") != "tests/fixtures/mcp/index.v1.json":
        raise ContractError("artifact references the wrong Phase 2 evidence files")
    phase_1 = _mapping(artifact.get("phase_1"), "artifact.phase_1")
    if phase_1.get("status") != "complete" or phase_1.get("preserved") is not True:
        raise ContractError("artifact does not explicitly preserve Phase 1")
    if not _list(artifact.get("nonclaims"), "artifact.nonclaims") or not _list(artifact.get("integration_gaps"), "artifact.integration_gaps"):
        raise ContractError("artifact must state nonclaims and integration gaps")
    commands = _list(artifact.get("commands"), "artifact.commands")
    if not commands:
        raise ContractError("artifact must include exact command results")
    for index, entry in enumerate(commands):
        entry = _mapping(entry, f"artifact.commands[{index}]")
        _require_exact_keys(entry, {"command", "result"}, f"artifact.commands[{index}]")
        _string(entry.get("command"), f"artifact.commands[{index}].command")
        result = _string(entry.get("result"), f"artifact.commands[{index}].result")
        if result != "pass" and not result.startswith("unavailable:"):
            raise ContractError(f"artifact.commands[{index}] has an unbounded result")
    checks = _mapping(artifact.get("checks"), "artifact.checks")
    if checks.get("tasks_checked") != list(PHASE_TASK_IDS) or checks.get("quality_checked") != list(QUALITY_IDS):
        raise ContractError("artifact check inventory does not enumerate all Phase 2 checks")
    redaction = _mapping(artifact.get("redaction_status"), "artifact.redaction_status")
    if redaction.get("status") != "applied" or redaction.get("raw_browser_ids") is not False or redaction.get("secrets") is not False:
        raise ContractError("artifact redaction status is incomplete")
    _sanitized_fixture_text("artifact", json.dumps(artifact, sort_keys=True))
    return artifact


def validate_manifest(root: Path, manifest_path: str | Path = MANIFEST_PATH) -> dict[str, Any]:
    root = safe_root(root)
    path = Path(manifest_path)
    if path.is_absolute():
        try:
            path = path.resolve().relative_to(root)
        except (OSError, ValueError) as exc:
            raise ContractError("manifest path escapes repository root") from exc
    manifest = parse_manifest(root / _relative_path(path, "manifest"))
    _validate_manifest_shape(manifest)
    _sanitized_fixture_text("manifest", json.dumps(manifest, sort_keys=True))
    _assert_file_paths(root, [manifest["phase_plan"], manifest["artifact"], manifest["traceability"], manifest["fixture_index"], manifest["error_fixture"]], "manifest references")
    validate_framing(root, manifest["framing"])
    validate_tasks(root, manifest["tasks"])
    validate_quality(root, manifest["quality_checklist"])
    validate_core_boundary(root)
    validate_identity(root, manifest["identity"])
    validate_mappings(root, manifest["mappings"])
    validate_mcp_fixtures(root, manifest)
    return manifest


def validate(root: Path | str | None = None) -> dict[str, Any]:
    """Run the complete read-only Phase 2 evidence gate."""
    root = safe_root(root)
    manifest = validate_manifest(root)
    validate_plan_and_phase_history(root)
    validate_traceability(root, manifest)
    validate_artifact(root, manifest["artifact"])
    return manifest


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", help="repository root; defaults to the checkout containing this script")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        root = safe_root(args.root)
        manifest = validate(root)
    except (ContractError, OSError, ValueError) as exc:
        print(f"check_phase_2_contracts: FAIL: {exc}", file=sys.stderr)
        return 1
    print(
        "check_phase_2_contracts: PASS "
        f"(deterministic Phase 2 contract evidence; {len(manifest['tasks'])} tasks; phase {manifest['status']})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
