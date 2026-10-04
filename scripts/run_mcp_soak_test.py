#!/usr/bin/env python3
"""Build a deterministic offline MCP soak model without waiting or running MCP."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from artifact_envelope import envelope as add_envelope
from artifact_envelope import write_json_atomic

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_ROOT = (ROOT / "artifacts").resolve()
MAX_DURATION_SECONDS = 24 * 60 * 60
MAX_CYCLES = 10_000
DURATION_RE = re.compile(r"^([0-9]+)(s|m|h)$")


def parse_duration(value: str) -> int:
    if len(value) > 16:
        raise argparse.ArgumentTypeError("duration argument is too long")
    match = DURATION_RE.fullmatch(value.strip().lower())
    if not match:
        raise argparse.ArgumentTypeError("duration must be an integer followed by s, m, or h")
    seconds = int(match.group(1)) * {"s": 1, "m": 60, "h": 3600}[match.group(2)]
    if not 1 <= seconds <= MAX_DURATION_SECONDS:
        raise argparse.ArgumentTypeError("duration must be between 1 second and 24 hours")
    return seconds


def safe_artifact_dir(value: str | Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = ROOT / path
    current = path
    while current != current.parent:
        if current.is_symlink():
            raise ValueError("artifact path components must not be symlinks")
        current = current.parent
    resolved = path.resolve()
    if resolved == ARTIFACT_ROOT or ARTIFACT_ROOT not in resolved.parents:
        raise ValueError("artifact directory must be a child of artifacts/")
    if resolved.exists() and not resolved.is_dir():
        raise ValueError("artifact directory must be a directory")
    return resolved


def build_report(duration_seconds: int = 600, cycles: int = 100) -> dict[str, Any]:
    if type(duration_seconds) is not int or not 1 <= duration_seconds <= MAX_DURATION_SECONDS:
        raise ValueError("duration must be between 1 second and 24 hours")
    if type(cycles) is not int or not 1 <= cycles <= MAX_CYCLES:
        raise ValueError(f"cycles must be in 1..{MAX_CYCLES}")
    event_backlog = [((index * 7) % 5) for index in range(cycles)]
    return {
        "schema_version": 1,
        "phase": 8,
        "kind": "mcp-soak-test",
        "status": "offline_model_completed",
        "evidence_mode": "deterministic-offline",
        "release_eligible": False,
        "duration": {"requested_seconds": duration_seconds, "simulated_runtime_seconds": 0, "cycles": cycles},
        "modeled_checks": ["initialize", "tools_list", "tool_call", "notification", "disconnect", "reconnect", "shutdown"],
        "synthetic_counters": {"event_queue_depth_max": max(event_backlog, default=0), "reconnects": cycles // 10, "unknown_outcomes": 0, "replayed_mutations": 0},
        "resource_observations": {name: {"value": None, "status": "not_measured_offline"} for name in ("rss_bytes", "file_descriptors", "threads", "cpu_percent")},
        "safety": {"network_access": False, "mcp_server_started": False, "chrome_launch": "never", "chrome_download": "never", "chrome_attach": "never"},
        "limitations": ["Requested duration is metadata only; the deterministic model does not sleep or measure resource slopes."],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration", type=parse_duration, default=600)
    parser.add_argument("--cycles", type=int, default=100)
    parser.add_argument("--artifact-dir", default="artifacts/p8-mcp-soak/")
    args = parser.parse_args(argv)
    try:
        output_dir = safe_artifact_dir(args.artifact_dir)
        report = build_report(args.duration, args.cycles)
        add_envelope(report, kind="mcp-soak-test", build_tuple={"phase": 8, "cycles": args.cycles}, environment={"network": "forbidden", "browser_launch": "never", "browser_download": "never", "browser_attach": "never"})
        write_json_atomic(output_dir / "report.json", report, max_bytes=2 * 1024 * 1024)
    except (OSError, ValueError, TypeError) as exc:
        print(f"run_mcp_soak_test: FAIL: {type(exc).__name__}", file=sys.stderr)
        return 2
    print(json.dumps({"status": report["status"], "release_eligible": False, "cycles": args.cycles}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
