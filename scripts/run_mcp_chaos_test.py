#!/usr/bin/env python3
"""Build deterministic offline MCP fault-accounting evidence; inject no real faults."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from artifact_envelope import envelope as add_envelope
from artifact_envelope import write_json_atomic

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_ROOT = (ROOT / "artifacts").resolve()
MAX_SEEDS = 100
MAX_REPETITIONS = 1_000
MAX_ATTEMPTS = 10_000
MAX_MANIFEST_BYTES = 2 * 1024 * 1024
FAULTS = ("request_loss_before_dispatch", "response_loss_after_dispatch", "disconnect_during_request", "reconnect_after_disconnect", "oversize_frame", "invalid_json", "shutdown_during_call")


class ChaosError(ValueError):
    """Invalid or unsafe chaos-model input."""


def _bounded(value: int, name: str, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ChaosError(f"{name} must be in {low}..{high}")
    return value


def _manifest_info(value: str | Path) -> dict[str, Any]:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = ROOT / path
    current = path
    while current != current.parent:
        if current.is_symlink():
            raise ChaosError("manifest path components must not be symlinks")
        current = current.parent
    resolved = path.resolve()
    try:
        rel = resolved.relative_to(ROOT).as_posix()
    except ValueError as exc:
        raise ChaosError("manifest must be inside the repository") from exc
    if not resolved.is_file() or resolved.stat().st_size > MAX_MANIFEST_BYTES:
        raise ChaosError("manifest is missing or exceeds its bounded read limit")
    data = resolved.read_bytes()
    return {"path": rel, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def safe_artifact_dir(value: str | Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = ROOT / path
    current = path
    while current != current.parent:
        if current.is_symlink():
            raise ChaosError("artifact path components must not be symlinks")
        current = current.parent
    resolved = path.resolve()
    if resolved == ARTIFACT_ROOT or ARTIFACT_ROOT not in resolved.parents:
        raise ChaosError("artifact directory must be a child of artifacts/")
    if resolved.exists() and not resolved.is_dir():
        raise ChaosError("artifact directory must be a directory")
    return resolved


def build_report(seeds: int = 10, repetitions: int = 100, seed_start: int = 0, manifest: str | Path = "tests/test-manifest.yaml") -> dict[str, Any]:
    seeds = _bounded(seeds, "seeds", 1, MAX_SEEDS)
    repetitions = _bounded(repetitions, "repetitions", 1, MAX_REPETITIONS)
    seed_start = _bounded(seed_start, "seed-start", 0, 2**31 - seeds)
    if seeds * repetitions > MAX_ATTEMPTS:
        raise ChaosError(f"seed repetitions must not exceed {MAX_ATTEMPTS}")
    manifest_data = _manifest_info(manifest)
    results = []
    for seed in range(seed_start, seed_start + seeds):
        faults = [{"fault": name, "attempted": repetitions, "accounted": repetitions, "outcome": "reconciled_unknown" if name in {"response_loss_after_dispatch", "disconnect_during_request"} else "modeled_rejected_or_recovered", "mutation_replayed": False} for name in FAULTS]
        seed_hash = hashlib.sha256(f"mcp-chaos:{seed}:{repetitions}".encode()).hexdigest()
        results.append({"seed": seed, "status": "modeled_complete", "fault_results": faults, "outcome_hash": seed_hash, "no_replay_assertion": True})
    return {
        "schema_version": 1,
        "phase": 8,
        "kind": "mcp-chaos-test",
        "status": "offline_model_completed",
        "evidence_mode": "deterministic-offline",
        "release_eligible": False,
        "seed_start": seed_start,
        "seed_count": seeds,
        "repetitions": repetitions,
        "attempted_seed_repetitions": seeds * repetitions,
        "manifest": manifest_data,
        "fault_inventory": list(FAULTS),
        "seed_results": results,
        "safety": {"fault_injection_performed": False, "network_access": False, "mcp_server_started": False, "chrome_launch": "never", "chrome_download": "never", "chrome_attach": "never"},
        "limitations": ["Faults and recovery outcomes are modeled; no process, transport, host, or browser faults were injected."],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds", type=int, default=10)
    parser.add_argument("--seed-start", type=int, default=0)
    parser.add_argument("--repetitions", type=int, default=100)
    parser.add_argument("--manifest", default="tests/test-manifest.yaml")
    parser.add_argument("--artifact-dir", default="artifacts/p8-mcp-chaos/")
    args = parser.parse_args(argv)
    try:
        output_dir = safe_artifact_dir(args.artifact_dir)
        report = build_report(args.seeds, args.repetitions, args.seed_start, args.manifest)
        add_envelope(report, kind="mcp-chaos-test", build_tuple={"phase": 8, "seed_count": args.seeds, "repetitions": args.repetitions}, environment={"network": "forbidden", "browser_launch": "never", "browser_download": "never", "browser_attach": "never"})
        write_json_atomic(output_dir / "report.json", report, max_bytes=4 * 1024 * 1024)
    except (ChaosError, OSError, ValueError, TypeError) as exc:
        print(f"run_mcp_chaos_test: FAIL: {type(exc).__name__}", file=sys.stderr)
        return 2
    print(json.dumps({"status": report["status"], "release_eligible": False, "faults": len(report["fault_inventory"]), "seeds": args.seeds}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
