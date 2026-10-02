
"""Run the Phase 0 direct-interface benchmark scaffold.

Offline mode measures deterministic local fixture parsing and serialization. It
never opens a browser, downloads dependencies, or consumes a CDP endpoint. The
other modes are explicit live lanes and fail closed until a real probe is wired.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import shutil
import subprocess
import sys
import time
from html.parser import HTMLParser
from pathlib import Path
from statistics import mean
from typing import Any, ClassVar

from artifact_envelope import envelope as add_envelope
from artifact_envelope import write_bytes_atomic, write_json_atomic

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "browser-task-spaces"
MANIFEST_PATH = FIXTURE_ROOT / "manifest.json"
LIVE_MODES = {"target", "headed", "managed"}
MIN_P95_SAMPLES = 200
MIN_P99_SAMPLES = 1_000
DEFAULT_SAMPLES = MIN_P99_SAMPLES
SMOKE_DEFAULT_SAMPLES = 30


class ControlCounter(HTMLParser):
    """Count controls and recursively inspect inline ``srcdoc`` frames."""

    CONTROL_TAGS: ClassVar[set[str]] = {"a", "button", "input", "select", "textarea"}

    def __init__(self, frame_depth: int = 0) -> None:
        super().__init__()
        self.controls = 0
        self.frames = 0
        self.frames_scanned = 0
        self.max_frame_depth = frame_depth
        self.text_chars = 0
        self.frame_depth = frame_depth

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.CONTROL_TAGS:
            self.controls += 1
        if tag != "iframe":
            return

        self.frames += 1
        attributes = dict(attrs)
        srcdoc = attributes.get("srcdoc")
        if srcdoc is None:
            return

        child = ControlCounter(self.frame_depth + 1)
        child.feed(srcdoc)
        child.close()
        self.controls += child.controls
        self.frames += child.frames
        self.frames_scanned += 1 + child.frames_scanned
        self.max_frame_depth = max(self.max_frame_depth, child.max_frame_depth)
        self.text_chars += child.text_chars

    def handle_data(self, data: str) -> None:
        self.text_chars += len(data)

    @property
    def frame_coverage(self) -> float | None:
        if self.frames == 0:
            return None
        return self.frames_scanned / self.frames


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def baseline_manifest_metadata() -> dict[str, Any]:
    manifest = read_json(MANIFEST_PATH)
    return {
        "path": str(MANIFEST_PATH.relative_to(ROOT)),
        "sha256": sha256_file(MANIFEST_PATH),
        "schema_version": manifest.get("schema_version"),
        "fixture_set": manifest.get("fixture_set"),
    }


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid or missing fixture manifest {path}: {exc}") from exc


def load_fixtures() -> dict[str, dict[str, Any]]:
    manifest = read_json(MANIFEST_PATH)
    records: dict[str, dict[str, Any]] = {}
    for item in manifest.get("fixtures", []):
        name = item.get("name")
        relative = item.get("file")
        if not isinstance(name, str) or not isinstance(relative, str):
            raise TypeError("fixture entries need string name and file")
        path = FIXTURE_ROOT / relative
        if path.parent != FIXTURE_ROOT or not path.is_file():
            raise ValueError(f"fixture {name!r} is not a local file")
        records[name] = {
            "name": name,
            "file": relative,
            "path": path,
            "sha256": sha256_file(path),
            "bytes": path.stat().st_size,
        }
    if not records:
        raise ValueError("fixture manifest is empty")
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
            missing.append(f"browser executable {browser_executable!r}")
        if not profile_dir:
            missing.append("--profile-dir for managed mode")
        elif not Path(profile_dir).is_dir():
            missing.append(f"existing profile directory {profile_dir!r}")
    return missing


def parse_csv(value: str, label: str) -> list[str]:
    values = [part.strip() for part in value.split(",") if part.strip()]
    if not values:
        raise ValueError(f"{label} cannot be empty")
    return values


def parse_positive_ints(value: str, label: str) -> list[int]:
    try:
        values = [int(part) for part in parse_csv(value, label)]
    except ValueError as exc:
        raise ValueError(f"{label} must be comma-separated positive integers") from exc
    if any(value <= 0 for value in values):
        raise ValueError(f"{label} must be comma-separated positive integers")
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
    read_done = time.perf_counter_ns()
    parser = ControlCounter()
    parser.feed(raw.decode("utf-8"))
    parser.close()
    metadata_done = time.perf_counter_ns()
    # This is a local, side-effect-free stand-in for an actionable operation.
    first_control = parser.controls > 0
    action_done = time.perf_counter_ns()
    summary = {
        "fixture": record["name"],
        "cache_state": cache_state,
        "spaces": spaces,
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
        "fixture_chars": len(raw.decode("utf-8")),
        "serialized_bytes": summary["serialized_bytes"],
        "dom_scans": 0 if cache_state == "clean" else 1,
        "actionable_controls": parser.controls,
        "frames": parser.frames,
        "frames_scanned": parser.frames_scanned,
        "frame_coverage": parser.frame_coverage,
        "max_frame_depth": parser.max_frame_depth,
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
    "metadata_ms",
    "synthetic_action_ms",
    "wait_ms",
    "total_ms",
)


def validate_sample(sample: dict[str, Any]) -> str | None:
    for key in REQUIRED_SAMPLE_METRICS:
        value = sample.get(key)
        if not isinstance(value, (int, float)) or not math.isfinite(float(value)) or float(value) < 0:
            return f"missing or invalid metric: {key}"
    coverage = sample.get("frame_coverage")
    if coverage is not None and (
        not isinstance(coverage, (int, float))
        or not math.isfinite(float(coverage))
        or not 0.0 <= float(coverage) <= 1.0
    ):
        return "frame coverage is outside [0, 1]"
    return None


def measure_sample(record: dict[str, Any], cache_state: str, spaces: int) -> dict[str, Any]:
    try:
        sample = measure_once(record, cache_state, spaces)
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        return sample_error(record, cache_state, spaces, exc)
    invalid_reason = validate_sample(sample)
    if invalid_reason is not None:
        sample["sample_status"] = "invalid"
        sample["invalid_reason"] = invalid_reason
    return sample


def sample_accounting(samples: list[dict[str, Any]]) -> dict[str, int]:
    statuses = [sample.get("sample_status") for sample in samples]
    valid = statuses.count("valid")
    errors = statuses.count("error")
    invalid = len(statuses) - valid - errors
    return {"attempted": len(samples), "valid": valid, "errors": errors, "invalid": invalid}


def summarize(samples: list[dict[str, Any]], record: dict[str, Any], cache_state: str, spaces: int) -> dict[str, Any]:
    def values(key: str) -> list[float]:
        return [float(sample[key]) for sample in samples if sample.get("sample_status") == "valid"]

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
            "first_useful_action_ms": latency_stats("first_useful_action_ms", "synthetic_offline_control_presence_check"),
            "metadata_ms": latency_stats("metadata_ms"),
            "action_ms": latency_stats("action_ms", "synthetic_offline_control_presence_check"),
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
    lines.extend(["", "Offline action timings are synthetic control-presence checks, not browser clicks. Live-only resource, event, reconnect, and human-tab metrics are null.", ""])
    return "\n".join(lines)


MAX_RAW_SAMPLE_FILE_BYTES = 7 * 1024 * 1024


def raw_sample_chunks(samples: list[dict[str, Any]]) -> list[tuple[str, bytes]]:
    chunks: list[bytes] = []
    current = bytearray()
    for sample in samples:
        line = (json.dumps(sample, separators=(",", ":"), sort_keys=True) + "\n").encode("utf-8")
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
    resolved = requested.resolve()
    artifacts = (ROOT / "artifacts").resolve()
    try:
        resolved.relative_to(artifacts)
    except ValueError as error:
        raise ValueError("artifact directory must be inside artifacts/") from error
    if resolved == artifacts:
        raise ValueError("artifact directory must be a child of artifacts/")
    return resolved


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
    parser.add_argument("--cache-states", default="cold,clean,dirty,resync")
    parser.add_argument("--spaces", default="1,2,4,8")
    parser.add_argument("--artifact-dir", type=Path, help="write baseline.json, baseline.md, and raw_samples.jsonl")
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
        fixtures = load_fixtures()
        fixture_names = parse_csv(args.fixtures, "--fixtures")
        cache_states = parse_csv(args.cache_states, "--cache-states")
        spaces = parse_positive_ints(args.spaces, "--spaces")
        selected = [fixtures[name] for name in fixture_names]
        if args.artifact_dir is not None:
            args.artifact_dir = safe_artifact_dir(args.artifact_dir)
    except (KeyError, TypeError, ValueError) as exc:
        print(f"direct benchmark error: {exc}", file=sys.stderr)
        return 2

    if args.dry_run:
        print(json.dumps({
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
            "would_write": str(args.artifact_dir) if args.artifact_dir else None,
        }, indent=2, sort_keys=True))
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

    fixture_set_hash = hashlib.sha256(
        "\n".join(f"{record['name']}:{record['sha256']}" for record in selected).encode("utf-8")
    ).hexdigest()
    rows: list[dict[str, Any]] = []
    raw_samples: list[dict[str, Any]] = []
    for record in selected:
        for cache_state in cache_states:
            for space_count in spaces:
                for _ in range(args.warmups):
                    measure_sample(record, cache_state, space_count)
                cell_samples = [measure_sample(record, cache_state, space_count) for _ in range(samples_per_cell)]
                raw_samples.extend(cell_samples)
                rows.append(summarize(cell_samples, record, cache_state, space_count))

    result = {
        "schema_version": 1,
        "phase": 0,
        "kind": "direct-benchmark-baseline",
        "mode": "offline",
        "fixture_set_sha256": fixture_set_hash,
        "baseline_manifest": baseline_manifest_metadata(),
        "fixtures": [{"name": record["name"], "file": record["file"], "sha256": record["sha256"], "bytes": record["bytes"]} for record in selected],
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
        "status": "offline-smoke" if args.smoke else "offline-baseline",
    }

    if args.artifact_dir:
        try:
            sample_chunks = raw_sample_chunks(raw_samples)
            result["raw_samples_files"] = [name for name, _ in sample_chunks]
            add_envelope(result, kind="direct-benchmark")
            args.artifact_dir.mkdir(parents=True, exist_ok=True)
            for old_path in args.artifact_dir.glob("raw_samples*.jsonl"):
                old_path.unlink()
            write_json_atomic(args.artifact_dir / "baseline.json", result)
            (args.artifact_dir / "baseline.md").write_text(markdown_report(result), encoding="utf-8")
            for name, content in sample_chunks:
                write_bytes_atomic(args.artifact_dir / name, content, max_bytes=MAX_RAW_SAMPLE_FILE_BYTES)
        except (OSError, ValueError) as error:
            print(f"direct benchmark error: {type(error).__name__}", file=sys.stderr)
            return 2
        print(f"wrote offline benchmark baseline: {args.artifact_dir} ({len(rows)} cells, {len(raw_samples)} samples)")
    else:
        print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
