#!/usr/bin/env python3
"""Fail-closed checker for Phase 5 performance evidence artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from artifact_envelope import redact_for_persistence
from p5_performance import (
    CACHE_STATES,
    METRICS,
    MIN_P95_SAMPLES,
    MIN_P99_SAMPLES,
    MIN_WARMUPS,
    SCENARIO_PROBES,
    SNAPSHOT_MODES,
    SPACE_LEVELS,
    TEMPERATURES,
    P5PerformanceError,
    primary_cells,
)
from p5_statistics import validate_distribution

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_ROOT = ROOT / "artifacts"
MAX_BYTES = 64 * 1024 * 1024
HEX64 = re.compile(r"^[0-9a-f]{64}$")
HEX40 = re.compile(r"^[0-9a-f]{40}$")
FORBIDDEN_EVIDENCE_WORDS = re.compile(
    r"(?i)(?:guessed|guess|legacy|operator[_ -]?claim|byte[_ -]?estimate|invented)"
)


class P5ArtifactError(ValueError):
    """A Phase 5 artifact is absent, stale, guessed, or incomplete."""


def _read_json(path: Path) -> Any:
    try:
        if not path.is_file() or path.is_symlink() or path.stat().st_size > MAX_BYTES:
            raise P5ArtifactError(f"missing, symlinked, or oversized artifact: {path.name}")
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise P5ArtifactError(f"invalid JSON artifact: {path.name}") from exc


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise P5ArtifactError(message)


def _reject_forbidden(value: Any, path: str = "report") -> None:
    if isinstance(value, str) and FORBIDDEN_EVIDENCE_WORDS.search(value):
        raise P5ArtifactError(f"guessed/legacy evidence is forbidden at {path}")
    if isinstance(value, dict):
        for key, child in value.items():
            _reject_forbidden(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_forbidden(child, f"{path}[{index}]")


def _parse_timestamp(value: Any, field: str) -> datetime:
    _require(isinstance(value, str), f"{field} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise P5ArtifactError(f"{field} is not an ISO timestamp") from exc
    _require(parsed.tzinfo is not None, f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _validate_provenance(report: dict[str, Any], mode: str) -> datetime:
    provenance = report.get("provenance")
    _require(isinstance(provenance, dict), "provenance is required")
    expected_source = "offline_fixture_model" if mode == "offline" else "production_path"
    _require(provenance.get("source_class") == expected_source, "provenance source class is not explicit")
    _require(provenance.get("evidence_mode") == mode, "provenance evidence mode is inconsistent")
    expected_basis = "deterministic_fixture_model" if mode == "offline" else "production_host_extension_observation"
    _require(provenance.get("measurement_basis") == expected_basis, "provenance measurement basis is missing")
    _require(isinstance(provenance.get("command"), list) and provenance["command"], "command provenance is required")
    _require(isinstance(provenance.get("nonce"), str) and provenance["nonce"], "run nonce provenance is required")
    timestamp = _parse_timestamp(provenance.get("timestamp"), "provenance.timestamp")
    _require(isinstance(provenance.get("build_tuple"), dict), "provenance build tuple is required")
    return timestamp


def _validate_redaction(report: dict[str, Any]) -> None:
    redaction = report.get("redaction_status")
    _require(isinstance(redaction, dict), "redaction_status is required")
    _require(redaction.get("status") == "applied", "central redaction was not applied")
    _require(isinstance(redaction.get("policy"), str) and redaction["policy"], "redaction policy is required")
    for field in ("raw_browser_ids", "secrets", "absolute_paths", "page_bodies"):
        _require(redaction.get(field) is False, f"redaction status does not prove {field}=false")
    normalized = redact_for_persistence(report, max_nodes=1_000_000)
    _require(normalized == report, "persisted report is not stable after central redaction")


def _validate_baseline(report: dict[str, Any], mode: str) -> None:
    baseline = report.get("baseline_manifest")
    _require(isinstance(baseline, dict), "baseline_manifest is required")
    required = (
        "schema_version",
        "commit",
        "build_mode",
        "os_cpu",
        "chrome_build",
        "extension_host_tuple",
        "fixture_data_hash",
        "tokenizer",
        "concurrency",
        "sample_policy",
        "sample_count",
        "statistical_method",
    )
    for field in required:
        _require(field in baseline, f"baseline_manifest.{field} is required")
    _require(baseline.get("schema_version") == 1, "baseline manifest schema is unsupported")
    commit = baseline.get("commit")
    if mode == "live":
        _require(isinstance(commit, str) and HEX40.fullmatch(commit) is not None, "live evidence requires a 40-character commit")
        _require(isinstance(baseline.get("chrome_build"), str) and not baseline["chrome_build"].startswith("required-"), "live Chrome build provenance is required")
        _require(isinstance(baseline.get("extension_host_tuple"), str) and not baseline["extension_host_tuple"].startswith("required-"), "live extension/host provenance is required")
    else:
        _require(isinstance(commit, str) and (commit == "unknown" or HEX40.fullmatch(commit) is not None), "offline commit provenance is invalid")
    _require(isinstance(baseline.get("fixture_data_hash"), str) and HEX64.fullmatch(baseline["fixture_data_hash"]) is not None, "fixture data hash is required")
    method = baseline["statistical_method"]
    _require(
        isinstance(method, dict)
        and method.get("name") == "bootstrap_percentile"
        and method.get("confidence_level") == 0.95
        and isinstance(method.get("resamples"), int)
        and method["resamples"] >= 10,
        "bootstrap 95% method is required",
    )
    tokenizer = baseline.get("tokenizer")
    _require(isinstance(tokenizer, dict), "tokenizer provenance is required")
    for field in ("name", "version", "encoding", "hash", "status", "source"):
        _require(field in tokenizer, f"tokenizer.{field} is required")
    _require(isinstance(tokenizer["hash"], str) and HEX64.fullmatch(tokenizer["hash"]) is not None, "tokenizer hash is invalid")
    if mode == "live":
        _require(tokenizer.get("status") == "measured", "live tokenizer must be measured")
        _require("offline" not in str(tokenizer.get("source")).lower(), "live tokenizer cannot be offline evidence")


def _validate_matrix(report: dict[str, Any]) -> list[dict[str, Any]]:
    matrix = report.get("matrix")
    _require(isinstance(matrix, dict), "matrix is required")
    lists = {}
    for field, required_values in (
        ("temperatures", TEMPERATURES),
        ("cache_states", CACHE_STATES),
        ("snapshot_modes", SNAPSHOT_MODES),
    ):
        values = matrix.get(field)
        _require(isinstance(values, list) and values and len(values) == len(set(values)), f"matrix.{field} is invalid")
        _require(set(values) == set(required_values), f"matrix.{field} must contain exactly the Phase 5 values")
        lists[field] = values
    spaces = matrix.get("spaces")
    _require(isinstance(spaces, list) and spaces and len(spaces) == len(set(spaces)), "matrix.spaces is invalid")
    _require(set(spaces) == set(SPACE_LEVELS), "matrix.spaces must contain exactly 1,2,4,8")
    lists["spaces"] = [int(value) for value in spaces]
    fixtures = matrix.get("fixtures")
    _require(isinstance(fixtures, list) and fixtures and len(fixtures) == len(set(fixtures)), "matrix.fixtures is invalid")
    lists["fixtures"] = fixtures
    expected_count = len(fixtures) * len(lists["temperatures"]) * len(lists["cache_states"]) * len(lists["snapshot_modes"]) * len(lists["spaces"])
    _require(matrix.get("expected_cell_count") == expected_count, "matrix expected_cell_count is inconsistent")
    cells = primary_cells(fixtures, lists["temperatures"], lists["cache_states"], lists["snapshot_modes"], lists["spaces"])
    return cells


def _validate_metric(metric: Any, name: str, *, mode: str, valid_count: int, blocking: bool) -> None:
    _require(isinstance(metric, dict), f"metric {name} is missing")
    status = metric.get("status")
    if mode == "offline" and name in {
        "stale_ref_rate",
        "unknown_outcome_rate",
        "user_tab_responsiveness_ms",
        "host_rss_bytes",
        "browser_rss_bytes",
    }:
        _require(status == "not_measured_offline", f"offline {name} must be explicitly unavailable")
        _require(metric.get("p95") is None and metric.get("p99") is None, f"offline {name} has an unproven value")
        return
    _require(status == ("modeled_offline" if mode == "offline" else "measured"), f"metric {name} has an invalid evidence status")
    _require(metric.get("evidence_mode") == mode, f"metric {name} evidence mode is inconsistent")
    _require(metric.get("measurement_basis") in {"offline_fixture_model", "production_observation"}, f"metric {name} basis is invalid")
    _require(metric.get("sample_count") == valid_count, f"metric {name} sample count does not match the cell")
    for field in ("p50", "p95", "p99", "mean"):
        value = metric.get(field)
        _require(isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)) and value >= 0, f"metric {name}.{field} is invalid")
    try:
        validate_distribution(metric)
    except (TypeError, ValueError) as exc:
        raise P5ArtifactError(f"metric {name} does not contain a bootstrap distribution") from exc
    if blocking:
        _require(valid_count >= MIN_P95_SAMPLES, f"blocking metric {name} has fewer than 200 valid samples")
        _require(valid_count >= MIN_P99_SAMPLES, f"blocking metric {name} has fewer than 1000 valid samples")


def _validate_cell(cell: Any, expected: dict[str, Any], *, mode: str) -> None:
    _require(isinstance(cell, dict), "cell must be an object")
    _require(cell.get("cell_key") == expected["cell_key"], "cell identity is inconsistent")
    for field in ("fixture", "temperature", "cache_state", "snapshot_mode", "spaces"):
        _require(cell.get(field) == expected[field], f"cell.{field} is inconsistent")
    _require(isinstance(cell.get("blocking"), bool), "cell blocking status is required")
    _require(isinstance(cell.get("warmups"), int) and cell["warmups"] >= MIN_WARMUPS, "cell has fewer than 10 warmups")
    _require(cell.get("warmup_policy") == "fixed_count", "cell warmup policy is not explicit")
    accounting = cell.get("sample_accounting")
    _require(isinstance(accounting, dict), "cell sample accounting is required")
    for field in ("attempted", "valid", "errors", "invalid", "excluded"):
        _require(isinstance(accounting.get(field), int) and accounting[field] >= 0, f"cell sample accounting {field} is invalid")
    _require(
        accounting["attempted"] == accounting["valid"] + accounting["errors"] + accounting["invalid"] + accounting["excluded"],
        "cell sample accounting does not sum",
    )
    metrics = cell.get("metrics")
    _require(isinstance(metrics, dict), "cell metrics are required")
    for name in METRICS:
        _validate_metric(metrics.get(name), name, mode=mode, valid_count=accounting["valid"], blocking=cell["blocking"])


def validate_report(report: dict[str, Any], *, mode: str, now: datetime | None = None, max_age_seconds: int = 7 * 24 * 3600) -> None:
    """Validate report structure without requiring an artifact directory."""
    _require(mode in {"offline", "live"}, "checker mode must be offline or live")
    _require(isinstance(report, dict), "report must be an object")
    _reject_forbidden(report)
    _require(report.get("schema_version") == 1, "Phase 5 schema_version 1 is required")
    _require(report.get("phase") == 5 and report.get("kind") == "p5-performance-baseline", "legacy or non-Phase-5 report refused")
    _require(report.get("evidence_mode") == mode, "report evidence mode is inconsistent")
    _require(report.get("release_eligible") is (mode == "live"), "release eligibility is inconsistent with evidence mode")
    timestamp = _validate_provenance(report, mode)
    _validate_redaction(report)
    _validate_baseline(report, mode)
    expected = _validate_matrix(report)
    cells = report.get("cells")
    _require(isinstance(cells, list), "cells are required")
    by_key = {cell.get("cell_key"): cell for cell in cells if isinstance(cell, dict)}
    _require(len(by_key) == len(cells), "duplicate or malformed cell keys")
    for expected_cell in expected:
        _require(expected_cell["cell_key"] in by_key, f"missing blocking cell {expected_cell['cell_key']}")
        _validate_cell(by_key[expected_cell["cell_key"]], expected_cell, mode=mode)
    _require(len(by_key) == len(expected), "report contains an undeclared or extra blocking cell")
    probes = report.get("scenario_probes")
    _require(isinstance(probes, list), "scenario probes are required")
    probe_names = {probe.get("scenario") for probe in probes if isinstance(probe, dict)}
    _require(probe_names == set(SCENARIO_PROBES), "scenario probe coverage is incomplete")
    for probe in probes:
        _require(isinstance(probe.get("sample_accounting"), dict), "scenario probe accounting is required")
        _require(isinstance(probe.get("warmups"), int) and probe["warmups"] >= MIN_WARMUPS, "scenario probe warmups are insufficient")
    artifacts = report.get("artifacts")
    _require(isinstance(artifacts, dict), "artifact declarations are required")
    raw_files = artifacts.get("raw_sample_files")
    _require(isinstance(raw_files, list) and raw_files and all(isinstance(name, str) and "/" not in name and "\\" not in name for name in raw_files), "raw sample file declarations are required")
    if mode == "offline":
        _require(report.get("status") in {"offline_schema_only", "offline_model_complete"}, "offline status is invalid")
    else:
        _require(report.get("status") == "live_measured", "live status is invalid")
    current = now or datetime.now(timezone.utc)
    age = (current.astimezone(timezone.utc) - timestamp).total_seconds()
    _require(age <= max_age_seconds, "Phase 5 artifact is stale")
    _require(age >= -300, "Phase 5 artifact timestamp is in the future")


def _read_raw_samples(directory: Path, names: list[str]) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    for name in names:
        path = directory / name
        _require(path.parent == directory and path.is_file() and not path.is_symlink(), f"raw sample file is missing: {name}")
        _require(path.stat().st_size <= MAX_BYTES, f"raw sample file is oversized: {name}")
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError) as exc:
            raise P5ArtifactError(f"raw sample file is unreadable: {name}") from exc
        _require(bool(lines), f"raw sample file is empty: {name}")
        for line in lines:
            try:
                sample = json.loads(line)
            except json.JSONDecodeError as exc:
                raise P5ArtifactError(f"raw sample file contains invalid JSON: {name}") from exc
            _require(isinstance(sample, dict), "raw sample must be an object")
            _require(redact_for_persistence(sample) == sample, "raw sample is not centrally redacted")
            _reject_forbidden(sample, f"raw_samples.{name}")
            samples.append(sample)
    return samples


def _validate_raw_samples(report: dict[str, Any], samples: list[dict[str, Any]], mode: str) -> None:
    cell_map = {cell["cell_key"]: cell for cell in report["cells"]}
    probe_map = {probe["cell_key"]: probe for probe in report["scenario_probes"]}
    counts: dict[str, dict[str, int]] = {}
    for sample in samples:
        key = sample.get("cell_key")
        _require(key in cell_map or key in probe_map, "raw sample references an undeclared cell")
        _require(sample.get("evidence_mode") == mode, "raw sample evidence mode is inconsistent")
        expected_basis = "offline_fixture_model" if mode == "offline" else "production_observation"
        _require(sample.get("measurement_basis") == expected_basis, "raw sample measurement basis is invalid")
        metrics = sample.get("metrics")
        _require(isinstance(metrics, dict), "raw sample metrics are required")
        for metric in METRICS:
            value = metrics.get(metric)
            if mode == "offline" and metric in {"stale_ref_rate", "unknown_outcome_rate", "user_tab_responsiveness_ms", "host_rss_bytes", "browser_rss_bytes"}:
                _require(value is None, f"offline raw sample contains unproven {metric}")
            else:
                _require(isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)) and float(value) >= 0, f"raw sample {metric} is invalid")
        status = sample.get("sample_status")
        _require(status in {"valid", "error", "invalid", "excluded"}, "raw sample status is invalid")
        counts.setdefault(key, {"attempted": 0, "valid": 0, "errors": 0, "invalid": 0, "excluded": 0})["attempted"] += 1
        counts[key][status if status != "valid" else "valid"] += 1
    for key, cell in {**cell_map, **probe_map}.items():
        accounting = cell["sample_accounting"]
        _require(counts.get(key) == accounting, f"raw sample accounting does not match {key}")


def validate_artifact_directory(
    artifact_dir: str | Path,
    *,
    mode: str,
    now: datetime | None = None,
    max_age_seconds: int = 7 * 24 * 3600,
) -> dict[str, Any]:
    """Validate one complete Phase 5 artifact generation and its raw samples."""
    directory = Path(artifact_dir).expanduser()
    if not directory.is_absolute():
        directory = ROOT / directory
    directory = directory.resolve()
    try:
        directory.relative_to(ARTIFACT_ROOT.resolve())
    except ValueError as exc:
        raise P5ArtifactError("artifact directory must be inside artifacts/") from exc
    _require(directory != ARTIFACT_ROOT.resolve() and directory.is_dir() and not directory.is_symlink(), "artifact directory is missing")
    report = _read_json(directory / "baseline.json")
    _require(isinstance(report, dict), "baseline report must be an object")
    validate_report(report, mode=mode, now=now, max_age_seconds=max_age_seconds)
    baseline_manifest = _read_json(directory / "baseline-manifest.json")
    _require(
        isinstance(baseline_manifest, dict)
        and baseline_manifest == report["baseline_manifest"],
        "baseline manifest is not bound to the baseline report",
    )
    generation = _read_json(directory / "generation-manifest.json")
    _require(isinstance(generation, dict) and generation.get("complete") is True, "generation manifest is incomplete")
    _require(generation.get("phase") == 5 and generation.get("kind") == "p5-performance-generation", "generation manifest is not Phase 5")
    _require(generation.get("generation_id") == report["artifacts"].get("generation_id"), "generation identity does not match baseline")
    files = generation.get("files")
    _require(isinstance(files, list) and files, "generation file manifest is empty")
    for entry in files:
        _require(isinstance(entry, dict) and isinstance(entry.get("name"), str) and "/" not in entry["name"] and "\\" not in entry["name"], "generation file entry is unsafe")
        path = directory / entry["name"]
        _require(path.is_file() and not path.is_symlink(), f"generation file is missing: {entry['name']}")
        content = path.read_bytes()
        _require(entry.get("bytes") == len(content) and entry.get("sha256") == hashlib.sha256(content).hexdigest(), f"generation hash mismatch: {entry['name']}")
    commit_path = directory / "COMMIT"
    commit = _read_json(commit_path)
    _require(
        isinstance(commit, dict)
        and commit.get("complete") is True
        and commit.get("generation_id") == generation["generation_id"],
        "commit marker is invalid",
    )
    manifest_hash = hashlib.sha256((directory / "generation-manifest.json").read_bytes()).hexdigest()
    _require(commit.get("manifest_sha256") == manifest_hash, "commit marker does not bind the generation manifest")
    samples = _read_raw_samples(directory, report["artifacts"]["raw_sample_files"])
    _require(len(samples) == report["artifacts"]["raw_sample_count"], "raw sample total does not match baseline")
    _validate_raw_samples(report, samples, mode)
    return report


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="Check a Phase 5 performance evidence generation.")
    value.add_argument("--mode", choices=("offline", "live"), required=True)
    value.add_argument("--artifact-dir", type=Path, default=Path("artifacts/p5-performance"))
    value.add_argument("--max-age-seconds", type=int, default=7 * 24 * 3600)
    return value


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        validate_artifact_directory(args.artifact_dir, mode=args.mode, max_age_seconds=args.max_age_seconds)
    except (P5ArtifactError, P5PerformanceError, OSError, TypeError, ValueError) as exc:
        print(f"check_p5_performance: FAIL: {exc}", file=sys.stderr)
        return 1
    print(f"check_p5_performance: PASS (Phase 5 evidence mode={args.mode})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
