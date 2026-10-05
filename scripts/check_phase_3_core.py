#!/usr/bin/env python3
"""Validate the deterministic Phase 3 host/broker evidence slice.

This checker is repository-only. It verifies implementation markers, named test
surfaces, the selected single-broker Native Messaging topology, and bounded
redacted evidence. It does not claim live Chrome, installer, distribution, or
production readiness.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = Path("tests/phase-3-manifest.yaml")
ARTIFACT_PATH = Path("artifacts/p3-core-review.md")
TRACEABILITY_PATH = Path("docs/traceability-phase-3-core.md")
CHROME_AUDIT_PATH = Path("docs/exec-plans/active/agentyc-browser-task-spaces/research/phase-3-chrome-docs-audit.md")
PLAN_PATH = Path("docs/exec-plans/active/agentyc-browser-task-spaces/plans/phase-3-core-implementation.md")
REGISTRY_PATH = Path("docs/exec-plans/active/agentyc-browser-task-spaces/plans/PLAN_INDEX.md")
README_PATH = Path("docs/exec-plans/active/agentyc-browser-task-spaces/README.md")
MAX_BYTES = 4 * 1024 * 1024

TASK_IDS = tuple(f"P3-T{index}" for index in range(1, 10))
QUALITY_MARKERS = (
    "One host lock/broker per profile instance.",
    "No browser process launch/download in the new host path.",
    "Ledger stores logical state and action metadata only.",
    "All mutation paths check lease epoch three times.",
    "User/unmanaged pages cannot be closed by release, stop, crash recovery",
    "Profile binding/rebinding and user-intent tickets are explicit security gates.",
    "Direct CDP and `BrowserSession` are behind explicit legacy/test boundaries.",
    "Host crash/extension loss/Chrome loss classify work correctly.",
)


class Phase3Error(ValueError):
    """A required Phase 3 repository invariant is missing."""


def read_text(root: Path, path: Path) -> str:
    candidate = root / path
    if path.is_absolute() or ".." in path.parts or candidate.is_symlink() or not candidate.is_file():
        raise Phase3Error(f"missing or unsafe path: {path}")
    if candidate.stat().st_size > MAX_BYTES:
        raise Phase3Error(f"bounded input exceeded: {path}")
    return candidate.read_text(encoding="utf-8")


def parse_manifest(root: Path) -> dict[str, Any]:
    try:
        value = json.loads(read_text(root, MANIFEST_PATH))
    except json.JSONDecodeError as exc:
        raise Phase3Error("Phase 3 manifest must be strict JSON") from exc
    if not isinstance(value, dict):
        raise Phase3Error("Phase 3 manifest root must be an object")
    return value


def parse_artifact(root: Path) -> dict[str, Any]:
    text = read_text(root, ARTIFACT_PATH)
    matches = re.findall(
        r"(?ms)^```json[ \t]+phase-3-core-v1[ \t]*\n(.*?)^```[ \t]*$",
        text,
    )
    if len(matches) != 1:
        raise Phase3Error("Phase 3 artifact must contain exactly one phase-3-core-v1 JSON block")
    try:
        value = json.loads(matches[0])
    except json.JSONDecodeError as exc:
        raise Phase3Error("Phase 3 artifact JSON is invalid") from exc
    if not isinstance(value, dict):
        raise Phase3Error("Phase 3 artifact JSON must be an object")
    return value


def require(value: bool, message: str) -> None:
    if not value:
        raise Phase3Error(message)


def check(root: Path = ROOT) -> dict[str, Any]:
    manifest = parse_manifest(root)
    plan = read_text(root, PLAN_PATH)
    registry = read_text(root, REGISTRY_PATH)
    readme = read_text(root, README_PATH)
    traceability = read_text(root, TRACEABILITY_PATH)
    chrome_audit = read_text(root, CHROME_AUDIT_PATH)
    artifact = parse_artifact(root)

    require(manifest.get("schema_version") == 1, "manifest schema_version must be 1")
    require(manifest.get("phase") == 3, "manifest phase must be 3")
    require(manifest.get("kind") == "host_core_evidence", "manifest kind is invalid")
    require(manifest.get("evidence_mode") == "deterministic", "Phase 3 evidence must be deterministic")
    require(manifest.get("status") in {"active", "complete"}, "manifest status must be active or complete")
    require(manifest.get("release_eligible") is False, "Phase 3 cannot claim release eligibility")
    require(manifest.get("phase_plan") == PLAN_PATH.as_posix(), "manifest phase plan is wrong")
    require(manifest.get("artifact") == ARTIFACT_PATH.as_posix(), "manifest artifact path is wrong")
    require(manifest.get("traceability") == TRACEABILITY_PATH.as_posix(), "manifest traceability path is wrong")
    require(manifest.get("chrome_audit") == CHROME_AUDIT_PATH.as_posix(), "manifest Chrome audit path is wrong")

    for task_id in TASK_IDS:
        require(re.search(rf"- \[x\] {re.escape(task_id)}\b", plan), f"{task_id} is not complete in the plan")
    plan_lines = plan.splitlines()
    for marker in QUALITY_MARKERS:
        require(
            any(line.startswith("- [x] ") and marker in line for line in plan_lines),
            f"quality marker is not complete: {marker}",
        )

    require(
        re.search(r"\|\s*3\s*\| `phase-3-core-implementation\.md`\s*\|\s*2\s*\|\s*complete\s*\|", registry)
        is not None,
        "registry does not mark Phase 3 complete",
    )
    require("Phase 3 is complete" in readme, "initiative README does not record Phase 3 completion")
    require("U3-1" in traceability and "single_broker_native_shim_forwarding" in traceability, "topology traceability is incomplete")

    host_binary = read_text(root, Path("crates/agentyc-host/src/bin/agentyc-native-host.rs"))
    broker = read_text(root, Path("crates/agentyc-host/src/broker.rs"))
    ledger = read_text(root, Path("crates/agentyc-host/src/ledger.rs"))
    bridge = read_text(root, Path("crates/agentyc-host/src/bridge.rs"))
    local_ipc = read_text(root, Path("crates/agentyc-host/src/local_ipc.rs"))
    native = read_text(root, Path("crates/agentyc-host/src/native_messaging.rs"))
    cli = read_text(root, Path("crates/agentyc/src/main.rs"))
    cli_manifest = read_text(root, Path("crates/agentyc/Cargo.toml"))

    for marker in (
        "Ledger::open",
        "forward_stdio_to_owner",
        "NativeForwardServer",
        "publish_endpoint_metadata",
        "mark_profile_rebind_required",
    ):
        require(marker in host_binary, f"native host topology marker missing: {marker}")
    for marker in (
        "acquire_mutation",
        "acquire_read",
        "pause_space",
        "handoff_space",
        "mark_profile_rebind_required",
        "HostDegradedReason",
    ):
        require(marker in broker, f"broker marker missing: {marker}")
    for marker in ("stale_lock_owner", "quarantine_bytes", "persist_current", "LEDGER_SCHEMA_VERSION"):
        require(marker in ledger, f"ledger recovery marker missing: {marker}")
    for marker in ("BridgeRouter", "fence_with_token", "close_page"):
        require(marker in bridge, f"bridge marker missing: {marker}")
    for marker in ("peer_matches_directory_owner", "FrameDecoder", "MAX_LOCAL_CLIENTS"):
        require(marker in local_ipc, f"local IPC security marker missing: {marker}")
    for marker in ("NativeForwardServer", "forward_stdio_to_owner", "MAX_NATIVE_CONTROL_BYTES"):
        require(marker in native, f"Native Messaging marker missing: {marker}")
    for marker in ("LocalSocketClient::connect", "run_remote_host_stdio"):
        require(marker in cli, f"host-backed CLI/MCP routing marker missing: {marker}")
    for crate in ("agentyc-cdp", "agentyc-browser", "agentyc-runtime"):
        require(crate not in cli_manifest, f"removed direct-CDP crate remains in CLI dependencies: {crate}")
    for crate_path in ("crates/agentyc-cdp", "crates/agentyc-browser", "crates/agentyc-runtime"):
        require(not (root / crate_path).exists(), f"removed direct-CDP crate path remains: {crate_path}")

    for test_path in (
        Path("crates/agentyc-host/tests/host_lifecycle.rs"),
        Path("crates/agentyc-host/tests/native_messaging.rs"),
        Path("crates/agentyc-host/tests/lease_state_machine.rs"),
    ):
        read_text(root, test_path)
    for test_name in manifest.get("required_tests", []):
        require(isinstance(test_name, str) and test_name, "required test names must be non-empty strings")

    require("developer.chrome.com/docs/extensions/develop/concepts/native-messaging" in chrome_audit, "official Native Messaging audit source missing")
    require("developer.chrome.com/docs/extensions/reference/api/debugger" in chrome_audit, "official debugger audit source missing")
    require("developer.chrome.com/docs/extensions/develop/concepts/service-workers/lifecycle" in chrome_audit, "official service-worker audit source missing")
    require("developer.chrome.com/docs/extensions/reference/api/tabs" in chrome_audit, "official tabs audit source missing")
    require("developer.chrome.com/docs/extensions/reference/api/sidePanel" in chrome_audit, "official side-panel audit source missing")

    require(artifact.get("schema_version") == 1, "artifact schema_version must be 1")
    require(artifact.get("phase") == 3, "artifact phase must be 3")
    require(artifact.get("evidence_mode") == "deterministic", "artifact evidence mode is invalid")
    require(artifact.get("release_eligible") is False, "artifact cannot claim release eligibility")
    require(isinstance(artifact.get("commands"), list) and artifact["commands"], "artifact command evidence is missing")
    require(isinstance(artifact.get("nonclaims"), list) and artifact["nonclaims"], "artifact nonclaims are missing")
    require("live existing-profile Chrome" in " ".join(artifact["nonclaims"]), "artifact must state the live Chrome nonclaim")

    return {
        "phase": 3,
        "status": manifest["status"],
        "tasks": len(TASK_IDS),
        "quality_items": len(QUALITY_MARKERS),
        "required_tests": len(manifest["required_tests"]),
        "evidence_mode": "deterministic",
        "release_eligible": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    try:
        result = check(args.root.resolve())
    except (OSError, Phase3Error) as exc:
        print(f"check_phase_3_core: FAIL: {exc}", file=sys.stderr)
        return 1
    print("check_phase_3_core: PASS")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
