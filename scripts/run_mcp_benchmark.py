#!/usr/bin/env python3
"""Build a deterministic offline MCP request benchmark; no MCP server is run."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any

from artifact_envelope import envelope as add_envelope
from artifact_envelope import write_json_atomic

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_ROOT = (ROOT / "artifacts").resolve()
CATALOG = ROOT / "tests" / "fixtures" / "mcp" / "tool_catalog.json"
MAX_SAMPLES = 10_000
MAX_INPUT_BYTES = 512 * 1024
MAX_ARTIFACT_BYTES = 2 * 1024 * 1024


class BenchmarkError(ValueError):
    """Invalid or out-of-bounds benchmark input."""


def _bounded_int(value: int, name: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise BenchmarkError(f"{name} must be in {minimum}..{maximum}")
    return value


def safe_artifact_dir(value: str | Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = ROOT / path
    current = path
    while current != current.parent:
        if current.is_symlink():
            raise BenchmarkError("artifact path components must not be symlinks")
        current = current.parent
    resolved = path.resolve()
    if resolved == ARTIFACT_ROOT or ARTIFACT_ROOT not in resolved.parents:
        raise BenchmarkError("artifact directory must be a child of artifacts/")
    if resolved.exists() and not resolved.is_dir():
        raise BenchmarkError("artifact directory must be a directory")
    return resolved


def _tool_names() -> list[str]:
    if CATALOG.is_symlink() or CATALOG.stat().st_size > MAX_INPUT_BYTES:
        raise BenchmarkError("MCP fixture catalog is unsafe or exceeds its read limit")
    try:
        catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BenchmarkError("MCP fixture catalog is invalid") from exc
    tools = catalog.get("tools") if isinstance(catalog, dict) else None
    names = [item.get("name") for item in tools] if isinstance(tools, list) else []
    if not names or len(names) > 256 or any(not isinstance(name, str) or len(name) > 128 for name in names):
        raise BenchmarkError("MCP fixture catalog has an invalid tool inventory")
    return names


def _percentile(values: list[float], percentile: int) -> float:
    return sorted(values)[max(0, math.ceil(len(values) * percentile / 100) - 1)]


def build_report(samples: int = 1_000, *, min_samples_p95: int = 200, min_samples_p99: int = 1_000) -> dict[str, Any]:
    samples = _bounded_int(samples, "samples", 1, MAX_SAMPLES)
    min_samples_p95 = _bounded_int(min_samples_p95, "min-samples-p95", 1, MAX_SAMPLES)
    min_samples_p99 = _bounded_int(min_samples_p99, "min-samples-p99", 1, MAX_SAMPLES)
    if min_samples_p95 > min_samples_p99 or samples < min_samples_p99:
        raise BenchmarkError("samples must meet ordered p95 and p99 minimums")
    tools = _tool_names()
    latencies = [0.25 + ((i * 17 + len(tools)) % 31) * 0.125 for i in range(samples)]
    fingerprint = hashlib.sha256("\n".join(tools).encode("utf-8")).hexdigest()
    return {
        "schema_version": 1,
        "phase": 8,
        "kind": "mcp-benchmark",
        "status": "offline_model_completed",
        "evidence_mode": "deterministic-offline",
        "release_eligible": False,
        "samples": samples,
        "sample_requirements": {"p95_minimum": min_samples_p95, "p99_minimum": min_samples_p99, "met": True},
        "tool_catalog": {"tool_count": len(tools), "sha256": fingerprint, "source": "tests/fixtures/mcp/tool_catalog.json"},
        "latency_ms": {f"p{p}": _percentile(latencies, p) for p in (50, 95, 99)},
        "modeled_operations_per_second": round(samples / (sum(latencies) / 1000), 6),
        "measurement_basis": "fixed arithmetic fixture model; no transport or server timing",
        "safety": {"network_access": False, "mcp_server_started": False, "chrome_launch": "never", "chrome_download": "never", "chrome_attach": "never"},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=None, help="sample count; defaults to --min-samples-p99")
    parser.add_argument("--min-samples-p95", type=int, default=200)
    parser.add_argument("--min-samples-p99", type=int, default=1_000)
    parser.add_argument("--artifact-dir", default="artifacts/p8-mcp-benchmark/")
    args = parser.parse_args(argv)
    try:
        output_dir = safe_artifact_dir(args.artifact_dir)
        samples = args.samples if args.samples is not None else args.min_samples_p99
        report = build_report(samples, min_samples_p95=args.min_samples_p95, min_samples_p99=args.min_samples_p99)
        add_envelope(report, kind="mcp-benchmark", build_tuple={"phase": 8, "samples": samples}, environment={"network": "forbidden", "browser_launch": "never", "browser_download": "never", "browser_attach": "never"})
        write_json_atomic(output_dir / "report.json", report, max_bytes=MAX_ARTIFACT_BYTES)
    except (BenchmarkError, OSError, ValueError, TypeError) as exc:
        print(f"run_mcp_benchmark: FAIL: {type(exc).__name__}", file=sys.stderr)
        return 2
    print(json.dumps({"status": report["status"], "evidence_mode": report["evidence_mode"], "release_eligible": False}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
