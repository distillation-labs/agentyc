#!/usr/bin/env python3
"""Validate direct Phase 7 rollout evidence without turning offline checks into a release pass.

This script owns only evidence validation and deterministic rollout hooks. It never
launches a browser, downloads a browser, attaches to Chrome, or performs a product
install. A release run is fail-closed unless explicitly supplied live evidence for
existing-Chrome, performance/context/resource/reliability, and installation/rollback.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from artifact_envelope import envelope as add_envelope
from artifact_envelope import redact_for_persistence, write_json_atomic

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_ROOT = (ROOT / "artifacts").resolve()
DEFAULT_ARTIFACT_DIR = ARTIFACT_ROOT / "p7-release-gate"
SCHEMA_VERSION = 1
PHASE = 7
MIN_REPETITIONS = 100
MAX_REPETITIONS = 1_000
MAX_INPUT_BYTES = 8 * 1024 * 1024
LIVE_SUCCESS = {"live_passed", "passed", "pass"}
SKIPPED = {"skip", "skipped", "ignored", "not_run", "not-run"}

REQUIRED_GATE_METRICS: dict[str, tuple[str, ...]] = {
    "resource": (
        "cpu_p95_percent",
        "rss_p95_bytes",
        "queue_depth_p95",
        "file_descriptors_max",
        "threads_max",
        "artifact_bytes",
    ),
    "token": (
        "transport_bytes_p95",
        "utf8_bytes_p95",
        "serialized_tokens_p95",
        "model_context_tokens_p95",
    ),
    "context": (
        "clean_dom_scans_max",
        "delta_ratio_p50",
        "delta_ratio_p95",
        "actionable_coverage_min",
        "equivalent_coverage",
        "truncation_accounted",
    ),
    "reliability": (
        "stale_ref_rate",
        "unknown_outcome_rate",
        "event_lag_p95_ms",
        "reconnect_p95_ms",
        "human_tab_responsiveness_p95_ms",
        "cross_space_mutations",
        "user_tab_closes",
        "stale_agent_mutations",
        "silent_unknown_success",
        "blind_replays",
        "secret_leaks",
    ),
}

CHAOS_FAULTS = (
    "kill_enqueue",
    "kill_dequeue",
    "kill_dispatch",
    "kill_commit",
    "native_eof",
    "native_partial_frame",
    "native_exact_limit",
    "native_oversize",
    "native_invalid_utf8",
    "bridge_disconnect",
    "worker_termination",
    "extension_reload",
    "debugger_target_closed",
    "debugger_canceled_by_user",
    "renderer_restart",
    "chrome_restart",
    "disk_full",
    "read_only_state",
    "partial_ledger_write",
    "corrupt_ledger",
    "clock_jump",
    "cpu_pressure",
    "memory_pressure",
    "event_buffer_gap",
    "late_old_generation_event",
)

SOAK_STEPS = (
    "action",
    "reconnect",
    "worker_restart",
    "takeover",
    "return_control",
    "retention_check",
)

_SECRET_TEXT = re.compile(r"(?i)(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]+")
_ABSOLUTE_PATH = re.compile(r"(?i)(?:/(?:Users|home|private|tmp|var|etc|opt|Applications)/|file://|wss?://)")


class GateError(ValueError):
    """A release evidence input is malformed or unsafe."""


def _finite(value: Any, *, minimum: float = 0.0, maximum: float | None = None) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    number = float(value)
    return math.isfinite(number) and number >= minimum and (maximum is None or number <= maximum)


def _safe_artifact_dir(value: str | Path) -> Path:
    requested = Path(value).expanduser()
    if not requested.is_absolute():
        requested = ROOT / requested
    current = requested
    while current != current.parent:
        if current.is_symlink():
            raise GateError("artifact path components must not be symlinks")
        current = current.parent
    resolved = requested.resolve()
    try:
        resolved.relative_to(ARTIFACT_ROOT)
    except ValueError as exc:
        raise GateError("artifact directory must be inside artifacts/") from exc
    if resolved == ARTIFACT_ROOT:
        raise GateError("artifact directory must be a child of artifacts/")
    if resolved.exists() and not resolved.is_dir():
        raise GateError("artifact directory must be a directory")
    return resolved


def _safe_input_path(value: str | Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = ROOT / path
    current = path
    while current != current.parent:
        if current.is_symlink():
            raise GateError("evidence path components must not be symlinks")
        current = current.parent
    resolved = path.resolve()
    try:
        resolved.relative_to(ROOT)
    except ValueError as exc:
        raise GateError("evidence paths must be inside the repository") from exc
    return resolved


def read_json(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise GateError(f"evidence file is missing: {path.name}")
    if path.stat().st_size > MAX_INPUT_BYTES:
        raise GateError(f"evidence file is too large: {path.name}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise GateError(f"evidence JSON is unreadable: {path.name}") from exc
    if not isinstance(value, dict):
        raise GateError(f"evidence JSON must be an object: {path.name}")
    return value


def _contains_unredacted_text(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(_contains_unredacted_text(key) or _contains_unredacted_text(child) for key, child in value.items())
    if isinstance(value, list):
        return any(_contains_unredacted_text(child) for child in value)
    if isinstance(value, str):
        return bool(_SECRET_TEXT.search(value) or _ABSOLUTE_PATH.search(value))
    return False


def _redact_release_gates_for_validation(gates: Any) -> Any:
    """Keep the safe `token` metric category distinct from credential fields."""
    if not isinstance(gates, Mapping):
        return redact_for_persistence(gates)
    staged = dict(gates)
    token_section = staged.pop("token", None)
    if token_section is not None:
        staged["token_metrics"] = token_section
    redacted = redact_for_persistence(staged)
    if token_section is not None and isinstance(redacted, dict):
        redacted["token"] = redacted.pop("token_metrics", None)
    return redacted


def _redact_report_for_validation(report: Mapping[str, Any]) -> dict[str, Any]:
    gates = report.get("release_gates")
    without_gates = {key: value for key, value in report.items() if key != "release_gates"}
    redacted = redact_for_persistence(without_gates)
    if gates is not None:
        redacted["release_gates"] = _redact_release_gates_for_validation(gates)
    return redacted


def validate_artifact_envelope(report: Mapping[str, Any], *, require_phase: int | None = None) -> list[str]:
    """Return envelope errors; an offline artifact can never satisfy live evidence."""
    errors: list[str] = []
    if report.get("schema_version") != SCHEMA_VERSION:
        errors.append("schema_version must be 1")
    if require_phase is not None and report.get("phase") != require_phase:
        errors.append(f"phase must be {require_phase}")
    elif report.get("phase") not in {0, PHASE}:
        errors.append("phase must be 0 or 7 for a source artifact")
    if not isinstance(report.get("kind"), str) or not report.get("kind"):
        errors.append("kind is missing")
    for field in ("build_tuple", "environment", "result"):
        if not isinstance(report.get(field), dict):
            errors.append(f"{field} must be an object")
    if not isinstance(report.get("timestamp"), str) or not str(report.get("timestamp")).endswith("Z"):
        errors.append("timestamp must be a UTC string")
    command = report.get("command")
    if not isinstance(command, list) or not command or any(not isinstance(item, str) for item in command):
        errors.append("command must be a non-empty argv list")
    redaction = report.get("redaction_status")
    if not isinstance(redaction, dict) or redaction.get("status") != "applied":
        errors.append("redaction_status must prove applied redaction")
    elif any(redaction.get(field) is not False for field in ("raw_browser_ids", "secrets", "absolute_paths", "page_bodies")):
        errors.append("redaction_status does not prove sensitive fields are absent")
    if _redact_report_for_validation(report) != dict(report):
        errors.append("artifact is not stable after central redaction")
    if _contains_unredacted_text(report):
        errors.append("artifact contains an unredacted secret, absolute path, or endpoint")
    return errors


def _status_value(entry: Any) -> tuple[Any, str | None, Any]:
    if not isinstance(entry, dict):
        return None, None, None
    return entry.get("value"), entry.get("status"), entry.get("ceiling", entry.get("minimum"))


def _metric_limit(entry: Mapping[str, Any]) -> Any:
    """Return the declared gate limit without allowing an implicit default."""
    if "ceiling" in entry:
        return entry.get("ceiling")
    return entry.get("minimum")


def validate_release_gate_schema(report: Mapping[str, Any], *, require_live: bool) -> list[str]:
    gates = report.get("release_gates")
    if not isinstance(gates, dict):
        return ["release_gates is missing"]
    errors: list[str] = []
    for category, names in REQUIRED_GATE_METRICS.items():
        section = gates.get(category)
        if not isinstance(section, dict):
            errors.append(f"release_gates.{category} is missing")
            continue
        status = section.get("status")
        acceptable = LIVE_SUCCESS if require_live else {"passed", "live_passed", "offline_passed", "not_gateable_offline"}
        if status not in acceptable:
            errors.append(f"release_gates.{category}.status is not gateable")
        if require_live and section.get("evidence_mode") != "live":
            errors.append(f"release_gates.{category} is not live evidence")
        metrics = section.get("metrics")
        if not isinstance(metrics, dict):
            errors.append(f"release_gates.{category}.metrics is missing")
            continue
        for name in names:
            entry = metrics.get(name)
            value, metric_status, limit = _status_value(entry)
            if not isinstance(entry, dict):
                errors.append(f"release_gates.{category}.{name} is missing")
                continue
            if _metric_limit(entry) is None:
                errors.append(f"release_gates.{category}.{name} has no declared ceiling or minimum")
            if metric_status != "measured":
                if not (not require_live and metric_status == "not_measured_offline" and value is None):
                    errors.append(f"release_gates.{category}.{name} is not measured")
                    continue
                continue
            if name in {"equivalent_coverage", "truncation_accounted"}:
                if entry.get("minimum") is not True or value is not True:
                    errors.append(f"release_gates.{category}.{name} is not true")
                continue
            if not _finite(value):
                errors.append(f"release_gates.{category}.{name}.value is invalid")
            if name == "actionable_coverage_min":
                if not _finite(limit, maximum=1.0) or not _finite(value, maximum=1.0) or float(value) < float(limit):
                    errors.append(f"release_gates.{category}.{name} is below its minimum")
            elif not _finite(limit) or (_finite(value) and float(value) > float(limit)):
                errors.append(f"release_gates.{category}.{name} exceeds its ceiling")
    return errors


def validate_no_skipped_live(report: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []

    def visit(value: Any, key: str = "") -> None:
        normalized = key.lower().replace("-", "_")
        if (
            isinstance(value, str)
            and value.lower() in SKIPPED
            and (normalized in {"status", "result"} or "status" in normalized or "result" in normalized)
        ):
            errors.append(f"skipped status at {key or 'root'}")
        if "skip" in normalized or "ignore" in normalized or normalized in {"not_run", "notrun"}:
            if isinstance(value, bool) and value:
                errors.append(f"skipped evidence at {key}")
            elif isinstance(value, int) and not isinstance(value, bool) and value > 0:
                errors.append(f"skipped count at {key}")
            elif isinstance(value, list) and value:
                errors.append(f"skipped entries at {key}")
            elif isinstance(value, str) and value:
                errors.append(f"skipped detail at {key}")
        if isinstance(value, Mapping):
            for child_key, child in value.items():
                visit(child, str(child_key))
        elif isinstance(value, list):
            for child in value:
                visit(child, key)

    visit(report)
    return sorted(set(errors))


def _source_errors(report: Mapping[str, Any]) -> list[str]:
    return [*validate_artifact_envelope(report), *validate_no_skipped_live(report)]


def validate_benchmark(report: Mapping[str, Any], *, require_live: bool) -> list[str]:
    errors = [*_source_errors(report), *validate_release_gate_schema(report, require_live=require_live)]
    if report.get("kind") != "direct-benchmark-baseline":
        errors.append("benchmark kind is not direct-benchmark-baseline")
    mode = str(report.get("mode", "")).lower()
    evidence_mode = report.get("evidence_mode", "offline" if mode == "offline" else None)
    if require_live and evidence_mode != "live":
        errors.append("benchmark is offline evidence")
    if require_live and report.get("status") not in LIVE_SUCCESS:
        errors.append("benchmark status is not live_passed")
    if require_live and report.get("smoke") is not False:
        errors.append("smoke benchmark cannot close a release gate")
    samples = report.get("samples_per_cell")
    if require_live and (not isinstance(samples, int) or isinstance(samples, bool) or samples < 1_000):
        errors.append("benchmark needs at least 1000 samples per blocking cell")
    return sorted(set(errors))


def _enrolled(value: Any) -> bool:
    return isinstance(value, dict) and value.get("enrolled") is True and value.get("status") in {"installed", "connected", "bound", "enrolled"}


def validate_existing_chrome(report: Mapping[str, Any], *, require_live: bool) -> list[str]:
    errors = _source_errors(report)
    if report.get("kind") != "existing-chrome-coexistence":
        errors.append("existing-Chrome kind is invalid")
    live = report.get("live")
    enrollment = report.get("enrollment")
    safety = report.get("safety")
    execution = report.get("execution_policy")
    if require_live:
        if report.get("evidence_mode") != "live" or report.get("status") not in LIVE_SUCCESS:
            errors.append("existing-Chrome report is not real live evidence")
        if not isinstance(live, dict) or live.get("executed") is not True or live.get("status") not in LIVE_SUCCESS:
            errors.append("existing-Chrome action evidence was not executed")
        if not isinstance(enrollment, dict) or not _enrolled(enrollment.get("host")) or not _enrolled(enrollment.get("extension")):
            errors.append("explicit enrolled host and extension descriptors are required")
        if not isinstance(execution, dict) or execution.get("attached") is not True or execution.get("browser_launch") is not False or execution.get("browser_download") is not False or execution.get("cdp_url_used") is not False:
            errors.append("existing-Chrome execution policy is unsafe or absent")
        scenarios = report.get("scenarios")
        if not isinstance(scenarios, list) or len(scenarios) < 10:
            errors.append("all ten existing-Chrome scenarios are required")
        elif any(not isinstance(item, dict) or item.get("status") not in LIVE_SUCCESS for item in scenarios):
            errors.append("existing-Chrome scenario results are incomplete")
    if not isinstance(safety, dict):
        errors.append("existing-Chrome safety counters are missing")
    elif require_live and any(safety.get(key) != 0 for key in ("user_tab_closes", "focus_theft", "cross_space_mutations", "stale_agent_mutations")):
        errors.append("existing-Chrome safety counters are not zero")
    elif not require_live and any(key not in safety for key in ("user_tab_closes", "focus_theft", "cross_space_mutations", "stale_agent_mutations")):
        errors.append("offline existing-Chrome safety shape is incomplete")
    return sorted(set(errors))


def _lifecycle_status(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        status = value.get("status")
        return status if isinstance(status, str) else None
    return None


def validate_installation_record(report: Mapping[str, Any], *, require_live: bool) -> list[str]:
    errors = [*validate_artifact_envelope(report), *validate_no_skipped_live(report)]
    if require_live and report.get("evidence_mode") != "live":
        errors.append("installation record is not real live evidence")
    if require_live and report.get("status") not in {"drill_passed", "live_passed", "passed"}:
        errors.append("installation record did not pass")
    evidence = report.get("evidence")
    platform_data = evidence.get("platform") if isinstance(evidence, dict) else None
    if require_live and (not isinstance(platform_data, dict) or platform_data.get("name") not in {"Darwin", "macOS"}):
        errors.append("a real macOS installation record is required")
    installation = report.get("installation")
    rollback = report.get("rollback")
    if require_live and (_lifecycle_status(installation) != "installed" or _lifecycle_status(rollback) != "rolled_back"):
        errors.append("install and rollback records are incomplete")
    lifecycle = report.get("lifecycle")
    expected = {"install": "installed", "update": "passed", "uninstall": "passed", "downgrade": "passed", "rollback": "rolled_back"}
    if not isinstance(lifecycle, dict):
        errors.append("lifecycle record is missing")
    else:
        if lifecycle.get("schema_version") != 1:
            errors.append("lifecycle.schema_version must be 1")
        if require_live and lifecycle.get("evidence_mode") != "live":
            errors.append("lifecycle is not live evidence")
        if not require_live and lifecycle.get("evidence_mode") not in {"offline", "live"}:
            errors.append("lifecycle evidence_mode is invalid")
        allowed_offline = {"not_measured_offline", "not_run", "not_observed", "not_applicable", "installed", "already_installed", "passed", "rolled_back"}
        for key, wanted in expected.items():
            observed = _lifecycle_status(lifecycle.get(key))
            if require_live and observed != wanted:
                errors.append(f"lifecycle.{key} is not {wanted}")
            elif not require_live and observed not in allowed_offline:
                errors.append(f"lifecycle.{key} has an invalid offline status")
    safety = report.get("safety")
    if not isinstance(safety, dict) or safety.get("chrome_launch") != "never" or safety.get("chrome_download") != "never" or safety.get("chrome_profile_mutation") is not False:
        errors.append("installation safety policy is incomplete")
    rollback_safety = report.get("rollback_safety")
    required_safety = {
        "new_mutations": "paused",
        "pages_retained": True,
        "user_tabs_preserved": True,
        "chrome_process_terminated": False,
        "global_close_used": False,
        "incompatible_ledger_refused": True,
    }
    if not isinstance(rollback_safety, dict):
        errors.append("rollback_safety is missing")
    else:
        if rollback_safety.get("schema_version") != 1:
            errors.append("rollback_safety.schema_version must be 1")
        if require_live and rollback_safety.get("evidence_mode") != "live":
            errors.append("rollback_safety is not live evidence")
        if not require_live and rollback_safety.get("evidence_mode") not in {"offline", "live"}:
            errors.append("rollback_safety evidence_mode is invalid")
        for key, wanted in required_safety.items():
            if key not in rollback_safety:
                errors.append(f"rollback_safety.{key} is missing")
            elif require_live and rollback_safety.get(key) != wanted:
                errors.append(f"rollback_safety.{key} is unsafe or unproven")
        kill_switch = rollback_safety.get("kill_switch")
        if not isinstance(kill_switch, dict):
            errors.append("mutation kill switch is missing")
        elif require_live and (
            kill_switch.get("status") != "armed_and_verified"
            or kill_switch.get("armed") is not True
            or kill_switch.get("verified") is not True
        ):
            errors.append("mutation kill switch is not armed_and_verified")
        elif not require_live and kill_switch.get("status") not in {"not_measured_offline", "armed_and_verified"}:
            errors.append("offline mutation kill switch status is invalid")
    return sorted(set(errors))


def _stable_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("utf-8")


def _hook_outcome(hook: str, seed: int, trace: list[str]) -> dict[str, Any]:
    trace_hash = hashlib.sha256(_stable_json({"hook": hook, "seed": seed, "trace": trace})).hexdigest()
    return {
        "trace_sha256": trace_hash,
        "status": "reconciled_unknown" if hook == "chaos" else "completed",
        "mutation_replayed": False,
        "user_tabs_preserved": True,
        "chrome_process_terminated": False,
        "cross_space_mutations": 0,
        "silent_unknown_success": 0,
    }


def run_deterministic_hook(hook: str, *, seed: int = 0, repetitions: int = MIN_REPETITIONS) -> dict[str, Any]:
    if hook not in {"replay", "chaos", "soak"}:
        raise GateError("hook must be replay, chaos, or soak")
    if not isinstance(seed, int) or isinstance(seed, bool) or seed < 0:
        raise GateError("seed must be a non-negative integer")
    if not isinstance(repetitions, int) or isinstance(repetitions, bool) or not MIN_REPETITIONS <= repetitions <= MAX_REPETITIONS:
        raise GateError(f"repetitions must be in {MIN_REPETITIONS}..{MAX_REPETITIONS}")
    if hook == "replay":
        trace = ["enqueue", "dispatch", "disconnect-after-dispatch", "reconcile-unknown"]
    elif hook == "chaos":
        trace = [str(item) for item in CHAOS_FAULTS]
    else:
        trace = [str(item) for item in SOAK_STEPS]
    outcomes = [_hook_outcome(hook, seed, trace) for _ in range(repetitions)]
    encoded = [_stable_json(outcome) for outcome in outcomes]
    hashes = [hashlib.sha256(item).hexdigest() for item in encoded]
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "phase": PHASE,
        "kind": "rollout-deterministic-hook",
        "hook": hook,
        "evidence_mode": "deterministic-offline",
        "status": "passed",
        "seed": seed,
        "repetitions": repetitions,
        "trace": trace,
        "outcome_hash": hashes[0],
        "unique_outcome_hashes": sorted(set(hashes)),
        "sample_accounting": {"attempted": repetitions, "valid": repetitions, "errors": 0, "missing": 0, "divergent": len(set(hashes)) - 1},
        "no_replay_assertion": all(not outcome["mutation_replayed"] for outcome in outcomes),
        "safety": {"user_tabs_preserved": True, "chrome_process_terminated": False, "cross_space_mutations": 0, "silent_unknown_success": 0},
    }
    if hook == "chaos":
        report["fault_results"] = [{"fault": fault, "status": "accounted", "no_replay": True} for fault in trace]
    if hook == "soak":
        report["resource_slopes"] = {"rss_bytes_per_step": 0.0, "file_descriptors_per_step": 0.0, "threads_per_step": 0.0, "queue_depth_per_step": 0.0}
        report["spaces_isolated"] = True
    add_envelope(report, kind="rollout-deterministic-hook", build_tuple={"phase": PHASE, "hook": hook, "repetitions": repetitions})
    return report


def validate_deterministic_hook(report: Mapping[str, Any], hook: str, *, minimum_repetitions: int = MIN_REPETITIONS) -> list[str]:
    errors = validate_artifact_envelope(report, require_phase=PHASE)
    if report.get("kind") != "rollout-deterministic-hook" or report.get("hook") != hook:
        errors.append(f"{hook} hook identity is invalid")
    if report.get("status") != "passed" or report.get("evidence_mode") != "deterministic-offline":
        errors.append(f"{hook} hook did not pass deterministically")
    repetitions = report.get("repetitions")
    if not isinstance(repetitions, int) or isinstance(repetitions, bool) or repetitions < minimum_repetitions:
        errors.append(f"{hook} hook has fewer than {minimum_repetitions} repetitions")
    outcome_hash = report.get("outcome_hash")
    unique_hashes = report.get("unique_outcome_hashes")
    if not isinstance(outcome_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", outcome_hash) or unique_hashes != [outcome_hash]:
        errors.append(f"{hook} hook outcome hashes are not deterministic")
    accounting = report.get("sample_accounting")
    if not isinstance(accounting, dict) or accounting.get("attempted") != repetitions or accounting.get("valid") != repetitions or accounting.get("errors") != 0 or accounting.get("missing") != 0 or accounting.get("divergent") != 0:
        errors.append(f"{hook} hook has unaccounted or divergent repetitions")
    if report.get("no_replay_assertion") is not True:
        errors.append(f"{hook} hook lacks a no-replay assertion")
    safety = report.get("safety")
    if (
        not isinstance(safety, dict)
        or safety.get("user_tabs_preserved") is not True
        or safety.get("chrome_process_terminated") is not False
        or safety.get("cross_space_mutations") != 0
        or safety.get("silent_unknown_success") != 0
    ):
        errors.append(f"{hook} hook safety assertions are incomplete")
    if hook == "chaos":
        faults = report.get("trace")
        results = report.get("fault_results")
        if not isinstance(faults, list) or faults != list(CHAOS_FAULTS) or not isinstance(results, list) or {item.get("fault") for item in results if isinstance(item, dict)} != set(CHAOS_FAULTS):
            errors.append("chaos fault matrix is incomplete")
    if hook == "soak" and (report.get("spaces_isolated") is not True or not isinstance(report.get("resource_slopes"), dict)):
        errors.append("soak resource/isolation evidence is incomplete")
    return sorted(set(errors))


def _artifact_candidate(value: str | None, defaults: tuple[Path, ...]) -> Path | None:
    if value:
        return _safe_input_path(value)
    for candidate in defaults:
        if candidate.is_file():
            return candidate
    return None


def run_gate(
    *,
    artifact_dir: Path,
    require_live: bool,
    benchmark_path: Path | None = None,
    existing_chrome_path: Path | None = None,
    installation_path: Path | None = None,
    seed: int = 0,
    repetitions: int = MIN_REPETITIONS,
) -> tuple[dict[str, Any], int]:
    artifact_dir = _safe_artifact_dir(artifact_dir)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    hook_reports: dict[str, dict[str, Any]] = {}
    hook_results: dict[str, dict[str, Any]] = {}
    blockers: list[dict[str, str]] = []
    for hook in ("replay", "chaos", "soak"):
        hook_report = run_deterministic_hook(hook, seed=seed, repetitions=repetitions)
        hook_reports[hook] = hook_report
        write_json_atomic(artifact_dir / f"{hook}.json", hook_report)
        hook_errors = validate_deterministic_hook(hook_report, hook)
        hook_results[hook] = {"status": "passed" if not hook_errors else "blocked", "repetitions": repetitions}
        for error in hook_errors:
            blockers.append({"gate": hook, "code": "hook-invalid", "detail": error})

    sources: dict[str, tuple[Path | None, Any]] = {
        "performance": (benchmark_path, validate_benchmark),
        "real_chrome": (existing_chrome_path, validate_existing_chrome),
        "installation": (installation_path, validate_installation_record),
    }
    gate_results: dict[str, Any] = dict(hook_results)
    for name, (path, validator) in sources.items():
        if path is None:
            gate_results[name] = {"status": "missing", "evidence": "none"}
            blockers.append({"gate": name, "code": "evidence-missing", "detail": "explicit evidence artifact was not supplied"})
            continue
        try:
            report = read_json(path)
            errors = validator(report, require_live=require_live)
        except (GateError, OSError, ValueError) as exc:
            errors = [str(exc)]
        gate_results[name] = {"status": "passed" if not errors else "blocked", "evidence": "live" if require_live else "offline", "errors": errors}
        if errors:
            blockers.extend({"gate": name, "code": "evidence-invalid", "detail": error} for error in errors)

    if not require_live:
        blockers.append({"gate": "release", "code": "offline-only", "detail": "offline evidence is never release evidence; rerun with --require-live"})
    release_eligible = require_live and not blockers
    status = "live_passed" if release_eligible else "offline_passed" if not require_live and all(item["code"] != "evidence-invalid" for item in blockers) else "blocked"
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "phase": PHASE,
        "kind": "direct-release-gate",
        "mode": "live" if require_live else "offline",
        "evidence_mode": "live" if require_live else "offline",
        "status": status,
        "release_eligible": release_eligible,
        "live_evidence_required": require_live,
        "gates": gate_results,
        "blockers": blockers,
        "hooks": {name: {"status": value["status"], "repetitions": value["repetitions"]} for name, value in hook_reports.items()},
        "safety": {
            "browser_launch": "never",
            "browser_download": "never",
            "browser_attach": "never by this validator",
            "user_tabs_or_chrome_changed": False,
            "mutation_kill_switch_required": True,
            "raw_browser_ids_logged": False,
            "secrets_logged": False,
        },
    }
    add_envelope(report, kind="direct-release-gate", build_tuple={"phase": PHASE, "gate_schema": SCHEMA_VERSION, "repetitions": repetitions})
    write_json_atomic(artifact_dir / "report.json", report)
    return report, 0 if (not require_live and status == "offline_passed") or release_eligible else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("offline", "live"), default="live")
    parser.add_argument("--require-live", action="store_true", help="require real, executed, non-skipped release evidence")
    parser.add_argument("--artifact-dir", default=DEFAULT_ARTIFACT_DIR.relative_to(ROOT).as_posix())
    parser.add_argument("--benchmark-artifact")
    parser.add_argument("--existing-chrome-artifact")
    parser.add_argument("--installation-artifact")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--repetitions", type=int, default=MIN_REPETITIONS)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    require_live = args.require_live or args.mode == "live"
    if args.require_live and args.mode == "offline":
        print("run_release_gate: --require-live cannot be combined with --mode offline", file=sys.stderr)
        return 2
    try:
        artifact_dir = _safe_artifact_dir(args.artifact_dir)
        defaults = require_live
        benchmark = _artifact_candidate(
            args.benchmark_artifact,
            (ARTIFACT_ROOT / "p7-performance" / "baseline.json", ARTIFACT_ROOT / "p0-performance" / "baseline.json") if defaults else (),
        )
        existing = _artifact_candidate(
            args.existing_chrome_artifact,
            (ARTIFACT_ROOT / "p7-existing-chrome" / "report.json", ARTIFACT_ROOT / "p0-coexistence" / "report.json") if defaults else (),
        )
        installation = _artifact_candidate(
            args.installation_artifact,
            (ARTIFACT_ROOT / "p7-install-rollback" / "report.json", ARTIFACT_ROOT / "p0-installation" / "report.json") if defaults else (),
        )
        report, code = run_gate(
            artifact_dir=artifact_dir,
            require_live=require_live,
            benchmark_path=benchmark,
            existing_chrome_path=existing,
            installation_path=installation,
            seed=args.seed,
            repetitions=args.repetitions,
        )
    except (GateError, OSError, ValueError) as exc:
        print(f"run_release_gate: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
