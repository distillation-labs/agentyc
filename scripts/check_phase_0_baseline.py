#!/usr/bin/env python3
"""Validate the Phase 0 baseline report and evidence gates.

This checker is read-only.  It validates the report's local schema and the
artifact layout, but it does not turn static, offline, smoke, or optional probe
results into live evidence.  Output contains only repository-relative paths.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BASELINE_REL = Path("research/phase-0-baseline.md")
ARTIFACT_ROOT_REL = Path("artifacts")
MAX_READ_BYTES = 8 * 1024 * 1024


REQUIRED_BASELINE_HEADINGS = (
    "## 1. Build tuple",
    "## 2. Exact tool counts",
    "## 3. CLI/runtime launch behavior",
    "## 4. Global close behavior",
    "## 5. Raw-ID output examples, redacted",
    "## 6. Known false-green test paths",
    "## 7. Validation results",
)

REQUIRED_CURRENT_FILES = (
    "environment.txt",
    "locked-package-versions.txt",
    "chrome-discovery.txt",
    "tool-counts.json",
    "default-launch-probe.json",
    "cli-run-close-latency.json",
    "browser-raw-id-close-all.json",
    "global-close-process-isolation.json",
    "test-path-inventory.txt",
)

EVIDENCE_PATH_RE = re.compile(r"`([^`]+)`")
ABSOLUTE_USER_PATH_RE = re.compile(
    r"(?i)(?:^|[\s\"'])/(?:Users|home|private|tmp|var)(?:/|[\s\"'])"
    r"|(?:^|[\s\"'])[A-Z]:[\\/]|(?:^|[\s\"'])file://(?!<REDACTED>|<REPO_ROOT>)"
)
SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)(?:token|secret|password|passwd|cookie|authorization|credential|private[_-]?key)"
    r"\s*[:=]\s*(?!false\b|true\b|null\b|\"<redacted>\"|\"\[REDACTED)"
    r"[^\s,}\]]+"
)
RAW_ID_RE = re.compile(
    r"(?i)(?:tab[_-]?id|target[_-]?id|targetid|session[_-]?id|sessionid|current[_-]?tab[_-]?id|"
    r"group[_-]?id|cdp[_-]?id|backend[_-]?node[_-]?id|raw[_-]?id|websocket[_-]?url|"
    r"debugger[_-]?endpoint|extension[_-]?id)\"?\s*[:=]\s*"
    r"\"?(?!\[REDACTED|<redacted>|null\b|false\b|true\b|not[_-])"
    r"[A-Za-z0-9_:/.-]{8,}"
)

SUCCESS_STATUSES = {"passed", "pass", "success", "completed", "live_passed"}
LIVE_SUCCESS_STATUSES = {"passed", "success", "completed", "live_passed", "live_baseline", "live-baseline"}


class Issue:
    """A safe, bounded validation result."""

    __slots__ = ("code", "detail", "gate", "path")

    def __init__(self, code: str, detail: str, *, path: str = "", gate: str = "schema") -> None:
        self.code = code
        self.path = path
        self.detail = detail
        self.gate = gate

    def as_dict(self) -> dict[str, str]:
        result = {"code": self.code, "gate": self.gate, "detail": self.detail}
        if self.path:
            result["path"] = self.path
        return result


class Checker:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.issues: list[Issue] = []

    def relative(self, path: Path) -> str:
        """Return a safe repository-relative path, never an absolute path."""
        try:
            relative = path.resolve().relative_to(self.root)
        except (OSError, ValueError):
            return "<out-of-root>"
        return relative.as_posix() or "."

    def add(self, code: str, detail: str, path: Path | None = None, *, gate: str = "schema") -> None:
        self.issues.append(
            Issue(code, detail, path=self.relative(path) if path is not None else "", gate=gate)
        )

    def safe_path(self, relative: str | Path) -> Path | None:
        relative_path = Path(relative)
        if relative_path.is_absolute() or ".." in relative_path.parts:
            self.add("path-out-of-root", "artifact path is outside the repository root", gate="safety")
            return None
        candidate = self.root / relative_path
        current = self.root
        for component in relative_path.parts:
            current = current / component
            if current.is_symlink():
                self.add("symlink-path-component", "artifact path contains a symlink component", candidate, gate="safety")
                return None
        try:
            resolved = candidate.resolve()
            resolved.relative_to(self.root)
        except (OSError, ValueError):
            self.add("path-out-of-root", "artifact path is outside the repository root", gate="safety")
            return None
        return resolved

    def read_text(self, path: Path, *, gate: str = "schema") -> str | None:
        safe = self.safe_path(path.relative_to(self.root))
        if safe is None:
            return None
        try:
            if safe.is_symlink():
                self.add("symlink-artifact", "artifact must not be a symlink", safe, gate="safety")
                return None
            if safe.stat().st_size > MAX_READ_BYTES:
                self.add("file-too-large", "artifact exceeds the bounded read limit", safe, gate=gate)
                return None
            return safe.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            self.add("file-unreadable", "artifact is missing or unreadable", safe, gate=gate)
            return None

    def read_json(self, path: Path, *, gate: str = "schema") -> Any | None:
        text = self.read_text(path, gate=gate)
        if text is None:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            self.add("invalid-json", "JSON artifact is not valid JSON", path, gate=gate)
            return None



def nested(data: Any, *keys: str) -> Any:
    current = data
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def contains_success(data: Any, keys: tuple[str, ...]) -> bool:
    """Find a success marker in a small structured report without trusting prose."""
    if isinstance(data, dict):
        for key in keys:
            value = data.get(key)
            if isinstance(value, str) and value.lower() in SUCCESS_STATUSES:
                return True
            if value is True:
                return True
        return any(contains_success(value, keys) for value in data.values())
    if isinstance(data, list):
        return any(contains_success(value, keys) for value in data)
    return False


def has_marker(data: Any, alternatives: tuple[str, ...]) -> bool:
    """Require an explicit boolean or success-valued evidence marker."""
    if isinstance(data, dict):
        for key, value in data.items():
            if key.lower() in alternatives and (value is True or (isinstance(value, str) and value.lower() in SUCCESS_STATUSES)):
                return True
            if has_marker(value, alternatives):
                return True
    elif isinstance(data, list):
        return any(has_marker(value, alternatives) for value in data)
    return False


def validate_baseline_document(checker: Checker) -> None:
    path = checker.safe_path(BASELINE_REL)
    if path is None:
        return
    text = checker.read_text(path)
    if text is None:
        return

    if not text.startswith("# Phase 0 baseline"):
        checker.add("baseline-title-missing", "baseline document has no Phase 0 title", path)
    for heading in REQUIRED_BASELINE_HEADINGS:
        if heading not in text:
            checker.add("baseline-section-missing", "required baseline section is missing", path)

    evidence_lines = [line for line in text.splitlines() if line.strip().startswith("Evidence:")]
    if len(evidence_lines) < 5:
        checker.add("baseline-evidence-sparse", "baseline needs at least five Evidence lines", path)
    else:
        for line in evidence_lines:
            for raw_reference in EVIDENCE_PATH_RE.findall(line):
                reference = raw_reference.strip().rstrip(",.;")
                reference = re.sub(r":\d+(?:-\d+)?$", "", reference)
                if not (
                    reference.startswith(("artifacts/", "crates/", "tests/", "extension/", "scripts/"))
                    or reference.endswith((".json", ".txt", ".log", ".rs", ".md"))
                ):
                    continue
                reference_path = Path(reference)
                if reference_path.is_absolute() or ".." in reference_path.parts:
                    checker.add(
                        "evidence-path-unbounded",
                        "baseline evidence reference is not repository-relative",
                        path,
                        gate="safety",
                    )
                    continue
                evidence_path = checker.safe_path(reference_path)
                if evidence_path is not None and not evidence_path.is_file() and "/" not in reference:
                    candidate = checker.safe_path(ARTIFACT_ROOT_REL / "p0-current" / reference_path)
                    if candidate is not None and candidate.is_file():
                        evidence_path = candidate
                if evidence_path is not None and not evidence_path.is_file():
                    checker.add(
                        "evidence-missing",
                        "baseline names evidence that is not present",
                        evidence_path,
                        gate="evidence",
                    )

    known_blocker_markers = (
        "known false-green",
        "browser-unavailable",
        "does not require Chrome",
        "ignored",
        "not run against",
    )
    if sum(marker.lower() in text.lower() for marker in known_blocker_markers) < 3:
        checker.add(
            "known-blockers-undocumented",
            "baseline must identify false-green or unavailable-live paths",
            path,
            gate="evidence",
        )
    if "redacted" not in text.lower():
        checker.add("redaction-disclosure-missing", "baseline does not disclose redacted output", path, gate="safety")


def validate_current_artifacts(checker: Checker) -> None:
    directory = checker.safe_path(ARTIFACT_ROOT_REL / "p0-current")
    if directory is None:
        return
    if not directory.is_dir():
        checker.add("artifact-dir-missing", "current baseline artifact directory is missing", directory, gate="evidence")
        return

    for filename in REQUIRED_CURRENT_FILES:
        path = directory / filename
        if not path.is_file():
            checker.add("current-evidence-missing", "required current-baseline evidence is missing", path, gate="evidence")

    tool_counts = checker.read_json(directory / "tool-counts.json")
    if isinstance(tool_counts, dict):
        for profile, expected in (("default", 61), ("extended", 76)):
            value = nested(tool_counts, profile, "tool_count")
            if value != expected:
                checker.add("tool-count-schema", "advertised tool count does not match the Phase 0 baseline", directory / "tool-counts.json")

    close_report = checker.read_json(directory / "global-close-process-isolation.json")
    if isinstance(close_report, dict):
        if close_report.get("status") != "completed":
            checker.add("close-evidence-incomplete", "global-close isolation report is not completed", directory / "global-close-process-isolation.json")
        if not isinstance(close_report.get("owned_processes_before_close_all"), int) or not isinstance(
            close_report.get("owned_processes_one_second_after_close_all"), int
        ):
            checker.add("close-evidence-schema", "global-close report lacks bounded process counts", directory / "global-close-process-isolation.json")

    latency = checker.read_json(directory / "cli-run-close-latency.json")
    if isinstance(latency, dict):
        runs = latency.get("runs")
        if not isinstance(runs, list) or not runs or any(
            not isinstance(run, dict) or run.get("exit_code") != 0 for run in runs
        ):
            checker.add("latency-evidence-schema", "CLI latency report lacks successful runs", directory / "cli-run-close-latency.json")

    raw_ids = checker.read_json(directory / "browser-raw-id-close-all.json")
    if isinstance(raw_ids, dict) and "redact" not in json.dumps(raw_ids, sort_keys=True).lower():
        checker.add("raw-id-redaction-missing", "raw browser identifier evidence is not marked redacted", directory / "browser-raw-id-close-all.json", gate="safety")


def validate_artifact_safety(checker: Checker) -> None:
    artifact_root = checker.safe_path(ARTIFACT_ROOT_REL)
    if artifact_root is None or not artifact_root.is_dir():
        return
    for path in sorted(artifact_root.rglob("*")):
        if path.is_symlink():
            checker.add("symlink-artifact", "artifact tree must not contain symlinks", path, gate="safety")
            continue
        if not path.is_file():
            continue
        try:
            if path.stat().st_size > MAX_READ_BYTES:
                checker.add("file-too-large", "artifact exceeds the bounded read limit", path, gate="safety")
                continue
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            checker.add("file-unreadable", "artifact is missing or unreadable", path, gate="safety")
            continue
        if "\x00" in text:
            checker.add("artifact-nul", "artifact contains NUL data", path, gate="safety")
        if SECRET_ASSIGNMENT_RE.search(text):
            checker.add("artifact-secret-like-value", "artifact contains a secret-like assignment", path, gate="safety")
        if ABSOLUTE_USER_PATH_RE.search(text):
            checker.add("artifact-path-not-redacted", "artifact contains an unredacted user path", path, gate="safety")
        if RAW_ID_RE.search(text):
            checker.add("artifact-raw-id", "artifact contains an unredacted browser identifier", path, gate="safety")


def validate_artifact_envelope(checker: Checker) -> None:
    """Require the common envelope on structured Phase 0 report artifacts."""
    artifact_root = checker.safe_path(ARTIFACT_ROOT_REL)
    if artifact_root is None or not artifact_root.is_dir():
        return
    for path in sorted(artifact_root.rglob("*.json")):
        if path.is_symlink():
            continue
        data = checker.read_json(path, gate="schema")
        if not isinstance(data, dict):
            continue
        required = ("schema_version", "build_tuple", "environment", "timestamp", "command", "result", "redaction_status")
        missing = [key for key in required if key not in data]
        if missing:
            checker.add("artifact-envelope-missing", "structured artifact is missing the common envelope", path)
            continue
        if data.get("schema_version") != 1 or not isinstance(data.get("build_tuple"), dict) or not isinstance(data.get("environment"), dict):
            checker.add("artifact-envelope-schema", "structured artifact envelope has invalid types", path)
        if not isinstance(data.get("timestamp"), str) or not data["timestamp"].endswith("Z"):
            checker.add("artifact-envelope-timestamp", "structured artifact timestamp is not UTC", path)
        if not isinstance(data.get("command"), list) or any(not isinstance(item, str) for item in data["command"]):
            checker.add("artifact-envelope-command", "structured artifact command is not a bounded argv list", path)
        redaction = data.get("redaction_status")
        if not isinstance(redaction, dict) or redaction.get("status") != "applied" or any(redaction.get(key) is not False for key in ("raw_browser_ids", "secrets", "absolute_paths", "page_bodies")):
            checker.add("artifact-envelope-redaction", "structured artifact does not prove redaction was applied", path, gate="safety")


def validate_extension_gate(checker: Checker) -> str:
    path = checker.safe_path(ARTIFACT_ROOT_REL / "p0-extension" / "report.json")
    if path is None or not path.is_file():
        checker.add("live-chrome-evidence-missing", "extension report is missing; live Chrome is not observed", path, gate="live_chrome")
        return "missing"
    report = checker.read_json(path, gate="live_chrome")
    if not isinstance(report, dict):
        checker.add("live-chrome-report-invalid", "extension report is not a JSON object", path, gate="live_chrome")
        return "invalid"

    live = report.get("live")
    safety = report.get("safety")
    if report.get("probe") != "P0-T2":
        checker.add("live-chrome-report-schema", "extension report is not the P0-T2 probe schema", path, gate="live_chrome")
    if report.get("mode") != "headed" or report.get("status") != "live_passed":
        checker.add("live-chrome-gate-missing", "only a headed live_passed extension probe can close the Chrome gate", path, gate="live_chrome")
    if not isinstance(live, dict) or live.get("requested") is not True or live.get("required") is not True or live.get("status") != "live_passed":
        checker.add("live-chrome-gate-missing", "extension report does not prove a required live Chrome run", path, gate="live_chrome")
    if not isinstance(report.get("handshake_transcript"), list) or not report["handshake_transcript"]:
        checker.add("live-chrome-evidence-incomplete", "extension report has no handshake transcript", path, gate="live_chrome")
    required_markers = (
        ("extension_loaded", "extension_installed", "extension_connected"),
        ("debugger_command_passed", "debugger_command", "debugger_attached"),
        ("event_received", "debugger_event_received", "event"),
        ("tab_group_created", "tab_group"),
        ("native_messaging_passed", "native_messaging", "handshake"),
    )
    if any(not has_marker(report, alternatives) for alternatives in required_markers):
        checker.add("live-chrome-evidence-incomplete", "extension report lacks explicit extension, debugger, event, tab-group, or Native Messaging evidence", path, gate="live_chrome")
    if not isinstance(safety, dict) or any(safety.get(key) is not False for key in ("default_chrome_launch", "default_profile_mutation", "raw_ids_logged", "secrets_logged")):
        checker.add("live-chrome-safety-schema", "extension report lacks the required safety assertions", path, gate="live_chrome")
    return "passed" if not any(issue.gate == "live_chrome" for issue in checker.issues) else "missing"


def validate_native_protocol_gate(checker: Checker) -> str:
    directory = checker.safe_path(ARTIFACT_ROOT_REL / "p0-native-protocol")
    if directory is None or not directory.is_dir():
        checker.add("native-protocol-dir-missing", "Native Messaging artifact directory is missing", directory, gate="live_native")
        return "missing"
    reports = sorted(directory.glob("*.json"))
    if not reports:
        checker.add("native-protocol-evidence-missing", "Native Messaging directory has no JSON evidence", directory, gate="live_native")
        return "missing"
    live_required = False
    offline_schema = False
    for path in reports:
        data = checker.read_json(path, gate="live_native")
        if not isinstance(data, dict):
            continue
        offline = data.get("offline")
        if isinstance(offline, dict) and isinstance(offline.get("cases"), dict) and isinstance(offline.get("limits"), dict):
            offline_schema = True
        live = data.get("live")
        if data.get("status") == "live_passed" and isinstance(live, dict) and live.get("required") is True and live.get("status") == "passed":
            live_required = True
    if not offline_schema:
        checker.add("native-protocol-schema", "Native Messaging offline case and limit schema is missing", directory, gate="evidence")
    if not live_required:
        checker.add("native-protocol-live-missing", "required live Native Messaging handshake evidence is absent", directory, gate="live_native")
    return "passed" if live_required else "missing"


def validate_directory_gate(checker: Checker, name: str, *, gate: str, description: str) -> str:
    directory = checker.safe_path(ARTIFACT_ROOT_REL / name)
    if directory is None or not directory.is_dir():
        checker.add("artifact-dir-missing", f"{description} artifact directory is missing", directory, gate=gate)
        return "missing"
    files = [path for path in directory.rglob("*") if path.is_file() and not path.is_symlink()]
    if not files:
        checker.add("artifact-evidence-missing", f"{description} artifact directory has no files", directory, gate=gate)
        return "missing"
    return "present"


def validate_coexistence_gate(checker: Checker) -> str:
    status = validate_directory_gate(checker, "p0-coexistence", gate="coexistence", description="coexistence")
    if status == "missing":
        return status
    directory = checker.safe_path(ARTIFACT_ROOT_REL / "p0-coexistence")
    assert directory is not None
    reports = sorted(directory.rglob("*.json"))
    if not reports:
        checker.add("coexistence-report-missing", "coexistence directory has no structured report", directory, gate="coexistence")
        return "missing"
    passed = False
    for path in reports:
        data = checker.read_json(path, gate="coexistence")
        if not isinstance(data, dict):
            continue
        live = data.get("live")
        spaces = data.get("spaces", nested(data, "scenario", "spaces"))
        agents = data.get("agents", nested(data, "scenario", "agents"))
        safety = data.get("safety")
        safety_zero = isinstance(safety, dict) and all(
            safety.get(key) == 0
            for key in ("user_tab_closes", "focus_theft", "cross_space_mutations")
        )
        live_ok = isinstance(live, dict) and live.get("requested") is True and live.get("required") is True and live.get("status") in LIVE_SUCCESS_STATUSES
        if (
            data.get("status") in LIVE_SUCCESS_STATUSES
            and str(data.get("mode", "")).lower() in {"headed", "existing-chrome", "existing_chrome"}
            and live_ok
            and isinstance(spaces, int)
            and spaces >= 2
            and isinstance(agents, int)
            and agents >= 2
            and safety_zero
        ):
            passed = True
    if not passed:
        checker.add("coexistence-live-missing", "two-space/two-agent headed coexistence proof is absent", directory, gate="coexistence")
        return "missing"
    return "passed"


def validate_installation_gate(checker: Checker) -> str:
    status = validate_directory_gate(checker, "p0-installation", gate="installation", description="installation")
    if status == "missing":
        return status
    directory = checker.safe_path(ARTIFACT_ROOT_REL / "p0-installation")
    assert directory is not None
    reports = sorted(directory.rglob("*.json"))
    passed = False
    for path in reports:
        data = checker.read_json(path, gate="installation")
        if not isinstance(data, dict):
            continue
        evidence = data.get("evidence")
        installation = data.get("installation")
        rollback = data.get("rollback")
        platform_data = evidence.get("platform") if isinstance(evidence, dict) else None
        registration = evidence.get("registration") if isinstance(evidence, dict) else None
        platform_name = platform_data.get("name") if isinstance(platform_data, dict) else None
        registration_scope = registration.get("scope") if isinstance(registration, dict) else None
        if (
            data.get("status") == "drill_passed"
            and isinstance(installation, dict)
            and installation.get("status") == "installed"
            and isinstance(rollback, dict)
            and rollback.get("status") == "rolled_back"
            and platform_name in {"Darwin", "macOS"}
            and registration_scope == "user-level"
        ):
            passed = True
    if not passed:
        checker.add("installation-live-missing", "successful macOS user-level installation and rollback evidence is absent", directory, gate="installation")
        return "missing"
    return "passed"


def performance_report_passed(data: dict[str, Any]) -> bool:
    mode = str(data.get("mode", "")).lower()
    if mode not in {"headed", "target", "managed", "existing-chrome", "existing_chrome", "live"}:
        return False
    if data.get("status") not in LIVE_SUCCESS_STATUSES:
        return False
    rows = data.get("rows")
    if not isinstance(rows, list) or not rows:
        return False
    for row in rows:
        if not isinstance(row, dict):
            return False
        samples = row.get("samples")
        tails = row.get("tail_gates")
        live_only = row.get("live_only")
        valid_samples = samples.get("valid") if isinstance(samples, dict) else None
        if not isinstance(valid_samples, int) or isinstance(valid_samples, bool) or valid_samples < 1_000:
            return False
        if not isinstance(tails, dict) or any(
            nested(tails, key, "status") != "gateable" for key in ("p95", "p99")
        ):
            return False
        if not isinstance(live_only, dict) or live_only.get("status") == "not_measured_offline":
            return False
        if not any(
            isinstance(live_only.get(key), (int, float)) and not isinstance(live_only.get(key), bool)
            for key in ("chrome_cpu_percent", "chrome_rss_bytes", "host_rss_bytes", "event_lag_ms", "human_tab_responsiveness_ms")
        ):
            return False
    return True


def validate_performance_gate(checker: Checker) -> str:
    status = validate_directory_gate(checker, "p0-performance", gate="performance", description="full performance")
    if status == "missing":
        smoke = checker.safe_path(ARTIFACT_ROOT_REL / "p0-performance-smoke")
        if smoke is not None and smoke.is_dir():
            checker.add("performance-smoke-only", "offline smoke evidence cannot close the full performance gate", smoke, gate="performance")
        return "missing"
    directory = checker.safe_path(ARTIFACT_ROOT_REL / "p0-performance")
    assert directory is not None
    reports = sorted(directory.rglob("*.json"))
    if not any(
        isinstance(data := checker.read_json(path, gate="performance"), dict) and performance_report_passed(data)
        for path in reports
    ):
        checker.add("performance-live-missing", "full live performance evidence with gateable tails and resource metrics is absent", directory, gate="performance")
        return "missing"
    return "passed"


def run(root: Path) -> dict[str, Any]:
    checker = Checker(root)
    validate_baseline_document(checker)
    validate_current_artifacts(checker)
    validate_artifact_safety(checker)
    validate_artifact_envelope(checker)
    gates = {
        "live_chrome": validate_extension_gate(checker),
        "live_native_messaging": validate_native_protocol_gate(checker),
        "coexistence": validate_coexistence_gate(checker),
        "installation": validate_installation_gate(checker),
        "performance": validate_performance_gate(checker),
    }
    # Only report a path if it is bounded and repository-relative.  De-duplicate
    # repeated checks so the output remains useful on large artifact trees.
    unique: list[Issue] = []
    seen: set[tuple[str, str, str, str]] = set()
    for issue in checker.issues:
        key = (issue.code, issue.gate, issue.path, issue.detail)
        if key not in seen:
            unique.append(issue)
            seen.add(key)
    checker.issues = unique
    return {
        "schema_version": 1,
        "status": "pass" if not checker.issues else "blocked",
        "baseline": "validated" if not any(issue.gate == "schema" for issue in checker.issues) else "invalid",
        "gates": gates,
        "blockers": [issue.as_dict() for issue in checker.issues],
        "redaction": {
            "output_paths": "repository-relative",
            "absolute_paths_emitted": False,
            "artifact_contents_emitted": False,
        },
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "baseline",
        nargs="?",
        help="optional repository-relative baseline path; kept for manifest command compatibility",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=ROOT,
        help="repository root to validate; output remains relative to this root",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = args.root.resolve()
    if args.baseline:
        baseline = (root / args.baseline).resolve() if not Path(args.baseline).is_absolute() else Path(args.baseline).resolve()
        try:
            baseline.relative_to(root)
        except ValueError:
            print(json.dumps({"status": "blocked", "blockers": [{"code": "baseline-out-of-root", "gate": "safety", "detail": "baseline path must remain inside the repository root"}], "redaction": {"absolute_paths_emitted": False}}, sort_keys=True))
            return 2
        if baseline != (root / BASELINE_REL).resolve():
            print(json.dumps({"status": "blocked", "blockers": [{"code": "baseline-path-mismatch", "gate": "schema", "detail": "baseline path must be research/phase-0-baseline.md"}], "redaction": {"absolute_paths_emitted": False}}, sort_keys=True))
            return 2
    if not root.is_dir():
        print(json.dumps({"status": "blocked", "blockers": [{"code": "root-missing", "gate": "schema", "detail": "repository root is missing"}], "redaction": {"absolute_paths_emitted": False}}, sort_keys=True))
        return 2
    result = run(root)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
