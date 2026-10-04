#!/usr/bin/env python3
"""Generate bounded deterministic offline Phase 7 replay evidence.

The runner validates the repository test manifest and repeats only the release
gate's deterministic replay model. It performs no product actions, network
access, or Chrome launch, download, or attach operation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import check_test_manifest
import run_release_gate
from artifact_envelope import envelope as add_envelope
from artifact_envelope import repository_relative, sha256_bytes, write_bytes_atomic, write_json_atomic, write_jsonl_atomic

ARTIFACT_ROOT = (ROOT / "artifacts").resolve()
DEFAULT_MANIFEST = ROOT / "tests" / "test-manifest.yaml"
DEFAULT_ARTIFACT_DIR = ARTIFACT_ROOT / "p7-faults"
REPORT_NAME = "report.json"
TRACE_NAME = "trace.json"
OUTCOMES_NAME = "outcomes.jsonl"
MAX_MANIFEST_BYTES = 2 * 1024 * 1024
MAX_ARTIFACT_BYTES = 8 * 1024 * 1024
MIN_REPETITIONS = run_release_gate.MIN_REPETITIONS
MAX_REPETITIONS = run_release_gate.MAX_REPETITIONS
MAX_SEEDS = 100


class ReplayMatrixError(ValueError):
    """Replay inputs or generated evidence are invalid or out of bounds."""


def _bounded_integer(value: int, name: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ReplayMatrixError(f"{name} must be in {minimum}..{maximum}")
    return value


def _safe_repository_file(value: str | Path) -> Path:
    requested = Path(value).expanduser()
    if not requested.is_absolute():
        requested = ROOT / requested
    current = requested
    while current != current.parent:
        if current.is_symlink():
            raise ReplayMatrixError("manifest path components must not be symlinks")
        current = current.parent
    resolved = requested.resolve()
    try:
        resolved.relative_to(ROOT)
    except ValueError as exc:
        raise ReplayMatrixError("manifest must be inside the repository") from exc
    if not resolved.is_file():
        raise ReplayMatrixError("manifest is missing")
    return resolved


def safe_artifact_dir(value: str | Path) -> Path:
    """Resolve an artifacts/ child and reject symlinks and path escapes."""
    requested = Path(value).expanduser()
    if not requested.is_absolute():
        requested = ROOT / requested
    current = requested
    while current != current.parent:
        if current.is_symlink():
            raise ReplayMatrixError("artifact path components must not be symlinks")
        current = current.parent
    resolved = requested.resolve()
    try:
        resolved.relative_to(ARTIFACT_ROOT)
    except ValueError as exc:
        raise ReplayMatrixError("artifact directory must be inside artifacts/") from exc
    if resolved == ARTIFACT_ROOT:
        raise ReplayMatrixError("artifact directory must be a child of artifacts/")
    if resolved.exists() and not resolved.is_dir():
        raise ReplayMatrixError("artifact directory must be a directory")
    return resolved


def manifest_metadata(value: str | Path = DEFAULT_MANIFEST) -> dict[str, Any]:
    manifest_path = _safe_repository_file(value)
    try:
        raw = manifest_path.read_bytes()
    except OSError as exc:
        raise ReplayMatrixError("manifest cannot be read") from exc
    if len(raw) > MAX_MANIFEST_BYTES:
        raise ReplayMatrixError("manifest exceeds the bounded read limit")
    try:
        check_test_manifest.validate(manifest_path)
    except (OSError, ValueError) as exc:
        raise ReplayMatrixError("manifest validation failed") from exc
    return {
        "path": repository_relative(manifest_path),
        "sha256": sha256_bytes(raw),
        "bytes": len(raw),
    }


def _stable_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("utf-8")


def run_matrix(
    *, repetitions: int = MIN_REPETITIONS, seed_start: int = 0, seeds: int = 1,
    manifest: str | Path = DEFAULT_MANIFEST,
) -> tuple[dict[str, Any], bytes, bytes]:
    """Run the deterministic replay model repeatedly and preserve exact outputs."""
    repetitions = _bounded_integer(repetitions, "repetitions", MIN_REPETITIONS, MAX_REPETITIONS)
    seeds = _bounded_integer(seeds, "seeds", 1, MAX_SEEDS)
    seed_start = _bounded_integer(seed_start, "seed-start", 0, 2**31 - seeds)
    manifest_info = manifest_metadata(manifest)

    trace = ["enqueue", "dispatch", "disconnect-after-dispatch", "reconcile-unknown"]
    trace_bytes = _stable_json({
        "schema_version": 1,
        "seed_start": seed_start,
        "seed_count": seeds,
        "trace": trace,
    }) + b"\n"
    outcome_rows: list[bytes] = []
    seed_reports: list[dict[str, Any]] = []

    for seed in range(seed_start, seed_start + seeds):
        hook = run_release_gate.run_deterministic_hook("replay", seed=seed, repetitions=repetitions)
        errors = run_release_gate.validate_deterministic_hook(hook, "replay")
        if errors:
            raise ReplayMatrixError("release-gate replay model validation failed")
        outcome = run_release_gate._hook_outcome("replay", seed, trace)
        encoded = _stable_json(outcome)
        outcome_hash = hashlib.sha256(encoded).hexdigest()
        if outcome_hash != hook.get("outcome_hash"):
            raise ReplayMatrixError("replay model outcome does not match its validated hook")
        repetition_hashes: list[str] = []
        for repetition in range(repetitions):
            repeated = _stable_json(run_release_gate._hook_outcome("replay", seed, trace))
            digest = hashlib.sha256(repeated).hexdigest()
            repetition_hashes.append(digest)
            outcome_rows.append(_stable_json({
                "seed": seed,
                "repetition": repetition,
                "outcome_sha256": digest,
                "outcome": json.loads(repeated),
            }) + b"\n")
        unique_hashes = sorted(set(repetition_hashes))
        if len(unique_hashes) != 1:
            raise ReplayMatrixError("replay outcomes diverged")
        seed_reports.append({
            "seed": seed,
            "attempted": repetitions,
            "valid": repetitions,
            "missing": 0,
            "errors": 0,
            "unique_outcome_hashes": unique_hashes,
            "outcome": outcome,
            "byte_comparison": {
                "compared_outcomes": repetitions,
                "identical": repetitions,
                "divergent": 0,
                "representative_outcome_bytes": len(encoded),
                "representative_outcome_sha256": outcome_hash,
            },
        })

    samples = b"".join(outcome_rows)
    if len(trace_bytes) > MAX_ARTIFACT_BYTES or len(samples) > MAX_ARTIFACT_BYTES:
        raise ReplayMatrixError("replay artifact exceeds the bounded write limit")
    report: dict[str, Any] = {
        "schema_version": 1,
        "phase": 7,
        "kind": "phase7-replay-matrix",
        "status": "offline_model_completed",
        "evidence_mode": "deterministic-offline",
        "release_eligible": False,
        "seed_start": seed_start,
        "seed_count": seeds,
        "repetitions_per_seed": repetitions,
        "attempted_repetitions": seeds * repetitions,
        "manifest": manifest_info,
        "trace": trace,
        "seed_results": seed_reports,
        "byte_comparison": {
            "attempted": seeds * repetitions,
            "valid": seeds * repetitions,
            "missing": 0,
            "errors": 0,
            "divergent": 0,
            "all_outcomes_identical_per_seed": True,
        },
        "artifacts": {
            "trace_file": TRACE_NAME,
            "trace_bytes": len(trace_bytes),
            "trace_sha256": sha256_bytes(trace_bytes),
            "outcomes_file": OUTCOMES_NAME,
            "outcome_records": seeds * repetitions,
            "outcomes_bytes": len(samples),
            "outcomes_sha256": sha256_bytes(samples),
        },
        "safety": {
            "network_access": False,
            "product_actions": False,
            "chrome_launch": "never",
            "chrome_download": "never",
            "chrome_attach": "never",
            "redacted_artifacts": True,
        },
        "limitations": [
            "Outcomes are deterministic model output, not product or host observations.",
            "No product actions, network access, or Chrome operations were performed.",
        ],
    }
    report["replay_command"] = [
        "python3", "scripts/run_replay_matrix.py",
        "--repetitions", str(repetitions), "--seed-start", str(seed_start), "--seeds", str(seeds),
        "--manifest", manifest_info["path"], "--artifact-dir", "artifacts/p7-faults",
    ]
    return report, trace_bytes, samples


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repetitions", type=int, default=MIN_REPETITIONS, help=f"repetitions per seed ({MIN_REPETITIONS}..{MAX_REPETITIONS})")
    parser.add_argument("--seed-start", type=int, default=0, help="first non-negative deterministic seed")
    parser.add_argument("--seeds", type=int, default=1, help=f"number of consecutive seeds (1..{MAX_SEEDS})")
    parser.add_argument("--manifest", default="tests/test-manifest.yaml", help="repository test manifest")
    parser.add_argument("--artifact-dir", default=DEFAULT_ARTIFACT_DIR.relative_to(ROOT).as_posix(), help="artifact output directory under artifacts/")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        artifact_dir = safe_artifact_dir(args.artifact_dir)
        report, trace, samples = run_matrix(
            repetitions=args.repetitions,
            seed_start=args.seed_start,
            seeds=args.seeds,
            manifest=args.manifest,
        )
        add_envelope(
            report,
            kind="phase7-replay-matrix",
            command=report["replay_command"],
            build_tuple={"phase": 7, "repetitions": args.repetitions, "seeds": args.seeds},
            environment={"network": "forbidden", "browser_launch": "never", "browser_download": "never", "browser_attach": "never"},
        )
        write_bytes_atomic(artifact_dir / TRACE_NAME, trace, max_bytes=MAX_ARTIFACT_BYTES)
        write_jsonl_atomic(artifact_dir / OUTCOMES_NAME, samples, max_bytes=MAX_ARTIFACT_BYTES)
        write_json_atomic(artifact_dir / REPORT_NAME, report, max_bytes=MAX_ARTIFACT_BYTES)
    except (OSError, TypeError, ValueError) as exc:
        print(f"run_replay_matrix: FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({
        "status": report["status"],
        "evidence_mode": report["evidence_mode"],
        "release_eligible": False,
        "seeds": args.seeds,
        "repetitions_per_seed": args.repetitions,
        "divergent": 0,
        "artifact_dir": repository_relative(artifact_dir),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
