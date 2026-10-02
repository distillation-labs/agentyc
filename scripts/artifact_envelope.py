"""Bounded, redacted envelopes for Phase 0 evidence artifacts."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MAX_ARTIFACT_BYTES = 8 * 1024 * 1024
_SECRET_FLAGS = {
    "--extension-id",
    "--extension-origin",
    "--host-path",
    "--manifest-path",
    "--profile-dir",
    "--registration-path",
    "--chrome-binary",
    "--artifact",
    "--artifact-dir",
    "--matrix",
}


def _safe_command(argv: Iterable[str] | None = None) -> list[str]:
    values = list(argv if argv is not None else sys.argv)
    output: list[str] = []
    redact_next = False
    for value in values:
        if redact_next:
            output.append("<redacted>")
            redact_next = False
            continue
        if value in _SECRET_FLAGS:
            output.append(value)
            redact_next = True
            continue
        if value.startswith(tuple(f"{flag}=" for flag in _SECRET_FLAGS)):
            output.append(value.split("=", 1)[0] + "=<redacted>")
            continue
        path = Path(value)
        if path.is_absolute():
            try:
                output.append(path.resolve().relative_to(ROOT).as_posix())
            except ValueError:
                output.append("<absolute-path-redacted>")
        else:
            output.append(value)
    if redact_next:
        output.append("<redacted>")
    return output


def _environment(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    value = {
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
) -> dict[str, Any]:
    """Add the required evidence envelope without retaining sensitive values."""
    status = report.get("status", "unknown")
    report.setdefault("schema_version", 1)
    report["build_tuple"] = {"phase": 0, "artifact_kind": kind, **(build_tuple or {})}
    report["environment"] = _environment({**(report.get("environment") or {}), **(environment or {})})
    report["timestamp"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    report["command"] = _safe_command(command)
    if result is not None:
        report["result"] = result
    else:
        report.setdefault("result", {"status": status, "kind": kind})
    report["redaction_status"] = {
        "status": "applied",
        "policy": "redacted-only",
        "raw_browser_ids": False,
        "secrets": False,
        "absolute_paths": False,
        "page_bodies": False,
    }
    return report


def write_bytes_atomic(path: Path, rendered: bytes, *, max_bytes: int = MAX_ARTIFACT_BYTES) -> None:
    """Write bounded bytes without exposing a partial artifact."""
    if len(rendered) > max_bytes:
        raise ValueError("artifact exceeds the bounded write limit")
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


def write_json_atomic(path: Path, value: dict[str, Any], *, max_bytes: int = MAX_ARTIFACT_BYTES) -> None:
    """Write a bounded JSON artifact without exposing a partial report."""
    rendered = (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n").encode("utf-8")
    write_bytes_atomic(path, rendered, max_bytes=max_bytes)
