#!/usr/bin/env python3
"""Check the Phase 1 transport-neutral identity and ownership contract.

This checker is intentionally read-only and stdlib-only.  It validates the
normative architecture documents and runs small negative identity fixtures
when requested; it does not inspect or modify implementation source.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAX_BYTES = 2 * 1024 * 1024
NORMATIVE_FILES = (
    Path("docs/architecture-existing-chrome.md"),
    Path("docs/security/extension-permissions.md"),
    Path("docs/security/host-protocol.md"),
)

# A raw browser value is unsafe when it is presented as a concrete field value.
# Names in explanatory prose are allowed; examples must use bounded sentinels.
RAW_ASSIGNMENT = re.compile(
    r"(?im)(?:[\"'`]?)(tab[_-]?id|target[_-]?id|session[_-]?id|group[_-]?id|"
    r"current[_-]?tab[_-]?id|websocket[_-]?url|debugger[_-]?endpoint)(?:[\"'`]?)"
    r"\s*[:=]\s*[\"']?([^,\s\"'}\]]+)"
)
BRACKETED_NAME = re.compile(r"\[[^\]]*id[^\]]*\]\s+name", re.IGNORECASE)
SAFE_VALUES = {
    "null",
    "false",
    "true",
    "<redacted>",
    "<redacted-id>",
    "<redacted id>",
    "<redacted browser id>",
    "[redacted]",
    "[redacted-id]",
    "[redacted_browser_id]",
    "not-authoritative",
    "untrusted",
    "internal",
}

REQUIRED_MARKERS = (
    "`space` is the only logical task-space object",
    "`space_id`",
    "`page_id`",
    "`frame_id`",
    "`document_id`",
    "`navigation_id`",
    "`action_id`",
    "`event_id`",
    "`ref_id`",
    "`snapshot_id`",
    "`profile_instance_id`",
    "`broker_epoch`",
    "`connection_epoch`",
    "`browser_session_epoch`",
    "`worker_instance_epoch`",
    "Chrome tab groups",
    "visual-group hint",
    "fence_pending",
    "`unknown`",
    "MUST NOT download, launch",
    "MUST NOT own authoritative leases",
    "MCP remains a compatibility adapter",
)


class ContractError(ValueError):
    """A normative contract invariant is missing or unsafe."""


def safe_root(value: str | None) -> Path:
    root = Path(value).expanduser() if value else ROOT
    if not root.is_dir():
        raise ContractError("repository root is not a directory")
    return root.resolve()


def read_bounded(root: Path, relative: Path) -> str:
    path = root / relative
    try:
        resolved = path.resolve()
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise ContractError(f"{relative.as_posix()} is outside the repository root") from exc
    current = root
    for component in relative.parts:
        current = current / component
        if current.is_symlink():
            raise ContractError(f"{relative.as_posix()} contains a symlink component")
    if not resolved.is_file() or resolved.is_symlink():
        raise ContractError(f"{relative.as_posix()} is missing")
    try:
        if resolved.stat().st_size > MAX_BYTES:
            raise ContractError(f"{relative.as_posix()} exceeds the bounded read limit")
        return resolved.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ContractError(f"{relative.as_posix()} is unreadable") from exc


def identity_violations(text: str) -> list[str]:
    violations: list[str] = []
    if BRACKETED_NAME.search(text):
        violations.append("bracketed id-plus-name presentation is forbidden")
    for match in RAW_ASSIGNMENT.finditer(text):
        value = match.group(2).strip().lower().rstrip(".;")
        if value not in SAFE_VALUES and not value.startswith("<redacted") and not value.startswith("[redacted"):
            violations.append(f"concrete browser identifier assigned to {match.group(1)}")
    return violations


def validate_documents(root: Path) -> None:
    documents = {relative: read_bounded(root, relative) for relative in NORMATIVE_FILES}
    architecture = documents[NORMATIVE_FILES[0]]
    combined = "\n".join(documents.values())

    missing = [marker for marker in REQUIRED_MARKERS if marker not in architecture and marker not in combined]
    if missing:
        raise ContractError("missing canonical markers: " + ", ".join(missing))

    if "`group_id`" not in architecture or "deprecated compatibility alias" not in architecture:
        raise ContractError("group_id is not explicitly limited to a deprecated compatibility alias")
    if "authorization" not in architecture.lower() or "visual presentation only" not in architecture:
        raise ContractError("tab groups are not explicitly non-authoritative visual presentation")
    if "profile_instance_id" not in architecture or "not authentication" not in architecture:
        raise ContractError("profile binding is not separated from authentication")
    if "explicit confirmation" not in architecture or "rebind_required" not in architecture:
        raise ContractError("rebind confirmation/fencing rule is missing")
    if "single-use" not in architecture or "user-intent ticket" not in architecture:
        raise ContractError("user-intent ticket rule is missing")
    if "no replay" not in architecture.lower() or "unknown" not in architecture.lower():
        raise ContractError("unknown outcome/no-replay rule is missing")

    violations = identity_violations(combined)
    if violations:
        raise ContractError("; ".join(sorted(set(violations))))


def validate_negative_fixtures() -> None:
    bad = (
        "[id] name",
        '{"tab_id": "123456789"}',
        'targetId: "target-12345678"',
        "session_id = session-12345678",
    )
    for fixture in bad:
        if not identity_violations(fixture):
            raise ContractError("negative identity fixture was accepted")

    good = (
        '"tab_id": "<redacted browser id>"',
        "target_id: not-authoritative",
        "group_id is a visual-group hint only",
    )
    for fixture in good:
        if identity_violations(fixture):
            raise ContractError("safe identity fixture was rejected")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", help="repository root; defaults to the checkout containing this script")
    parser.add_argument(
        "--negative-identity-fixtures",
        action="store_true",
        help="also run deterministic forbidden/safe identity examples",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        root = safe_root(args.root)
        validate_documents(root)
        if args.negative_identity_fixtures:
            validate_negative_fixtures()
    except (ContractError, OSError) as exc:
        print(f"check_core_contracts: FAIL: {exc}", file=sys.stderr)
        return 1
    suffix = "; negative identity fixtures" if args.negative_identity_fixtures else ""
    print(f"check_core_contracts: PASS (canonical logical IDs and ownership{suffix})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
