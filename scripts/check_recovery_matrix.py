#!/usr/bin/env python3
"""Validate the Phase 1 persistence, reconciliation, and recovery matrix."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAX_BYTES = 4 * 1024 * 1024
DEFAULT_ARTIFACT = Path("docs/architecture-existing-chrome.md")

SCENARIOS: dict[str, tuple[str, ...]] = {
    "partial writes": ("partial ledger write", "partial write"),
    "host crash": ("host crash", "broker crash"),
    "Chrome restart": ("chrome restart", "browser restart"),
    "profile mismatch": ("profile mismatch", "copied profile"),
    "extension update": ("extension update/reinstall", "extension update"),
    "old binary": ("old binary", "incompatible ledger"),
    "rollback": ("rollback or kill switch", "rollback"),
    "two-phase create/claim crash": ("two-phase create/claim crash", "two-phase"),
    "cleanup confirmation": ("cleanup confirmation", "cleanup authorization"),
}

REQUIRED_MARKERS = (
    "authoritative selective control-plane record",
    "target/session/frame/document bindings",
    "non-authoritative",
    "corrupt, truncated, incompatible",
    "quarantined",
    "atomic replacement",
    "unknown",
    "no replay",
    "no implicit cleanup",
    "fresh generation",
    "single-use user ticket",
    "user/unmanaged pages",
    "rebind_required",
    "broker_epoch",
    "browser-session epoch",
    "extension/worker epoch",
)


class RecoveryError(ValueError):
    """A recovery scenario or fail-closed rule is missing."""


def resolve_root(value: str | None) -> Path:
    root = Path(value).expanduser() if value else ROOT
    if not root.is_dir():
        raise RecoveryError("repository root is not a directory")
    return root.resolve()


def read_artifact(root: Path, requested: str | None) -> str:
    relative = Path(requested) if requested else DEFAULT_ARTIFACT
    path = relative if relative.is_absolute() else root / relative
    try:
        resolved = path.resolve()
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise RecoveryError("recovery artifact must remain inside the repository root") from exc
    try:
        parts = resolved.relative_to(root).parts
    except ValueError as exc:
        raise RecoveryError("recovery artifact path is invalid") from exc
    current = root
    for component in parts:
        current = current / component
        if current.is_symlink():
            raise RecoveryError("recovery artifact path contains a symlink component")
    if not resolved.is_file() or resolved.is_symlink():
        raise RecoveryError("recovery artifact is missing")
    try:
        if resolved.stat().st_size > MAX_BYTES:
            raise RecoveryError("recovery artifact exceeds the bounded read limit")
        return resolved.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise RecoveryError("recovery artifact is unreadable") from exc


def validate(text: str) -> None:
    lowered = text.lower()
    missing_scenarios = [
        name for name, aliases in SCENARIOS.items() if not any(alias.lower() in lowered for alias in aliases)
    ]
    if missing_scenarios:
        raise RecoveryError("missing recovery scenarios: " + ", ".join(missing_scenarios))

    missing_markers = [marker for marker in REQUIRED_MARKERS if marker.lower() not in lowered]
    if missing_markers:
        raise RecoveryError("missing recovery invariants: " + ", ".join(missing_markers))

    header = next((line for line in lowered.splitlines() if "fault/scenario" in line and line.count("|") >= 4), "")
    if not re.search(r"\|\s*fault/scenario\s*\|", header) or not re.search(r"\|\s*durable result\s*\|", header):
        raise RecoveryError("recovery matrix must name fault/scenario and durable result columns")
    if not re.search(r"\|\s*restart/reconciliation behavior\s*\|", header):
        raise RecoveryError("recovery matrix must name restart/reconciliation behavior")
    if not re.search(r"\|\s*cleanup and user result\s*\|", header):
        raise RecoveryError("recovery matrix must name cleanup and user result")
    if "never close user tabs" not in lowered or "never ... kill user chrome" in lowered:
        # The second clause protects against a malformed literal ellipsis rule;
        # the first is the required safety statement in the normative artifact.
        raise RecoveryError("rollback/stop must retain user tabs and Chrome")
    if "no auto-adoption" not in lowered or "no adoption or close" not in lowered:
        raise RecoveryError("reconciliation must not auto-adopt or close ambiguous pages")
    if "two-phase" in lowered and "idempotent" not in lowered:
        raise RecoveryError("two-phase create/claim recovery must be idempotent")
    if "dispatched actions may be unknown" not in lowered and "in-flight actions unknown" not in lowered:
        raise RecoveryError("host/browser loss must classify dispatched actions as unknown")
    if "profile mismatch" in lowered and "retain pages" not in lowered:
        raise RecoveryError("profile mismatch must retain pages")
    if "cleanup confirmation" in lowered and "individual ownership" not in lowered:
        raise RecoveryError("cleanup confirmation must prove individual ownership")
    if "group_id" in lowered and not any(marker in lowered for marker in ("visual-group", "visual presentation")):
        raise RecoveryError("group_id is not qualified as visual-only")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", help="repository-relative sanitized recovery artifact")
    parser.add_argument("--root", help="repository root; defaults to the checkout containing this script")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        root = resolve_root(args.root)
        validate(read_artifact(root, args.artifact))
    except (RecoveryError, OSError) as exc:
        print(f"check_recovery_matrix: FAIL: {exc}", file=sys.stderr)
        return 1
    print("check_recovery_matrix: PASS (ledger durability, reconciliation, retention, and cleanup proof)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
