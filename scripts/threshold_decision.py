#!/usr/bin/env python3
"""Validate the strict P1-T7 threshold decision record.

The record freezes provisional Phase 7 limits without turning offline contract
validation into production evidence.  This module is stdlib-only so the release
checker can use it without starting a browser, host, network client, or build.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from collections.abc import Mapping
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MAX_RECORD_BYTES = 4 * 1024 * 1024
SCHEMA_VERSION = 1
RECORD_VERSION = "p1-t7-threshold-decision-v1"
KIND = "p1-t7-threshold-decision"
PHASE = 1
EFFECTIVE_PHASE = 7
OFFLINE_MODE = "offline"
LIVE_MODE = "live"
OFFLINE_STATUS = "not_measured_offline"
LIVE_STATUS = "measured"
OWNER = "Japneet Kalkat"
DECISION_ID_RE = re.compile(r"^p1-t7-\d{4}-\d{2}-\d{2}-v\d+$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")

SAFETY_COUNTER_IDS = (
    "authorization_bypasses",
    "cross_space_mutations",
    "focus_theft",
    "stale_agent_mutations",
    "user_tab_closes",
    "secret_leaks",
    "blind_replays",
    "silent_unknown_success",
)

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

REQUIRED_EXCLUSIONS = (
    "disposable_cdp",
    "host_only",
    "acknowledgement_only",
    "byte_estimate",
    "source_inspection",
    "operator_claim_only",
)

# These strings are deliberately checked only inside live evidence.  The
# exclusion list must name the evidence that is not allowed to become a claim.
FORBIDDEN_LIVE_EVIDENCE = (
    "disposable",
    "cdp",
    "host_only",
    "host-only",
    "host only",
    "acknowledgement",
    "acknowledgment",
    "operator_claim",
    "operator claim",
    "byte_estimate",
    "byte-estimate",
    "bytes/4",
    "bytes / 4",
    "deterministic_byte_estimate",
    "source_inspection",
    "source inspection",
)

ARTIFACT_ENVELOPE_KEYS = {
    "build_tuple",
    "environment",
    "timestamp",
    "nonce",
    "command",
    "result",
    "provenance",
    "redaction_status",
}
TOP_LEVEL_KEYS = {
    "schema_version",
    *ARTIFACT_ENVELOPE_KEYS,
    "record_version",
    "phase",
    "kind",
    "decision_id",
    "decision_date",
    "decision_status",
    "effective_phase",
    "owner",
    "signoff",
    "production_path",
    "exclusions",
    "sample_policy",
    "metric_register",
    "evidence_mode",
    "evidence",
    "safety_counters",
    "chaos",
    "threshold_change",
    "release_eligible",
}
SIGNOFF_KEYS = {"owner", "signed_at", "status", "scope"}
PRODUCTION_PATH_KEYS = {"path_id", "description", "components", "required"}
EXCLUSION_KEYS = {"id", "description", "not_release_evidence"}
SAMPLE_POLICY_KEYS = {
    "warmups",
    "p95_min_valid_samples",
    "p99_min_valid_samples",
    "smoke_sample_limit",
    "confidence_interval",
    "raw_samples_required",
    "predeclared_exclusions_required",
    "non_valid_samples_count_against_budget",
    "accounting_categories",
}
CI_POLICY_KEYS = {"method", "confidence_level"}
METRIC_KEYS = {
    "id",
    "area",
    "description",
    "unit",
    "aggregation",
    "blocking",
    "limit",
    "provisional_limits",
    "regression_budget",
    "method",
    "required_dimensions",
    "sample_requirement",
    "owner",
    "evidence_mode",
    "value",
    "status",
}
LIMIT_KEYS = {"kind", "value", "unit", "provisional", "comparison"}
PROVISIONAL_LIMIT_KEYS = {"name", "operator", "value", "unit", "provisional"}
REGRESSION_BUDGET_KEYS = {"kind", "value", "unit", "provisional", "comparison"}
EVIDENCE_KEYS = {
    "mode",
    "status",
    "provenance",
    "raw_samples",
    "confidence_intervals",
    "valid_sample_counts",
    "regression_deltas",
    "redaction_status",
}
PROVENANCE_KEYS = {"source_class", "path_id", "run_id", "timestamp", "command", "build_tuple"}
BUILD_TUPLE_KEYS = {
    "commit",
    "build_mode",
    "os_cpu",
    "chrome_build",
    "extension_host_tuple",
    "fixture_data_hash",
    "tokenizer",
    "concurrency",
    "cache_state",
    "statistical_method",
}
RAW_SAMPLES_KEYS = {"files", "sha256", "sample_count"}
CONFIDENCE_INTERVALS_KEYS = {"method", "confidence_level", "metrics"}
CONFIDENCE_INTERVAL_KEYS = {"lower", "upper", "confidence_level", "sample_count"}
REDACTION_KEYS = {
    "status",
    "policy",
    "raw_browser_ids",
    "secrets",
    "absolute_paths",
    "page_bodies",
}
SAFETY_COUNTER_KEYS = {"value", "status", "evidence_mode"}
CHAOS_KEYS = {
    "declared_faults",
    "accounted_faults",
    "unaccounted_faults",
    "no_replay_assertion",
    "evidence_mode",
}
CHAOS_ENTRY_KEYS = {"status", "value", "evidence_mode", "no_replay_assertion"}
THRESHOLD_CHANGE_KEYS = {"changed", "previous_decision_id", "new_decision_id", "reason"}


class DecisionError(ValueError):
    """The threshold decision record is malformed or unsafe."""


def _expect_exact_keys(value: Any, expected: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DecisionError(f"{label} must be an object")
    actual = set(value)
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    if missing or extra:
        details: list[str] = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if extra:
            details.append("unexpected " + ", ".join(extra))
        raise DecisionError(f"{label} has invalid fields: " + "; ".join(details))
    return value


def _non_empty_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DecisionError(f"{label} must be a non-empty string")
    return value


def _finite_number(value: Any, label: str, *, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DecisionError(f"{label} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise DecisionError(f"{label} must be a finite number")
    if minimum is not None and number < minimum:
        raise DecisionError(f"{label} must be >= {minimum}")
    return number


def _non_negative_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise DecisionError(f"{label} must be a non-negative integer")
    return value


def _valid_timestamp(value: Any, label: str) -> None:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise DecisionError(f"{label} must be a UTC timestamp ending in Z")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise DecisionError(f"{label} is not an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise DecisionError(f"{label} must be UTC")


def _valid_date(value: Any, label: str) -> None:
    if not isinstance(value, str):
        raise DecisionError(f"{label} must be an ISO date")
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise DecisionError(f"{label} must be an ISO date") from exc


def _valid_sha256(value: Any, label: str) -> None:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise DecisionError(f"{label} must be a lowercase SHA-256")


def _safe_id(value: Any, label: str) -> None:
    if not isinstance(value, str) or not SAFE_ID_RE.fullmatch(value):
        raise DecisionError(f"{label} must be a bounded identifier")


def _reject_absolute_or_parent_path(value: str, label: str) -> None:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or "\x00" in value:
        raise DecisionError(f"{label} must be repository-relative and bounded")
    if value.startswith(("file:", "http:", "https:")):
        raise DecisionError(f"{label} must not contain a URL")


def _deep_forbidden_text(value: Any, path: str = "") -> str | None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            found = _deep_forbidden_text(child, f"{path}.{key}" if path else str(key))
            if found:
                return found
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found = _deep_forbidden_text(child, f"{path}[{index}]")
            if found:
                return found
    elif isinstance(value, str):
        lowered = value.lower().replace("_", "_")
        for marker in FORBIDDEN_LIVE_EVIDENCE:
            if marker in lowered:
                return f"{path} contains forbidden live evidence marker {marker!r}"
    return None


def _safe_record_path(root: Path, value: str | Path, *, require_file: bool = True) -> Path:
    requested = Path(value).expanduser()
    if not requested.is_absolute():
        requested = root / requested
    try:
        resolved = requested.resolve()
        resolved.relative_to(root.resolve())
    except (OSError, ValueError) as exc:
        raise DecisionError("decision record path must be inside the repository") from exc
    current = root.resolve()
    relative = resolved.relative_to(current)
    for component in relative.parts:
        current = current / component
        if current.is_symlink():
            raise DecisionError("decision record path must not contain symlinks")
    if require_file and (resolved.is_symlink() or not resolved.is_file()):
        raise DecisionError("decision record file is missing")
    return resolved


def _read_json(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise DecisionError(f"decision record is missing: {path}")
    if path.stat().st_size > MAX_RECORD_BYTES:
        raise DecisionError("decision record exceeds the bounded read limit")

    def reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise DecisionError(f"decision record contains duplicate field: {key}")
            result[key] = value
        return result

    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=reject_duplicate_pairs,
            parse_constant=lambda constant: (_ for _ in ()).throw(
                DecisionError(f"decision record contains non-finite JSON value: {constant}")
            ),
        )
    except DecisionError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DecisionError("decision record is unreadable JSON") from exc
    if not isinstance(value, dict):
        raise DecisionError("decision record must be a JSON object")
    return value


def load_decision_record(root: Path, value: str | Path) -> dict[str, Any]:
    """Load one bounded, repository-local, duplicate-free JSON decision record."""
    return _read_json(_safe_record_path(root, value))


def _metric_specs() -> list[dict[str, Any]]:
    """Return the canonical P1-T7 metric register definition."""
    return [
        {
            "id": "end_to_end_first_action",
            "area": "first action",
            "description": "Submission through host scheduling, extension dispatch/observation, and response receipt.",
            "unit": "ms",
            "aggregation": "p95 total latency",
            "blocking": True,
            "limit": {
                "kind": "absolute_ceiling",
                "value": 1000.0,
                "unit": "ms",
                "provisional": True,
                "comparison": "p95 total_ms <= 1000 ms",
            },
            "provisional_limits": [
                {"name": "p95_total_ms", "operator": "<=", "value": 1000.0, "unit": "ms", "provisional": True},
                {"name": "p99_total_ms", "operator": "<=", "value": 1500.0, "unit": "ms", "provisional": True},
                {"name": "deadline_miss_rate", "operator": "<=", "value": 0.01, "unit": "fraction", "provisional": True},
            ],
            "regression_budget": {
                "kind": "relative_increase",
                "value": 0.10,
                "unit": "fraction",
                "provisional": True,
                "comparison": "p95 may not regress more than 10% from the signed baseline",
            },
            "method": "Timestamp a direct client request and receipt across client, IPC, host queue, bridge, browser, serialization, and response spans.",
            "required_dimensions": ["action_type", "deadline", "outcome", "queue_span", "bridge_span", "browser_span", "serialization_span"],
            "sample_requirement": "p95",
        },
        {
            "id": "batch_round_trips",
            "area": "batching",
            "description": "Independent requests compared sequentially with SDK batch execution on equivalent work.",
            "unit": "fraction",
            "aggregation": "p95 round-trip reduction",
            "blocking": True,
            "limit": {
                "kind": "absolute_minimum",
                "value": 0.50,
                "unit": "fraction",
                "provisional": True,
                "comparison": "batch round-trip reduction >= 50%",
            },
            "provisional_limits": [
                {"name": "round_trip_reduction_p50", "operator": ">=", "value": 0.50, "unit": "fraction", "provisional": True},
                {"name": "round_trip_reduction_p95", "operator": ">=", "value": 0.50, "unit": "fraction", "provisional": True},
                {"name": "equivalent_work", "operator": "==", "value": 1.0, "unit": "boolean", "provisional": True},
            ],
            "regression_budget": {
                "kind": "relative_decrease",
                "value": 0.10,
                "unit": "fraction",
                "provisional": True,
                "comparison": "round-trip reduction may lose at most 10% of the signed baseline",
            },
            "method": "Run the same request set through sequential SDK calls and SDK batch calls on the host-backed production path; count protocol round trips and elapsed time.",
            "required_dimensions": ["request_set", "batch_size", "concurrency", "per_item_outcome", "equivalent_work"],
            "sample_requirement": "p95",
        },
        {
            "id": "snapshot_scans_payload_context",
            "area": "snapshots and context",
            "description": "Clean scans, delta/full cost, actionable coverage, serialized payload, and deployed model-context tokens.",
            "unit": "composite",
            "aggregation": "p50/p95 ratios and counts",
            "blocking": True,
            "limit": {
                "kind": "absolute_ceiling",
                "value": 0.60,
                "unit": "ratio",
                "provisional": True,
                "comparison": "delta p95 ratio <= 0.60 with zero clean scans and equivalent coverage",
            },
            "provisional_limits": [
                {"name": "clean_dom_ax_scans", "operator": "==", "value": 0.0, "unit": "count", "provisional": True},
                {"name": "delta_ratio_p50", "operator": "<=", "value": 0.35, "unit": "ratio", "provisional": True},
                {"name": "delta_ratio_p95", "operator": "<=", "value": 0.60, "unit": "ratio", "provisional": True},
                {"name": "equivalent_actionable_coverage", "operator": "==", "value": 1.0, "unit": "boolean", "provisional": True},
                {"name": "deployed_tokenizer_present", "operator": "==", "value": 1.0, "unit": "boolean", "provisional": True},
            ],
            "regression_budget": {
                "kind": "relative_increase",
                "value": 0.10,
                "unit": "fraction",
                "provisional": True,
                "comparison": "equivalent snapshot costs may regress by at most 10% from baseline",
            },
            "method": "Record DOM/AX scans, actionable controls, UTF-8 and transport bytes, serialized payload tokens, deployed model-context tokens, truncation, cache state, and full/min/delta fallback.",
            "required_dimensions": ["snapshot_mode", "cache_state", "frame_topology", "actionable_controls", "coverage", "tokenizer", "truncation", "fixture_hash"],
            "sample_requirement": "p99",
        },
        {
            "id": "event_lag_and_event_driven_waits",
            "area": "events and waits",
            "description": "Broker event production through client observation, including reconnect/resync and polling comparison.",
            "unit": "ms",
            "aggregation": "p95 event lag",
            "blocking": True,
            "limit": {
                "kind": "absolute_ceiling",
                "value": 500.0,
                "unit": "ms",
                "provisional": True,
                "comparison": "p95 event lag <= 500 ms",
            },
            "provisional_limits": [
                {"name": "event_lag_p95_ms", "operator": "<=", "value": 500.0, "unit": "ms", "provisional": True},
                {"name": "reconnect_resync_lag_p95_ms", "operator": "<=", "value": 2000.0, "unit": "ms", "provisional": True},
                {"name": "missed_or_stale_watermarks", "operator": "==", "value": 0.0, "unit": "count", "provisional": True},
            ],
            "regression_budget": {
                "kind": "relative_increase",
                "value": 0.10,
                "unit": "fraction",
                "provisional": True,
                "comparison": "p95 event lag may regress by at most 10% from baseline",
            },
            "method": "Measure broker sequence/timestamp production to client observation and compare event-driven waits with polling under the same workload.",
            "required_dimensions": ["event_sequence", "queue_depth", "reconnect", "resync", "polling_control", "wait_type"],
            "sample_requirement": "p95",
        },
        {
            "id": "action_deadlines_and_outcomes",
            "area": "deadlines and outcomes",
            "description": "Declared deadlines, elapsed time, terminal outcome, deadline misses, and unknown outcomes.",
            "unit": "fraction",
            "aggregation": "miss and unknown rates",
            "blocking": True,
            "limit": {
                "kind": "absolute_ceiling",
                "value": 0.01,
                "unit": "fraction",
                "provisional": True,
                "comparison": "deadline miss rate and unknown outcome rate <= 1%",
            },
            "provisional_limits": [
                {"name": "deadline_miss_rate", "operator": "<=", "value": 0.01, "unit": "fraction", "provisional": True},
                {"name": "unknown_outcome_rate", "operator": "<=", "value": 0.01, "unit": "fraction", "provisional": True},
                {"name": "silent_unknown_success", "operator": "==", "value": 0.0, "unit": "count", "provisional": True},
            ],
            "regression_budget": {
                "kind": "relative_increase",
                "value": 0.10,
                "unit": "fraction",
                "provisional": True,
                "comparison": "miss and unknown rates may increase by at most 10% of baseline",
            },
            "method": "Record deadline, elapsed time, terminal succeeded/failed/cancelled/unknown status, and classify every lost dispatched mutation as unknown without replay.",
            "required_dimensions": ["action_type", "declared_deadline", "elapsed_ms", "terminal_status", "dispatch_status", "unknown_reason"],
            "sample_requirement": "p95",
        },
        {
            "id": "cpu_rss_and_capacity",
            "area": "resource capacity",
            "description": "Separate host and Chrome CPU/RSS with queue and bounded resource-growth dimensions.",
            "unit": "composite",
            "aggregation": "p95 and maximum",
            "blocking": True,
            "limit": {
                "kind": "absolute_ceiling",
                "value": 536870912.0,
                "unit": "bytes",
                "provisional": True,
                "comparison": "host RSS p95 <= 512 MiB while all component ceilings hold",
            },
            "provisional_limits": [
                {"name": "host_cpu_p95_percent", "operator": "<=", "value": 80.0, "unit": "percent", "provisional": True},
                {"name": "host_rss_p95_bytes", "operator": "<=", "value": 536870912.0, "unit": "bytes", "provisional": True},
                {"name": "chrome_cpu_p95_percent", "operator": "<=", "value": 80.0, "unit": "percent", "provisional": True},
                {"name": "chrome_rss_p95_bytes", "operator": "<=", "value": 1073741824.0, "unit": "bytes", "provisional": True},
                {"name": "queue_depth_p95", "operator": "<=", "value": 100.0, "unit": "count", "provisional": True},
                {"name": "file_descriptors_max", "operator": "<=", "value": 512.0, "unit": "count", "provisional": True},
                {"name": "threads_max", "operator": "<=", "value": 128.0, "unit": "count", "provisional": True},
            ],
            "regression_budget": {
                "kind": "relative_increase",
                "value": 0.10,
                "unit": "fraction",
                "provisional": True,
                "comparison": "each resource ceiling may regress by at most 10% from baseline",
            },
            "method": "Sample host and Chrome processes separately with identity, interval, warmup, workload, throughput, queue, tabs, descriptors, threads, and artifact-growth metadata.",
            "required_dimensions": ["process_identity", "sampling_interval", "warmup", "throughput", "queue_depth", "tabs", "file_descriptors", "threads", "artifact_growth"],
            "sample_requirement": "p95",
        },
        {
            "id": "human_tab_responsiveness",
            "area": "human coexistence",
            "description": "Bounded user-visible input and focus responsiveness on an unrelated human-owned headed tab.",
            "unit": "ms",
            "aggregation": "p95 observed input/focus latency",
            "blocking": True,
            "limit": {
                "kind": "absolute_ceiling",
                "value": 500.0,
                "unit": "ms",
                "provisional": True,
                "comparison": "p95 user input/focus latency <= 500 ms",
            },
            "provisional_limits": [
                {"name": "input_latency_p95_ms", "operator": "<=", "value": 500.0, "unit": "ms", "provisional": True},
                {"name": "focus_theft", "operator": "==", "value": 0.0, "unit": "count", "provisional": True},
                {"name": "user_tab_closes", "operator": "==", "value": 0.0, "unit": "count", "provisional": True},
            ],
            "regression_budget": {
                "kind": "relative_increase",
                "value": 0.10,
                "unit": "fraction",
                "provisional": True,
                "comparison": "p95 responsiveness may regress by at most 10% from baseline",
            },
            "method": "In a headed existing-Chrome run, observe real input/focus checkpoints on an unrelated human tab while product actions execute; page timers and operator acknowledgments are not observations.",
            "required_dimensions": ["headed", "existing_chrome", "human_tab", "input_checkpoint", "focus_checkpoint", "retained_tab"],
            "sample_requirement": "p95",
        },
        {
            "id": "stale_refs_unknowns_and_safety",
            "area": "safety and isolation",
            "description": "Stale references, unknowns, cross-space and stale-agent mutation protection, replay, secrets, and user-tab safety.",
            "unit": "composite",
            "aggregation": "rates and zero-count invariants",
            "blocking": True,
            "limit": {
                "kind": "absolute_ceiling",
                "value": 0.01,
                "unit": "fraction",
                "provisional": True,
                "comparison": "stale-ref rate <= 1%; every zero-tolerance safety counter == 0",
            },
            "provisional_limits": [
                {"name": "stale_ref_rate", "operator": "<=", "value": 0.01, "unit": "fraction", "provisional": True},
                {"name": "cross_space_mutations", "operator": "==", "value": 0.0, "unit": "count", "provisional": True},
                {"name": "stale_agent_mutations", "operator": "==", "value": 0.0, "unit": "count", "provisional": True},
                {"name": "blind_replays", "operator": "==", "value": 0.0, "unit": "count", "provisional": True},
                {"name": "silent_unknown_success", "operator": "==", "value": 0.0, "unit": "count", "provisional": True},
                {"name": "secret_leaks", "operator": "==", "value": 0.0, "unit": "count", "provisional": True},
                {"name": "unauthorized_user_tab_mutations", "operator": "==", "value": 0.0, "unit": "count", "provisional": True},
            ],
            "regression_budget": {
                "kind": "absolute",
                "value": 0.0,
                "unit": "count",
                "provisional": True,
                "comparison": "zero-tolerance counters have no regression budget",
            },
            "method": "Across required action scenarios, count attempted, rejected, and successful safety events with traceable scenario IDs; zero-tolerance violations are automatic no-go.",
            "required_dimensions": ["scenario_id", "attempted", "rejected", "successful", "stale_ref", "cross_space", "stale_agent", "replay", "secret", "user_tab"],
            "sample_requirement": "p95",
        },
        {
            "id": "fault_recovery_lag",
            "area": "chaos and recovery",
            "description": "Recovery lag and deadline behavior for every declared seeded fault with no replay and page retention.",
            "unit": "ms",
            "aggregation": "p95 recovery lag",
            "blocking": True,
            "limit": {
                "kind": "absolute_ceiling",
                "value": 5000.0,
                "unit": "ms",
                "provisional": True,
                "comparison": "p95 recovery lag <= 5000 ms and every declared fault accounted",
            },
            "provisional_limits": [
                {"name": "recovery_lag_p95_ms", "operator": "<=", "value": 5000.0, "unit": "ms", "provisional": True},
                {"name": "declared_faults_accounted", "operator": "==", "value": 1.0, "unit": "boolean", "provisional": True},
                {"name": "no_replay_assertion", "operator": "==", "value": 1.0, "unit": "boolean", "provisional": True},
                {"name": "user_pages_retained", "operator": "==", "value": 1.0, "unit": "boolean", "provisional": True},
            ],
            "regression_budget": {
                "kind": "relative_increase",
                "value": 0.10,
                "unit": "fraction",
                "provisional": True,
                "comparison": "recovery lag may regress by at most 10% from baseline",
            },
            "method": "Inject each declared fault, measure event/reconnect/recovery lag and deadline behavior, and verify distinct epochs, no replay, and page/user-tab retention.",
            "required_dimensions": ["fault_id", "seed", "recovery_lag", "deadline", "epoch", "no_replay", "page_retention", "user_tab_retention"],
            "sample_requirement": "p95",
        },
    ]


def metric_specs() -> tuple[dict[str, Any], ...]:
    """Expose an immutable-ish copy of the canonical metric definitions."""
    return tuple(_metric_specs())


METRIC_IDS = tuple(spec["id"] for spec in _metric_specs())


def _canonical_thresholds() -> dict[str, Any]:
    return {
        "sample_policy": {
            "warmups": 10,
            "p95_min_valid_samples": 200,
            "p99_min_valid_samples": 1000,
            "smoke_sample_limit": 30,
            "confidence_interval": {
                "method": "bootstrap",
                "confidence_level": 0.95,
            },
        },
        "metric_register": {
            spec["id"]: {
                "limit": spec["limit"],
                "provisional_limits": spec["provisional_limits"],
                "regression_budget": spec["regression_budget"],
            }
            for spec in _metric_specs()
        },
    }


def _threshold_signature(record: Mapping[str, Any]) -> str:
    metrics = record.get("metric_register")
    if not isinstance(metrics, Mapping):
        raise DecisionError("metric_register is required for threshold comparison")
    policy = record.get("sample_policy")
    if not isinstance(policy, Mapping):
        raise DecisionError("sample_policy is required for threshold comparison")
    selected = {
        "sample_policy": {
            key: policy[key]
            for key in ("warmups", "p95_min_valid_samples", "p99_min_valid_samples", "smoke_sample_limit", "confidence_interval")
        },
        "metric_register": {
            metric_id: {
                "limit": metrics[metric_id]["limit"],
                "provisional_limits": metrics[metric_id]["provisional_limits"],
                "regression_budget": metrics[metric_id]["regression_budget"],
            }
            for metric_id in METRIC_IDS
        },
    }
    return json.dumps(selected, sort_keys=True, separators=(",", ":"))


def make_offline_record(
    *,
    decision_id: str = "p1-t7-2026-10-04-v1",
    decision_date: str = "2026-10-04",
    signed_at: str = "2026-10-04T12:00:00Z",
) -> dict[str, Any]:
    """Build the canonical offline record used by the committed artifact."""
    metrics: dict[str, Any] = {}
    for spec in _metric_specs():
        metrics[spec["id"]] = {
            **spec,
            "owner": OWNER,
            "evidence_mode": OFFLINE_MODE,
            "value": None,
            "status": OFFLINE_STATUS,
        }

    exclusions = [
        {
            "id": "disposable_cdp",
            "description": "A disposable browser measured through a CDP or page-evaluation harness.",
            "not_release_evidence": True,
        },
        {
            "id": "host_only",
            "description": "A host-only, direct-socket, or test-host observation without the enrolled extension and Chrome path.",
            "not_release_evidence": True,
        },
        {
            "id": "acknowledgement_only",
            "description": "An operator acknowledgment, checkpoint, or assertion without a browser observation.",
            "not_release_evidence": True,
        },
        {
            "id": "byte_estimate",
            "description": "A byte-derived token estimate, including UTF-8 bytes divided by four, in place of deployed tokenizer output.",
            "not_release_evidence": True,
        },
        {
            "id": "source_inspection",
            "description": "Static source inspection or deterministic hook output presented as live production behavior.",
            "not_release_evidence": True,
        },
        {
            "id": "operator_claim_only",
            "description": "A human or runner claim without current-run production-path provenance and persisted observations.",
            "not_release_evidence": True,
        },
    ]
    accounted_faults = {
        fault: {
            "status": OFFLINE_STATUS,
            "value": None,
            "evidence_mode": OFFLINE_MODE,
            "no_replay_assertion": None,
        }
        for fault in CHAOS_FAULTS
    }
    safety = {
        counter: {"value": None, "status": OFFLINE_STATUS, "evidence_mode": OFFLINE_MODE}
        for counter in SAFETY_COUNTER_IDS
    }
    timestamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    command = [
        "python3",
        "scripts/check_release_gate.py",
        "--phase",
        "1",
        "--decision-record",
        "artifacts/p1-t7-threshold-decision.json",
        "--mode",
        OFFLINE_MODE,
    ]
    build_tuple = {
        "phase": PHASE,
        "artifact_kind": KIND,
        "producer": "scripts/threshold_decision.py",
        "producer_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    environment = {
        "platform": sys.platform,
        "python": sys.version.split()[0],
        "cwd": "repository-relative",
        "network": "forbidden",
        "browser_launch": False,
        "browser_attach": False,
    }
    redaction_status = {
        "status": "applied",
        "policy": "bounded-static-threshold-record",
        "raw_browser_ids": False,
        "secrets": False,
        "absolute_paths": False,
        "page_bodies": False,
    }
    provenance = {
        "nonce": decision_id,
        "timestamp": timestamp,
        "command": command,
        "build_tuple": build_tuple,
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "build_tuple": build_tuple,
        "environment": environment,
        "timestamp": timestamp,
        "nonce": decision_id,
        "command": command,
        "result": {"status": "pass", "kind": KIND},
        "provenance": provenance,
        "redaction_status": redaction_status,
        "record_version": RECORD_VERSION,
        "phase": PHASE,
        "kind": KIND,
        "decision_id": decision_id,
        "decision_date": decision_date,
        "decision_status": "provisional",
        "effective_phase": EFFECTIVE_PHASE,
        "owner": OWNER,
        "signoff": {
            "owner": OWNER,
            "signed_at": signed_at,
            "status": "signed",
            "scope": "P1-T7 provisional performance, context, reliability, and safety thresholds for Phase 7 evidence",
        },
        "production_path": {
            "path_id": "direct_existing_chrome",
            "description": "Direct CLI/SDK request through owner-only local IPC, Rust host/broker, Chrome Native Messaging, the enrolled MV3 extension, and the existing user Chrome profile.",
            "components": [
                "direct_cli_sdk",
                "owner_only_local_ipc",
                "rust_host_broker",
                "chrome_native_messaging",
                "enrolled_mv3_extension",
                "existing_chrome_user_profile",
            ],
            "required": True,
        },
        "exclusions": exclusions,
        "sample_policy": {
            "warmups": 10,
            "p95_min_valid_samples": 200,
            "p99_min_valid_samples": 1000,
            "smoke_sample_limit": 30,
            "confidence_interval": {
                "method": "bootstrap",
                "confidence_level": 0.95,
            },
            "raw_samples_required": True,
            "predeclared_exclusions_required": True,
            "non_valid_samples_count_against_budget": True,
            "accounting_categories": [
                "success",
                "timeout",
                "error",
                "invalid_measurement",
                "infrastructure_failure",
            ],
        },
        "metric_register": metrics,
        "evidence_mode": OFFLINE_MODE,
        "evidence": {
            "mode": OFFLINE_MODE,
            "status": OFFLINE_STATUS,
            "provenance": None,
            "raw_samples": None,
            "confidence_intervals": None,
            "valid_sample_counts": None,
            "regression_deltas": None,
            "redaction_status": None,
        },
        "safety_counters": safety,
        "chaos": {
            "declared_faults": list(CHAOS_FAULTS),
            "accounted_faults": accounted_faults,
            "unaccounted_faults": [],
            "no_replay_assertion": None,
            "evidence_mode": OFFLINE_MODE,
        },
        "threshold_change": {
            "changed": False,
            "previous_decision_id": None,
            "new_decision_id": decision_id,
            "reason": None,
        },
        "release_eligible": False,
    }


def _validate_artifact_envelope(record: Mapping[str, Any]) -> None:
    build_tuple = record["build_tuple"]
    if not isinstance(build_tuple, dict) or build_tuple.get("phase") != PHASE:
        raise DecisionError("build_tuple must identify Phase 1")
    if build_tuple.get("artifact_kind") != KIND:
        raise DecisionError("build_tuple.artifact_kind must identify the P1-T7 record")
    if build_tuple.get("producer") != "scripts/threshold_decision.py":
        raise DecisionError("build_tuple.producer is invalid")
    producer_sha256 = build_tuple.get("producer_sha256")
    if not isinstance(producer_sha256, str) or not SHA256_RE.fullmatch(producer_sha256):
        raise DecisionError("build_tuple.producer_sha256 must be a lowercase SHA-256")
    environment = record["environment"]
    if not isinstance(environment, dict) or environment.get("network") != "forbidden":
        raise DecisionError("environment must record a forbidden-network deterministic run")
    for key in ("browser_launch", "browser_attach"):
        if environment.get(key) is not False:
            raise DecisionError(f"environment.{key} must be false")
    _valid_timestamp(record["timestamp"], "timestamp")
    _non_empty_text(record["nonce"], "nonce")
    command = record["command"]
    if not isinstance(command, list) or not command or any(not isinstance(item, str) or not item for item in command):
        raise DecisionError("command must be a bounded argv list")
    if any("/" in item and item.startswith("/") or "http:" in item or "https:" in item for item in command):
        raise DecisionError("command must contain repository-relative, non-network arguments")
    result = record["result"]
    if not isinstance(result, dict) or result.get("status") != "pass" or result.get("kind") != KIND:
        raise DecisionError("result must record a passing P1-T7 decision artifact")
    provenance = _expect_exact_keys(record["provenance"], {"nonce", "timestamp", "command", "build_tuple"}, "provenance")
    if (
        provenance["nonce"] != record["nonce"]
        or provenance["timestamp"] != record["timestamp"]
        or provenance["command"] != command
        or provenance["build_tuple"] != build_tuple
    ):
        raise DecisionError("provenance must exactly match the common artifact envelope")
    redaction = _expect_exact_keys(record["redaction_status"], {"status", "policy", "raw_browser_ids", "secrets", "absolute_paths", "page_bodies"}, "redaction_status")
    if redaction["status"] != "applied" or any(redaction[key] is not False for key in ("raw_browser_ids", "secrets", "absolute_paths", "page_bodies")):
        raise DecisionError("redaction_status does not prove bounded artifact redaction")


def _validate_signoff(record: Mapping[str, Any]) -> None:
    _valid_date(record["decision_date"], "decision_date")
    owner = _non_empty_text(record["owner"], "owner")
    if owner != OWNER:
        raise DecisionError(f"owner must be {OWNER}")
    signoff = _expect_exact_keys(record["signoff"], SIGNOFF_KEYS, "signoff")
    if signoff["owner"] != owner:
        raise DecisionError("signoff owner must match owner")
    if signoff["status"] != "signed":
        raise DecisionError("signoff status must be signed")
    _valid_timestamp(signoff["signed_at"], "signoff.signed_at")
    if signoff["signed_at"][:10] != record["decision_date"]:
        raise DecisionError("signoff date must match decision_date")
    _non_empty_text(signoff["scope"], "signoff.scope")


def _validate_production_path(value: Any) -> None:
    path = _expect_exact_keys(value, PRODUCTION_PATH_KEYS, "production_path")
    if path["path_id"] != "direct_existing_chrome":
        raise DecisionError("production_path must be direct_existing_chrome")
    _non_empty_text(path["description"], "production_path.description")
    if path["components"] != [
        "direct_cli_sdk",
        "owner_only_local_ipc",
        "rust_host_broker",
        "chrome_native_messaging",
        "enrolled_mv3_extension",
        "existing_chrome_user_profile",
    ]:
        raise DecisionError("production_path components must be the enrolled existing-Chrome production path")
    if path["required"] is not True:
        raise DecisionError("production_path.required must be true")


def _validate_exclusions(value: Any) -> None:
    if not isinstance(value, list) or len(value) != len(REQUIRED_EXCLUSIONS):
        raise DecisionError("exclusions must contain the complete fixed exclusion list")
    ids: list[str] = []
    for index, item in enumerate(value):
        exclusion = _expect_exact_keys(item, EXCLUSION_KEYS, f"exclusions[{index}]")
        _safe_id(exclusion["id"], f"exclusions[{index}].id")
        _non_empty_text(exclusion["description"], f"exclusions[{index}].description")
        if exclusion["not_release_evidence"] is not True:
            raise DecisionError(f"exclusions[{index}] must be marked not_release_evidence")
        ids.append(exclusion["id"])
    if set(ids) != set(REQUIRED_EXCLUSIONS) or len(ids) != len(set(ids)):
        raise DecisionError("exclusions do not match the canonical P1-T7 exclusion list")


def _validate_sample_policy(value: Any) -> None:
    policy = _expect_exact_keys(value, SAMPLE_POLICY_KEYS, "sample_policy")
    if _non_negative_int(policy["warmups"], "sample_policy.warmups") < 10:
        raise DecisionError("sample_policy.warmups must be at least 10")
    if _non_negative_int(policy["p95_min_valid_samples"], "sample_policy.p95_min_valid_samples") < 200:
        raise DecisionError("sample_policy.p95_min_valid_samples must be at least 200")
    if _non_negative_int(policy["p99_min_valid_samples"], "sample_policy.p99_min_valid_samples") < 1000:
        raise DecisionError("sample_policy.p99_min_valid_samples must be at least 1000")
    if _non_negative_int(policy["smoke_sample_limit"], "sample_policy.smoke_sample_limit") > 30:
        raise DecisionError("sample_policy.smoke_sample_limit must be at most 30")
    ci = _expect_exact_keys(policy["confidence_interval"], CI_POLICY_KEYS, "sample_policy.confidence_interval")
    if ci["method"] != "bootstrap":
        raise DecisionError("sample_policy confidence interval method must be bootstrap")
    if _finite_number(ci["confidence_level"], "sample_policy.confidence_level") != 0.95:
        raise DecisionError("sample_policy confidence level must be 0.95")
    for key in ("raw_samples_required", "predeclared_exclusions_required", "non_valid_samples_count_against_budget"):
        if policy[key] is not True:
            raise DecisionError(f"sample_policy.{key} must be true")
    categories = policy["accounting_categories"]
    if not isinstance(categories, list) or set(categories) != {
        "success",
        "timeout",
        "error",
        "invalid_measurement",
        "infrastructure_failure",
    }:
        raise DecisionError("sample_policy accounting categories are incomplete")


def _validate_limit(value: Any, label: str) -> None:
    limit = _expect_exact_keys(value, LIMIT_KEYS, label)
    if limit["kind"] not in {"absolute_ceiling", "absolute_minimum"}:
        raise DecisionError(f"{label}.kind is unsupported")
    _finite_number(limit["value"], f"{label}.value", minimum=0)
    _non_empty_text(limit["unit"], f"{label}.unit")
    if limit["provisional"] is not True:
        raise DecisionError(f"{label}.provisional must be true")
    _non_empty_text(limit["comparison"], f"{label}.comparison")


def _validate_provisional_limits(value: Any, label: str) -> None:
    if not isinstance(value, list) or not value:
        raise DecisionError(f"{label} must be a non-empty list")
    names: set[str] = set()
    for index, item in enumerate(value):
        limit = _expect_exact_keys(item, PROVISIONAL_LIMIT_KEYS, f"{label}[{index}]")
        _safe_id(limit["name"], f"{label}[{index}].name")
        if limit["name"] in names:
            raise DecisionError(f"{label} contains duplicate names")
        names.add(limit["name"])
        if limit["operator"] not in {"<=", ">=", "=="}:
            raise DecisionError(f"{label}[{index}].operator is unsupported")
        _finite_number(limit["value"], f"{label}[{index}].value", minimum=0)
        _non_empty_text(limit["unit"], f"{label}[{index}].unit")
        if limit["provisional"] is not True:
            raise DecisionError(f"{label}[{index}].provisional must be true")


def _validate_regression_budget(value: Any, label: str) -> None:
    budget = _expect_exact_keys(value, REGRESSION_BUDGET_KEYS, label)
    if budget["kind"] not in {"relative_increase", "relative_decrease", "absolute"}:
        raise DecisionError(f"{label}.kind is unsupported")
    _finite_number(budget["value"], f"{label}.value", minimum=0)
    _non_empty_text(budget["unit"], f"{label}.unit")
    if budget["provisional"] is not True:
        raise DecisionError(f"{label}.provisional must be true")
    _non_empty_text(budget["comparison"], f"{label}.comparison")


def _validate_metric_register(record: Mapping[str, Any]) -> None:
    metrics = record["metric_register"]
    if not isinstance(metrics, dict) or set(metrics) != set(METRIC_IDS):
        raise DecisionError("metric_register must contain every canonical P1-T7 metric exactly once")
    owner = record["owner"]
    for spec in _metric_specs():
        metric_id = spec["id"]
        metric = _expect_exact_keys(metrics[metric_id], METRIC_KEYS, f"metric_register.{metric_id}")
        if metric["id"] != metric_id:
            raise DecisionError(f"metric_register.{metric_id}.id does not match its key")
        for field in ("area", "description", "unit", "aggregation", "method"):
            _non_empty_text(metric[field], f"metric_register.{metric_id}.{field}")
        if metric["blocking"] is not True:
            raise DecisionError(f"metric_register.{metric_id}.blocking must be true")
        _validate_limit(metric["limit"], f"metric_register.{metric_id}.limit")
        _validate_provisional_limits(metric["provisional_limits"], f"metric_register.{metric_id}.provisional_limits")
        _validate_regression_budget(metric["regression_budget"], f"metric_register.{metric_id}.regression_budget")
        dimensions = metric["required_dimensions"]
        if not isinstance(dimensions, list) or not dimensions or any(not isinstance(item, str) or not item for item in dimensions):
            raise DecisionError(f"metric_register.{metric_id}.required_dimensions must be non-empty strings")
        if metric["sample_requirement"] not in {"p95", "p99"}:
            raise DecisionError(f"metric_register.{metric_id}.sample_requirement must be p95 or p99")
        if metric["owner"] != owner:
            raise DecisionError(f"metric_register.{metric_id}.owner must match owner")
        if metric["evidence_mode"] != record["evidence_mode"]:
            raise DecisionError(f"metric_register.{metric_id}.evidence_mode does not match record")
        status = metric["status"]
        if record["evidence_mode"] == OFFLINE_MODE:
            if metric["value"] is not None or status != OFFLINE_STATUS:
                raise DecisionError(f"metric_register.{metric_id} must be null/not_measured_offline")
        else:
            if status != LIVE_STATUS:
                raise DecisionError(f"metric_register.{metric_id}.status must be measured for live evidence")
            _finite_number(metric["value"], f"metric_register.{metric_id}.value", minimum=0)


def _validate_safety_counters(value: Any, mode: str) -> None:
    counters = value
    if not isinstance(counters, dict) or set(counters) != set(SAFETY_COUNTER_IDS):
        raise DecisionError("safety_counters must contain every zero-tolerance counter exactly once")
    for counter_id in SAFETY_COUNTER_IDS:
        counter = _expect_exact_keys(counters[counter_id], SAFETY_COUNTER_KEYS, f"safety_counters.{counter_id}")
        if counter["evidence_mode"] != mode:
            raise DecisionError(f"safety_counters.{counter_id}.evidence_mode does not match record")
        if mode == OFFLINE_MODE:
            if counter["value"] is not None or counter["status"] != OFFLINE_STATUS:
                raise DecisionError(f"safety_counters.{counter_id} must be null/not_measured_offline")
        else:
            if counter["status"] != LIVE_STATUS:
                raise DecisionError(f"safety_counters.{counter_id}.status must be measured for live evidence")
            value_number = _non_negative_int(counter["value"], f"safety_counters.{counter_id}.value")
            if value_number != 0:
                raise DecisionError(f"safety counter {counter_id} is nonzero")


def _validate_chaos(value: Any, mode: str) -> None:
    chaos = _expect_exact_keys(value, CHAOS_KEYS, "chaos")
    if chaos["evidence_mode"] != mode:
        raise DecisionError("chaos.evidence_mode does not match record")
    declared = chaos["declared_faults"]
    if not isinstance(declared, list) or tuple(declared) != CHAOS_FAULTS:
        raise DecisionError("chaos.declared_faults must contain the canonical ordered fault list")
    accounted = chaos["accounted_faults"]
    if not isinstance(accounted, dict) or set(accounted) != set(CHAOS_FAULTS):
        raise DecisionError("chaos has unaccounted faults")
    if chaos["unaccounted_faults"] != []:
        raise DecisionError("chaos.unaccounted_faults must be empty")
    if mode == OFFLINE_MODE:
        if chaos["no_replay_assertion"] is not None:
            raise DecisionError("offline chaos no_replay_assertion must be null")
    elif chaos["no_replay_assertion"] is not True:
        raise DecisionError("live chaos requires a no-replay assertion")
    for fault in CHAOS_FAULTS:
        entry = _expect_exact_keys(accounted[fault], CHAOS_ENTRY_KEYS, f"chaos.accounted_faults.{fault}")
        if entry["evidence_mode"] != mode:
            raise DecisionError(f"chaos.accounted_faults.{fault}.evidence_mode does not match record")
        if mode == OFFLINE_MODE:
            if entry["status"] != OFFLINE_STATUS or entry["value"] is not None or entry["no_replay_assertion"] is not None:
                raise DecisionError(f"chaos.accounted_faults.{fault} must be null/not_measured_offline")
        else:
            if entry["status"] != LIVE_STATUS or entry["no_replay_assertion"] is not True:
                raise DecisionError(f"live chaos fault {fault} is incomplete")
            _finite_number(entry["value"], f"chaos.accounted_faults.{fault}.value", minimum=0)


def _validate_redaction(value: Any) -> None:
    redaction = _expect_exact_keys(value, REDACTION_KEYS, "evidence.redaction_status")
    if redaction["status"] != "applied":
        raise DecisionError("live evidence redaction_status must be applied")
    _non_empty_text(redaction["policy"], "evidence.redaction_status.policy")
    for key in ("raw_browser_ids", "secrets", "absolute_paths", "page_bodies"):
        if redaction[key] is not False:
            raise DecisionError(f"evidence.redaction_status.{key} must be false")


def _validate_live_evidence(record: Mapping[str, Any], root: Path | None) -> None:
    evidence = _expect_exact_keys(record["evidence"], EVIDENCE_KEYS, "evidence")
    if evidence["mode"] != LIVE_MODE or evidence["status"] != LIVE_STATUS:
        raise DecisionError("live evidence must be marked measured/live")
    forbidden = _deep_forbidden_text(evidence)
    if forbidden:
        raise DecisionError(forbidden)

    provenance = _expect_exact_keys(evidence["provenance"], PROVENANCE_KEYS, "evidence.provenance")
    if provenance["source_class"] != "production_path" or provenance["path_id"] != "direct_existing_chrome":
        raise DecisionError("live evidence must come from the direct existing-Chrome production path")
    _safe_id(provenance["run_id"], "evidence.provenance.run_id")
    _valid_timestamp(provenance["timestamp"], "evidence.provenance.timestamp")
    command = provenance["command"]
    if not isinstance(command, list) or not command or any(not isinstance(item, str) or not item for item in command):
        raise DecisionError("evidence.provenance.command must be a non-empty argv list")
    for index, item in enumerate(command):
        _reject_absolute_or_parent_path(item, f"evidence.provenance.command[{index}]")
    build = _expect_exact_keys(provenance["build_tuple"], BUILD_TUPLE_KEYS, "evidence.provenance.build_tuple")
    for key in BUILD_TUPLE_KEYS - {"fixture_data_hash", "concurrency"}:
        _non_empty_text(build[key], f"evidence.provenance.build_tuple.{key}")
    _valid_sha256(build["fixture_data_hash"], "evidence.provenance.build_tuple.fixture_data_hash")
    if _non_negative_int(build["concurrency"], "evidence.provenance.build_tuple.concurrency") < 1:
        raise DecisionError("evidence.provenance.build_tuple.concurrency must be positive")

    raw = _expect_exact_keys(evidence["raw_samples"], RAW_SAMPLES_KEYS, "evidence.raw_samples")
    files = raw["files"]
    if not isinstance(files, list) or not files or len(files) != len(set(files)):
        raise DecisionError("live evidence.raw_samples.files must be a non-empty unique list")
    for index, file_name in enumerate(files):
        if not isinstance(file_name, str) or not file_name.endswith((".json", ".jsonl")):
            raise DecisionError(f"evidence.raw_samples.files[{index}] must be a JSON/JSONL artifact")
        _reject_absolute_or_parent_path(file_name, f"evidence.raw_samples.files[{index}]")
        if root is not None:
            candidate = _safe_record_path(root, file_name)
            if not candidate.is_file():
                raise DecisionError(f"raw sample artifact is missing: {file_name}")
    _valid_sha256(raw["sha256"], "evidence.raw_samples.sha256")
    if _non_negative_int(raw["sample_count"], "evidence.raw_samples.sample_count") < 1:
        raise DecisionError("evidence.raw_samples.sample_count must be positive")

    intervals = _expect_exact_keys(evidence["confidence_intervals"], CONFIDENCE_INTERVALS_KEYS, "evidence.confidence_intervals")
    if intervals["method"] != "bootstrap" or _finite_number(intervals["confidence_level"], "evidence.confidence_intervals.confidence_level") != 0.95:
        raise DecisionError("live confidence intervals must be bootstrap 95%")
    interval_metrics = intervals["metrics"]
    if not isinstance(interval_metrics, dict) or set(interval_metrics) != set(METRIC_IDS):
        raise DecisionError("live confidence intervals are missing metric entries")
    valid_counts = evidence["valid_sample_counts"]
    if not isinstance(valid_counts, dict) or set(valid_counts) != set(METRIC_IDS):
        raise DecisionError("live evidence is missing valid sample counts")
    policy = record["sample_policy"]
    for metric_id in METRIC_IDS:
        metric = record["metric_register"][metric_id]
        count = _non_negative_int(valid_counts[metric_id], f"evidence.valid_sample_counts.{metric_id}")
        minimum = policy["p99_min_valid_samples"] if metric["sample_requirement"] == "p99" else policy["p95_min_valid_samples"]
        if count < minimum:
            raise DecisionError(f"live valid sample count for {metric_id} is below {minimum}")
        interval = _expect_exact_keys(interval_metrics[metric_id], CONFIDENCE_INTERVAL_KEYS, f"evidence.confidence_intervals.metrics.{metric_id}")
        lower = _finite_number(interval["lower"], f"confidence interval lower for {metric_id}")
        upper = _finite_number(interval["upper"], f"confidence interval upper for {metric_id}")
        if upper < lower or _finite_number(interval["confidence_level"], f"confidence level for {metric_id}") != 0.95:
            raise DecisionError(f"confidence interval for {metric_id} is invalid")
        if _non_negative_int(interval["sample_count"], f"confidence interval sample count for {metric_id}") != count:
            raise DecisionError(f"confidence interval sample count for {metric_id} does not match valid count")

    deltas = evidence["regression_deltas"]
    if not isinstance(deltas, dict) or set(deltas) != set(METRIC_IDS):
        raise DecisionError("live evidence is missing regression deltas")
    for metric_id in METRIC_IDS:
        delta = _finite_number(deltas[metric_id], f"evidence.regression_deltas.{metric_id}", minimum=0)
        budget = record["metric_register"][metric_id]["regression_budget"]["value"]
        if delta > budget:
            raise DecisionError(f"regression budget exceeded for {metric_id}")
        _validate_metric_limit(record["metric_register"][metric_id], metric_id)

    _validate_redaction(evidence["redaction_status"])


def _validate_metric_limit(metric: Mapping[str, Any], metric_id: str) -> None:
    value = _finite_number(metric["value"], f"metric_register.{metric_id}.value", minimum=0)
    limit = metric["limit"]
    if limit["kind"] == "absolute_ceiling" and value > limit["value"]:
        raise DecisionError(f"absolute ceiling exceeded for {metric_id}")
    if limit["kind"] == "absolute_minimum" and value < limit["value"]:
        raise DecisionError(f"absolute minimum missed for {metric_id}")


def _validate_evidence(record: Mapping[str, Any], root: Path | None) -> None:
    evidence = _expect_exact_keys(record["evidence"], EVIDENCE_KEYS, "evidence")
    if evidence["mode"] != record["evidence_mode"]:
        raise DecisionError("evidence.mode does not match evidence_mode")
    if record["evidence_mode"] == OFFLINE_MODE:
        if evidence["status"] != OFFLINE_STATUS:
            raise DecisionError("offline evidence must be not_measured_offline")
        for key in ("provenance", "raw_samples", "confidence_intervals", "valid_sample_counts", "regression_deltas", "redaction_status"):
            if evidence[key] is not None:
                raise DecisionError(f"offline evidence.{key} must be null")
    else:
        _validate_live_evidence(record, root)


def _validate_threshold_change(record: Mapping[str, Any], previous_record: Mapping[str, Any] | None) -> None:
    change = _expect_exact_keys(record["threshold_change"], THRESHOLD_CHANGE_KEYS, "threshold_change")
    if change["new_decision_id"] != record["decision_id"]:
        raise DecisionError("threshold_change.new_decision_id must match decision_id")
    if change["changed"] is not True and change["changed"] is not False:
        raise DecisionError("threshold_change.changed must be boolean")
    if change["reason"] is not None and (not isinstance(change["reason"], str) or not change["reason"].strip()):
        raise DecisionError("threshold_change.reason must be null or non-empty")
    previous_id = change["previous_decision_id"]
    if previous_id is not None:
        if not isinstance(previous_id, str) or not DECISION_ID_RE.fullmatch(previous_id):
            raise DecisionError("threshold_change.previous_decision_id is invalid")
        if previous_id == record["decision_id"]:
            raise DecisionError("threshold changes require a new decision id")

    signature = _threshold_signature(record)
    canonical_signature = json.dumps(_canonical_thresholds(), sort_keys=True, separators=(",", ":"))
    if previous_record is None:
        if change["changed"] is False and signature != canonical_signature:
            raise DecisionError("threshold changes require a new decision id")
        if change["changed"] is True and (
            previous_id is None or change["reason"] is None or signature == canonical_signature
        ):
            raise DecisionError("a threshold change requires a reason and a new decision id")
        return

    previous_decision_id = previous_record.get("decision_id")
    if not isinstance(previous_decision_id, str) or not DECISION_ID_RE.fullmatch(previous_decision_id):
        raise DecisionError("previous decision record has an invalid decision_id")
    previous_signature = _threshold_signature(previous_record)
    changed = signature != previous_signature
    if changed != change["changed"]:
        raise DecisionError("threshold_change.changed does not match the previous decision record")
    if changed:
        if previous_id != previous_decision_id or change["reason"] is None or record["decision_id"] == previous_decision_id:
            raise DecisionError("threshold changes require a new decision id")
    elif previous_id is not None:
        raise DecisionError("unchanged thresholds must not claim a previous decision")


def validate_decision_record(
    record: Mapping[str, Any],
    *,
    mode: str | None = None,
    root: Path | None = None,
    previous_record: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate a P1-T7 record and return it; raise DecisionError on failure."""
    value = _expect_exact_keys(record, TOP_LEVEL_KEYS, "decision record")
    if value["schema_version"] != SCHEMA_VERSION or value["record_version"] != RECORD_VERSION:
        raise DecisionError("decision record schema/version is unsupported")
    if value["phase"] != PHASE or value["kind"] != KIND or value["effective_phase"] != EFFECTIVE_PHASE:
        raise DecisionError("decision record phase or kind is invalid")
    if not isinstance(value["decision_id"], str) or not DECISION_ID_RE.fullmatch(value["decision_id"]):
        raise DecisionError("decision_id must be a dated versioned P1-T7 id")
    if value["decision_status"] != "provisional":
        raise DecisionError("decision_status must be provisional until live Phase 7 evidence is complete")
    requested_mode = mode or value["evidence_mode"]
    if requested_mode not in {OFFLINE_MODE, LIVE_MODE}:
        raise DecisionError("mode must be offline or live")
    if value["evidence_mode"] != requested_mode:
        raise DecisionError("requested mode does not match decision record evidence_mode")
    _validate_artifact_envelope(value)
    _validate_signoff(value)
    _validate_production_path(value["production_path"])
    _validate_exclusions(value["exclusions"])
    _validate_sample_policy(value["sample_policy"])
    _validate_metric_register(value)
    _validate_evidence(value, root)
    _validate_safety_counters(value["safety_counters"], requested_mode)
    _validate_chaos(value["chaos"], requested_mode)
    _validate_threshold_change(value, previous_record)

    if requested_mode == OFFLINE_MODE:
        if value["release_eligible"] is not False:
            raise DecisionError("offline decision records must set release_eligible false")
    else:
        if value["release_eligible"] is not True:
            raise DecisionError("complete live evidence must set release_eligible true")
    return value


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record", required=True, help="repository-relative P1-T7 decision record")
    parser.add_argument("--mode", choices=(OFFLINE_MODE, LIVE_MODE), required=True)
    parser.add_argument("--root", help="repository root; defaults to the checkout containing this script")
    parser.add_argument(
        "--previous-decision-record",
        "--baseline-decision-record",
        dest="previous_decision_record",
        help="optional prior record used to prove threshold changes have a new decision id",
    )
    return parser.parse_args(argv)


def resolve_root(value: str | None) -> Path:
    root = Path(value).expanduser() if value else ROOT
    try:
        resolved = root.resolve(strict=True)
    except OSError as exc:
        raise DecisionError("repository root is missing or unreadable") from exc
    if not resolved.is_dir():
        raise DecisionError("repository root is not a directory")
    return resolved


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        root = resolve_root(args.root)
        record = load_decision_record(root, args.record)
        previous = load_decision_record(root, args.previous_decision_record) if args.previous_decision_record else None
        validate_decision_record(record, mode=args.mode, root=root, previous_record=previous)
    except (DecisionError, OSError) as exc:
        print(f"threshold_decision: FAIL: {exc}", file=sys.stderr)
        return 1
    eligible = "true" if record["release_eligible"] else "false"
    print(f"threshold_decision: PASS (mode={args.mode}; release_eligible={eligible})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
