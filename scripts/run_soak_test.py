#!/usr/bin/env python3
"""Write a deterministic offline Phase 7 soak-test simulation report.

This producer does not run the product, launch/download/attach Chrome, or
measure live resources. Its modeled checks are scaffolding, not release evidence.
"""

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
DEFAULT_ARTIFACT_DIR = ARTIFACT_ROOT / "p7-soak-smoke"
REPORT_NAME = "report.json"
MAX_DURATION_SECONDS = 24 * 60 * 60
SIMULATED_CYCLES = 100
DURATION_RE = re.compile(r"^([0-9]+)(s|m|h)$")


def parse_duration(value: str) -> int:
    """Parse a bounded duration such as 10m into seconds."""
    match = DURATION_RE.fullmatch(value.strip().lower())
    if match is None:
        raise argparse.ArgumentTypeError("duration must be an integer followed by s, m, or h")
    amount = int(match.group(1))
    multiplier = {"s": 1, "m": 60, "h": 3600}[match.group(2)]
    seconds = amount * multiplier
    if seconds < 1 or seconds > MAX_DURATION_SECONDS:
        raise argparse.ArgumentTypeError("duration must be between 1 second and 24 hours")
    return seconds


def safe_artifact_dir(value: str | Path) -> Path:
    """Resolve an artifact directory without allowing symlinks or escapes."""
    requested = Path(value).expanduser()
    if not requested.is_absolute():
        requested = ROOT / requested
    current = requested
    while current != current.parent:
        if current.is_symlink():
            raise ValueError("artifact path components must not be symlinks")
        current = current.parent
    resolved = requested.resolve(strict=False)
    if resolved == ARTIFACT_ROOT or ARTIFACT_ROOT not in resolved.parents:
        raise ValueError("artifact directory must be a child of repository artifacts/")
    if resolved.exists() and not resolved.is_dir():
        raise ValueError("artifact directory exists but is not a directory")
    return resolved


def _resource_slopes(duration_seconds: int) -> dict[str, dict[str, Any]]:
    """Create fixed, synthetic samples and per-hour slopes for report shape."""
    hours = duration_seconds / 3600
    profiles = {
        "rss_bytes": (128 * 1024 * 1024, 1024.0, 512 * 1024 * 1024),
        "file_descriptors": (32.0, 0.0, 1024.0),
        "threads": (12.0, 0.0, 256.0),
        "queue_depth": (0.0, 0.01, 1000.0),
        "event_buffer_entries": (0.0, 0.02, 10000.0),
        "tabs": (0.0, 0.0, 256.0),
        "ledger_entries": (8.0, 0.05, 100000.0),
    }
    result: dict[str, dict[str, Any]] = {}
    for name, (baseline, slope_per_hour, ceiling) in profiles.items():
        samples = [baseline + slope_per_hour * hours * fraction for fraction in (0, 0.25, 0.5, 0.75, 1)]
        observed_slope = (samples[-1] - samples[0]) / hours if hours else 0.0
        result[name] = {
            "samples": samples,
            "slope_per_hour": observed_slope,
            "slope_ceiling_per_hour": slope_per_hour,
            "value_ceiling": ceiling,
            "within_modeled_bounds": max(samples) <= ceiling and observed_slope <= slope_per_hour,
            "measurement": "synthetic_offline",
        }
    return result


def build_report(duration_seconds: int) -> dict[str, Any]:
    """Build deterministic modeled lifecycle and soak observations."""
    if type(duration_seconds) is not int or not 1 <= duration_seconds <= MAX_DURATION_SECONDS:
        raise ValueError("duration must be between 1 second and 24 hours")
    lifecycle_steps = [
        "actions",
        "reconnects",
        "worker_restarts",
        "chrome_restarts",
        "takeover",
        "return_control",
        "retention",
        "artifact_write",
    ]
    return {
        "schema_version": 1,
        "artifact_kind": "phase-7-soak-test",
        "status": "offline_simulation_complete",
        "evidence_mode": "offline_simulation",
        "release_eligible": False,
        "live_evidence": False,
        "limitations": [
            "This is deterministic modeled output, not live evidence.",
            "No product process, Chrome instance, extension, or browser endpoint was exercised.",
            "Synthetic resource samples do not establish production resource slopes or isolation.",
            "Logical space labels are not cookie or storage isolation.",
        ],
        "duration": {
            "requested_seconds": duration_seconds,
            "maximum_seconds": MAX_DURATION_SECONDS,
            "runtime_seconds": 0,
            "simulation_cycles": SIMULATED_CYCLES,
        },
        "lifecycle": {
            "status": "modeled_only",
            "steps": [
                {"name": name, "status": "modeled", "observed_live": False}
                for name in lifecycle_steps
            ],
        },
        "resource_slopes": _resource_slopes(duration_seconds),
        "isolation": {
            "status": "modeled_only",
            "spaces": 2,
            "cross_space_mutations": 0,
            "observed_live": False,
            "storage_isolation_claimed": False,
        },
        "no_replay": {
            "status": "modeled_only",
            "dispatch_loss_scenario": "mutation_not_replayed",
            "replayed_mutations": 0,
            "observed_live": False,
        },
        "safety": {
            "network": "forbidden",
            "chrome_launch": "never",
            "chrome_download": "never",
            "chrome_attach": "never",
            "product_processes_started": False,
            "raw_browser_ids_logged": False,
            "secrets_logged": False,
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration", type=parse_duration, default=parse_duration("10m"))
    parser.add_argument(
        "--artifact-dir",
        default=DEFAULT_ARTIFACT_DIR.relative_to(ROOT).as_posix(),
        help="artifact output directory inside artifacts/",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        artifact_dir = safe_artifact_dir(args.artifact_dir)
        report = build_report(args.duration)
        add_envelope(report, kind="phase-7-soak-test")
        output = artifact_dir / REPORT_NAME
        write_json_atomic(output, report)
    except (OSError, ValueError, TypeError) as error:
        print(json.dumps({"status": "artifact_write_failed", "detail": type(error).__name__}), file=sys.stderr)
        return 2
    print(json.dumps({"status": report["status"], "record": output.relative_to(ROOT).as_posix()}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
