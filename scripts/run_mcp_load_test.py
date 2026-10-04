#!/usr/bin/env python3
"""Build a deterministic offline MCP concurrency/load model."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

from artifact_envelope import envelope as add_envelope
from artifact_envelope import write_json_atomic

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_ROOT = (ROOT / "artifacts").resolve()
MAX_CLIENTS = 32
MAX_REQUESTS_PER_CLIENT = 1_000
MAX_ATTEMPTS = 10_000
MAX_CONCURRENT = 8
MAX_QUEUE = 64


class LoadError(ValueError):
    """Invalid or out-of-bounds load input."""


def _bounded(value: int, name: str, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise LoadError(f"{name} must be in {low}..{high}")
    return value


def safe_artifact_dir(value: str | Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = ROOT / path
    current = path
    while current != current.parent:
        if current.is_symlink():
            raise LoadError("artifact path components must not be symlinks")
        current = current.parent
    resolved = path.resolve()
    if resolved == ARTIFACT_ROOT or ARTIFACT_ROOT not in resolved.parents:
        raise LoadError("artifact directory must be a child of artifacts/")
    if resolved.exists() and not resolved.is_dir():
        raise LoadError("artifact directory must be a directory")
    return resolved


def _pct(values: list[float], p: int) -> float | None:
    if not values:
        return None
    return sorted(values)[max(0, math.ceil(len(values) * p / 100) - 1)]


def build_report(clients: int = 8, requests_per_client: int = 100) -> dict[str, Any]:
    clients = _bounded(clients, "clients", 1, MAX_CLIENTS)
    requests_per_client = _bounded(requests_per_client, "requests-per-client", 1, MAX_REQUESTS_PER_CLIENT)
    attempted = clients * requests_per_client
    if attempted > MAX_ATTEMPTS:
        raise LoadError(f"total requests must not exceed {MAX_ATTEMPTS}")
    capacity = MAX_CONCURRENT + MAX_QUEUE
    admitted = min(attempted, capacity)
    rejected = attempted - admitted
    latencies = [2.0 + ((i * 5 + clients) % 11) + (i // MAX_CONCURRENT) * 0.75 for i in range(admitted)]
    return {
        "schema_version": 1,
        "phase": 8,
        "kind": "mcp-load-test",
        "status": "offline_model_completed",
        "evidence_mode": "deterministic-offline",
        "release_eligible": False,
        "clients": clients,
        "requests_per_client": requests_per_client,
        "accounting": {"attempted": attempted, "admitted": admitted, "rejected": rejected, "typed_overload_rejections": {"ModeledQueueFull": rejected}},
        "limits": {"concurrent": MAX_CONCURRENT, "queued": MAX_QUEUE, "basis": "unsigned offline model parameters"},
        "latency_ms": {f"p{p}": _pct(latencies, p) for p in (50, 95, 99)},
        "safety": {"network_access": False, "mcp_server_started": False, "chrome_launch": "never", "chrome_download": "never", "chrome_attach": "never"},
        "limitations": ["No transport, MCP process, host, browser, or system resource was exercised or measured."],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clients", type=int, default=8)
    parser.add_argument("--requests-per-client", type=int, default=100)
    parser.add_argument("--artifact-dir", default="artifacts/p8-mcp-load/")
    args = parser.parse_args(argv)
    try:
        output_dir = safe_artifact_dir(args.artifact_dir)
        report = build_report(args.clients, args.requests_per_client)
        add_envelope(report, kind="mcp-load-test", build_tuple={"phase": 8, "clients": args.clients, "requests_per_client": args.requests_per_client}, environment={"network": "forbidden", "browser_launch": "never", "browser_download": "never", "browser_attach": "never"})
        write_json_atomic(output_dir / "report.json", report, max_bytes=2 * 1024 * 1024)
    except (LoadError, OSError, ValueError, TypeError) as exc:
        print(f"run_mcp_load_test: FAIL: {type(exc).__name__}", file=sys.stderr)
        return 2
    print(json.dumps({"status": report["status"], "release_eligible": False, "accounting": report["accounting"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
