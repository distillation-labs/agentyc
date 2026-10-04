#!/usr/bin/env python3
"""Build a fail-closed offline MCP release/rollback drill report.

This is an artifact and accounting rehearsal only: it does not install, run,
upgrade, or roll back an MCP server and cannot establish release eligibility.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import run_mcp_benchmark as benchmark
import run_mcp_chaos_test as chaos
import run_mcp_load_test as load_test
import run_mcp_soak_test as soak
from artifact_envelope import envelope as add_envelope
from artifact_envelope import write_json_atomic

ARTIFACT_ROOT = (ROOT / "artifacts").resolve()


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


def build_report() -> dict[str, Any]:
    components = {
        "benchmark": benchmark.build_report(1_000),
        "load": load_test.build_report(clients=2, requests_per_client=8),
        "soak": soak.build_report(duration_seconds=60, cycles=10),
        "chaos": chaos.build_report(seeds=2, repetitions=5),
    }
    return {
        "schema_version": 1,
        "phase": 8,
        "kind": "mcp-release-drill",
        "status": "offline_drill_completed",
        "evidence_mode": "deterministic-offline",
        "release_eligible": False,
        "components": {name: {"status": item["status"], "release_eligible": False} for name, item in components.items()},
        "rollback_model": {"upgrade_performed": False, "rollback_performed": False, "artifact_recovery_modeled": True, "state_restoration_verified": False},
        "component_reports": components,
        "safety": {"network_access": False, "mcp_server_started": False, "installation_performed": False, "chrome_launch": "never", "chrome_download": "never", "chrome_attach": "never"},
        "limitations": ["This drill validates deterministic report construction only; release, installation, rollback, and state restoration remain unverified."],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-dir", default="artifacts/p8-mcp-release/")
    args = parser.parse_args(argv)
    try:
        output_dir = safe_artifact_dir(args.artifact_dir)
        report = build_report()
        add_envelope(report, kind="mcp-release-drill", build_tuple={"phase": 8}, environment={"network": "forbidden", "browser_launch": "never", "browser_download": "never", "browser_attach": "never"})
        write_json_atomic(output_dir / "report.json", report, max_bytes=8 * 1024 * 1024)
    except (OSError, ValueError, TypeError) as exc:
        print(f"run_mcp_release_drill: FAIL: {type(exc).__name__}", file=sys.stderr)
        return 2
    print(json.dumps({"status": report["status"], "release_eligible": False, "components": sorted(report["components"])}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
