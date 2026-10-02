#!/usr/bin/env python3
"""Validate the Phase 1 release-gate policy without running release commands.

The checker is a deterministic policy/schema check.  It deliberately does not
turn an offline baseline or a missing live browser run into release evidence.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAX_BYTES = 4 * 1024 * 1024
RELEASE_DOC = Path("docs/release-gate.md")
ARCHITECTURE_DOC = Path("docs/architecture-existing-chrome.md")
PHASE0_BASELINE = Path("research/phase-0-baseline.md")

RELEASE_MARKERS = (
    "cargo fmt --all -- --check",
    "cargo clippy --workspace --all-targets -- -D warnings",
    "cargo build --release -p agentyc --locked",
    "cargo test --workspace --release --locked",
    "200 valid samples for p95",
    "1,000 for p99",
    "bootstrap 95% confidence intervals",
    "Thirty samples are smoke-only",
    "skipped/ignored",
    "existing-Chrome",
    "missing redacted artifacts",
    "authorization bypass",
    "cross-space mutation",
    "user-tab close",
    "stale-agent mutation after takeover",
    "secret leak",
    "blind replay",
    "silent unknown-success",
)
ARCHITECTURE_MARKERS = (
    "`space_id`",
    "host broker",
    "visual presentation only",
    "fence_pending",
    "MUST NOT download, launch",
    "MUST NOT own authoritative leases",
    "no automatic browser launch",
)
BASELINE_MARKERS = (
    "# Phase 0 baseline",
    "## 3. CLI/runtime launch behavior",
    "## 4. Global close behavior",
    "## 5. Raw-ID output examples, redacted",
    "## 6. Known false-green test paths",
    "redacted",
    "false-green",
)


class GateError(ValueError):
    """A release policy or evidence disclosure is incomplete."""


def resolve_root(value: str | None) -> Path:
    root = Path(value).expanduser() if value else ROOT
    if not root.is_dir():
        raise GateError("repository root is not a directory")
    return root.resolve()


def read_file(root: Path, relative: Path, *, required: bool = True) -> str:
    path = root / relative
    try:
        resolved = path.resolve()
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise GateError(f"{relative.as_posix()} is outside the repository root") from exc
    current = root
    for component in relative.parts:
        current = current / component
        if current.is_symlink():
            raise GateError(f"{relative.as_posix()} contains a symlink component")
    if not resolved.is_file() or resolved.is_symlink():
        if required:
            raise GateError(f"{relative.as_posix()} is missing")
        return ""
    try:
        if resolved.stat().st_size > MAX_BYTES:
            raise GateError(f"{relative.as_posix()} exceeds the bounded read limit")
        return resolved.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise GateError(f"{relative.as_posix()} is unreadable") from exc


def require_markers(text: str, markers: tuple[str, ...], label: str) -> None:
    # Markdown line wrapping must not change the contract check.
    normalized = " ".join(text.lower().split())
    missing = [marker for marker in markers if " ".join(marker.lower().split()) not in normalized]
    if missing:
        raise GateError(f"{label} is missing: " + ", ".join(missing))


def validate_policy(root: Path, phase: int) -> None:
    if phase < 0:
        raise GateError("phase must be non-negative")
    release = read_file(root, RELEASE_DOC)
    architecture = read_file(root, ARCHITECTURE_DOC)
    require_markers(release, RELEASE_MARKERS, "release-gate policy")
    require_markers(architecture, ARCHITECTURE_MARKERS, "existing-Chrome architecture")

    release_normalized = " ".join(release.lower().split())
    if "planned gate" not in release_normalized or "does not enforce the full production matrix" not in release_normalized:
        raise GateError("release policy must distinguish planned gates from currently enforced gates")
    if "confidence interval" not in release_normalized or "raw samples" not in release_normalized:
        raise GateError("blocking tail metrics lack confidence/raw-sample policy")
    if "no automatic browser launch" in release_normalized and "no" not in release_normalized:
        raise GateError("invalid browser-launch policy")
    if "release fails" not in release_normalized:
        raise GateError("release failure rule is missing")
    if "optional/manual lane" not in release_normalized:
        raise GateError("skipped/ignored tests are not separated into an explicit optional lane")

    # Phase 0 is the only evidence phase this checker needs to inspect now.
    # Later phases can use the same policy with their own artifact validators.
    if phase == 0:
        baseline = read_file(root, PHASE0_BASELINE)
        require_markers(baseline, BASELINE_MARKERS, "Phase 0 baseline")
        baseline_lower = baseline.lower()
        if "all archived outputs" not in baseline_lower or "redacted" not in baseline_lower:
            raise GateError("Phase 0 baseline does not disclose archived/redacted evidence")
        if "known false-green" not in baseline_lower:
            raise GateError("Phase 0 baseline must disclose false-green paths")
    elif phase > 0:
        # A later phase is policy-checkable but cannot be claimed complete by
        # this Phase 1 checker without its phase-specific evidence manifest.
        raise GateError("only Phase 0 policy/evidence disclosure is owned by this checker")

    # The architecture document is the source of the product safety boundary;
    # do not let the release document silently widen it.
    if "automatic browser launch" not in architecture.lower() or "MCP remains a compatibility adapter" not in architecture:
        raise GateError("architecture boundary does not disclose no-auto-launch/MCP-adapter rules")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", type=int, required=True, help="phase policy/evidence to validate; Phase 0 is supported")
    parser.add_argument("--root", help="repository root; defaults to the checkout containing this script")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        validate_policy(resolve_root(args.root), args.phase)
    except (GateError, OSError) as exc:
        print(f"check_release_gate: FAIL: {exc}", file=sys.stderr)
        return 1
    print(f"check_release_gate: PASS (Phase {args.phase} policy and disclosed evidence)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
