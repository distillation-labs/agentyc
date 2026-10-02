"""Bounded, redacted persistence primitives for Phase 0 evidence artifacts."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import secrets
import sys
import tempfile
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MAX_ARTIFACT_BYTES = 8 * 1024 * 1024
MAX_REDACTION_DEPTH = 12
MAX_REDACTION_ITEMS = 4096
MAX_REDACTION_NODES = 50_000
MAX_REDACTION_STRING_CHARS = 16_384
MAX_COMMAND_ARGS = 256

# Values for these flags are either credentials, browser identifiers, private
# paths, or pointers to private artifacts.  Keep this list central: every
# producer uses the same command redactor before it persists argv.
_SECRET_FLAGS = {
    "--extension-id",
    "--extension-origin",
    "--host-path",
    "--manifest-path",
    "--profile-dir",
    "--registration-path",
    "--chrome-binary",
    "--browser-executable",
    "--artifact",
    "--artifact-dir",
    "--matrix",
    "--harness",
    "--cdp-url",
    "--websocket-url",
    "--target-id",
    "--tab-id",
    "--session-id",
    "--token",
    "--secret",
    "--password",
    "--cookie",
    "--authorization",
}

_SECRET_KEY_WORDS = {
    "token",
    "secret",
    "password",
    "passwd",
    "cookie",
    "authorization",
    "credential",
    "privatekey",
    "private_key",
    "apikey",
    "api_key",
    "accesskey",
    "access_key",
    "refreshtoken",
    "refresh_token",
    "bearer",
}
_ID_KEY_WORDS = {
    "id",
    "identifier",
    "request_id",
    "requestid",
    "operation_id",
    "operationid",
    "space_id",
    "spaceid",
    "agent_id",
    "agentid",
    "window_id",
    "windowid",
    "frame_id",
    "frameid",
    "node_id",
    "nodeid",
    "browser_context_id",
    "browsercontextid",
    "connection_id",
    "connectionid",
    "tabid",
    "tab_id",
    "targetid",
    "target_id",
    "sessionid",
    "session_id",
    "currenttabid",
    "current_tab_id",
    "groupid",
    "group_id",
    "cdpid",
    "cdp_id",
    "backendnodeid",
    "backend_node_id",
    "rawid",
    "raw_id",
    "messageid",
    "message_id",
    "websocketurl",
    "websocket_url",
    "debuggerendpoint",
    "debugger_endpoint",
    "extensionid",
    "extension_id",
}
_PATH_KEY_WORDS = {
    "path",
    "file",
    "filepath",
    "file_path",
    "directory",
    "dir",
    "cwd",
    "artifact",
    "artifactdir",
    "artifact_dir",
    "manifest",
    "manifestpath",
    "manifest_path",
    "profile",
    "profiledir",
    "profile_dir",
    "executable",
    "output",
    "outputpath",
    "output_path",
}
_PAGE_BODY_KEY_WORDS = {
    "body",
    "pagebody",
    "page_body",
    "html",
    "htmlbody",
    "html_body",
    "dom",
    "snapshot",
    "srcdoc",
    "textcontent",
    "text_content",
    "pagecontent",
    "page_content",
    "responsebody",
    "response_body",
}
_ERROR_KEY_WORDS = {
    "error",
    "errors",
    "exception",
    "stderr",
    "stdout",
    "traceback",
    "stacktrace",
    "stack_trace",
}
_SAFE_REPOSITORY_PREFIXES = (
    "artifacts/",
    "crates/",
    "extension/",
    "research/",
    "scripts/",
    "tests/",
)

_ABSOLUTE_PATH_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9_.-])(?:/(?:Users|home|private|tmp|var|etc|opt|Applications)/[^\s\"'`,;)}\]]+|[A-Z]:[\\/][^\s\"'`,;)}\]]+)"
)
_FILE_URL_RE = re.compile(r"(?i)file://[^\s\"'`,;)}\]]+")
_HTTP_URL_RE = re.compile(r"(?i)https?://[^\s\"'`,;)}\]]+")
_WS_URL_RE = re.compile(r"(?i)wss?://[^\s\"'`,;)}\]]+")
_ASSIGNMENT_RE = re.compile(
    r"(?i)(\b(?:token|secret|password|passwd|cookie|authorization|credential|private[_-]?key|api[_-]?key)\s*[=:]\s*)([^\s,}\]]+)"
)
_RAW_ID_ASSIGNMENT_RE = re.compile(
    r"(?i)(\b(?:id|identifier|request[_-]?id|operation[_-]?id|space[_-]?id|agent[_-]?id|window[_-]?id|frame[_-]?id|node[_-]?id|connection[_-]?id|tab[_-]?id|target[_-]?id|session[_-]?id|group[_-]?id|extension[_-]?id|websocket[_-]?url|debugger[_-]?endpoint)\s*[=:]\s*)([^\s,}\]]+)"
)

_REDACTED = "<redacted>"
_REDACTED_ERROR = "<redacted error>"
_REDACTED_ID = "<redacted id>"
_REDACTED_PATH = "<absolute-path-redacted>"
_REDACTED_PAGE = "<redacted page body>"
_REDACTED_SECRET = "<redacted secret>"
_TRUNCATED = "<truncated>"


def utc_timestamp() -> str:
    """Return a stable UTC timestamp suitable for freshness validation."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def new_nonce() -> str:
    """Return a run nonce that is safe to persist and bind across artifacts."""
    return secrets.token_hex(16)


def sha256_bytes(value: bytes) -> str:
    if not isinstance(value, bytes):
        raise TypeError("sha256_bytes requires bytes")
    return hashlib.sha256(value).hexdigest()


def repository_relative(value: Path | str) -> str:
    """Return a repository-relative path or a bounded sentinel."""
    candidate = Path(value)
    if not candidate.is_absolute():
        text = candidate.as_posix()
        if text == "." or (not text.startswith("../") and text != ".."):
            return text
        return "<out-of-root>"
    try:
        return candidate.resolve().relative_to(ROOT).as_posix()
    except (OSError, ValueError):
        return "<absolute-path-redacted>"


def _normal_key(value: str) -> str:
    return value.strip().lower().replace("-", "_")


def _key_kind(key: str) -> str:
    normalized = _normal_key(key)
    compact = normalized.replace("_", "")
    secret_compact = {word.replace("_", "") for word in _SECRET_KEY_WORDS}
    id_compact = {word.replace("_", "") for word in _ID_KEY_WORDS}
    page_compact = {word.replace("_", "") for word in _PAGE_BODY_KEY_WORDS}
    error_compact = {word.replace("_", "") for word in _ERROR_KEY_WORDS}
    path_compact = {word.replace("_", "") for word in _PATH_KEY_WORDS}
    if (
        normalized in _SECRET_KEY_WORDS
        or compact in secret_compact
        or normalized.endswith(tuple(f"_{word}" for word in _SECRET_KEY_WORDS))
    ):
        return "secret"
    if normalized in {"generation_id", "artifact_id"}:
        return "normal"
    if (
        normalized in _ID_KEY_WORDS
        or compact in id_compact
        or normalized.endswith(("_id", "_ids", "_identifier", "_identifiers"))
    ):
        return "id"
    if (
        normalized in _PAGE_BODY_KEY_WORDS
        or compact in page_compact
        or normalized.endswith(("_body", "_html", "_dom", "_snapshot", "_srcdoc", "_text_content"))
    ):
        return "page"
    if (
        normalized in _ERROR_KEY_WORDS
        or compact in error_compact
        or normalized.startswith("error_")
        or normalized.endswith("_error")
    ):
        return "error"
    if (
        normalized in _PATH_KEY_WORDS
        or compact in path_compact
        or normalized.endswith(("_path", "_paths", "_directory", "_filename"))
    ):
        return "path"
    return "normal"


def _safe_path_text(value: str) -> str:
    stripped = value.strip()
    if _FILE_URL_RE.search(stripped):
        return _FILE_URL_RE.sub(_REDACTED_PATH, stripped)
    if stripped.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:[\\/]", stripped):
        relative = repository_relative(stripped)
        return relative if not relative.startswith("<") else _REDACTED_PATH
    if ".." in Path(stripped).parts:
        return "<relative-path-redacted>"
    return stripped


def _redact_embedded_text(value: str) -> str:
    value = _ASSIGNMENT_RE.sub(lambda match: match.group(1) + _REDACTED_SECRET, value)
    value = _RAW_ID_ASSIGNMENT_RE.sub(lambda match: match.group(1) + _REDACTED_ID, value)
    value = _FILE_URL_RE.sub(_REDACTED_PATH, value)
    value = _HTTP_URL_RE.sub("<url-redacted>", value)
    value = _WS_URL_RE.sub("<websocket-url-redacted>", value)
    value = _ABSOLUTE_PATH_RE.sub(lambda match: repository_relative(match.group(0)), value)
    if len(value) > MAX_REDACTION_STRING_CHARS:
        value = value[:MAX_REDACTION_STRING_CHARS] + _TRUNCATED
    return value


def _redact_string(value: str, key: str) -> str:
    kind = _key_kind(key)
    if kind == "secret":
        return _REDACTED_SECRET
    if kind == "id":
        return _REDACTED_ID
    if kind == "page":
        return _REDACTED_PAGE
    if kind == "error":
        return _REDACTED_ERROR
    if kind == "path":
        return _safe_path_text(value)
    return _redact_embedded_text(value)


def redact_for_persistence(
    value: Any,
    *,
    max_depth: int = MAX_REDACTION_DEPTH,
    max_items: int = MAX_REDACTION_ITEMS,
    max_nodes: int = MAX_REDACTION_NODES,
) -> Any:
    """Recursively redact and bound JSON-like data before it is persisted.

    This is intentionally an allowlist of safe representations rather than a
    best-effort ``repr`` of arbitrary objects. Unknown objects, deep values,
    excess collection members, non-finite numbers, page bodies, identifiers,
    paths, and error strings become explicit sentinels.
    """
    state = {"nodes": 0}

    def visit(current: Any, *, key: str, depth: int) -> Any:
        state["nodes"] += 1
        if depth > max_depth or state["nodes"] > max_nodes:
            return "<redacted:bound>"
        kind = _key_kind(key)
        if kind == "secret" and not isinstance(current, (bool, type(None))):
            return _REDACTED_SECRET
        if kind == "id" and not isinstance(current, (bool, type(None))):
            return _REDACTED_ID
        if kind == "page" and not isinstance(current, (bool, type(None))):
            return _REDACTED_PAGE
        if kind == "error" and not isinstance(current, (bool, type(None), int, float)):
            return _REDACTED_ERROR
        if isinstance(current, dict):
            result: dict[str, Any] = {}
            for index, (raw_key, child) in enumerate(current.items()):
                if index >= max_items:
                    result["<redacted:items>"] = "<redacted:bound>"
                    break
                safe_key = raw_key if isinstance(raw_key, str) else str(raw_key)
                safe_key = safe_key[:256]
                result[safe_key] = visit(child, key=safe_key, depth=depth + 1)
            return result
        if isinstance(current, (list, tuple, set)):
            result_list = [visit(child, key="", depth=depth + 1) for child in list(current)[:max_items]]
            if len(current) > max_items:
                result_list.append("<redacted:bound>")
            return result_list
        if isinstance(current, str):
            return _redact_string(current, key)
        if isinstance(current, bytes):
            return "<redacted bytes>"
        if current is None or isinstance(current, (bool, int)):
            return current
        if isinstance(current, float):
            return current if math.isfinite(current) else "<non-finite>"
        return "<redacted:unsupported>"

    return visit(value, key="", depth=0)


def _safe_command_value(value: str) -> str:
    if value.startswith("-"):
        return _redact_embedded_text(value)
    if value.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:[\\/]", value):
        return repository_relative(value)
    return _redact_embedded_text(value)


def _safe_command(argv: Iterable[str] | None = None) -> list[str]:
    """Redact argv values, including ``--flag=value`` assignments."""
    values = list(argv if argv is not None else sys.argv)
    output: list[str] = []
    redact_next = False
    for value in values[:MAX_COMMAND_ARGS]:
        if not isinstance(value, str):
            output.append("<redacted>")
            continue
        if redact_next:
            output.append(_REDACTED)
            redact_next = False
            continue
        flag, separator, _assigned = value.partition("=")
        normalized_flag = flag.lower()
        if normalized_flag in _SECRET_FLAGS:
            if separator:
                output.append(flag + "=" + _REDACTED)
            else:
                output.append(value)
                redact_next = True
            continue
        if separator and normalized_flag.startswith("--") and _key_kind(normalized_flag[2:]) in {"secret", "id", "path", "page", "error"}:
            output.append(flag + "=" + _REDACTED)
            continue
        # Also cover shell-style assignments that can occur in a captured
        # command, for example ``PROFILE_DIR=/private/...``.
        assignment_key, assignment_separator, assignment_value = value.partition("=")
        if assignment_separator and _key_kind(assignment_key) in {"secret", "id", "path", "page", "error"}:
            safe_value = _REDACTED if _key_kind(assignment_key) != "path" else _safe_path_text(assignment_value)
            output.append(assignment_key + "=" + safe_value)
            continue
        output.append(_safe_command_value(value))
    if len(values) > MAX_COMMAND_ARGS:
        output.append("<redacted:bound>")
    if redact_next:
        output.append(_REDACTED)
    return output


def _environment(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    value: dict[str, Any] = {
        "platform": sys.platform,
        "python": sys.version.split()[0],
        "cwd": "repository-relative",
        "network": "forbidden",
    }
    if extra:
        value.update(extra)
    return value


def envelope(
    report: dict[str, Any],
    *,
    kind: str,
    command: Iterable[str] | None = None,
    build_tuple: dict[str, Any] | None = None,
    environment: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
    nonce: str | None = None,
) -> dict[str, Any]:
    """Attach exact provenance and sanitize the complete report in place."""
    status = report.get("status", "unknown")
    safe_command = _safe_command(command)
    artifact_nonce = nonce or report.get("nonce") or new_nonce()
    timestamp = utc_timestamp()
    build = {
        "phase": 0,
        "artifact_kind": kind,
        "producer": "scripts/artifact_envelope.py",
        "producer_sha256": sha256_bytes(Path(__file__).read_bytes()),
        **(build_tuple or {}),
    }
    report.setdefault("schema_version", 1)
    report["build_tuple"] = build
    report["environment"] = _environment({**(report.get("environment") or {}), **(environment or {})})
    report["timestamp"] = timestamp
    report["nonce"] = artifact_nonce
    report["command"] = safe_command
    if result is not None:
        report["result"] = result
    else:
        report.setdefault("result", {"status": status, "kind": kind})
    report["provenance"] = {
        "nonce": artifact_nonce,
        "timestamp": timestamp,
        "command": safe_command,
        "build_tuple": build,
    }
    report["redaction_status"] = {
        "status": "applied",
        "policy": "central-allowlist-bounded-recursive-redaction",
        "raw_browser_ids": False,
        "secrets": False,
        "absolute_paths": False,
        "page_bodies": False,
        "errors": False,
        "max_depth": MAX_REDACTION_DEPTH,
        "max_items": MAX_REDACTION_ITEMS,
    }
    redacted = redact_for_persistence(report)
    report.clear()
    report.update(redacted)
    return report


def _reject_symlink_components(path: Path) -> None:
    current = path
    while current != current.parent:
        if current.is_symlink():
            raise ValueError("artifact path components must not be symlinks")
        current = current.parent


def write_bytes_atomic(path: Path, rendered: bytes, *, max_bytes: int = MAX_ARTIFACT_BYTES) -> None:
    """Write bounded bytes without exposing a partial artifact."""
    if not isinstance(rendered, bytes):
        raise TypeError("atomic byte writer requires bytes")
    if len(rendered) > max_bytes:
        raise ValueError("artifact exceeds the bounded write limit")
    _reject_symlink_components(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("wb", dir=path.parent, prefix=f".{path.name}.tmp-", delete=False) as handle:
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        temporary.replace(path)
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


def write_text_atomic(path: Path, value: str, *, max_bytes: int = MAX_ARTIFACT_BYTES) -> None:
    """Persist bounded text through the same redaction and atomicity boundary."""
    if not isinstance(value, str):
        raise TypeError("atomic text writer requires str")
    safe_value = _redact_embedded_text(value)
    write_bytes_atomic(path, safe_value.encode("utf-8"), max_bytes=max_bytes)


def _redact_json_lines(rendered: bytes) -> bytes:
    """Redact JSONL records when the byte writer is used for raw samples."""
    try:
        text = rendered.decode("utf-8")
        lines = text.splitlines()
        if not lines or any(not line.strip() for line in lines):
            raise ValueError("JSONL artifact must contain non-empty JSON lines")
        records = [json.loads(line) for line in lines]
    except UnicodeDecodeError as exc:
        raise ValueError("JSONL artifact must be UTF-8") from exc
    except json.JSONDecodeError as exc:
        raise ValueError("JSONL artifact contains invalid JSON") from exc
    redacted = [redact_for_persistence(record) for record in records]
    return b"".join((json.dumps(record, separators=(",", ":"), sort_keys=True, ensure_ascii=True) + "\n").encode("utf-8") for record in redacted)


def write_json_atomic(path: Path, value: Any, *, max_bytes: int = MAX_ARTIFACT_BYTES) -> None:
    """Write a bounded JSON artifact after recursive redaction."""
    safe_value = redact_for_persistence(value)
    rendered = (json.dumps(safe_value, indent=2, sort_keys=True, ensure_ascii=True, allow_nan=False) + "\n").encode("utf-8")
    write_bytes_atomic(path, rendered, max_bytes=max_bytes)


def write_jsonl_atomic(path: Path, value: bytes, *, max_bytes: int = MAX_ARTIFACT_BYTES) -> None:
    """Write bounded, redacted JSONL through the common persistence boundary."""
    safe_value = _redact_json_lines(value)
    write_bytes_atomic(path, safe_value, max_bytes=max_bytes)
