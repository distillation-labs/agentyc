#!/usr/bin/env python3
"""Validate the Phase 0 baseline report and evidence gates.

This checker is read-only.  It validates the report's local schema and the
artifact layout, but it does not turn static, offline, smoke, or optional probe
results into live evidence.  Output contains only repository-relative paths.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import struct
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BASELINE_REL = Path("research/phase-0-baseline.md")
ARTIFACT_ROOT_REL = Path("artifacts")
MAX_READ_BYTES = 8 * 1024 * 1024
MAX_FRESHNESS_SECONDS = 7 * 24 * 60 * 60
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
NONCE_RE = re.compile(r"^[0-9a-f]{16,128}$")
GENERATION_MANIFEST_NAME = "generation-manifest.json"
COMMIT_MARKER_NAME = "COMMIT"
PRIVATE_ARTIFACT_NAMES = {".install-owner", ".install-journal", ".install-lock"}
MAX_EXTENSION_FILES = 64
MAX_EXTENSION_BYTES = 8 * 1024 * 1024


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



def extension_tree_sha256(directory: Path) -> str:
    root = Path(directory)
    if not root.is_dir() or root.is_symlink():
        raise ValueError("probe extension tree is not a real directory")
    files: list[tuple[str, bytes]] = []
    total = 0
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        if path.is_symlink():
            raise ValueError("probe extension tree contains a symlink")
        if path.is_dir():
            continue
        if not path.is_file():
            raise ValueError("probe extension tree contains a non-file")
        size = path.stat().st_size
        if len(files) >= MAX_EXTENSION_FILES or total + size > MAX_EXTENSION_BYTES:
            raise ValueError("probe extension tree exceeds the bounded limit")
        data = path.read_bytes()
        if len(data) != size:
            raise ValueError("probe extension tree changed while being hashed")
        total += len(data)
        files.append((path.relative_to(root).as_posix(), data))
    if not files:
        raise ValueError("probe extension tree is empty")
    digest = hashlib.sha256()
    for relative, data in files:
        encoded = relative.encode("utf-8")
        digest.update(struct.pack(">I", len(encoded)))
        digest.update(encoded)
        digest.update(struct.pack(">Q", len(data)))
        digest.update(data)
    return digest.hexdigest()


def nested(data: Any, *keys: str) -> Any:
    current = data
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def contains_success(data: Any, keys: tuple[str, ...]) -> bool:
    """Check only the supplied object; nested prose cannot forge success."""
    if not isinstance(data, dict):
        return False
    return any(
        data.get(key) is True
        or (isinstance(data.get(key), str) and data[key].lower() in SUCCESS_STATUSES)
        for key in keys
    )


def has_marker(data: Any, alternatives: tuple[str, ...]) -> bool:
    """Require an explicit marker on the expected object, never recursively."""
    if not isinstance(data, dict):
        return False
    return any(
        data.get(key) is True
        or (isinstance(data.get(key), str) and data[key].lower() in SUCCESS_STATUSES)
        for key in alternatives
    )


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
        if path.name in PRIVATE_ARTIFACT_NAMES or path.name.endswith(".publish.lock"):
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
    expected_transcript = ["hello_accepted", "probe_accepted"]
    if report.get("handshake_transcript") != expected_transcript:
        checker.add("live-chrome-evidence-incomplete", "extension report lacks the exact Native Messaging handshake transcript", path, gate="live_chrome")
    if not isinstance(live, dict) or live.get("handshake_transcript") != expected_transcript:
        checker.add("live-chrome-evidence-incomplete", "live extension evidence lacks the exact Native Messaging handshake transcript", path, gate="live_chrome")
    required_fields = (
        "extension_loaded",
        "extension_identity_passed",
        "extension_build_binding_passed",
        "fixture_identity_passed",
        "debugger_command_passed",
        "debugger_event_received",
        "tab_group_created",
        "native_messaging_passed",
        "chrome_mediated_native_messaging",
        "debugger_cleanup_passed",
        "cleanup_passed",
        "screenshot_captured",
    )
    if not isinstance(live, dict) or any(live.get(field) is not True for field in required_fields):
        checker.add("live-chrome-evidence-incomplete", "extension report lacks exact identity, debugger, event, tab-group, Chrome-mediated Native Messaging, or cleanup evidence", path, gate="live_chrome")
    if not isinstance(live, dict) or live.get("launched_by_probe") is not True:
        checker.add("live-chrome-isolation-missing", "live extension evidence is not bound to the probe-launched isolated browser", path, gate="live_chrome")
    screenshots = live.get("screenshots") if isinstance(live, dict) else None
    if not isinstance(screenshots, list) or not any(isinstance(item, dict) and item.get("captured") is True for item in screenshots):
        checker.add("live-chrome-screenshot-missing", "live extension evidence has no bounded screenshot capture", path, gate="live_chrome")
    permission_prompts = live.get("permission_prompts") if isinstance(live, dict) else None
    if not isinstance(permission_prompts, dict) or permission_prompts.get("status") not in {"recorded", "none_observed"}:
        checker.add("live-chrome-permission-evidence-missing", "Chrome permission prompt outcome was not manually recorded", path, gate="live_chrome")
    operator_provenance = {
        "operator_assisted": True,
        "load_method": "chrome_extensions_load_unpacked",
        "load_extension_flag_used": False,
        "developer_private_used": False,
        "extensions_ui_dom_access": False,
    }
    if not isinstance(live, dict) or any(live.get(key) != expected for key, expected in operator_provenance.items()):
        checker.add(
            "live-chrome-install-provenance-missing",
            "live extension evidence must use the documented operator-assisted Load unpacked flow",
            path,
            gate="live_chrome",
        )
    for hash_name in ("runner_sha256", "source_extension_tree_sha256", "staged_extension_tree_sha256"):
        if not isinstance(live, dict) or not SHA256_RE.fullmatch(str(live.get(hash_name, ""))):
            checker.add("live-chrome-build-binding-missing", "live extension evidence lacks a bounded runner or staged-tree hash", path, gate="live_chrome")
            break
    if isinstance(live, dict):
        runner_path = checker.safe_path(Path("scripts/run_chrome_probe.py"))
        extension_path = checker.safe_path(Path("extension/probes"))
        try:
            runner_hash = hashlib.sha256(runner_path.read_bytes()).hexdigest() if runner_path is not None else None
            source_hash = extension_tree_sha256(extension_path) if extension_path is not None else None
        except (OSError, ValueError):
            runner_hash = None
            source_hash = None
        if live.get("runner_sha256") != runner_hash or live.get("source_extension_tree_sha256") != source_hash:
            checker.add(
                "live-chrome-build-binding-mismatch",
                "live extension evidence is not bound to the current probe runner and source tree",
                path,
                gate="live_chrome",
            )
    if not isinstance(safety, dict) or any(safety.get(key) is not False for key in ("default_chrome_launch", "default_profile_mutation", "raw_ids_logged", "secrets_logged")) or safety.get("fixture_only_mutation") is not True:
        checker.add("live-chrome-safety-schema", "extension report lacks the required safety assertions", path, gate="live_chrome")
    return "passed" if not any(issue.gate == "live_chrome" for issue in checker.issues) else "missing"


def validate_native_protocol_gate(checker: Checker, *, extension_gate_passed: bool = False) -> str:
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
    preflight_path = checker.safe_path(ARTIFACT_ROOT_REL / "p0-installation-preflight" / "report.json")
    preflight = checker.read_json(preflight_path, gate="live_native") if preflight_path is not None and preflight_path.is_file() else None
    if (
        not isinstance(preflight, dict)
        or preflight.get("kind") != "installation-preflight"
        or preflight.get("status") != "ready"
        or preflight.get("evidence_mode") != "offline"
    ):
        checker.add(
            "native-protocol-install-preflight-missing",
            "Chrome-mediated Native Messaging evidence requires the separate installation preflight artifact",
            preflight_path,
            gate="live_native",
        )
    for path in reports:
        data = checker.read_json(path, gate="live_native")
        if not isinstance(data, dict):
            continue
        offline = data.get("offline")
        if isinstance(offline, dict) and isinstance(offline.get("cases"), dict) and isinstance(offline.get("limits"), dict):
            offline_schema = True
        # A direct host smoke is intentionally not Chrome-mediated evidence.

    # P0-T2 drives chrome.runtime.connectNative through the MV3 extension. It
    # is the authoritative Chrome-mediated Native Messaging lane; P0-T3's
    # direct host smoke only proves framing and host response validation.
    extension_path = checker.safe_path(ARTIFACT_ROOT_REL / "p0-extension" / "report.json")
    extension_report = checker.read_json(extension_path, gate="live_native") if extension_path is not None and extension_path.is_file() else None
    extension_live = extension_report.get("live") if isinstance(extension_report, dict) else None
    if (
        extension_gate_passed
        and isinstance(extension_report, dict)
        and extension_report.get("status") == "live_passed"
        and isinstance(extension_live, dict)
        and extension_live.get("required") is True
        and extension_live.get("status") == "live_passed"
        and extension_live.get("chrome_mediated_native_messaging") is True
        and extension_live.get("handshake_transcript") == ["hello_accepted", "probe_accepted"]
        and extension_live.get("native_messaging_passed") is True
        and extension_live.get("extension_build_binding_passed") is True
        and isinstance(extension_live.get("permission_prompts"), dict)
        and extension_live["permission_prompts"].get("status") in {"recorded", "none_observed"}
    ):
        live_required = True

    if not offline_schema:
        checker.add("native-protocol-schema", "Native Messaging offline case and limit schema is missing", directory, gate="evidence")
    if not extension_gate_passed:
        live_required = False
    if not live_required:
        checker.add("native-protocol-live-missing", "required Chrome-mediated Native Messaging handshake evidence is absent from a passing Chrome extension probe", directory, gate="live_native")
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
        lifecycle = data.get("lifecycle")
        safety = data.get("safety")
        command = data.get("command")
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
            and isinstance(lifecycle, dict)
            and lifecycle.get("install") == "installed"
            and all(lifecycle.get(key) == "passed" for key in ("update", "uninstall", "downgrade"))
            and lifecycle.get("rollback") == "rolled_back"
            and isinstance(safety, dict)
            and safety.get("chrome_launch") == "never"
            and safety.get("chrome_download") == "never"
            and safety.get("chrome_profile_mutation") is False
            and safety.get("raw_browser_ids_logged") is False
            and safety.get("user_registration_mutation") is True
            and isinstance(command, list)
            and "--drill" in command
            and "--required" in command
            and rollback.get("user_tabs_or_chrome_changed") is False
            and platform_name in {"Darwin", "macOS"}
            and registration_scope == "user-level"
        ):
            passed = True
    if not passed:
        checker.add("installation-live-missing", "successful macOS user-level install/update/uninstall/downgrade/rollback evidence is absent", directory, gate="installation")
        return "missing"
    return "passed"


PERFORMANCE_LIVE_MODES = {"headed", "target", "managed", "existing-chrome", "existing_chrome", "live"}
GATEABLE_STATUSES = {"gateable", "passed", "pass", "ok"}
MEASURED_STATUSES = {"measured", "live_measured", "live_passed", "gateable", "passed", "pass", "ok"}
MAX_FUTURE_SKEW_SECONDS = 5 * 60


def _is_nonnegative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _finite_number(value: Any, *, lower: float | None = None, upper: float | None = None) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    number = float(value)
    if not math.isfinite(number):
        return False
    return (lower is None or number >= lower) and (upper is None or number <= upper)


def _sha256(value: Any) -> bool:
    return isinstance(value, str) and SHA256_RE.fullmatch(value) is not None


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _valid_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or not value.endswith("Z"):
        return False
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return False
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        return False
    age = (datetime.now(timezone.utc) - parsed).total_seconds()
    return -MAX_FUTURE_SKEW_SECONDS <= age <= MAX_FRESHNESS_SECONDS


def _all_numbers_finite(value: Any) -> bool:
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, dict):
        return all(_all_numbers_finite(key) and _all_numbers_finite(child) for key, child in value.items())
    if isinstance(value, (list, tuple)):
        return all(_all_numbers_finite(child) for child in value)
    return True


def _unique_strings(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(item, str) and item for item in value) and len(value) == len(set(value))


def _safe_name(value: Any, suffix: str) -> bool:
    if not isinstance(value, str) or not value or "\\" in value:
        return False
    path = Path(value)
    return path.name == value and not path.is_absolute() and ".." not in path.parts and value.endswith(suffix)


def _safe_relative_reference(value: Any) -> bool:
    if not isinstance(value, str) or not value or "\\" in value:
        return False
    path = Path(value)
    return not path.is_absolute() and ".." not in path.parts and path.as_posix() == value


def _safe_root_file(root: Path, relative: str) -> Path | None:
    if not _safe_relative_reference(relative):
        return None
    candidate = root / relative
    current = root
    for component in Path(relative).parts:
        current = current / component
        if current.is_symlink():
            return None
    try:
        resolved = candidate.resolve()
        resolved.relative_to(root.resolve())
    except (OSError, ValueError):
        return None
    return resolved if resolved.is_file() else None


def _fixture_set_sha256(fixtures: list[dict[str, Any]]) -> str:
    return hashlib.sha256(
        "\n".join(f"{fixture['name']}:{fixture['sha256']}" for fixture in fixtures).encode("utf-8")
    ).hexdigest()


def _validate_ci(stat: Any, valid_samples: int) -> bool:
    if not isinstance(stat, dict):
        return False
    for key in ("p50", "p95", "p99", "mean"):
        if not _finite_number(stat.get(key), lower=0):
            return False
    if not (stat["p50"] <= stat["p95"] <= stat["p99"]):
        return False
    interval = stat.get("mean_confidence_interval")
    if not isinstance(interval, dict):
        return False
    if not _finite_number(interval.get("lower")) or not _finite_number(interval.get("upper")):
        return False
    if interval["lower"] > interval["upper"]:
        return False
    if not _finite_number(interval.get("confidence_level"), lower=0, upper=1):
        return False
    if not _is_nonnegative_int(interval.get("sample_count")) or interval["sample_count"] != valid_samples:
        return False
    return isinstance(stat.get("measurement_status"), str) and bool(stat["measurement_status"])


def _validate_latency(row: dict[str, Any], valid_samples: int) -> bool:
    latency = row.get("latency_ms")
    if not isinstance(latency, dict):
        return False
    required = ("read_ms", "first_useful_action_ms", "metadata_ms", "action_ms", "synthetic_action_ms", "wait_ms", "total_ms")
    return all(_validate_ci(latency.get(key), valid_samples) for key in required)


def _validate_live_metrics(row: dict[str, Any]) -> bool:
    live_only = row.get("live_only")
    if not isinstance(live_only, dict) or live_only.get("status") not in MEASURED_STATUSES:
        return False
    ranges = {
        "chrome_cpu_percent": (0.0, 100.0),
        "chrome_rss_bytes": (0.0, None),
        "host_rss_bytes": (0.0, None),
        "event_lag_ms": (0.0, None),
        "reconnect_ms": (0.0, None),
        "stale_ref_rate": (0.0, 1.0),
        "unknown_outcome_rate": (0.0, 1.0),
        "human_tab_responsiveness_ms": (0.0, None),
    }
    for key, (lower, upper) in ranges.items():
        if not _finite_number(live_only.get(key), lower=lower, upper=upper):
            return False

    reliability = row.get("reliability_gates")
    if not isinstance(reliability, dict):
        return False
    for key in ("stale_ref_rate", "unknown_outcome_rate", "reconnect_ms"):
        gate = reliability.get(key)
        if not isinstance(gate, dict) or gate.get("status") not in GATEABLE_STATUSES:
            return False
        lower, upper = ranges[key]
        if not _finite_number(gate.get("value"), lower=lower, upper=upper):
            return False
        if gate["value"] != live_only[key]:
            return False

    human = row.get("human_tab_gate")
    if not isinstance(human, dict) or human.get("status") not in GATEABLE_STATUSES:
        return False
    if not _finite_number(human.get("responsiveness_ms"), lower=0):
        return False
    return human["responsiveness_ms"] == live_only["human_tab_responsiveness_ms"]


def _validate_context(row: dict[str, Any]) -> bool:
    context = row.get("context")
    if context is None:
        return True
    if not isinstance(context, dict):
        return False
    for key, value in context.items():
        if ("coverage" in key or key.endswith("_rate")) and not _finite_number(value, lower=0, upper=1):
            return False
        if key in {"fixture_utf8_bytes", "fixture_chars", "serialized_bytes", "frames_discovered", "frames_scanned", "max_frame_depth", "text_chars", "srcdoc_chars"} and not _is_nonnegative_int(value):
            return False
    return True


def _safe_child(directory: Path, name: str) -> Path | None:
    if not _safe_name(name, ".jsonl") and name not in {"baseline.json", "baseline.md", GENERATION_MANIFEST_NAME, COMMIT_MARKER_NAME}:
        return None
    candidate = directory / name
    current = directory
    for component in Path(name).parts:
        current = current / component
        if current.is_symlink():
            return None
    try:
        candidate.resolve().relative_to(directory.resolve())
    except (OSError, ValueError):
        return None
    return candidate


RAW_SAMPLE_METRICS = (
    "read_ms",
    "metadata_ms",
    "action_ms",
    "first_useful_action_ms",
    "synthetic_action_ms",
    "wait_ms",
    "total_ms",
)


def _raw_percentile(values: list[float], percent: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = round((len(ordered) - 1) * percent / 100.0)
    return ordered[min(index, len(ordered) - 1)]


def _validate_raw_files(data: dict[str, Any], artifact_dir: Path | None) -> bool:
    raw_files = data.get("raw_samples_files")
    declarations = data.get("raw_sample_declarations")
    if not isinstance(raw_files, list) or not raw_files or any(not _safe_name(name, ".jsonl") for name in raw_files):
        return False
    if len(raw_files) != len(set(raw_files)):
        return False
    accounting = data.get("sample_accounting")
    if not isinstance(accounting, dict) or not isinstance(declarations, dict) or declarations.get("total_samples") != accounting.get("attempted"):
        return False
    if declarations.get("required_metrics") != list(RAW_SAMPLE_METRICS):
        return False
    declared_files = declarations.get("files")
    if not isinstance(declared_files, list) or [item.get("name") for item in declared_files if isinstance(item, dict)] != raw_files:
        return False
    if len(declared_files) != len(raw_files):
        return False
    total_declared = 0
    raw_cells: dict[tuple[str, str, int], dict[str, Any]] = {}
    for declaration in declared_files:
        if not isinstance(declaration, dict) or not _safe_name(declaration.get("name"), ".jsonl"):
            return False
        if not _sha256(declaration.get("sha256")):
            return False
        if not _is_nonnegative_int(declaration.get("bytes")) or not _is_nonnegative_int(declaration.get("sample_count")):
            return False
        total_declared += declaration["sample_count"]
        if artifact_dir is None:
            continue
        path = _safe_child(artifact_dir, declaration["name"])
        if path is None or not path.is_file() or path.is_symlink():
            return False
        try:
            content = path.read_bytes()
        except OSError:
            return False
        if len(content) != declaration["bytes"] or sha256_bytes(content) != declaration["sha256"]:
            return False
        lines = content.splitlines()
        if len(lines) != declaration["sample_count"] or any(not line.strip() for line in lines):
            return False
        for line in lines:
            try:
                sample = json.loads(line.decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError):
                return False
            if not isinstance(sample, dict) or sample.get("nonce") != data.get("nonce"):
                return False
            fixture = sample.get("fixture")
            cache_state = sample.get("cache_state")
            spaces = sample.get("spaces")
            sample_status = sample.get("sample_status")
            if not isinstance(fixture, str) or not isinstance(cache_state, str) or not isinstance(spaces, int) or isinstance(spaces, bool) or sample_status not in {"valid", "error", "invalid"}:
                return False
            cell = (fixture, cache_state, spaces)
            cell_accounting = raw_cells.setdefault(
                cell,
                {"attempted": 0, "valid": 0, "errors": 0, "invalid": 0, "metrics": {}},
            )
            cell_accounting["attempted"] += 1
            cell_accounting["valid" if sample_status == "valid" else "errors" if sample_status == "error" else "invalid"] += 1
            if sample_status == "valid":
                for metric in RAW_SAMPLE_METRICS:
                    value = sample.get(metric)
                    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or float(value) < 0:
                        return False
                    cell_accounting["metrics"].setdefault(metric, []).append(float(value))
    if total_declared != accounting.get("attempted"):
        return False
    if artifact_dir is None:
        return True
    expected_cells: dict[tuple[str, str, int], dict[str, int]] = {}
    for row in data.get("rows", []):
        if not isinstance(row, dict) or not isinstance(row.get("samples"), dict):
            return False
        fixture = row.get("fixture")
        cache_state = row.get("cache_state")
        spaces = row.get("spaces")
        if not isinstance(fixture, str) or not isinstance(cache_state, str) or not isinstance(spaces, int) or isinstance(spaces, bool):
            return False
        expected_cells[(fixture, cache_state, spaces)] = row["samples"]
    raw_counts = {
        cell: {key: values[key] for key in ("attempted", "valid", "errors", "invalid")}
        for cell, values in raw_cells.items()
    }
    if raw_counts != expected_cells:
        return False
    latency_mapping = {
        "read_ms": "read_ms",
        "first_useful_action_ms": "first_useful_action_ms",
        "metadata_ms": "metadata_ms",
        "action_ms": "action_ms",
        "synthetic_action_ms": "synthetic_action_ms",
        "wait_ms": "wait_ms",
        "total_ms": "total_ms",
    }
    for row in data.get("rows", []):
        cell = (row["fixture"], row["cache_state"], row["spaces"])
        metrics = raw_cells[cell].get("metrics", {})
        for row_metric, sample_metric in latency_mapping.items():
            values = metrics.get(sample_metric)
            stat = row.get("latency_ms", {}).get(row_metric)
            if not isinstance(values, list) or not isinstance(stat, dict):
                return False
            expected_stats = {
                "p50": _raw_percentile(values, 50),
                "p95": _raw_percentile(values, 95),
                "p99": _raw_percentile(values, 99),
                "mean": sum(values) / len(values),
            }
            for key, expected_value in expected_stats.items():
                if not _finite_number(stat.get(key), lower=0) or not math.isclose(float(stat[key]), expected_value, rel_tol=1e-9, abs_tol=1e-9):
                    return False
            interval = stat.get("mean_confidence_interval")
            if not isinstance(interval, dict) or interval.get("method") != "normal_approximation" or interval.get("status") != "approximate" or interval.get("confidence_level") != 0.95 or interval.get("sample_count") != len(values):
                return False
            average = sum(values) / len(values)
            if len(values) == 1:
                margin = 0.0
            else:
                variance = math.fsum((value - average) ** 2 for value in values) / (len(values) - 1)
                margin = 1.96 * math.sqrt(variance / len(values))
            if not _finite_number(interval.get("lower")) or not _finite_number(interval.get("upper")):
                return False
            if not math.isclose(float(interval["lower"]), average - margin, rel_tol=1e-9, abs_tol=1e-9) or not math.isclose(float(interval["upper"]), average + margin, rel_tol=1e-9, abs_tol=1e-9):
                return False
    return True


def _validate_fixture_files(data: dict[str, Any], root: Path | None) -> bool:
    if root is None:
        return True
    baseline_manifest = data["baseline_manifest"]
    manifest_path = baseline_manifest["path"]
    manifest = _safe_root_file(root, manifest_path)
    if manifest is None:
        return False
    try:
        manifest_bytes = manifest.read_bytes()
        if sha256_bytes(manifest_bytes) != baseline_manifest["sha256"]:
            return False
        if "bytes" in baseline_manifest and len(manifest_bytes) != baseline_manifest["bytes"]:
            return False
    except OSError:
        return False
    for fixture in data["fixtures"]:
        relative = fixture["file"]
        path = _safe_root_file(manifest.parent, relative)
        if path is None:
            return False
        try:
            content = path.read_bytes()
        except OSError:
            return False
        if sha256_bytes(content) != fixture["sha256"] or len(content) != fixture["bytes"]:
            return False
    return True


def performance_report_passed(
    data: dict[str, Any],
    artifact_dir: Path | None = None,
    root: Path | None = None,
) -> bool:
    """Return true only for a complete, fresh, live, non-smoke matrix."""
    if not isinstance(data, dict) or not _all_numbers_finite(data):
        return False
    if str(data.get("mode", "")).lower() not in PERFORMANCE_LIVE_MODES:
        return False
    if data.get("status") not in LIVE_SUCCESS_STATUSES or data.get("kind") != "direct-benchmark-baseline":
        return False
    if data.get("smoke") is not False:
        return False
    samples_per_cell = data.get("samples_per_cell")
    if not isinstance(samples_per_cell, int) or isinstance(samples_per_cell, bool) or samples_per_cell < 1_000:
        return False

    nonce = data.get("nonce")
    if not isinstance(nonce, str) or NONCE_RE.fullmatch(nonce) is None:
        return False
    timestamp = data.get("timestamp")
    if not _valid_timestamp(timestamp):
        return False
    command = data.get("command")
    build_tuple = data.get("build_tuple")
    provenance = data.get("provenance")
    if not isinstance(command, list) or not command or any(not isinstance(item, str) for item in command):
        return False
    if not isinstance(build_tuple, dict) or not build_tuple:
        return False
    if build_tuple.get("artifact_kind") != "direct-benchmark" or build_tuple.get("benchmark_kind") != "direct-benchmark-baseline":
        return False
    if not isinstance(provenance, dict):
        return False
    if provenance.get("nonce") != nonce or provenance.get("timestamp") != timestamp:
        return False
    if provenance.get("command") != command or provenance.get("build_tuple") != build_tuple:
        return False

    manifest = data.get("baseline_manifest")
    manifest_hash = data.get("manifest_sha256")
    fixture_set_hash = data.get("fixture_set_sha256")
    if not isinstance(manifest, dict) or not _sha256(manifest.get("sha256")) or manifest.get("sha256") != manifest_hash:
        return False
    if "bytes" in manifest and not _is_nonnegative_int(manifest.get("bytes")):
        return False
    if not _sha256(fixture_set_hash):
        return False
    manifest_path = manifest.get("path")
    if not _safe_relative_reference(manifest_path):
        return False
    if build_tuple.get("fixture_manifest") != manifest_path or build_tuple.get("fixture_manifest_sha256") != manifest_hash:
        return False

    fixtures = data.get("fixtures")
    binding = data.get("fixture_binding")
    if not isinstance(fixtures, list) or not fixtures or not isinstance(binding, dict):
        return False
    fixture_names: set[str] = set()
    for fixture in fixtures:
        if not isinstance(fixture, dict) or not isinstance(fixture.get("name"), str) or not fixture["name"] or fixture["name"] in fixture_names:
            return False
        if not _safe_relative_reference(fixture.get("file")) or not _sha256(fixture.get("sha256")) or not _is_nonnegative_int(fixture.get("bytes")):
            return False
        fixture_names.add(fixture["name"])
    if binding.get("manifest_path") != manifest_path or binding.get("manifest_sha256") != manifest_hash or binding.get("fixture_set_sha256") != fixture_set_hash or binding.get("fixtures") != fixtures:
        return False
    if _fixture_set_sha256(fixtures) != fixture_set_hash or not _validate_fixture_files(data, root):
        return False

    cache_states = data.get("cache_states")
    spaces = data.get("spaces")
    if not isinstance(cache_states, list) or not cache_states or not _unique_strings(cache_states):
        return False
    if not isinstance(spaces, list) or not spaces or len(spaces) != len(set(spaces)):
        return False
    if not all(isinstance(cache_state, str) and cache_state for cache_state in cache_states):
        return False
    if any(isinstance(space, bool) or not isinstance(space, int) or space <= 0 for space in spaces):
        return False
    cache_state_values: list[str] = cache_states
    space_values: list[int] = [space for space in spaces if isinstance(space, int) and not isinstance(space, bool)]
    if len(space_values) != len(spaces):
        return False

    expected = {(fixture["name"], cache_state, space) for fixture in fixtures for cache_state in cache_state_values for space in space_values}
    rows = data.get("rows")
    if not isinstance(rows, list) or len(rows) != len(expected):
        return False
    seen: set[tuple[str, str, int]] = set()
    fixture_hashes = {fixture["name"]: fixture["sha256"] for fixture in fixtures}
    totals = {"attempted": 0, "valid": 0, "errors": 0, "invalid": 0}
    for row in rows:
        if not isinstance(row, dict):
            return False
        fixture_name = row.get("fixture")
        cache_state = row.get("cache_state")
        space = row.get("spaces")
        if not isinstance(fixture_name, str) or not isinstance(cache_state, str) or not isinstance(space, int) or isinstance(space, bool):
            return False
        key = (fixture_name, cache_state, space)
        if key not in expected or key in seen or row.get("fixture_sha256") != fixture_hashes.get(fixture_name):
            return False
        seen.add(key)
        samples = row.get("samples")
        if not isinstance(samples, dict) or any(not _is_nonnegative_int(samples.get(name)) for name in ("attempted", "valid", "errors", "invalid")):
            return False
        if samples["attempted"] != samples_per_cell or samples["attempted"] != samples["valid"] + samples["errors"] + samples["invalid"]:
            return False
        if not _validate_latency(row, samples["valid"]) or not _validate_live_metrics(row) or not _validate_context(row):
            return False
        tails = row.get("tail_gates")
        if not isinstance(tails, dict):
            return False
        for key_name, minimum in (("p95", 200), ("p99", 1_000)):
            gate = tails.get(key_name)
            if not isinstance(gate, dict) or gate.get("status") != "gateable" or gate.get("minimum_samples") != minimum or samples["valid"] < minimum:
                return False
        for name in totals:
            totals[name] += samples[name]
    if seen != expected:
        return False
    accounting = data.get("sample_accounting")
    if not isinstance(accounting, dict) or any(accounting.get(name) != totals[name] for name in totals):
        return False
    return _validate_raw_files(data, artifact_dir)


def _validate_generation(checker: Checker, directory: Path, report: dict[str, Any]) -> bool:
    manifest_path = directory / GENERATION_MANIFEST_NAME
    commit_path = directory / COMMIT_MARKER_NAME
    generation = checker.read_json(manifest_path, gate="performance")
    commit = checker.read_json(commit_path, gate="performance")
    if not isinstance(generation, dict) or not isinstance(commit, dict):
        return False
    nonce = report.get("nonce")
    generation_id = f"generation-{nonce}"
    if generation.get("complete") is not True or generation.get("kind") != "direct-benchmark-generation":
        return False
    if generation.get("nonce") != nonce or generation.get("generation_id") != generation_id:
        return False
    if generation.get("raw_samples_files") != report.get("raw_samples_files"):
        return False
    generation_provenance = generation.get("provenance")
    if not isinstance(generation_provenance, dict) or generation_provenance.get("nonce") != nonce or generation_provenance.get("command") != report.get("command") or generation_provenance.get("build_tuple") != report.get("build_tuple"):
        return False

    files = generation.get("files")
    if not isinstance(files, list) or not files:
        return False
    declared_names: set[str] = set()
    for declaration in files:
        if not isinstance(declaration, dict):
            return False
        name = declaration.get("name")
        if not isinstance(name, str) or name in declared_names or not _safe_name(name, ".jsonl") and name not in {"baseline.json", "baseline.md"}:
            return False
        if not _sha256(declaration.get("sha256")) or not _is_nonnegative_int(declaration.get("bytes")):
            return False
        path = _safe_child(directory, name)
        if path is None or not path.is_file() or path.is_symlink():
            return False
        try:
            content = path.read_bytes()
        except OSError:
            return False
        if len(content) != declaration["bytes"] or sha256_bytes(content) != declaration["sha256"]:
            return False
        declared_names.add(name)
    if "baseline.json" not in declared_names or "baseline.md" not in declared_names:
        return False
    expected_raw_names = report.get("raw_samples_files")
    if not isinstance(expected_raw_names, list) or declared_names != {"baseline.json", "baseline.md", *expected_raw_names}:
        return False
    expected_names = declared_names | {GENERATION_MANIFEST_NAME, COMMIT_MARKER_NAME}
    try:
        actual_names = set()
        for path in directory.iterdir():
            if not path.is_file() or path.is_symlink():
                return False
            actual_names.add(path.name)
    except OSError:
        return False
    if actual_names != expected_names:
        return False

    try:
        manifest_hash = sha256_bytes(manifest_path.read_bytes())
    except OSError:
        return False
    if commit.get("complete") is not True or commit.get("kind") != "direct-benchmark-commit" or commit.get("manifest") != GENERATION_MANIFEST_NAME or commit.get("manifest_sha256") != manifest_hash or commit.get("nonce") != nonce or commit.get("generation_id") != generation_id:
        return False
    baseline_path = directory / "baseline.json"
    baseline = checker.read_json(baseline_path, gate="performance")
    return isinstance(baseline, dict) and baseline.get("nonce") == nonce and performance_report_passed(baseline, artifact_dir=directory, root=checker.root)


def validate_performance_gate(checker: Checker) -> str:
    status = validate_directory_gate(checker, "p0-performance", gate="performance", description="full performance")
    if status == "missing":
        smoke = checker.safe_path(ARTIFACT_ROOT_REL / "p0-performance-smoke")
        if smoke is not None and smoke.is_dir():
            checker.add("performance-smoke-only", "offline smoke evidence cannot close the full performance gate", smoke, gate="performance")
        return "missing"
    directory = checker.safe_path(ARTIFACT_ROOT_REL / "p0-performance")
    assert directory is not None
    report_path = directory / "baseline.json"
    report = checker.read_json(report_path, gate="performance")
    if not isinstance(report, dict) or not performance_report_passed(report, artifact_dir=directory, root=checker.root):
        checker.add("performance-live-missing", "full live performance evidence with an exact non-smoke matrix is absent", directory, gate="performance")
        return "missing"
    if not _validate_generation(checker, directory, report):
        checker.add("performance-generation-incomplete", "performance baseline is not published as a complete committed generation", directory, gate="performance")
        return "missing"
    return "passed"


def run(root: Path) -> dict[str, Any]:
    checker = Checker(root)
    validate_baseline_document(checker)
    validate_current_artifacts(checker)
    validate_artifact_safety(checker)
    validate_artifact_envelope(checker)
    live_chrome_status = validate_extension_gate(checker)
    gates = {
        "live_chrome": live_chrome_status,
        "live_native_messaging": validate_native_protocol_gate(
            checker,
            extension_gate_passed=live_chrome_status == "passed",
        ),
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
