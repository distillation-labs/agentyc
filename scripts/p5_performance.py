"""Phase 5 context/performance evidence model and artifact publisher.

This module is deliberately separate from the existing Phase 0 direct/CDP runner.
Offline mode is deterministic fixture evidence only. Live mode ingests samples from
an independently owned production-path harness and refuses to invent observations.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import platform
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from artifact_envelope import (
    envelope as add_envelope,
)
from artifact_envelope import (
    new_nonce,
    redact_for_persistence,
    repository_relative,
    sha256_bytes,
    write_bytes_atomic,
    write_json_atomic,
    write_jsonl_atomic,
    write_text_atomic,
)
from p5_statistics import stable_seed, summarize_distribution

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = ROOT / "tests" / "fixtures"
FIXTURE_MANIFEST = FIXTURE_ROOT / "p5-performance" / "manifest.json"
ARTIFACT_ROOT = ROOT / "artifacts"
PHASE = 5
SCHEMA_VERSION = 1
MIN_WARMUPS = 10
MIN_P95_SAMPLES = 200
MIN_P99_SAMPLES = 1_000
DEFAULT_SAMPLES = MIN_P99_SAMPLES
SMOKE_SAMPLES = 30
DEFAULT_BOOTSTRAP_RESAMPLES = 200
MAX_SAMPLES = 100_000
MAX_FIXTURE_BYTES = 2 * 1024 * 1024
MAX_ARTIFACT_BYTES = 64 * 1024 * 1024
MAX_REDACTION_NODES = 1_000_000
TEMPERATURES = ("cold", "warm")
CACHE_STATES = ("clean", "dirty", "resync")
SNAPSHOT_MODES = ("full", "min", "focus", "delta")
SPACE_LEVELS = (1, 2, 4, 8)
SCENARIO_PROBES = ("mutation-burst", "event-gap", "reconnect", "user-tab-active")
METRICS = (
    "snapshot_latency_ms",
    "warm_action_ms",
    "warm_wait_ms",
    "batch_separate_round_trip_ms",
    "batch_round_trip_ms",
    "transport_bytes",
    "utf8_bytes",
    "serialized_tokens",
    "model_context_tokens",
    "artifact_throughput_bytes_per_sec",
    "event_lag_ms",
    "stale_ref_rate",
    "unknown_outcome_rate",
    "user_tab_responsiveness_ms",
    "host_rss_bytes",
    "browser_rss_bytes",
)
OFFLINE_UNAVAILABLE = {
    "stale_ref_rate",
    "unknown_outcome_rate",
    "user_tab_responsiveness_ms",
    "host_rss_bytes",
    "browser_rss_bytes",
}
FORBIDDEN_EVIDENCE_WORDS = re.compile(
    r"(?i)(?:guessed|guess|legacy|operator[_ -]?claim|byte[_ -]?estimate|invented)"
)


class P5PerformanceError(ValueError):
    """An unsafe, incomplete, or internally inconsistent Phase 5 run."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _git_commit() -> str:
    supplied = os.environ.get("P5_BENCHMARK_COMMIT", "").strip()
    if supplied:
        return supplied
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    value = result.stdout.strip()
    return value if re.fullmatch(r"[0-9a-f]{40}", value) else "unknown"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
        if len(raw) > 256 * 1024:
            raise P5PerformanceError("fixture manifest exceeds the bounded read limit")
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise P5PerformanceError(f"cannot read JSON {repository_relative(path)}") from exc
    if not isinstance(value, dict):
        raise P5PerformanceError("fixture manifest must be an object")
    return value


def _safe_fixture_path(relative: str) -> Path:
    candidate = (FIXTURE_MANIFEST.parent / relative).resolve()
    try:
        candidate.relative_to(FIXTURE_ROOT.resolve())
    except ValueError as exc:
        raise P5PerformanceError("fixture path escapes tests/fixtures") from exc
    current = FIXTURE_ROOT
    for component in candidate.relative_to(FIXTURE_ROOT.resolve()).parts:
        current = current / component
        if current.is_symlink():
            raise P5PerformanceError("fixture path components must not be symlinks")
    if not candidate.is_file() or candidate.stat().st_size > MAX_FIXTURE_BYTES:
        raise P5PerformanceError("fixture is missing or exceeds the bounded read limit")
    return candidate


def load_fixtures(names: list[str] | None = None) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Load the immutable Phase 5 fixture manifest and content hashes."""
    raw_manifest = FIXTURE_MANIFEST.read_bytes()
    manifest = _read_json(FIXTURE_MANIFEST)
    entries = manifest.get("fixtures")
    if not isinstance(entries, list) or not entries:
        raise P5PerformanceError("Phase 5 fixture manifest is empty")
    selected_names = set(names) if names is not None else None
    records: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise P5PerformanceError("fixture entries must be objects")
        name = entry.get("name")
        relative = entry.get("file")
        topology = entry.get("topology")
        if not all(isinstance(value, str) and value for value in (name, relative, topology)):
            raise P5PerformanceError("fixture entries need name, file, and topology")
        if name in records:
            raise P5PerformanceError("fixture names must be unique")
        if selected_names is not None and name not in selected_names:
            continue
        path = _safe_fixture_path(relative)
        content = path.read_bytes()
        records[name] = {
            "name": name,
            "file": repository_relative(path),
            "path": path,
            "sha256": hashlib.sha256(content).hexdigest(),
            "bytes": len(content),
            "topology": topology,
            "mutation_profile": entry.get("mutation_profile", "unspecified"),
            "controls": len(re.findall(rb"<(?:a|button|input|select|textarea)\b", content, re.I)),
            "frames": len(re.findall(rb"<iframe\b", content, re.I)),
        }
    if selected_names is not None and set(records) != selected_names:
        missing = sorted(selected_names - set(records))
        raise P5PerformanceError("unknown Phase 5 fixture: " + ", ".join(missing))
    if not records:
        raise P5PerformanceError("no Phase 5 fixtures selected")
    fixture_hash_input = [sha256_bytes(raw_manifest)]
    fixture_hash_input.extend(
        f"{record['name']}:{record['file']}:{record['sha256']}" for record in records.values()
    )
    metadata = {
        "path": repository_relative(FIXTURE_MANIFEST),
        "sha256": sha256_bytes(raw_manifest),
        "fixture_data_hash": hashlib.sha256("\n".join(fixture_hash_input).encode("utf-8")).hexdigest(),
        "fixture_set": manifest.get("fixture_set"),
        "schema_version": manifest.get("schema_version"),
    }
    return metadata, records


def parse_csv(value: str, label: str, allowed: tuple[str, ...] | None = None) -> list[str]:
    parts = [part.strip() for part in value.split(",") if part.strip()]
    if not parts or len(parts) != len(set(parts)):
        raise P5PerformanceError(f"{label} must be a non-empty list without duplicates")
    if allowed is not None:
        unknown = [part for part in parts if part not in allowed]
        if unknown:
            raise P5PerformanceError(f"{label} contains unsupported values: {', '.join(unknown)}")
    return parts


def parse_spaces(value: str) -> list[int]:
    values = parse_csv(value, "--spaces")
    try:
        parsed = [int(value) for value in values]
    except ValueError as exc:
        raise P5PerformanceError("--spaces must contain positive integers") from exc
    if any(value not in SPACE_LEVELS for value in parsed):
        raise P5PerformanceError("--spaces must be selected from 1,2,4,8")
    return parsed


def cell_key(cell: dict[str, Any]) -> str:
    fields = ("fixture", "temperature", "cache_state", "snapshot_mode", "spaces")
    return ";".join(f"{field}={cell[field]}" for field in fields)


def scenario_key(scenario: str, fixture: str) -> str:
    return f"scenario={scenario};fixture={fixture}"


def primary_cells(
    fixtures: list[str], temperatures: list[str], caches: list[str], modes: list[str], spaces: list[int]
) -> list[dict[str, Any]]:
    return [
        {
            "cell_key": cell_key(
                {
                    "fixture": fixture,
                    "temperature": temperature,
                    "cache_state": cache_state,
                    "snapshot_mode": snapshot_mode,
                    "spaces": space_count,
                }
            ),
            "fixture": fixture,
            "temperature": temperature,
            "cache_state": cache_state,
            "snapshot_mode": snapshot_mode,
            "spaces": space_count,
        }
        for fixture in fixtures
        for temperature in temperatures
        for cache_state in caches
        for snapshot_mode in modes
        for space_count in spaces
    ]


def _model_sample(
    record: dict[str, Any], cell: dict[str, Any], sample_index: int, *, scenario: str | None = None
) -> dict[str, Any]:
    key = cell["cell_key"] if scenario is None else scenario_key(scenario, record["name"])
    entropy = stable_seed("p5-offline", key, sample_index)
    jitter = (entropy % 10_000) / 10_000.0
    temperature_factor = 1.85 if cell.get("temperature") == "cold" else 1.0
    cache_factor = {"clean": 0.55, "dirty": 1.0, "resync": 1.45}[cell.get("cache_state", "dirty")]
    mode_factor = {"full": 1.0, "min": 0.68, "focus": 0.46, "delta": 0.28}[cell.get("snapshot_mode", "full")]
    space_factor = 1.0 + (int(cell.get("spaces", 1)) - 1) * 0.06
    scenario_factor = {
        None: 1.0,
        "mutation-burst": 1.35,
        "event-gap": 1.2,
        "reconnect": 1.5,
        "user-tab-active": 1.1,
    }[scenario]
    fixture_bytes = float(record["bytes"])
    controls = max(1, int(record["controls"]))
    serialized_bytes = int(
        max(64.0, fixture_bytes * mode_factor + controls * 24 + (entropy % 31))
    )
    transport_bytes = serialized_bytes + 32 + int(cell.get("spaces", 1)) * 4
    tokens = math.ceil(serialized_bytes / 4)
    base = temperature_factor * cache_factor * space_factor * scenario_factor
    snapshot_ms = 0.12 + (fixture_bytes / 2_500.0) * base * (0.8 + jitter * 0.2)
    action_ms = 0.20 + controls * 0.018 * (0.7 + jitter * 0.3) * (1.0 if cell.get("temperature") == "warm" else 1.45)
    wait_ms = 0.35 + (0.03 * controls + 0.02 * int(record["frames"])) * base * (0.8 + jitter * 0.2)
    separate_ms = 0.7 + 0.08 * int(cell.get("spaces", 1)) + 0.02 * controls + jitter * 0.03
    batch_ms = separate_ms * 0.48
    artifact_ms = 0.25 + serialized_bytes / 80_000.0
    event_lag = 0.10 + wait_ms * (1.0 if scenario != "event-gap" else 3.0)
    throughput = serialized_bytes / (artifact_ms / 1_000.0)
    metrics: dict[str, float | None] = {
        "snapshot_latency_ms": round(snapshot_ms, 6),
        "warm_action_ms": round(action_ms, 6),
        "warm_wait_ms": round(wait_ms, 6),
        "batch_separate_round_trip_ms": round(separate_ms, 6),
        "batch_round_trip_ms": round(batch_ms, 6),
        "transport_bytes": float(transport_bytes),
        "utf8_bytes": float(serialized_bytes),
        "serialized_tokens": float(tokens),
        "model_context_tokens": float(tokens),
        "artifact_throughput_bytes_per_sec": round(throughput, 6),
        "event_lag_ms": round(event_lag, 6),
        "stale_ref_rate": None,
        "unknown_outcome_rate": None,
        "user_tab_responsiveness_ms": None,
        "host_rss_bytes": None,
        "browser_rss_bytes": None,
    }
    return {
        "cell_key": key,
        "sample_index": sample_index,
        "sample_status": "valid",
        "measurement_basis": "offline_fixture_model",
        "evidence_mode": "offline",
        "scenario": scenario,
        "metrics": metrics,
    }


def _accounting(samples: list[dict[str, Any]]) -> dict[str, int]:
    statuses = [sample.get("sample_status") for sample in samples]
    valid = statuses.count("valid")
    errors = statuses.count("error")
    invalid = statuses.count("invalid")
    excluded = statuses.count("excluded")
    return {
        "attempted": len(samples),
        "valid": valid,
        "errors": errors,
        "invalid": invalid,
        "excluded": excluded,
    }


def _metric_summary(
    samples: list[dict[str, Any]], metric: str, *, evidence_mode: str, bootstrap_resamples: int, seed: int
) -> dict[str, Any]:
    accounting = _accounting(samples)
    values = [
        float(sample["metrics"][metric])
        for sample in samples
        if sample.get("sample_status") == "valid"
        and isinstance(sample.get("metrics"), dict)
        and isinstance(sample["metrics"].get(metric), (int, float))
        and not isinstance(sample["metrics"].get(metric), bool)
        and math.isfinite(float(sample["metrics"][metric]))
        and float(sample["metrics"][metric]) >= 0.0
    ]
    if not values:
        status = "not_measured_offline" if evidence_mode == "offline" else "missing_measurement"
        return {
            "status": status,
            "evidence_mode": evidence_mode,
            "measurement_basis": "not_observed",
            "sample_count": accounting["valid"],
            "p50": None,
            "p95": None,
            "p99": None,
            "mean": None,
            "confidence_intervals": None,
        }
    distribution = summarize_distribution(
        values,
        seed=seed,
        bootstrap_resamples=bootstrap_resamples,
    )
    return {
        "status": "modeled_offline" if evidence_mode == "offline" else "measured",
        "evidence_mode": evidence_mode,
        "measurement_basis": "offline_fixture_model" if evidence_mode == "offline" else "production_observation",
        **distribution,
    }


def _summarize_cell(
    cell: dict[str, Any],
    samples: list[dict[str, Any]],
    *,
    evidence_mode: str,
    warmups: int,
    blocking: bool,
    bootstrap_resamples: int,
) -> dict[str, Any]:
    accounting = _accounting(samples)
    metrics = {
        metric: _metric_summary(
            samples,
            metric,
            evidence_mode=evidence_mode,
            bootstrap_resamples=bootstrap_resamples,
            seed=stable_seed("p5-ci", cell["cell_key"], metric),
        )
        for metric in METRICS
    }
    return {
        **cell,
        "blocking": blocking,
        "warmups": warmups,
        "warmup_policy": "fixed_count",
        "sample_accounting": accounting,
        "metrics": metrics,
        "actionable_control_coverage": 1.0 if evidence_mode == "offline" else None,
        "equivalent_coverage_status": "modeled_offline" if evidence_mode == "offline" else "measured",
    }


def _tokenizer() -> dict[str, Any]:
    return {
        "name": "p5-offline-utf8-ceil4",
        "version": "1",
        "encoding": "UTF-8 bytes divided by four, rounded up",
        "hash": hashlib.sha256(b"p5-offline-utf8-ceil4-v1").hexdigest(),
        "status": "modeled_offline",
        "source": "benchmark_defined_offline_tokenizer",
    }


def _baseline_manifest(
    fixture_metadata: dict[str, Any],
    records: dict[str, dict[str, Any]],
    matrix: dict[str, Any],
    *,
    sample_count: int,
    warmups: int,
    bootstrap_resamples: int,
    evidence_mode: str,
) -> dict[str, Any]:
    tokenizer = _tokenizer()
    return {
        "schema_version": 1,
        "commit": _git_commit(),
        "build_mode": "offline-fixture-model" if evidence_mode == "offline" else "production-live",
        "os_cpu": f"{platform.system()}-{platform.machine()}",
        "chrome_build": None if evidence_mode == "offline" else "required-from-live-input",
        "extension_host_tuple": None if evidence_mode == "offline" else "required-from-live-input",
        "fixture_manifest": fixture_metadata["path"],
        "fixture_manifest_sha256": fixture_metadata["sha256"],
        "fixture_data_hash": fixture_metadata["fixture_data_hash"],
        "fixtures": [
            {
                "name": record["name"],
                "file": record["file"],
                "sha256": record["sha256"],
                "bytes": record["bytes"],
                "topology": record["topology"],
            }
            for record in records.values()
        ],
        "tokenizer": tokenizer,
        "concurrency": {"space_levels": matrix["spaces"]},
        "cache_states": matrix["cache_states"],
        "snapshot_modes": matrix["snapshot_modes"],
        "temperatures": matrix["temperatures"],
        "sample_policy": {
            "warmups": warmups,
            "warmup_policy": "fixed_count",
            "p95_min_valid_samples": MIN_P95_SAMPLES,
            "p99_min_valid_samples": MIN_P99_SAMPLES,
            "blocking": matrix["blocking"],
        },
        "sample_count": {"valid_per_blocking_cell": sample_count, "blocking_cells": matrix["expected_cell_count"]},
        "statistical_method": {
            "name": "bootstrap_percentile",
            "confidence_level": 0.95,
            "resamples": bootstrap_resamples,
            "seed_derivation": "sha256(cell_key,metric)",
        },
    }


def _base_report(
    *,
    evidence_mode: str,
    status: str,
    matrix: dict[str, Any],
    records: dict[str, dict[str, Any]],
    fixture_metadata: dict[str, Any],
    cells: list[dict[str, Any]],
    scenario_probes: list[dict[str, Any]],
    raw_samples: list[dict[str, Any]],
    warmups: int,
    bootstrap_resamples: int,
    blocking: bool,
    command: list[str],
    nonce: str | None = None,
) -> dict[str, Any]:
    baseline = _baseline_manifest(
        fixture_metadata,
        records,
        matrix,
        sample_count=matrix["samples_per_cell"],
        warmups=warmups,
        bootstrap_resamples=bootstrap_resamples,
        evidence_mode=evidence_mode,
    )
    add_envelope(
        baseline,
        kind="p5-performance-baseline-manifest",
        command=command,
        build_tuple={
            "source_class": "offline_fixture_model" if evidence_mode == "offline" else "production_path",
            "commit": baseline["commit"],
            "build_mode": baseline["build_mode"],
            "fixture_data_hash": baseline["fixture_data_hash"],
            "tokenizer": baseline["tokenizer"]["name"],
        },
        result={"status": "complete", "kind": "p5-performance-baseline-manifest"},
        nonce=nonce,
    )
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "phase": PHASE,
        "kind": "p5-performance-baseline",
        "status": status,
        "evidence_mode": evidence_mode,
        "release_eligible": bool(evidence_mode == "live" and blocking),
        "baseline_manifest": baseline,
        "fixture_set": fixture_metadata,
        "tokenizer": baseline["tokenizer"],
        "matrix": matrix,
        "coverage": {
            "topologies": sorted({record["topology"] for record in records.values()}),
            "required_scenarios": list(SCENARIO_PROBES),
            "scenario_probe_count": len(scenario_probes),
            "blocking_cells": sum(bool(cell["blocking"]) for cell in cells),
        },
        "cells": cells,
        "scenario_probes": scenario_probes,
        "sample_accounting": _accounting(raw_samples),
        "artifacts": {
            "raw_sample_files": ["raw_samples.jsonl"],
            "raw_sample_count": len(raw_samples),
            "baseline_manifest_file": "baseline-manifest.json",
        },
        "metric_policy": {
            "offline_unavailable": sorted(OFFLINE_UNAVAILABLE),
            "live_required": list(METRICS),
            "guessed_or_legacy_rejected": True,
        },
    }
    # The shared envelope helper has a deliberately small Phase 0 node bound.
    # Build its small provenance envelope separately, then apply the same central
    # redaction policy to the larger Phase 5 matrix with an explicit Phase 5 bound.
    envelope_stub: dict[str, Any] = {
        "phase": PHASE,
        "kind": "p5-performance-baseline",
        "status": status,
        "evidence_mode": evidence_mode,
    }
    add_envelope(
        envelope_stub,
        kind="p5-performance",
        command=command,
        build_tuple={
            "source_class": "offline_fixture_model" if evidence_mode == "offline" else "production_path",
            "commit": baseline["commit"],
            "build_mode": baseline["build_mode"],
            "fixture_data_hash": baseline["fixture_data_hash"],
            "tokenizer": baseline["tokenizer"]["name"],
        },
        nonce=nonce,
    )
    safe_report = redact_for_persistence(report, max_nodes=MAX_REDACTION_NODES)
    safe_report.update(
        {
            key: envelope_stub[key]
            for key in (
                "schema_version",
                "build_tuple",
                "environment",
                "timestamp",
                "nonce",
                "command",
                "result",
                "provenance",
                "redaction_status",
            )
        }
    )
    safe_report["provenance"]["source_class"] = "offline_fixture_model" if evidence_mode == "offline" else "production_path"
    safe_report["provenance"]["evidence_mode"] = evidence_mode
    safe_report["provenance"]["measurement_basis"] = (
        "deterministic_fixture_model" if evidence_mode == "offline" else "production_host_extension_observation"
    )
    return safe_report


def build_offline_report(
    *,
    fixture_names: list[str] | None = None,
    temperatures: list[str] | None = None,
    caches: list[str] | None = None,
    modes: list[str] | None = None,
    spaces: list[int] | None = None,
    samples: int = DEFAULT_SAMPLES,
    warmups: int = MIN_WARMUPS,
    bootstrap_resamples: int = DEFAULT_BOOTSTRAP_RESAMPLES,
    blocking: bool = True,
    command: list[str] | None = None,
    nonce: str | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Build a deterministic offline report and its unpersisted raw samples."""
    if samples < 1 or samples > MAX_SAMPLES:
        raise P5PerformanceError("samples must be between 1 and 100000")
    if blocking and samples < MIN_P99_SAMPLES:
        raise P5PerformanceError(
            f"blocking cells require at least {MIN_P99_SAMPLES} valid samples"
        )
    if warmups < MIN_WARMUPS:
        raise P5PerformanceError(f"at least {MIN_WARMUPS} warmups are required")
    if bootstrap_resamples < 10 or bootstrap_resamples > 10_000:
        raise P5PerformanceError("bootstrap resamples must be between 10 and 10000")
    fixture_metadata, records = load_fixtures(fixture_names)
    selected_names = list(records)
    temperatures = temperatures or list(TEMPERATURES)
    caches = caches or list(CACHE_STATES)
    modes = modes or list(SNAPSHOT_MODES)
    spaces = spaces or list(SPACE_LEVELS)
    for values, allowed, label in (
        (temperatures, TEMPERATURES, "temperatures"),
        (caches, CACHE_STATES, "cache states"),
        (modes, SNAPSHOT_MODES, "snapshot modes"),
    ):
        if set(values) - set(allowed) or len(values) != len(set(values)) or not values:
            raise P5PerformanceError(f"invalid {label}")
    if any(value not in SPACE_LEVELS for value in spaces) or len(spaces) != len(set(spaces)) or not spaces:
        raise P5PerformanceError("invalid space levels")
    matrix = {
        "fixtures": selected_names,
        "temperatures": temperatures,
        "cache_states": caches,
        "snapshot_modes": modes,
        "spaces": spaces,
        "expected_cell_count": len(selected_names) * len(temperatures) * len(caches) * len(modes) * len(spaces),
        "samples_per_cell": samples,
        "blocking": blocking,
    }
    cells: list[dict[str, Any]] = []
    raw_samples: list[dict[str, Any]] = []
    for cell in primary_cells(selected_names, temperatures, caches, modes, spaces):
        for index in range(warmups):
            _model_sample(records[cell["fixture"]], cell, index)
        cell_samples = [_model_sample(records[cell["fixture"]], cell, index) for index in range(samples)]
        raw_samples.extend(cell_samples)
        cells.append(
            _summarize_cell(
                cell,
                cell_samples,
                evidence_mode="offline",
                warmups=warmups,
                blocking=blocking,
                bootstrap_resamples=bootstrap_resamples,
            )
        )
    scenario_probes: list[dict[str, Any]] = []
    probe_record = records[selected_names[0]]
    for scenario in SCENARIO_PROBES:
        scenario_cell = {
            "cell_key": scenario_key(scenario, probe_record["name"]),
            "fixture": probe_record["name"],
            "scenario": scenario,
            "spaces": max(spaces),
        }
        probe_count = min(samples, SMOKE_SAMPLES) if not blocking else samples
        probe_samples = [
            _model_sample(probe_record, scenario_cell, index, scenario=scenario)
            for index in range(probe_count)
        ]
        raw_samples.extend(probe_samples)
        scenario_probes.append(
            _summarize_cell(
                scenario_cell,
                probe_samples,
                evidence_mode="offline",
                warmups=warmups,
                blocking=False,
                bootstrap_resamples=bootstrap_resamples,
            )
        )
    report = _base_report(
        evidence_mode="offline",
        status="offline_schema_only" if not blocking else "offline_model_complete",
        matrix=matrix,
        records=records,
        fixture_metadata=fixture_metadata,
        cells=cells,
        scenario_probes=scenario_probes,
        raw_samples=raw_samples,
        warmups=warmups,
        bootstrap_resamples=bootstrap_resamples,
        blocking=blocking,
        command=command or ["python3", "scripts/run_p5_performance.py", "--mode", "offline"],
        nonce=nonce,
    )
    return report, raw_samples


def _safe_artifact_dir(value: str | Path) -> Path:
    requested = Path(value).expanduser()
    if not requested.is_absolute():
        requested = ROOT / requested
    resolved = requested.resolve()
    try:
        resolved.relative_to(ARTIFACT_ROOT.resolve())
    except ValueError as exc:
        raise P5PerformanceError("artifact directory must be inside artifacts/") from exc
    if resolved == ARTIFACT_ROOT.resolve() or (requested.exists() and not requested.is_dir()):
        raise P5PerformanceError("artifact directory must be a child directory")
    current = requested
    while current != current.parent:
        if current.is_symlink():
            raise P5PerformanceError("artifact path components must not be symlinks")
        current = current.parent
    return resolved


def _raw_chunks(samples: list[dict[str, Any]]) -> list[tuple[str, bytes]]:
    chunks: list[tuple[str, bytes]] = []
    current = bytearray()
    for sample in samples:
        safe = redact_for_persistence(sample)
        line = (json.dumps(safe, separators=(",", ":"), sort_keys=True, ensure_ascii=True, allow_nan=False) + "\n").encode("utf-8")
        if len(line) > MAX_ARTIFACT_BYTES:
            raise P5PerformanceError("one raw sample exceeds the artifact bound")
        if current and len(current) + len(line) > MAX_ARTIFACT_BYTES:
            chunks.append(("raw_samples.jsonl" if not chunks else f"raw_samples-{len(chunks):03d}.jsonl", bytes(current)))
            current = bytearray()
        current.extend(line)
    if current or not chunks:
        chunks.append(("raw_samples.jsonl" if not chunks else f"raw_samples-{len(chunks):03d}.jsonl", bytes(current)))
    return chunks


def _file_hash(path: Path) -> dict[str, Any]:
    content = path.read_bytes()
    return {"name": path.name, "sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)}


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Phase 5 performance evidence",
        "",
        f"- Evidence mode: `{report['evidence_mode']}`",
        f"- Status: `{report['status']}`",
        f"- Release eligible: `{report['release_eligible']}`",
        f"- Fixture data hash: `{report['baseline_manifest']['fixture_data_hash']}`",
        f"- Statistical method: `{report['baseline_manifest']['statistical_method']['name']}` at 95%",
        "",
        "| Fixture | Temperature | Cache | Snapshot | Spaces | Valid | Blocking | Snapshot p95 (ms) | Warm action p95 (ms) |",
        "|---|---|---|---|---:|---:|---|---:|---:|",
    ]
    for cell in report["cells"]:
        metrics = cell["metrics"]
        lines.append(
            f"| {cell['fixture']} | {cell['temperature']} | {cell['cache_state']} | {cell['snapshot_mode']} | "
            f"{cell['spaces']} | {cell['sample_accounting']['valid']} | {cell['blocking']} | "
            f"{metrics['snapshot_latency_ms']['p95'] if metrics['snapshot_latency_ms']['p95'] is not None else 'n/a'} | "
            f"{metrics['warm_action_ms']['p95'] if metrics['warm_action_ms']['p95'] is not None else 'n/a'} |"
        )
    lines.extend(["", "Offline values are deterministic fixture-model evidence and cannot close a live release gate.", ""])
    return "\n".join(lines)


def publish_artifact(artifact_dir: str | Path, report: dict[str, Any], samples: list[dict[str, Any]]) -> Path:
    """Publish a complete, hashed, redacted Phase 5 evidence generation."""
    destination = _safe_artifact_dir(artifact_dir)
    chunks = _raw_chunks(samples)
    staged_report = copy.deepcopy(report)
    generation_id = f"generation-{staged_report.get('nonce', new_nonce())}"
    staged_report["artifacts"]["raw_sample_files"] = [name for name, _ in chunks]
    staged_report["artifacts"]["raw_sample_count"] = len(samples)
    staged_report["artifacts"]["generation_id"] = generation_id
    staged_report["raw_sample_declarations"] = [
        {
            "name": name,
            "sha256": hashlib.sha256(content).hexdigest(),
            "bytes": len(content),
            "sample_count": content.count(b"\n"),
        }
        for name, content in chunks
    ]
    stage = Path(tempfile.mkdtemp(prefix=".p5-performance-", dir=destination.parent))
    previous: Path | None = None
    try:
        safe_baseline = redact_for_persistence(staged_report, max_nodes=MAX_REDACTION_NODES)
        write_bytes_atomic(
            stage / "baseline.json",
            (json.dumps(safe_baseline, indent=2, sort_keys=True, ensure_ascii=True, allow_nan=False) + "\n").encode("utf-8"),
            max_bytes=MAX_ARTIFACT_BYTES,
        )
        write_json_atomic(
            stage / "baseline-manifest.json",
            redact_for_persistence(staged_report["baseline_manifest"], max_nodes=MAX_REDACTION_NODES),
        )
        write_text_atomic(stage / "baseline.md", _markdown(safe_baseline))
        for name, content in chunks:
            write_jsonl_atomic(stage / name, content, max_bytes=MAX_ARTIFACT_BYTES)
        file_names = ["baseline.json", "baseline-manifest.json", "baseline.md", *[name for name, _ in chunks]]
        generation = {
            "schema_version": 1,
            "phase": PHASE,
            "kind": "p5-performance-generation",
            "generation_id": generation_id,
            "complete": True,
            "files": [_file_hash(stage / name) for name in file_names],
            "raw_sample_files": [name for name, _ in chunks],
            "redaction_status": {"status": "applied", "policy": "central-allowlist-bounded-recursive-redaction"},
        }
        add_envelope(
            generation,
            kind="p5-performance-generation",
            command=staged_report["command"],
            build_tuple={
                "source_class": staged_report["evidence_mode"],
                "commit": staged_report["baseline_manifest"]["commit"],
            },
            environment=staged_report["environment"],
            result={"status": "complete", "file_count": len(file_names)},
            nonce=staged_report.get("nonce"),
        )
        write_json_atomic(stage / "generation-manifest.json", generation)
        manifest_hash = hashlib.sha256((stage / "generation-manifest.json").read_bytes()).hexdigest()
        write_text_atomic(
            stage / "COMMIT",
            json.dumps(
                {"schema_version": 1, "kind": "p5-performance-commit", "generation_id": generation_id, "manifest_sha256": manifest_hash, "complete": True},
                sort_keys=True,
            )
            + "\n",
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            previous = destination.parent / f".{destination.name}.previous-{staged_report.get('nonce', new_nonce())[:12]}"
            destination.replace(previous)
        stage.replace(destination)
        stage = Path()
        return destination
    finally:
        if stage != Path() and stage.exists():
            shutil.rmtree(stage, ignore_errors=True)


def build_live_report(payload: dict[str, Any], *, command: list[str] | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Build a live report only from a production-path sample package."""
    if payload.get("evidence_mode") != "live":
        raise P5PerformanceError("live input must explicitly declare evidence_mode=live")
    provenance = payload.get("provenance")
    if not isinstance(provenance, dict) or provenance.get("source_class") != "production_path":
        raise P5PerformanceError("live input must prove production_path provenance")
    redaction = payload.get("redaction_status")
    if not isinstance(redaction, dict) or redaction.get("status") != "applied":
        raise P5PerformanceError("live input must include applied redaction status")
    samples = payload.get("samples")
    scenario_samples = payload.get("scenario_samples", [])
    matrix = payload.get("matrix")
    baseline = payload.get("baseline_manifest")
    if not isinstance(samples, list) or not isinstance(scenario_samples, list) or not isinstance(matrix, dict) or not isinstance(baseline, dict):
        raise P5PerformanceError("live input requires samples, scenario_samples, matrix, and baseline_manifest")
    if not samples:
        raise P5PerformanceError("live input contains no blocking samples")
    fixture_names = [str(value) for value in matrix.get("fixtures", [])]
    fixture_metadata, records = load_fixtures(fixture_names)
    temperatures = [str(value) for value in matrix.get("temperatures", [])]
    caches = [str(value) for value in matrix.get("cache_states", [])]
    modes = [str(value) for value in matrix.get("snapshot_modes", [])]
    spaces = [int(value) for value in matrix.get("spaces", [])]
    expected = primary_cells(list(records), temperatures, caches, modes, spaces)
    grouped: dict[str, list[dict[str, Any]]] = {cell["cell_key"]: [] for cell in expected}

    def add_live_sample(sample: Any, groups: dict[str, list[dict[str, Any]]]) -> None:
        if not isinstance(sample, dict) or sample.get("evidence_mode") != "live" or sample.get("measurement_basis") != "production_observation":
            raise P5PerformanceError("live samples must be production observations")
        key = sample.get("cell_key")
        if key not in groups:
            raise P5PerformanceError("live sample references an undeclared cell")
        if sample.get("sample_status") not in {"valid", "error", "invalid", "excluded"}:
            raise P5PerformanceError("live sample status must be explicitly accounted")
        metrics = sample.get("metrics")
        if not isinstance(metrics, dict) or any(
            metric not in metrics
            or not isinstance(metrics[metric], (int, float))
            or isinstance(metrics[metric], bool)
            or not math.isfinite(float(metrics[metric]))
            or float(metrics[metric]) < 0
            for metric in METRICS
        ):
            raise P5PerformanceError("live sample is missing a finite required metric")
        groups[key].append(sample)

    for sample in samples:
        add_live_sample(sample, grouped)
    if any(not grouped[cell["cell_key"]] for cell in expected):
        raise P5PerformanceError("live input is missing a required blocking cell")
    if any(_accounting(grouped[cell["cell_key"]])["valid"] < MIN_P99_SAMPLES for cell in expected):
        raise P5PerformanceError(
            f"live blocking cells require at least {MIN_P99_SAMPLES} valid samples"
        )

    warmups = int(payload.get("warmups", 0))
    if warmups < MIN_WARMUPS:
        raise P5PerformanceError(f"live input requires at least {MIN_WARMUPS} warmups")
    bootstrap_resamples = int(payload.get("bootstrap_resamples", DEFAULT_BOOTSTRAP_RESAMPLES))
    cells = [
        _summarize_cell(
            cell,
            grouped[cell["cell_key"]],
            evidence_mode="live",
            warmups=warmups,
            blocking=True,
            bootstrap_resamples=bootstrap_resamples,
        )
        for cell in expected
    ]
    probe_record = records[fixture_names[0]]
    probe_groups: dict[str, list[dict[str, Any]]] = {
        scenario_key(scenario, probe_record["name"]): [] for scenario in SCENARIO_PROBES
    }
    for sample in scenario_samples:
        add_live_sample(sample, probe_groups)
    if any(not group for group in probe_groups.values()):
        raise P5PerformanceError("live input is missing a required scenario probe")
    scenario_probes = [
        _summarize_cell(
            {"cell_key": key, "fixture": probe_record["name"], "scenario": scenario, "spaces": max(spaces)},
            group,
            evidence_mode="live",
            warmups=warmups,
            blocking=False,
            bootstrap_resamples=bootstrap_resamples,
        )
        for scenario in SCENARIO_PROBES
        for key, group in probe_groups.items()
        if key == scenario_key(scenario, probe_record["name"])
    ]
    matrix = {
        "fixtures": fixture_names,
        "temperatures": temperatures,
        "cache_states": caches,
        "snapshot_modes": modes,
        "spaces": spaces,
        "expected_cell_count": len(expected),
        "samples_per_cell": max(len(group) for group in grouped.values()),
        "blocking": True,
    }
    all_samples = [*samples, *scenario_samples]
    report = _base_report(
        evidence_mode="live",
        status="live_measured",
        matrix=matrix,
        records=records,
        fixture_metadata=fixture_metadata,
        cells=cells,
        scenario_probes=scenario_probes,
        raw_samples=all_samples,
        warmups=warmups,
        bootstrap_resamples=bootstrap_resamples,
        blocking=True,
        command=command or ["python3", "scripts/run_p5_performance.py", "--mode", "live"],
    )
    report["release_eligible"] = True
    report["baseline_manifest"] = copy.deepcopy(baseline)
    report["tokenizer"] = copy.deepcopy(baseline.get("tokenizer"))
    report["provenance"] = redact_for_persistence(provenance, max_nodes=MAX_REDACTION_NODES)
    report["redaction_status"] = redact_for_persistence(redaction, max_nodes=MAX_REDACTION_NODES)
    report["sample_accounting"] = _accounting(all_samples)
    report["artifacts"]["raw_sample_count"] = len(all_samples)
    report["raw_sample_declarations"] = []
    return report, all_samples


__all__ = [
    "ARTIFACT_ROOT",
    "CACHE_STATES",
    "DEFAULT_BOOTSTRAP_RESAMPLES",
    "DEFAULT_SAMPLES",
    "FIXTURE_MANIFEST",
    "METRICS",
    "MIN_P95_SAMPLES",
    "MIN_P99_SAMPLES",
    "MIN_WARMUPS",
    "P5PerformanceError",
    "SCENARIO_PROBES",
    "SNAPSHOT_MODES",
    "SPACE_LEVELS",
    "TEMPERATURES",
    "build_live_report",
    "build_offline_report",
    "cell_key",
    "load_fixtures",
    "parse_csv",
    "parse_spaces",
    "publish_artifact",
]
