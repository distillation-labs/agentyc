"""Run the Phase 0 direct-interface benchmark scaffold.

Offline mode measures deterministic local fixture parsing and serialization. It
never opens a browser, downloads dependencies, or consumes a CDP endpoint. The
other modes are explicit live lanes and fail closed until a real probe is wired.
"""

from __future__ import annotations

import argparse
import contextlib
import errno
import hashlib
import json
import math
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from html.parser import HTMLParser
from pathlib import Path
from statistics import mean
from typing import Any, ClassVar

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows has no fcntl
    fcntl = None

from artifact_envelope import envelope as add_envelope
from artifact_envelope import (
    new_nonce,
    redact_for_persistence,
    repository_relative,
    sha256_bytes,
    write_json_atomic,
    write_jsonl_atomic,
    write_text_atomic,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "browser-task-spaces"
MANIFEST_PATH = FIXTURE_ROOT / "manifest.json"
LIVE_MODES = {"target", "headed", "managed"}
MIN_P95_SAMPLES = 200
MIN_P99_SAMPLES = 1_000
DEFAULT_SAMPLES = MIN_P99_SAMPLES
SMOKE_DEFAULT_SAMPLES = 30
DEFAULT_CACHE_STATES = ("cold", "clean", "dirty", "resync")
MAX_MANIFEST_BYTES = 256 * 1024
MAX_FIXTURE_BYTES = 2 * 1024 * 1024
MAX_FIXTURE_TEXT_CHARS = 2 * 1024 * 1024
MAX_MANIFEST_ITEMS = 256
MAX_MANIFEST_JSON_DEPTH = 12
MAX_MANIFEST_JSON_NODES = 10_000
MAX_IFRAME_DEPTH = 8
MAX_IFRAME_COUNT = 128
MAX_TEXT_CHARS = 250_000
MAX_SRCDOC_CHARS = 100_000
MAX_SPACES = 256
MAX_RAW_SAMPLE_FILE_BYTES = 7 * 1024 * 1024
GENERATION_MANIFEST_NAME = "generation-manifest.json"
COMMIT_MARKER_NAME = "COMMIT"


class _ParserBudget:
    def __init__(self) -> None:
        self.frame_count = 0
        self.text_chars = 0
        self.srcdoc_chars = 0


class ControlCounter(HTMLParser):
    """Count controls and recursively inspect bounded inline ``srcdoc`` frames."""

    CONTROL_TAGS: ClassVar[set[str]] = {"a", "button", "input", "select", "textarea"}

    def __init__(self, frame_depth: int = 0, *, budget: _ParserBudget | None = None) -> None:
        super().__init__(convert_charrefs=True)
        self.controls = 0
        self.frames = 0
        self.frames_scanned = 0
        self.max_frame_depth = frame_depth
        self.text_chars = 0
        self.srcdoc_chars = 0
        self.frame_depth = frame_depth
        self._budget = budget or _ParserBudget()

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized_tag = tag.lower()
        if normalized_tag in self.CONTROL_TAGS:
            self.controls += 1
        if normalized_tag != "iframe":
            return

        self._budget.frame_count += 1
        if self._budget.frame_count > MAX_IFRAME_COUNT:
            raise ValueError("fixture iframe count exceeds the bounded parser limit")
        self.frames += 1
        attributes = dict(attrs)
        srcdoc = attributes.get("srcdoc")
        if srcdoc is None:
            return
        if self.frame_depth >= MAX_IFRAME_DEPTH:
            raise ValueError("fixture iframe depth exceeds the bounded parser limit")
        if len(srcdoc) > MAX_SRCDOC_CHARS:
            raise ValueError("fixture iframe srcdoc exceeds the bounded parser limit")
        self._budget.srcdoc_chars += len(srcdoc)
        self.srcdoc_chars += len(srcdoc)
        if self._budget.srcdoc_chars > MAX_SRCDOC_CHARS * MAX_IFRAME_COUNT:
            raise ValueError("fixture cumulative srcdoc exceeds the bounded parser limit")

        child = ControlCounter(self.frame_depth + 1, budget=self._budget)
        child.feed(srcdoc)
        child.close()
        self.controls += child.controls
        self.frames += child.frames
        self.frames_scanned += 1 + child.frames_scanned
        self.max_frame_depth = max(self.max_frame_depth, child.max_frame_depth)
        self.text_chars += child.text_chars
        self.srcdoc_chars += child.srcdoc_chars

    def handle_data(self, data: str) -> None:
        self._budget.text_chars += len(data)
        if self._budget.text_chars > MAX_TEXT_CHARS:
            raise ValueError("fixture text exceeds the bounded parser limit")
        self.text_chars += len(data)

    @property
    def frame_coverage(self) -> float | None:
        if self.frames == 0:
            return None
        return self.frames_scanned / self.frames


def sha256_file(path: Path, *, max_bytes: int | None = None) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        total = 0
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if max_bytes is not None and total > max_bytes:
                raise ValueError("file exceeds the bounded read limit")
            digest.update(chunk)
    return digest.hexdigest()


def _validate_json_shape(value: Any, *, depth: int = 0, nodes: list[int] | None = None) -> None:
    counters = nodes if nodes is not None else [0]
    counters[0] += 1
    if counters[0] > MAX_MANIFEST_JSON_NODES or depth > MAX_MANIFEST_JSON_DEPTH:
        raise ValueError("fixture manifest exceeds the bounded JSON limit")
    if isinstance(value, dict):
        if len(value) > MAX_MANIFEST_ITEMS:
            raise ValueError("fixture manifest object exceeds the bounded item limit")
        for key, child in value.items():
            if not isinstance(key, str) or len(key) > 512:
                raise ValueError("fixture manifest key is invalid or too long")
            _validate_json_shape(child, depth=depth + 1, nodes=counters)
    elif isinstance(value, list):
        if len(value) > MAX_MANIFEST_ITEMS:
            raise ValueError("fixture manifest list exceeds the bounded item limit")
        for child in value:
            _validate_json_shape(child, depth=depth + 1, nodes=counters)
    elif isinstance(value, str) and len(value) > MAX_FIXTURE_TEXT_CHARS:
        raise ValueError("fixture manifest string exceeds the bounded text limit")


def read_json(path: Path) -> Any:
    try:
        raw = path.read_bytes()
        if len(raw) > MAX_MANIFEST_BYTES:
            raise ValueError("fixture manifest exceeds the bounded read limit")
        value = json.loads(raw.decode("utf-8"))
        _validate_json_shape(value)
        return value
    except (FileNotFoundError, json.JSONDecodeError, UnicodeError, ValueError) as exc:
        if isinstance(exc, ValueError) and str(exc).startswith("fixture manifest"):
            raise
        raise ValueError(f"invalid or missing fixture manifest {repository_relative(path)}") from exc


def _safe_fixture_path(relative: str) -> Path:
    relative_path = Path(relative)
    if (
        relative_path.is_absolute()
        or not relative_path.parts
        or ".." in relative_path.parts
        or len(relative_path.parts) != 1
        or relative_path.name != relative
    ):
        raise ValueError("fixtures must be direct repository-local files")
    current = FIXTURE_ROOT
    if current.is_symlink():
        raise ValueError("fixture root must not be a symlink")
    for component in relative_path.parts:
        current = current / component
        if current.is_symlink():
            raise ValueError("fixture path components must not be symlinks")
    if not current.is_file():
        raise ValueError(f"fixture {relative!r} is not a local file")
    try:
        resolved = current.resolve()
        resolved.relative_to(FIXTURE_ROOT.resolve())
    except (OSError, ValueError) as exc:
        raise ValueError("fixture path is outside the fixture root") from exc
    size = current.stat().st_size
    if size > MAX_FIXTURE_BYTES:
        raise ValueError("fixture exceeds the bounded read limit")
    return current


def load_fixture_bundle() -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    current = MANIFEST_PATH
    while current != current.parent:
        if current.is_symlink():
            raise ValueError("fixture manifest path components must not be symlinks")
        current = current.parent
    raw_manifest = MANIFEST_PATH.read_bytes()
    if len(raw_manifest) > MAX_MANIFEST_BYTES:
        raise ValueError("fixture manifest exceeds the bounded read limit")
    try:
        manifest = json.loads(raw_manifest.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise ValueError(f"invalid or missing fixture manifest {repository_relative(MANIFEST_PATH)}") from exc
    _validate_json_shape(manifest)
    if not isinstance(manifest, dict):
        raise TypeError("fixture manifest must be an object")
    fixture_items = manifest.get("fixtures")
    if not isinstance(fixture_items, list) or not fixture_items:
        raise ValueError("fixture manifest is empty")
    if len(fixture_items) > MAX_MANIFEST_ITEMS:
        raise ValueError("fixture manifest has too many fixtures")

    records: dict[str, dict[str, Any]] = {}
    seen_files: set[str] = set()
    for item in fixture_items:
        if not isinstance(item, dict):
            raise TypeError("fixture entries must be objects")
        name = item.get("name")
        relative = item.get("file")
        if not isinstance(name, str) or not name or len(name) > 128:
            raise TypeError("fixture entries need bounded string names")
        if not isinstance(relative, str):
            raise TypeError("fixture entries need string file names")
        if name in records or relative in seen_files:
            raise ValueError("fixture names and files must be unique")
        path = _safe_fixture_path(relative)
        if relative in seen_files:
            raise ValueError("fixture files must be unique")
        seen_files.add(relative)
        records[name] = {
            "name": name,
            "file": relative,
            "path": path,
            "sha256": sha256_file(path, max_bytes=MAX_FIXTURE_BYTES),
            "bytes": path.stat().st_size,
        }
    if not records:
        raise ValueError("fixture manifest is empty")
    metadata = {
        "path": MANIFEST_PATH.relative_to(ROOT).as_posix(),
        "sha256": sha256_bytes(raw_manifest),
        "bytes": len(raw_manifest),
        "schema_version": manifest.get("schema_version"),
        "fixture_set": manifest.get("fixture_set"),
    }
    return metadata, records


def baseline_manifest_metadata() -> dict[str, Any]:
    metadata, _ = load_fixture_bundle()
    return metadata


def load_fixtures() -> dict[str, dict[str, Any]]:
    _, records = load_fixture_bundle()
    return records


def browser_candidates() -> list[Path]:
    values = [
        os.environ.get("AGENTYC_CHROME_PATH", ""),
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
    ]
    result = [Path(value) for value in values if value]
    for command in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        found = shutil.which(command)
        if found:
            result.append(Path(found))
    return list(dict.fromkeys(result))


def running_chrome() -> bool:
    if platform.system() == "Windows":
        return False
    try:
        result = subprocess.run(
            ["ps", "-axo", "comm="], check=False, capture_output=True, text=True, timeout=2
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    names = {Path(line.strip()).name.lower() for line in result.stdout.splitlines() if line.strip()}
    return bool(names & {"google chrome", "google-chrome", "google-chrome-stable", "chromium", "chromium-browser"})


def live_missing(mode: str, browser_executable: str | None, profile_dir: str | None) -> list[str]:
    missing: list[str] = []
    if mode in {"target", "headed"}:
        if not (Path(browser_executable).is_file() if browser_executable else any(path.is_file() for path in browser_candidates())):
            missing.append("an installed Chrome/Chromium executable")
        if platform.system() == "Linux" and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
            missing.append("a headed display (DISPLAY or WAYLAND_DISPLAY)")
        if not running_chrome():
            missing.append("an already running headed Chrome target")
    if mode == "managed":
        if not browser_executable:
            missing.append("--browser-executable for managed mode")
        elif not Path(browser_executable).is_file():
            missing.append("the supplied browser executable")
        if not profile_dir:
            missing.append("--profile-dir for managed mode")
        elif not Path(profile_dir).is_dir():
            missing.append("the supplied existing profile directory")
    return missing


def parse_csv(value: str, label: str) -> list[str]:
    values = [part.strip() for part in value.split(",") if part.strip()]
    if not values:
        raise ValueError(f"{label} cannot be empty")
    if len(values) != len(set(values)):
        raise ValueError(f"{label} must not contain duplicates")
    return values


def parse_positive_ints(value: str, label: str) -> list[int]:
    try:
        values = [int(part) for part in parse_csv(value, label)]
    except ValueError as exc:
        raise ValueError(f"{label} must be comma-separated positive integers") from exc
    if any(value <= 0 or value > MAX_SPACES for value in values):
        raise ValueError(f"{label} must be comma-separated integers in 1..{MAX_SPACES}")
    return values


def percentile(values: list[float], percent: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = round((len(ordered) - 1) * percent / 100.0)
    return ordered[min(index, len(ordered) - 1)]


def mean_confidence_interval(values: list[float]) -> dict[str, Any]:
    """Return an approximate 95% CI for the mean without external dependencies."""
    if not values:
        return {
            "method": "normal_approximation",
            "confidence_level": 0.95,
            "sample_count": 0,
            "lower": 0.0,
            "upper": 0.0,
            "status": "not_available",
        }
    average = mean(values)
    if len(values) == 1:
        margin = 0.0
    else:
        variance = math.fsum((value - average) ** 2 for value in values) / (len(values) - 1)
        margin = 1.96 * math.sqrt(variance / len(values))
    return {
        "method": "normal_approximation",
        "confidence_level": 0.95,
        "sample_count": len(values),
        "lower": average - margin,
        "upper": average + margin,
        "status": "approximate",
    }


def measure_once(record: dict[str, Any], cache_state: str, spaces: int) -> dict[str, Any]:
    started = time.perf_counter_ns()
    raw = record["path"].read_bytes()
    if len(raw) > MAX_FIXTURE_BYTES:
        raise ValueError("fixture exceeds the bounded read limit")
    read_done = time.perf_counter_ns()
    decoded = raw.decode("utf-8")
    if len(decoded) > MAX_FIXTURE_TEXT_CHARS:
        raise ValueError("fixture exceeds the bounded text limit")
    parser = ControlCounter()
    parser.feed(decoded)
    parser.close()
    metadata_done = time.perf_counter_ns()
    # This is a local, side-effect-free stand-in for an actionable operation.
    first_control = parser.controls > 0
    action_done = time.perf_counter_ns()
    summary = {
        "controls": parser.controls,
        "frames": parser.frames,
        "first_control_available": first_control,
        "serialized_bytes": len(json.dumps({"controls": parser.controls, "frames": parser.frames}, sort_keys=True)),
    }
    _ = json.dumps(summary, sort_keys=True)
    finished = time.perf_counter_ns()
    synthetic_action_ms = (action_done - metadata_done) / 1_000_000
    return {
        "fixture": record["name"],
        "cache_state": cache_state,
        "spaces": spaces,
        "sample_status": "valid",
        "read_ms": (read_done - started) / 1_000_000,
        "metadata_ms": (metadata_done - read_done) / 1_000_000,
        "action_ms": synthetic_action_ms,
        "first_useful_action_ms": (action_done - started) / 1_000_000,
        "synthetic_action_ms": synthetic_action_ms,
        "wait_ms": (finished - action_done) / 1_000_000,
        "total_ms": (finished - started) / 1_000_000,
        "fixture_bytes": len(raw),
        "fixture_chars": len(decoded),
        "serialized_bytes": summary["serialized_bytes"],
        "dom_scans": 0 if cache_state == "clean" else 1,
        "actionable_controls": parser.controls,
        "frames": parser.frames,
        "frames_scanned": parser.frames_scanned,
        "frame_coverage": parser.frame_coverage,
        "max_frame_depth": parser.max_frame_depth,
        "text_chars": parser.text_chars,
        "srcdoc_chars": parser.srcdoc_chars,
    }


def sample_error(record: dict[str, Any], cache_state: str, spaces: int, exc: Exception) -> dict[str, Any]:
    return {
        "fixture": record["name"],
        "cache_state": cache_state,
        "spaces": spaces,
        "sample_status": "error",
        "error_type": type(exc).__name__,
        "error": str(exc),
    }


REQUIRED_SAMPLE_METRICS = (
    "read_ms",
    "metadata_ms",
    "action_ms",
    "first_useful_action_ms",
    "synthetic_action_ms",
    "wait_ms",
    "total_ms",
)


def validate_sample(sample: dict[str, Any]) -> str | None:
    for key in REQUIRED_SAMPLE_METRICS:
        value = sample.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or float(value) < 0:
            return f"missing or invalid metric: {key}"
    coverage = sample.get("frame_coverage")
    if coverage is not None and (
        isinstance(coverage, bool)
        or not isinstance(coverage, (int, float))
        or not math.isfinite(float(coverage))
        or not 0.0 <= float(coverage) <= 1.0
    ):
        return "frame coverage is outside [0, 1]"
    for key in ("fixture_bytes", "fixture_chars", "frames", "frames_scanned", "max_frame_depth", "text_chars", "srcdoc_chars"):
        value = sample.get(key)
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
            return f"missing or invalid count: {key}"
    return None


def measure_sample(record: dict[str, Any], cache_state: str, spaces: int, *, nonce: str | None = None) -> dict[str, Any]:
    try:
        sample = measure_once(record, cache_state, spaces)
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        sample = sample_error(record, cache_state, spaces, exc)
    else:
        invalid_reason = validate_sample(sample)
        if invalid_reason is not None:
            sample["sample_status"] = "invalid"
            sample["invalid_reason"] = invalid_reason
    if nonce is not None:
        sample["nonce"] = nonce
    return sample


def sample_accounting(samples: list[dict[str, Any]]) -> dict[str, int]:
    statuses = [sample.get("sample_status") for sample in samples]
    valid = statuses.count("valid")
    errors = statuses.count("error")
    invalid = len(statuses) - valid - errors
    return {"attempted": len(samples), "valid": valid, "errors": errors, "invalid": invalid}


def summarize(samples: list[dict[str, Any]], record: dict[str, Any], cache_state: str, spaces: int) -> dict[str, Any]:
    def values(key: str) -> list[float]:
        return [float(sample[key]) for sample in samples if sample.get("sample_status") == "valid" and key in sample]

    accounting = sample_accounting(samples)
    valid = accounting["valid"]
    p95_gate = "gateable" if valid >= MIN_P95_SAMPLES else "not_gateable"
    p99_gate = "gateable" if valid >= MIN_P99_SAMPLES else "not_gateable"

    def latency_stats(key: str, measurement_status: str = "measured") -> dict[str, Any]:
        metric_values = values(key)
        return {
            "p50": percentile(metric_values, 50),
            "p95": percentile(metric_values, 95),
            "p99": percentile(metric_values, 99),
            "mean": mean(metric_values) if metric_values else 0.0,
            "mean_confidence_interval": mean_confidence_interval(metric_values),
            "measurement_status": measurement_status,
        }

    valid_samples = [sample for sample in samples if sample.get("sample_status") == "valid"]
    first_valid = valid_samples[0] if valid_samples else {}
    frame_count = max((int(sample.get("frames", 0)) for sample in valid_samples), default=0)
    frames_scanned = max((int(sample.get("frames_scanned", 0)) for sample in valid_samples), default=0)
    frame_coverage = frames_scanned / frame_count if frame_count else None
    return {
        "fixture": record["name"],
        "fixture_sha256": record["sha256"],
        "cache_state": cache_state,
        "spaces": spaces,
        "samples": accounting,
        "latency_ms": {
            "read_ms": latency_stats("read_ms"),
            "first_useful_action_ms": latency_stats("first_useful_action_ms", "synthetic_offline_control_presence_check"),
            "metadata_ms": latency_stats("metadata_ms"),
            "action_ms": latency_stats("action_ms", "synthetic_offline_control_presence_check"),
            "synthetic_action_ms": latency_stats("synthetic_action_ms", "synthetic_offline_control_presence_check"),
            "wait_ms": latency_stats("wait_ms"),
            "total_ms": latency_stats("total_ms"),
        },
        "offline_action": {
            "status": "synthetic",
            "operation": "control_presence_check",
            "browser_action_executed": False,
            "note": "The offline lane parses fixture controls; it does not click or execute a browser action.",
        },
        "round_trips": {
            "separate_calls": 3,
            "batch_calls": 1,
            "batch_reduction_percent": 66.67,
            "measurement_status": "offline_model_of_call_counts",
        },
        "context": {
            "fixture_utf8_bytes": record["bytes"],
            "fixture_chars": len(record["path"].read_text(encoding="utf-8")),
            "serialized_bytes": first_valid.get("serialized_bytes", 0),
            "serialized_tokens": None,
            "model_context_tokens": None,
            "tokenizer": None,
            "token_measurement_status": "not_available_in_offline_scaffold",
            "clean_snapshot_dom_scans": 0 if cache_state == "clean" else 1,
            "full_snapshot_actionable_control_coverage": frame_coverage,
            "delta_snapshot_actionable_control_coverage": frame_coverage,
            "frames_discovered": frame_count,
            "frames_scanned": frames_scanned,
            "nested_frame_coverage": frame_coverage,
            "max_frame_depth": max((int(sample.get("max_frame_depth", 0)) for sample in valid_samples), default=0),
            "text_chars": max((int(sample.get("text_chars", 0)) for sample in valid_samples), default=0),
            "srcdoc_chars": max((int(sample.get("srcdoc_chars", 0)) for sample in valid_samples), default=0),
        },
        "live_only": {
            "chrome_cpu_percent": None,
            "chrome_rss_bytes": None,
            "host_rss_bytes": None,
            "event_lag_ms": None,
            "reconnect_ms": None,
            "stale_ref_rate": None,
            "unknown_outcome_rate": None,
            "human_tab_responsiveness_ms": None,
            "status": "not_measured_offline",
        },
        "reliability_gates": {
            "stale_ref_rate": {"status": "not_measured_offline", "value": None},
            "unknown_outcome_rate": {"status": "not_measured_offline", "value": None},
            "reconnect_ms": {"status": "not_measured_offline", "value": None},
        },
        "human_tab_gate": {"status": "not_measured_offline", "responsiveness_ms": None},
        "tail_gates": {
            "p95": {"status": p95_gate, "minimum_samples": MIN_P95_SAMPLES},
            "p99": {"status": p99_gate, "minimum_samples": MIN_P99_SAMPLES},
            "note": "Tail thresholds are fixed at 200 valid samples for p95 and 1000 valid samples for p99; smoke runs are explicitly non-gating.",
        },
    }


def markdown_report(result: dict[str, Any]) -> str:
    lines = [
        "# Phase 0 direct benchmark baseline",
        "",
        f"- Mode: `{result['mode']}`",
        f"- Kind: `{result['kind']}`",
        f"- Run nonce: `{result['nonce']}`",
        f"- Fixture set SHA-256: `{result['fixture_set_sha256']}`",
        f"- Fixture manifest SHA-256: `{result['baseline_manifest']['sha256']}`",
        "- Confidence intervals: approximate normal 95% intervals for per-cell latency means",
        "- Browser launches: `0`",
        "- Browser downloads: `0`",
        "- CDP URL: `not used`",
        "- Tokenizer: `not available in offline scaffold`",
        "",
        "| Fixture | Cache | Spaces | Samples (valid/error/invalid) | Synthetic local action p50 (ms) | Metadata p95 (ms) | Synthetic action p95 (ms) | p95 gate | p99 gate |",
        "|---|---:|---:|---:|---:|---:|---:|---|---|",
    ]
    for row in result["rows"]:
        lines.append(
            f"| {row['fixture']} | {row['cache_state']} | {row['spaces']} | "
            f"{row['samples']['valid']}/{row['samples']['errors']}/{row['samples']['invalid']} | "
            f"{row['latency_ms']['first_useful_action_ms']['p50']:.4f} (synthetic) | "
            f"{row['latency_ms']['metadata_ms']['p95']:.4f} | "
            f"{row['latency_ms']['action_ms']['p95']:.4f} | "
            f"{row['tail_gates']['p95']['status']} | {row['tail_gates']['p99']['status']} |"
        )
    lines.extend(
        [
            "",
            "Offline action timings are synthetic control-presence checks, not browser clicks. Live-only resource, reliability, reconnect, and human-tab metrics are null.",
            "",
        ]
    )
    return "\n".join(lines)


def raw_sample_chunks(samples: list[dict[str, Any]]) -> list[tuple[str, bytes]]:
    chunks: list[bytes] = []
    current = bytearray()
    for sample in samples:
        safe_sample = redact_for_persistence(sample)
        line = (json.dumps(safe_sample, separators=(",", ":"), sort_keys=True, ensure_ascii=True, allow_nan=False) + "\n").encode("utf-8")
        if len(line) > MAX_RAW_SAMPLE_FILE_BYTES:
            raise ValueError("one raw sample exceeds the bounded artifact limit")
        if current and len(current) + len(line) > MAX_RAW_SAMPLE_FILE_BYTES:
            chunks.append(bytes(current))
            current = bytearray()
        current.extend(line)
    if current or not chunks:
        chunks.append(bytes(current))
    return [
        ("raw_samples.jsonl" if index == 0 else f"raw_samples-{index:03d}.jsonl", chunk)
        for index, chunk in enumerate(chunks)
    ]


def safe_artifact_dir(value: Path) -> Path:
    requested = value if value.is_absolute() else ROOT / value
    current = requested
    while current != current.parent:
        if current.is_symlink():
            raise ValueError("artifact path components must not be symlinks")
        current = current.parent
    if requested.exists() and not requested.is_dir():
        raise ValueError("artifact directory must be a directory")
    resolved = requested.resolve()
    artifacts = (ROOT / "artifacts").resolve()
    try:
        resolved.relative_to(artifacts)
    except ValueError as error:
        raise ValueError("artifact directory must be inside artifacts/") from error
    if resolved == artifacts:
        raise ValueError("artifact directory must be a child of artifacts/")
    return resolved


def _fixture_set_hash(selected: list[dict[str, Any]]) -> str:
    return hashlib.sha256(
        "\n".join(f"{record['name']}:{record['sha256']}" for record in selected).encode("utf-8")
    ).hexdigest()


def _raw_sample_declarations(chunks: list[tuple[str, bytes]], sample_count: int) -> dict[str, Any]:
    declarations: list[dict[str, Any]] = []
    for name, content in chunks:
        declarations.append(
            {
                "name": name,
                "sha256": sha256_bytes(content),
                "bytes": len(content),
                "sample_count": content.count(b"\n"),
            }
        )
    return {
        "files": declarations,
        "total_samples": sample_count,
        "required_metrics": list(REQUIRED_SAMPLE_METRICS),
    }


def _file_metadata(directory: Path, names: list[str]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for name in names:
        path = directory / name
        result.append({"name": name, "sha256": sha256_file(path), "bytes": path.stat().st_size})
    return result


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@contextlib.contextmanager
def _publication_lock(lock_path: Path):
    """Serialize publication so two runs cannot replace the same generation."""
    target = Path(lock_path)
    current = target
    while current != current.parent:
        if current.is_symlink():
            raise ValueError("benchmark publication lock components must not be symlinks")
        current = current.parent
    target.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_RDWR | os.O_CREAT
    remove_fallback = False
    if fcntl is None:
        flags |= os.O_EXCL
        remove_fallback = True
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor: int | None = None
    try:
        try:
            descriptor = os.open(str(target), flags, 0o600)
        except FileExistsError as error:
            raise ValueError("benchmark publication is already in progress") from error
        handle = os.fdopen(descriptor, "a+b")
        descriptor = None
        try:
            if fcntl is not None:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError as error:
                    if error.errno in {errno.EACCES, errno.EAGAIN}:
                        raise ValueError("benchmark publication is already in progress") from error
                    raise
            yield handle
        finally:
            if fcntl is not None:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
            handle.close()
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if remove_fallback:
            try:
                target.unlink()
            except OSError:
                pass


def publish_benchmark(
    artifact_dir: Path,
    result: dict[str, Any],
    markdown: str,
    sample_chunks: list[tuple[str, bytes]],
) -> dict[str, Any]:
    """Publish one complete benchmark generation without destroying its predecessor."""
    safe_dir = safe_artifact_dir(artifact_dir)
    with _publication_lock(safe_dir.parent / f".{safe_dir.name}.publish.lock"):
        return _publish_benchmark_locked(safe_dir, result, markdown, sample_chunks)


def _publish_benchmark_locked(
    artifact_dir: Path,
    result: dict[str, Any],
    markdown: str,
    sample_chunks: list[tuple[str, bytes]],
) -> dict[str, Any]:
    artifact_dir = safe_artifact_dir(artifact_dir)
    parent = artifact_dir.parent
    parent.mkdir(parents=True, exist_ok=True)
    nonce = str(result.get("nonce") or new_nonce())
    generation_id = f"generation-{nonce}"
    previous_path: Path | None = None
    if artifact_dir.exists():
        previous_path = parent / f".{artifact_dir.name}.previous-{nonce[:12]}"
        if previous_path.exists() or previous_path.is_symlink():
            raise ValueError("previous generation destination already exists")

    stage = Path(tempfile.mkdtemp(prefix=f".{artifact_dir.name}.staging-", dir=parent))
    moved_previous = False
    try:
        write_json_atomic(stage / "baseline.json", result)
        write_text_atomic(stage / "baseline.md", markdown)
        raw_names: list[str] = []
        seen_raw_names: set[str] = set()
        for name, content in sample_chunks:
            if (
                not name.startswith("raw_samples")
                or "/" in name
                or "\\" in name
                or name in seen_raw_names
            ):
                raise ValueError("raw sample declaration has an unsafe or duplicate name")
            write_jsonl_atomic(stage / name, content, max_bytes=MAX_RAW_SAMPLE_FILE_BYTES)
            seen_raw_names.add(name)
            raw_names.append(name)

        generation_manifest: dict[str, Any] = {
            "schema_version": 1,
            "phase": 0,
            "kind": "direct-benchmark-generation",
            "generation_id": generation_id,
            "nonce": nonce,
            "complete": True,
            "previous_generation": repository_relative(previous_path) if previous_path else None,
            "files": _file_metadata(stage, ["baseline.json", "baseline.md", *raw_names]),
            "raw_samples_files": raw_names,
        }
        add_envelope(
            generation_manifest,
            kind="direct-benchmark-generation",
            command=result.get("command"),
            build_tuple=result.get("build_tuple") if isinstance(result.get("build_tuple"), dict) else None,
            nonce=nonce,
        )
        write_json_atomic(stage / GENERATION_MANIFEST_NAME, generation_manifest)
        manifest_hash = sha256_file(stage / GENERATION_MANIFEST_NAME)
        commit_marker = {
            "schema_version": 1,
            "kind": "direct-benchmark-commit",
            "generation_id": generation_id,
            "nonce": nonce,
            "complete": True,
            "manifest": GENERATION_MANIFEST_NAME,
            "manifest_sha256": manifest_hash,
        }
        write_text_atomic(
            stage / COMMIT_MARKER_NAME,
            json.dumps(redact_for_persistence(commit_marker), sort_keys=True, separators=(",", ":")) + "\n",
            max_bytes=64 * 1024,
        )
        _fsync_directory(stage)

        if artifact_dir.exists():
            if previous_path is None:
                raise ValueError("missing previous generation destination")
            artifact_dir.replace(previous_path)
            moved_previous = True
        stage.replace(artifact_dir)
        _fsync_directory(parent)
        return {
            "generation_id": generation_id,
            "manifest": GENERATION_MANIFEST_NAME,
            "commit_marker": COMMIT_MARKER_NAME,
            "previous_generation": repository_relative(previous_path) if previous_path else None,
        }
    except Exception:
        if moved_previous and previous_path is not None and not artifact_dir.exists() and previous_path.exists():
            previous_path.replace(artifact_dir)
        raise
    finally:
        if stage.exists():
            shutil.rmtree(stage, ignore_errors=True)


def parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Measure local Phase 0 fixtures and emit an honest direct benchmark baseline.",
        epilog="offline is safe by default. target/headed/managed are explicit live lanes; they never launch/download Chrome and target does not require a CDP URL.",
    )
    parser.add_argument("--mode", choices=("offline", "target", "headed", "managed"), default="offline")
    parser.add_argument("--warmups", type=int, default=10, help="warmup iterations per cell (default: 10)")
    parser.add_argument("--samples", type=int, help=f"measured iterations per cell (default: {DEFAULT_SAMPLES}; smoke default: {SMOKE_DEFAULT_SAMPLES})")
    parser.add_argument("--smoke", action="store_true", help="run a short, explicitly non-gating smoke sample")
    parser.add_argument("--fixtures", default="small-form,dense-admin-table,dynamic-feed,nested-frame")
    parser.add_argument("--cache-states", default=",".join(DEFAULT_CACHE_STATES))
    parser.add_argument("--spaces", default="1,2,4,8")
    parser.add_argument("--artifact-dir", type=Path, help="write a complete benchmark generation")
    parser.add_argument("--browser-executable")
    parser.add_argument("--profile-dir")
    parser.add_argument("--dry-run", action="store_true", help="validate local inputs and print the plan without measuring or writing")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    samples_per_cell = args.samples if args.samples is not None else (SMOKE_DEFAULT_SAMPLES if args.smoke else DEFAULT_SAMPLES)
    try:
        if args.warmups < 0 or samples_per_cell <= 0:
            raise ValueError("warmups may be zero; samples must be positive")
        if not args.smoke and samples_per_cell < MIN_P99_SAMPLES:
            raise ValueError(f"non-smoke runs require at least {MIN_P99_SAMPLES} samples per cell; use --smoke for a short run")
        manifest_metadata, fixtures = load_fixture_bundle()
        fixture_names = parse_csv(args.fixtures, "--fixtures")
        cache_states = parse_csv(args.cache_states, "--cache-states")
        if any(state not in DEFAULT_CACHE_STATES for state in cache_states):
            raise ValueError(f"--cache-states must be drawn from {','.join(DEFAULT_CACHE_STATES)}")
        spaces = parse_positive_ints(args.spaces, "--spaces")
        selected = [fixtures[name] for name in fixture_names]
        if args.artifact_dir is not None:
            args.artifact_dir = safe_artifact_dir(args.artifact_dir)
    except (KeyError, TypeError, ValueError, OSError) as exc:
        print(f"direct benchmark error: {type(exc).__name__}", file=sys.stderr)
        return 2

    if args.dry_run:
        print(
            json.dumps(
                {
                    "mode": args.mode,
                    "action": "validate local fixtures",
                    "fixture_names": fixture_names,
                    "cache_states": cache_states,
                    "spaces": spaces,
                    "warmups": args.warmups,
                    "samples": samples_per_cell,
                    "smoke": args.smoke,
                    "tail_thresholds": {"p95": MIN_P95_SAMPLES, "p99": MIN_P99_SAMPLES},
                    "would_probe_browser": False,
                    "would_launch_browser": False,
                    "would_download_browser": False,
                    "would_write": repository_relative(args.artifact_dir) if args.artifact_dir else None,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    if args.mode in LIVE_MODES:
        missing = live_missing(args.mode, args.browser_executable, args.profile_dir)
        if missing:
            print(
                "required live benchmark probe unavailable: " + "; ".join(missing) + ". "
                "No browser was launched or downloaded; use --mode offline for the local baseline.",
                file=sys.stderr,
            )
            return 2
        print(
            "required live benchmark probe unavailable: this scaffolding only measures offline fixtures. "
            "No CDP URL is required for target mode; supply a real explicit probe implementation.",
            file=sys.stderr,
        )
        return 2

    run_nonce = new_nonce()
    rows: list[dict[str, Any]] = []
    raw_samples: list[dict[str, Any]] = []
    for record in selected:
        for cache_state in cache_states:
            for space_count in spaces:
                for _ in range(args.warmups):
                    measure_sample(record, cache_state, space_count, nonce=run_nonce)
                cell_samples = [measure_sample(record, cache_state, space_count, nonce=run_nonce) for _ in range(samples_per_cell)]
                raw_samples.extend(cell_samples)
                rows.append(summarize(cell_samples, record, cache_state, space_count))

    fixture_set_hash = _fixture_set_hash(selected)
    result: dict[str, Any] = {
        "schema_version": 1,
        "phase": 0,
        "kind": "direct-benchmark-baseline",
        "mode": "offline",
        "status": "offline-smoke" if args.smoke else "offline-baseline",
        "nonce": run_nonce,
        "fixture_set_sha256": fixture_set_hash,
        "baseline_manifest": manifest_metadata,
        "manifest_sha256": manifest_metadata["sha256"],
        "fixture_binding": {
            "manifest_path": manifest_metadata["path"],
            "manifest_sha256": manifest_metadata["sha256"],
            "fixture_set_sha256": fixture_set_hash,
            "fixtures": [
                {"name": record["name"], "file": record["file"], "sha256": record["sha256"], "bytes": record["bytes"]}
                for record in selected
            ],
        },
        "fixtures": [
            {"name": record["name"], "file": record["file"], "sha256": record["sha256"], "bytes": record["bytes"]}
            for record in selected
        ],
        "cache_states": cache_states,
        "spaces": spaces,
        "warmups": args.warmups,
        "samples_per_cell": samples_per_cell,
        "smoke": args.smoke,
        "tail_thresholds": {"p95": MIN_P95_SAMPLES, "p99": MIN_P99_SAMPLES},
        "environment": {"platform": platform.platform(), "python": platform.python_version()},
        "browser_policy": {"automatic_launch": False, "automatic_download": False, "cdp_url_used": False},
        "tokenizer": {"name": None, "version": None, "status": "not_available_in_offline_scaffold"},
        "rows": rows,
        "sample_accounting": sample_accounting(raw_samples),
        "confidence_intervals": {
            "method": "normal_approximation",
            "confidence_level": 0.95,
            "scope": "per-cell latency mean",
            "status": "approximate",
        },
    }
    add_envelope(
        result,
        kind="direct-benchmark",
        build_tuple={
            "benchmark_kind": "direct-benchmark-baseline",
            "benchmark_script": repository_relative(Path(__file__)),
            "benchmark_script_sha256": sha256_file(Path(__file__)),
            "fixture_manifest": manifest_metadata["path"],
            "fixture_manifest_sha256": manifest_metadata["sha256"],
        },
        nonce=run_nonce,
    )

    if args.artifact_dir:
        try:
            sample_chunks = raw_sample_chunks(raw_samples)
            result["raw_samples_files"] = [name for name, _ in sample_chunks]
            result["raw_sample_declarations"] = _raw_sample_declarations(sample_chunks, len(raw_samples))
            # Re-apply the central boundary after adding persistence metadata.
            result.update(redact_for_persistence(result))
            publication = publish_benchmark(args.artifact_dir, result, markdown_report(result), sample_chunks)
        except (OSError, ValueError, TypeError) as error:
            print(f"direct benchmark error: {type(error).__name__}", file=sys.stderr)
            return 2
        print(
            f"wrote offline benchmark baseline: {repository_relative(args.artifact_dir)} "
            f"({len(rows)} cells, {len(raw_samples)} samples, {publication['generation_id']})"
        )
    else:
        print(json.dumps(redact_for_persistence(result), indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
