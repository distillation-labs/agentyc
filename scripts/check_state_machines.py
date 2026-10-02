#!/usr/bin/env python3
"""Validate the Phase 1 lifecycle and fencing transition artifact.

The checker accepts the normative architecture document by default or a
sanitized ``--artifact`` supplied by a later validation run.  It checks
coverage, ownership, guard/effect/error columns, and fail-closed takeover
semantics without executing browser or host code.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAX_BYTES = 4 * 1024 * 1024
DEFAULT_ARTIFACT = Path("docs/architecture-existing-chrome.md")

STATE_GROUPS: dict[str, tuple[str, ...]] = {
    "task-space": (
        "created",
        "agent_owned",
        "handoff_requested",
        "draining",
        "paused",
        "user_owned",
        "recovering",
        "finished",
        "released",
        "orphaned",
    ),
    "page": (
        "planned",
        "creating",
        "managed",
        "target_lost",
        "rebinding",
        "user_owned",
        "closing",
        "closed",
        "retired",
        "unknown",
        "unmanaged",
        "adoptable",
    ),
    "action": (
        "queued",
        "rejected",
        "running",
        "succeeded",
        "failed",
        "cancelled",
        "unknown",
        "reconciled",
        "requires_confirmation",
    ),
    "fence": (
        "fence_pending",
        "fence_dispatched",
        "fence_acknowledged",
    ),
    "connection": (
        "disconnected",
        "handshaking",
        "connected",
        "draining",
        "bridge_lost",
        "reconnecting",
        "rejected",
    ),
}

REQUIRED_CASES: dict[str, tuple[str, ...]] = {
    "stale epoch": ("stale epoch", "stale_lease", "old-epoch"),
    "duplicate claim": ("duplicate claim", "claim idempotency"),
    "disconnect": ("disconnect", "bridge_lost"),
    "extension restart": ("extension/worker restart", "worker restart", "worker_instance_epoch"),
    "user takeover": ("user takeover", "takeover"),
    "extension fence barrier": ("extension fence barrier", "fence barrier", "fence_acknowledged"),
    "page close": ("page close", "close only proven"),
    "browser restart": ("browser restart", "browser-session epoch"),
    "rebind-required": ("rebind-required", "rebind_required"),
    "broker restart": ("broker restart", "broker_epoch"),
}


class StateMachineError(ValueError):
    """A required state or transition contract is absent."""


def resolve_root(value: str | None) -> Path:
    root = Path(value).expanduser() if value else ROOT
    if not root.is_dir():
        raise StateMachineError("repository root is not a directory")
    return root.resolve()


def read_artifact(root: Path, requested: str | None) -> tuple[Path, str]:
    relative = Path(requested) if requested else DEFAULT_ARTIFACT
    path = relative if relative.is_absolute() else root / relative
    try:
        resolved = path.resolve()
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise StateMachineError("artifact must remain inside the repository root") from exc
    current = root
    try:
        parts = resolved.relative_to(root).parts
    except ValueError as exc:
        raise StateMachineError("artifact path is invalid") from exc
    for component in parts:
        current = current / component
        if current.is_symlink():
            raise StateMachineError("artifact path contains a symlink component")
    if not resolved.is_file() or resolved.is_symlink():
        raise StateMachineError("state-machine artifact is missing")
    try:
        if resolved.stat().st_size > MAX_BYTES:
            raise StateMachineError("state-machine artifact exceeds the bounded read limit")
        return resolved, resolved.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise StateMachineError("state-machine artifact is unreadable") from exc


def has_any(text: str, values: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(value.lower() in lowered for value in values)


def validate(text: str) -> None:
    lowered = text.lower()
    missing_states = [
        f"{group}:{state}"
        for group, states in STATE_GROUPS.items()
        for state in states
        if state.lower() not in lowered
    ]
    if missing_states:
        raise StateMachineError("missing canonical states: " + ", ".join(missing_states))

    headings = (
        ("task-space", ("task-space lifecycle", "task-space state machine", "space lifecycle")),
        ("page", ("page lifecycle", "page state machine")),
        ("action", ("action lifecycle", "action state machine")),
        ("connection", ("connection lifecycle", "connection state machine")),
        ("transitions", ("transition contract", "required transition coverage")),
    )
    missing_headings = [name for name, choices in headings if not has_any(text, choices)]
    if missing_headings:
        raise StateMachineError("missing state-machine sections: " + ", ".join(missing_headings))

    # The header is intentionally strict: every transition must identify who
    # starts it, what guards it, what is durable, and what duplicate/error the
    # user sees. The exact prose in each cell can evolve in later phases.
    header_found = any(
        "transition" in line.lower()
        and "initiator" in line.lower()
        and "owner" in line.lower()
        and "guard" in line.lower()
        and ("durable" in line.lower() or "record" in line.lower() or "effect" in line.lower())
        and ("duplicate" in line.lower() or "error" in line.lower())
        and ("user" in line.lower() or "result" in line.lower())
        for line in text.splitlines()
        if line.count("|") >= 5
    )
    if not header_found:
        raise StateMachineError(
            "transition table must name transition, initiator/owner, guard, durable effect, duplicate/error, and user result"
        )

    missing_cases = [name for name, aliases in REQUIRED_CASES.items() if not has_any(text, aliases)]
    if missing_cases:
        raise StateMachineError("missing required transition coverage: " + ", ".join(missing_cases))

    required_invariants = (
        "host broker",
        "MUST NOT own authoritative leases",
        "fresh lease",
        "no replay",
        "unknown",
        "lower-epoch commands",
        "single-use",
        "explicit confirmation",
        "retained",
    )
    missing_invariants = [marker for marker in required_invariants if marker.lower() not in lowered]
    if missing_invariants:
        raise StateMachineError("missing lifecycle invariant: " + ", ".join(missing_invariants))

    if "group_id" in lowered and not has_any(text, ("visual-group", "visual presentation", "deprecated compatibility")):
        raise StateMachineError("group_id appears without visual-only/deprecated qualification")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", help="repository-relative sanitized state artifact")
    parser.add_argument("--root", help="repository root; defaults to the checkout containing this script")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        root = resolve_root(args.root)
        _path, text = read_artifact(root, args.artifact)
        validate(text)
    except (StateMachineError, OSError) as exc:
        print(f"check_state_machines: FAIL: {exc}", file=sys.stderr)
        return 1
    print("check_state_machines: PASS (space/page/action/connection states and takeover fence)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
