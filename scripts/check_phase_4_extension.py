#!/usr/bin/env python3
"""Check the deterministic Phase 4 MV3 extension slice.

The active gate accepts deterministic implementation evidence while explicitly
refusing to claim headed existing-profile Chrome or production distribution.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PLAN = Path("docs/exec-plans/active/agentyc-browser-task-spaces/plans/phase-4-extension.md")
MANIFEST = Path("tests/phase-4-manifest.yaml")
ARTIFACT = Path("artifacts/p4-extension-review.md")
AUDIT = Path("docs/exec-plans/active/agentyc-browser-task-spaces/research/phase-4-chrome-docs-audit.md")
LIVE_ARTIFACT = Path("artifacts/p4-live-disposable/report.json")
MAX_BYTES = 4 * 1024 * 1024
TASK_IDS = tuple(f"P4-T{i}" for i in range(1, 8))


class Phase4Error(ValueError):
    pass


def read(root: Path, path: Path) -> str:
    if path.is_absolute() or ".." in path.parts:
        raise Phase4Error(f"unsafe path: {path}")
    candidate = root / path
    if candidate.is_symlink() or not candidate.is_file() or candidate.stat().st_size > MAX_BYTES:
        raise Phase4Error(f"missing, symlinked, or oversized path: {path}")
    return candidate.read_text(encoding="utf-8")


def manifest(root: Path) -> dict[str, Any]:
    try:
        value = json.loads(read(root, MANIFEST))
    except json.JSONDecodeError as exc:
        raise Phase4Error("Phase 4 manifest must be strict JSON") from exc
    if not isinstance(value, dict):
        raise Phase4Error("Phase 4 manifest root must be an object")
    return value


def artifact(root: Path) -> dict[str, Any]:
    text = read(root, ARTIFACT)
    blocks = re.findall(r"(?ms)^```json[ \t]+phase-4-extension-v1[ \t]*\n(.*?)^```[ \t]*$", text)
    if len(blocks) != 1:
        raise Phase4Error("Phase 4 artifact must contain one phase-4-extension-v1 block")
    try:
        value = json.loads(blocks[0])
    except json.JSONDecodeError as exc:
        raise Phase4Error("Phase 4 artifact JSON is invalid") from exc
    if not isinstance(value, dict):
        raise Phase4Error("Phase 4 artifact JSON must be an object")
    return value


def require(condition: bool, message: str) -> None:
    if not condition:
        raise Phase4Error(message)


def check(root: Path = ROOT) -> dict[str, Any]:
    plan = read(root, PLAN)
    values = manifest(root)
    evidence = artifact(root)
    audit = read(root, AUDIT)
    live_text = read(root, LIVE_ARTIFACT) if (root / LIVE_ARTIFACT).is_file() else None
    require(values.get("schema_version") == 1 and values.get("phase") == 4, "manifest identity is invalid")
    require(values.get("status") in {"active", "complete"}, "manifest status is invalid")
    require(values.get("release_eligible") is False, "Phase 4 cannot claim release eligibility")
    require(values.get("plan") == PLAN.as_posix(), "manifest plan path is invalid")
    require(values.get("audit") == AUDIT.as_posix(), "manifest audit path is invalid")
    require("status: active" in plan or "status: complete" in plan, "Phase 4 plan status is missing")

    for path in (
        Path("extension/package-lock.json"),
        Path("docs/user-control.md"),
        Path("docs/security/logging.md"),
        Path("crates/agentyc-host/src/chrome_bridge.rs"),
        Path("extension/tests/reconnect-debugger.test.mjs"),
        Path("extension/tests/content-bridge-phase4.test.mjs"),
        Path("extension/tests/tabs-registry-security.test.mjs"),
        Path("scripts/run_phase4_live_probe.py"),
    ):
        read(root, path)

    debugger = read(root, Path("extension/src/debugger-bridge.mjs"))
    frames = read(root, Path("extension/src/frames.mjs"))
    worker = read(root, Path("extension/src/service-worker.mjs"))
    fake = read(root, Path("extension/tests/fake-chrome.mjs"))
    for marker in ("Target.setAutoAttach", "flatten: true", "Target.attachedToTarget", "Target.detachedFromTarget", "sessionId"):
        require(marker in debugger, f"debugger OOPIF marker missing: {marker}")
    require("resolveFrameScope" in frames and "frameScope" in debugger, "logical frame routing marker missing")
    require('action === "stop"' in worker and 'space.${action}' in worker, "side-panel action routing marker missing")
    require("debuggerInternalCommands" in fake, "fake Chrome internal-command evidence marker missing")
    for source in (
        "https://developer.chrome.com/docs/extensions/develop/concepts/native-messaging",
        "https://developer.chrome.com/docs/extensions/reference/api/debugger",
        "https://developer.chrome.com/docs/extensions/develop/concepts/service-workers/lifecycle",
        "https://developer.chrome.com/docs/extensions/reference/api/tabs",
        "https://developer.chrome.com/docs/extensions/reference/api/sidePanel",
    ):
        require(source in audit, f"official Chrome source missing: {source}")

    require(evidence.get("schema_version") == 1 and evidence.get("phase") == 4, "artifact identity is invalid")
    require(evidence.get("release_eligible") is False, "artifact cannot claim release eligibility")
    if live_text is not None:
        try:
            live = json.loads(live_text)
        except json.JSONDecodeError as exc:
            raise Phase4Error("live Phase 4 artifact is invalid JSON") from exc
        require(live.get("status") == "passed", "live disposable Phase 4 artifact did not pass")
        require(live.get("evidence_mode") == "live_disposable_profile", "live Phase 4 evidence mode is invalid")
        require(live.get("release_eligible") is False, "live Phase 4 artifact cannot claim release eligibility")
    require(isinstance(evidence.get("nonclaims"), list) and evidence["nonclaims"], "Phase 4 nonclaims missing")
    require("headed existing-profile Chrome" in " ".join(evidence["nonclaims"]), "headed Chrome nonclaim missing")

    return {
        "phase": 4,
        "status": values["status"],
        "deterministic": True,
        "release_eligible": False,
        "tasks": len(TASK_IDS),
        "live_disposable": live_text is not None,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    try:
        result = check(args.root.resolve())
    except (OSError, Phase4Error) as exc:
        print(f"check_phase_4_extension: FAIL: {exc}", file=sys.stderr)
        return 1
    print("check_phase_4_extension: PASS")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
