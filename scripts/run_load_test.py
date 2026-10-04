#!/usr/bin/env python3
"""Run a bounded, deterministic offline model of direct-product load.

This scaffold models FIFO read admission using the host scheduler's current
limits. It does not exercise the host or observe machine resources, and it
never launches, downloads, or attaches to Chrome. Offline results are not
release evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any

from artifact_envelope import envelope as add_envelope
from artifact_envelope import write_json_atomic, write_jsonl_atomic

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_ROOT = ROOT / "artifacts"
REPORT_NAME = "report.json"
SAMPLES_NAME = "samples.jsonl"
PHASE = 7
SCHEMA_VERSION = 1

# These values mirror current repository defaults, not signed release limits.
MAX_SPACES = 64  # crates/agentyc-host/src/ledger.rs: LedgerLimits::default
MAX_CONCURRENT_READS = 8  # crates/agentyc-host/src/scheduler.rs: SchedulerLimits::default
MAX_QUEUED_READS = 64
OPERATIONS_PER_SPACE = 2
OPERATION_LIMIT = MAX_SPACES * OPERATIONS_PER_SPACE
DEADLINE_MS = 35.0
MAX_ARTIFACT_BYTES = 8 * 1024 * 1024


class LoadTestError(ValueError):
    """An unsafe or out-of-bounds load-test request."""


def parse_spaces(value: str) -> list[int | str]:
    """Parse positive space counts and the one symbolic maximum token."""
    if len(value) > 256:
        raise LoadTestError("--spaces exceeds the bounded argument length")
    parts = [part.strip().lower() for part in value.split(",")]
    if not parts or len(parts) > MAX_SPACES + 1 or any(not part for part in parts):
        raise LoadTestError("--spaces must be a non-empty comma-separated list")
    if len(parts) != len(set(parts)):
        raise LoadTestError("--spaces must not contain duplicates")

    parsed: list[int | str] = []
    for part in parts:
        if part == "max":
            parsed.append("max")
            continue
        try:
            count = int(part)
        except ValueError as exc:
            raise LoadTestError("--spaces accepts positive integers or max") from exc
        if str(count) != part or count < 1 or count > MAX_SPACES:
            raise LoadTestError(f"--spaces values must be in 1..{MAX_SPACES}")
        parsed.append(count)
    return parsed


def safe_artifact_dir(value: str | Path) -> Path:
    """Resolve a child of artifacts/ and reject all symlink path components."""
    requested = Path(value).expanduser()
    if not requested.is_absolute():
        requested = ROOT / requested
    current = requested
    while current != current.parent:
        if current.is_symlink():
            raise LoadTestError("artifact path components must not be symlinks")
        current = current.parent
    resolved = requested.resolve()
    try:
        resolved.relative_to(ARTIFACT_ROOT.resolve())
    except ValueError as exc:
        raise LoadTestError("artifact directory must be inside artifacts/") from exc
    if resolved == ARTIFACT_ROOT.resolve():
        raise LoadTestError("artifact directory must be a child of artifacts/")
    if resolved.exists() and not resolved.is_dir():
        raise LoadTestError("artifact directory must be a directory")
    return resolved


def percentile(values: list[float], percent: int) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(percent / 100 * len(ordered)) - 1)]


def distribution(values: list[float]) -> dict[str, float | None]:
    return {f"p{percent}": percentile(values, percent) for percent in (50, 95, 99)}


def simulate_cell(spaces: int, *, cell_index: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Simulate a burst of bounded read work without sleeping or using I/O."""
    offered = spaces * OPERATIONS_PER_SPACE
    admitted_limit = MAX_CONCURRENT_READS + MAX_QUEUED_READS
    admitted = min(offered, admitted_limit)
    rejected = offered - admitted
    latencies: list[float] = []
    queue_depths: list[float] = []
    event_lags: list[float] = []
    rows: list[dict[str, Any]] = []
    deadline_failures = 0

    for ordinal in range(offered):
        if ordinal >= admitted_limit:
            rows.append({
                "cell_index": cell_index,
                "spaces": spaces,
                "operation": ordinal,
                "sample_status": "rejected_overload",
                "outcome_code": "ReadQueueFull",
                "latency_ms": None,
                "queue_depth": MAX_QUEUED_READS,
            })
            queue_depths.append(float(MAX_QUEUED_READS))
            continue

        queue_depth = max(0, ordinal - MAX_CONCURRENT_READS + 1)
        queue_depths.append(float(queue_depth))
        # Fixed service costs and FIFO worker lanes make the model repeatable.
        service_ms = 3.0 + ((ordinal * 7 + spaces) % 5)
        worker_lane = ordinal % MAX_CONCURRENT_READS
        prior_rounds = ordinal // MAX_CONCURRENT_READS
        latency_ms = service_ms * (prior_rounds + 1) + worker_lane * 0.125
        latencies.append(latency_ms)
        event_lag_ms = 0.5 + queue_depth * 0.125
        event_lags.append(event_lag_ms)
        deadline_exceeded = latency_ms > DEADLINE_MS
        deadline_failures += int(deadline_exceeded)
        rows.append({
            "cell_index": cell_index,
            "spaces": spaces,
            "operation": ordinal,
            "sample_status": "deadline_exceeded" if deadline_exceeded else "completed",
            "outcome_code": "Timeout" if deadline_exceeded else None,
            "latency_ms": latency_ms,
            "queue_depth": queue_depth,
            "event_lag_ms": event_lag_ms,
        })

    makespan_ms = max(latencies, default=0.0)
    throughput = (admitted / (makespan_ms / 1000.0)) if makespan_ms else 0.0
    return ({
        "spaces": spaces,
        "offered_operations": offered,
        "admitted_operations": admitted,
        "completed_within_deadline": admitted - deadline_failures,
        "rejected_operations": rejected,
        "sample_accounting": {
            "attempted": offered,
            "admitted": admitted,
            "deadline_failures": deadline_failures,
            "typed_overload_rejections": rejected,
        },
        "latency_ms": distribution(latencies),
        "throughput_operations_per_second": round(throughput, 6),
        "simulated_duration_ms": makespan_ms,
        "queue_depth": {
            **distribution(queue_depths),
            "max": max(queue_depths, default=0.0),
        },
        "event_lag_ms": distribution(event_lags),
        "deadline": {"limit_ms": DEADLINE_MS, "failures": deadline_failures},
        "overload_outcomes": {
            "status": "typed_rejections_observed_in_model" if rejected else "none_in_model",
            "rejected_by_type": {"ReadQueueFull": rejected},
            "scheduler_error_types": ["ReadQueueFull", "MutationQueueFull", "GlobalMutationQueueFull"],
        },
        "metric_basis": "deterministic_offline_model_not_host_observation",
    }, rows)


def resource_report(spaces: int, sample_bytes: int) -> dict[str, Any]:
    """Separate exact model/accounting fields from unavailable live resources."""
    modeled_ledger_bytes = 256 + spaces * 512
    return {
        "cpu_percent": {"value": None, "status": "not_measured_offline"},
        "rss_bytes": {"value": None, "status": "not_measured_offline"},
        "file_descriptors": {"value": None, "status": "not_measured_offline"},
        "threads": {"value": None, "status": "not_measured_offline"},
        "tabs": {
            "value": spaces,
            "status": "logical_space_proxy_not_browser_tabs",
            "measurement_basis": "offline_model",
        },
        "ledger_size_bytes": {
            "value": modeled_ledger_bytes,
            "status": "estimated_offline",
            "measurement_basis": "256_byte_fixed_overhead_plus_512_bytes_per_space",
        },
        "artifact_bandwidth": {
            "bytes_written": sample_bytes,
            "bytes_per_admitted_operation": round(sample_bytes / max(1, spaces * OPERATIONS_PER_SPACE), 3),
            "status": "measured_local_artifact_output",
            "scope": "samples.jsonl_only_report_excluded",
        },
    }


def build_report(spaces_argument: str, requested: list[int | str]) -> tuple[dict[str, Any], bytes]:
    cells: list[dict[str, Any]] = []
    all_rows: list[dict[str, Any]] = []
    resolved: list[int] = []
    for index, item in enumerate(requested):
        count = MAX_SPACES if item == "max" else int(item)
        resolved.append(count)
        cell, rows = simulate_cell(count, cell_index=index)
        cells.append(cell)
        all_rows.extend(rows)

    samples = b"".join(
        (json.dumps(row, separators=(",", ":"), sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
        for row in all_rows
    )
    if len(samples) > MAX_ARTIFACT_BYTES:
        raise LoadTestError("sample artifact exceeds the bounded write limit")
    first_saturated = next((cell["spaces"] for cell in cells if cell["rejected_operations"]), None)
    report = {
        "schema_version": SCHEMA_VERSION,
        "phase": PHASE,
        "kind": "direct-load-test",
        "status": "offline_model_completed",
        "evidence_mode": "offline",
        "release_eligible": False,
        "saturation": {
            "first_probed_space_count_with_typed_overload": first_saturated,
            "model_capacity_boundary_spaces": math.ceil((MAX_CONCURRENT_READS + MAX_QUEUED_READS + 1) / OPERATIONS_PER_SPACE),
            "status": "unsigned_offline_model_only",
        },
        "requested_spaces": spaces_argument,
        "space_levels": resolved,
        "capacity_limits": {
            "status": "unsigned_repository_defaults_not_release_limits",
            "max_spaces": MAX_SPACES,
            "max_concurrent_reads": MAX_CONCURRENT_READS,
            "max_queued_reads": MAX_QUEUED_READS,
            "operations_per_space": OPERATIONS_PER_SPACE,
            "source": ["crates/agentyc-host/src/ledger.rs", "crates/agentyc-host/src/scheduler.rs"],
        },
        "cells": cells,
        "resources_at_max_requested_level": resource_report(max(resolved), len(samples)),
        "artifacts": {
            "samples_file": SAMPLES_NAME,
            "sample_records": len(all_rows),
            "samples_bytes": len(samples),
            "samples_sha256": hashlib.sha256(samples).hexdigest(),
        },
        "safety": {
            "network_access": False,
            "chrome_launch": "never",
            "chrome_download": "never",
            "chrome_attach": "never",
            "host_execution": False,
            "redacted_artifacts": True,
        },
        "limitations": [
            "All workload, latency, queue, event-lag, deadline, and saturation results are deterministic model outputs, not host observations.",
            "CPU, RSS, file descriptors, and threads are not measured offline.",
            "Repository defaults are unsigned and are not approved release limits.",
        ],
    }
    return report, samples


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--spaces", default="1,2,4,8,max", help="comma-separated space counts and max (default: 1,2,4,8,max)")
    result.add_argument("--artifact-dir", default="artifacts/p7-load/", help="output directory inside artifacts/")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        requested = parse_spaces(args.spaces)
        artifact_dir = safe_artifact_dir(args.artifact_dir)
        report, samples = build_report(args.spaces, requested)
        artifact_dir.mkdir(parents=True, exist_ok=True)
        write_jsonl_atomic(artifact_dir / SAMPLES_NAME, samples, max_bytes=MAX_ARTIFACT_BYTES)
        add_envelope(
            report,
            kind="direct-load-test",
            build_tuple={"phase": PHASE, "load_test_schema": SCHEMA_VERSION},
        )
        write_json_atomic(artifact_dir / REPORT_NAME, report, max_bytes=MAX_ARTIFACT_BYTES)
    except (OSError, TypeError, ValueError) as exc:
        print(f"load test error: {type(exc).__name__}", file=sys.stderr)
        return 2

    print(json.dumps({
        "status": report["status"],
        "evidence_mode": "offline",
        "release_eligible": False,
        "cells": len(report["cells"]),
        "first_probed_saturation": report["saturation"]["first_probed_space_count_with_typed_overload"],
        "artifact_dir": "artifacts/validated",
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
