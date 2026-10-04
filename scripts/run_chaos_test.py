#!/usr/bin/env python3
"""Generate bounded, deterministic offline Phase 7 chaos evidence.

This runner validates the repository test manifest and exercises only the
release gate's deterministic chaos model. It never starts, downloads, or
attaches to Chrome and does not perform network access.
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

import check_test_manifest
import run_release_gate
from artifact_envelope import envelope as add_envelope
from artifact_envelope import repository_relative, sha256_bytes, write_json_atomic

ARTIFACT_ROOT = (ROOT / "artifacts").resolve()
DEFAULT_MANIFEST = ROOT / "tests" / "test-manifest.yaml"
DEFAULT_ARTIFACT_DIR = ARTIFACT_ROOT / "p7-chaos"
MAX_SEEDS = 100
MIN_REPETITIONS = run_release_gate.MIN_REPETITIONS
MAX_REPETITIONS = run_release_gate.MAX_REPETITIONS
MAX_MANIFEST_BYTES = 2 * 1024 * 1024


class ChaosRunnerError(ValueError):
    """The chaos run inputs or evidence are invalid."""


def _bounded_integer(value: int, name: str, minimum: int, maximum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
        raise ChaosRunnerError(f"{name} must be in {minimum}..{maximum}")
    return value


def _safe_repository_file(value: str | Path, description: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = ROOT / path
    current = path
    while current != current.parent:
        if current.is_symlink():
            raise ChaosRunnerError(f"{description} path components must not be symlinks")
        current = current.parent
    resolved = path.resolve()
    try:
        resolved.relative_to(ROOT)
    except ValueError as exc:
        raise ChaosRunnerError(f"{description} must be inside the repository") from exc
    if not resolved.is_file():
        raise ChaosRunnerError(f"{description} is missing")
    return resolved


def _safe_artifact_dir(value: str | Path) -> Path:
    requested = Path(value).expanduser()
    if not requested.is_absolute():
        requested = ROOT / requested
    current = requested
    while current != current.parent:
        if current.is_symlink():
            raise ChaosRunnerError("artifact path components must not be symlinks")
        current = current.parent
    resolved = requested.resolve()
    try:
        resolved.relative_to(ARTIFACT_ROOT)
    except ValueError as exc:
        raise ChaosRunnerError("artifact directory must be inside artifacts/") from exc
    if resolved == ARTIFACT_ROOT:
        raise ChaosRunnerError("artifact directory must be a child of artifacts/")
    if resolved.exists() and not resolved.is_dir():
        raise ChaosRunnerError("artifact directory must be a directory")
    return resolved


def _manifest_metadata(value: str | Path) -> dict[str, Any]:
    manifest_path = _safe_repository_file(value, "manifest")
    try:
        raw = manifest_path.read_bytes()
    except OSError as exc:
        raise ChaosRunnerError("manifest cannot be read") from exc
    if len(raw) > MAX_MANIFEST_BYTES:
        raise ChaosRunnerError("manifest exceeds the bounded read limit")
    try:
        check_test_manifest.validate(manifest_path)
    except (OSError, ValueError) as exc:
        raise ChaosRunnerError("manifest validation failed") from exc
    return {
        "path": repository_relative(manifest_path),
        "sha256": sha256_bytes(raw),
        "bytes": len(raw),
    }


def validate_report(report: dict[str, Any], *, seeds: int, repetitions: int) -> list[str]:
    """Validate complete fault accounting and fail-closed outcome assertions."""
    errors: list[str] = []
    if report.get("kind") != "phase7-chaos-test" or report.get("evidence_mode") != "deterministic-offline":
        errors.append("chaos report identity or evidence mode is invalid")
    if report.get("status") != "passed" or report.get("seed_count") != seeds or report.get("repetitions") != repetitions:
        errors.append("chaos report run accounting is invalid")
    seed_results = report.get("seed_results")
    if not isinstance(seed_results, list) or len(seed_results) != seeds:
        errors.append("chaos seed accounting is incomplete")
        return errors
    seed_start = report.get("seed_start")
    if not isinstance(seed_start, int) or isinstance(seed_start, bool) or seed_start < 0:
        errors.append("chaos seed start is invalid")
        return errors

    expected_faults = list(run_release_gate.CHAOS_FAULTS)
    for expected_seed, item in enumerate(seed_results, start=seed_start):
        if not isinstance(item, dict) or item.get("seed") != expected_seed:
            errors.append("chaos seed sequence is invalid")
            continue
        if (
            item.get("status") != "reconciled_unknown"
            or item.get("hook_status") != "passed"
            or item.get("no_replay_assertion") is not True
        ):
            errors.append(f"seed {expected_seed} lacks unknown/no-replay assertions")
        if item.get("silent_unknown_success") != 0:
            errors.append(f"seed {expected_seed} permits silent unknown success")
        if item.get("attempted_repetitions") != repetitions:
            errors.append(f"seed {expected_seed} repetition accounting is incomplete")
        faults = item.get("fault_results")
        if not isinstance(faults, list) or [fault.get("fault") for fault in faults if isinstance(fault, dict)] != expected_faults:
            errors.append(f"seed {expected_seed} fault matrix is incomplete")
            continue
        for fault in faults:
            if (
                fault.get("status") != "reconciled_unknown"
                or fault.get("accounted") is not True
                or fault.get("attempted") != repetitions
                or fault.get("mutation_replayed") is not False
            ):
                errors.append(f"seed {expected_seed} has an unsafe fault result")
    if report.get("chrome_safety") != {
        "launch": "never",
        "download": "never",
        "attach": "never",
    }:
        errors.append("chaos report does not prove Chrome was untouched")
    return sorted(set(errors))


def build_report(
    *, seeds: int = 100, seed_start: int = 0, repetitions: int = MIN_REPETITIONS,
    manifest: str | Path = DEFAULT_MANIFEST,
) -> dict[str, Any]:
    seeds = _bounded_integer(seeds, "seeds", 1, MAX_SEEDS)
    seed_start = _bounded_integer(seed_start, "seed-start", 0, 2**31 - seeds)
    repetitions = _bounded_integer(repetitions, "repetitions", MIN_REPETITIONS, MAX_REPETITIONS)
    manifest_info = _manifest_metadata(manifest)
    fault_names = list(run_release_gate.CHAOS_FAULTS)
    seed_results: list[dict[str, Any]] = []

    for seed in range(seed_start, seed_start + seeds):
        hook = run_release_gate.run_deterministic_hook("chaos", seed=seed, repetitions=repetitions)
        hook_errors = run_release_gate.validate_deterministic_hook(hook, "chaos")
        if hook_errors:
            raise ChaosRunnerError("release-gate chaos evidence validation failed")
        hook_faults = hook["fault_results"]
        if [entry.get("fault") for entry in hook_faults] != fault_names:
            raise ChaosRunnerError("release-gate fault inventory does not match CHAOS_FAULTS")
        seed_results.append(
            {
                "seed": seed,
                "status": "reconciled_unknown",
                "hook_status": hook["status"],
                "attempted_repetitions": repetitions,
                "outcome_hash": hook["outcome_hash"],
                "trace": hook["trace"],
                "no_replay_assertion": hook["no_replay_assertion"],
                "silent_unknown_success": hook["safety"]["silent_unknown_success"],
                "fault_results": [
                    {
                        "fault": fault["fault"],
                        "accounted": True,
                        "attempted": repetitions,
                        "status": "reconciled_unknown",
                        "mutation_replayed": False,
                    }
                    for fault in hook_faults
                ],
                "replay_command": [
                    "python3", "scripts/run_chaos_test.py", "--seeds", "1",
                    "--seed-start", str(seed), "--repetitions", str(repetitions),
                    "--manifest", manifest_info["path"], "--artifact-dir", "artifacts/p7-chaos/",
                ],
            }
        )

    report: dict[str, Any] = {
        "schema_version": 1,
        "phase": 7,
        "kind": "phase7-chaos-test",
        "evidence_mode": "deterministic-offline",
        "status": "passed",
        "seed_start": seed_start,
        "seed_count": seeds,
        "repetitions": repetitions,
        "attempted_seed_repetitions": seeds * repetitions,
        "manifest": manifest_info,
        "fault_inventory": fault_names,
        "seed_results": seed_results,
        "chrome_safety": {"launch": "never", "download": "never", "attach": "never"},
    }
    errors = validate_report(report, seeds=seeds, repetitions=repetitions)
    if errors:
        raise ChaosRunnerError("chaos report validation failed")
    add_envelope(
        report,
        kind="phase7-chaos-test",
        command=[
            "python3", "scripts/run_chaos_test.py", "--seeds", str(seeds),
            "--seed-start", str(seed_start), "--repetitions", str(repetitions),
            "--manifest", manifest_info["path"], "--artifact-dir", "artifacts/p7-chaos/",
        ],
        build_tuple={"phase": 7, "seed_count": seeds, "repetitions": repetitions},
        environment={"network": "forbidden", "browser_launch": "never", "browser_download": "never", "browser_attach": "never"},
    )
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds", type=int, default=100, help=f"number of consecutive seeds (1..{MAX_SEEDS})")
    parser.add_argument("--seed-start", type=int, default=0, help="first non-negative deterministic seed")
    parser.add_argument("--repetitions", type=int, default=MIN_REPETITIONS, help=f"repetitions per seed ({MIN_REPETITIONS}..{MAX_REPETITIONS})")
    parser.add_argument("--manifest", default="tests/test-manifest.yaml", help="repository test manifest")
    parser.add_argument("--artifact-dir", default="artifacts/p7-chaos/", help="output directory under artifacts/")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        artifact_dir = _safe_artifact_dir(args.artifact_dir)
        report = build_report(
            seeds=args.seeds,
            seed_start=args.seed_start,
            repetitions=args.repetitions,
            manifest=args.manifest,
        )
        if validate_report(report, seeds=args.seeds, repetitions=args.repetitions):
            raise ChaosRunnerError("chaos report validation failed")
        write_json_atomic(artifact_dir / "report.json", report)
    except (ChaosRunnerError, OSError, ValueError) as exc:
        print(f"run_chaos_test: FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
