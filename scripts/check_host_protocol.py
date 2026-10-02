#!/usr/bin/env python3
"""Validate the Phase 1 host/local-IPC/Native-Messaging trust contract."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAX_BYTES = 5 * 1024 * 1024
DEFAULT_ARTIFACT = Path("docs/security/host-protocol.md")

REQUIRED_SECTIONS = (
    "trust boundaries",
    "endpoint and host-lock rules",
    "framing and bounded allocation",
    "envelope and handshake contract",
    "requests, actions, cancellation, and events",
    "artifact transfer",
    "version compatibility and failure matrix",
    "required negative tests and checker contract",
)
REQUIRED_MARKERS = (
    "OS peer identity",
    "restrictive",
    "symlink",
    "same OS user",
    "Remote TCP is disabled by default",
    "one broker owns one enrolled profile binding",
    "4-byte unsigned big-endian payload length",
    "UTF-8 JSON envelope",
    "validates the length before allocating",
    "1 MiB",
    "64 MiB",
    "256 KiB",
    "32 MiB",
    "4 MiB",
    "broker_epoch",
    "connection_epoch",
    "worker_instance_epoch",
    "browser_session_epoch",
    "request_id",
    "action_id",
    "idempotency_key",
    "event watermark",
    "transport metadata",
    "allowed_origins",
    "wildcards are forbidden",
    "profile_instance_id",
    "fresh connection nonce",
    "independent monotonic",
    "reconnect always creates a new connection epoch",
    "protocol_mismatch",
    "ledger_incompatible",
    "cancel",
    "unknown",
    "event_lagged",
    "artifact_begin",
    "artifact_chunk",
    "artifact_end",
    "cumulative artifact",
    "forged origin",
    "direct native-binary execution",
    "symlinked endpoint",
    "replayed nonce",
    "reconnect with sequence reset",
    "chunk flood",
    "host crash",
    "fence_pending",
    "no automatic browser launch",
)


class ProtocolError(ValueError):
    """A host trust or framing invariant is missing."""


def resolve_root(value: str | None) -> Path:
    root = Path(value).expanduser() if value else ROOT
    if not root.is_dir():
        raise ProtocolError("repository root is not a directory")
    return root.resolve()


def read_artifact(root: Path, requested: str | None) -> str:
    relative = Path(requested) if requested else DEFAULT_ARTIFACT
    path = relative if relative.is_absolute() else root / relative
    try:
        resolved = path.resolve()
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise ProtocolError("host-protocol artifact must remain inside the repository root") from exc
    try:
        parts = resolved.relative_to(root).parts
    except ValueError as exc:
        raise ProtocolError("host-protocol artifact path is invalid") from exc
    current = root
    for component in parts:
        current = current / component
        if current.is_symlink():
            raise ProtocolError("host-protocol artifact path contains a symlink component")
    if not resolved.is_file() or resolved.is_symlink():
        raise ProtocolError("host-protocol artifact is missing")
    try:
        if resolved.stat().st_size > MAX_BYTES:
            raise ProtocolError("host-protocol artifact exceeds the bounded read limit")
        return resolved.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ProtocolError("host-protocol artifact is unreadable") from exc


def validate(text: str) -> None:
    lowered = text.lower()
    missing_sections = [section for section in REQUIRED_SECTIONS if section.lower() not in lowered]
    if missing_sections:
        raise ProtocolError("missing host-protocol sections: " + ", ".join(missing_sections))
    missing_markers = [marker for marker in REQUIRED_MARKERS if marker.lower() not in lowered]
    if missing_markers:
        raise ProtocolError("missing host-protocol markers: " + ", ".join(missing_markers))

    if not re.search(r"4-byte\s+unsigned\s+big-endian", text, re.IGNORECASE):
        raise ProtocolError("local framing is not explicitly four-byte big-endian")
    if not re.search(r"before\s+allocat", lowered):
        raise ProtocolError("frame length is not checked before allocation")
    if "client-provided principal" not in lowered or "not authentication" not in lowered:
        raise ProtocolError("client-supplied principal/profile selector is not separated from authentication")
    if "origin field" not in lowered or not re.search(r"not(?:\*{1,3})?\s+read\s+from", lowered):
        raise ProtocolError("transport origin is not separated from JSON origin fields")
    if "exact stable extension origin/id" not in lowered:
        raise ProtocolError("exact extension origin/identity requirement is missing")
    if "sequence numbers start at one" not in lowered:
        raise ProtocolError("per-direction sequence reset rule is missing")
    if not re.search(r"\b(?:no|not)\s+(?:blind\s+)?replay", lowered) or not re.search(
        r"\bnever\s+replay", lowered
    ):
        raise ProtocolError("lost dispatch/no-replay rule is missing")
    if "atomic replacement" not in lowered or "fail closed" not in lowered:
        raise ProtocolError("atomic ledger/path fail-closed rule is missing")
    if "same-user threat" not in lowered or "does not claim" not in lowered:
        raise ProtocolError("same-user threat limit is missing")
    if "lower-epoch commands" not in lowered or "durable acknowledgement" not in lowered:
        raise ProtocolError("takeover extension fence acknowledgement rule is missing")

    # A wildcard origin must never be accepted even if a later edit adds a
    # prose example. The document may mention that wildcards are forbidden.
    if re.search(r"(?:allowed_origins|allowed origins)[^\n]{0,120}(?:\*://|<all_urls>|\*\.\*)", text, re.IGNORECASE):
        raise ProtocolError("allowed origins contain a wildcard")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", help="repository-relative sanitized host trust artifact")
    parser.add_argument("--root", help="repository root; defaults to the checkout containing this script")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        root = resolve_root(args.root)
        validate(read_artifact(root, args.artifact))
    except (ProtocolError, OSError) as exc:
        print(f"check_host_protocol: FAIL: {exc}", file=sys.stderr)
        return 1
    print("check_host_protocol: PASS (local IPC, Native Messaging trust, framing, and recovery)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
