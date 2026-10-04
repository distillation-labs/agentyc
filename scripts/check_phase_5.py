#!/usr/bin/env python3
"""Fail-closed structural and traceability checker for Phase 5.

This checker intentionally distinguishes implementation/test evidence from the
Phase 4 real-Chrome dependency and production-path performance evidence.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "tests/phase-5-manifest.yaml"
DEFAULT_PLAN = ROOT / "docs/exec-plans/active/agentyc-browser-task-spaces/plans/phase-5-context-and-automation.md"
DEFAULT_TRACEABILITY = ROOT / "docs/exec-plans/active/agentyc-browser-task-spaces/research/phase-5-traceability.md"
TASK_IDS = tuple(f"P5-T{i}" for i in range(1, 9))
STATUSES = {"pending", "active", "complete"}
TASK_STATUSES = {"pending", "partial", "implemented_unverified", "complete", "blocked"}


class Phase5Error(ValueError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise Phase5Error(message)


def read_text(path: Path, label: str) -> str:
    require(path.is_file() and not path.is_symlink(), f"{label} is missing or symlinked: {path}")
    require(path.stat().st_size <= 4 * 1024 * 1024, f"{label} is oversized: {path}")
    return path.read_text(encoding="utf-8")


def read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(read_text(path, label))
    except json.JSONDecodeError as exc:
        raise Phase5Error(f"{label} must be strict JSON: {exc}") from exc
    require(isinstance(value, dict), f"{label} root must be an object")
    return value


def check(manifest_path: Path = DEFAULT_MANIFEST, *, require_complete: bool = False) -> dict[str, Any]:
    manifest = read_json(manifest_path, "Phase 5 manifest")
    plan = read_text(DEFAULT_PLAN, "Phase 5 plan")
    traceability = read_text(DEFAULT_TRACEABILITY, "Phase 5 traceability")

    require(manifest.get("schema_version") == 1, "Phase 5 manifest schema_version must be 1")
    require(manifest.get("phase") == 5, "Phase 5 manifest phase must be 5")
    status = manifest.get("status")
    require(status in STATUSES, "Phase 5 manifest status is invalid")
    require(manifest.get("release_eligible") is False or status == "complete", "pending/active Phase 5 cannot be release eligible")
    require(manifest.get("plan") == "docs/exec-plans/active/agentyc-browser-task-spaces/plans/phase-5-context-and-automation.md", "Phase 5 plan path is invalid")
    require(manifest.get("traceability") == "docs/exec-plans/active/agentyc-browser-task-spaces/research/phase-5-traceability.md", "Phase 5 traceability path is invalid")
    require("status: pending" in plan or "status: active" in plan or "status: complete" in plan, "Phase 5 plan front matter status is missing")
    require("P5-T1" in traceability and "P5-T8" in traceability, "traceability does not cover all Phase 5 tasks")

    dependency = manifest.get("dependency")
    require(isinstance(dependency, dict), "Phase 5 dependency record is required")
    dependency = cast(dict[str, Any], dependency)
    require(dependency.get("phase") == 4, "Phase 5 must depend on Phase 4")
    require(dependency.get("status") in STATUSES, "Phase 5 dependency status is invalid")
    require(isinstance(dependency.get("real_chrome_gate"), bool), "Phase 5 real-Chrome dependency gate is required")

    tasks = manifest.get("tasks")
    require(isinstance(tasks, list) and len(tasks) == len(TASK_IDS), "Phase 5 task list must contain exactly eight tasks")
    tasks = cast(list[dict[str, Any]], tasks)
    by_id: dict[str, dict[str, Any]] = {}
    for raw_task in tasks:
        require(isinstance(raw_task, dict), "Phase 5 task entry must be an object")
        task = cast(dict[str, Any], raw_task)
        task_id = cast(str, task.get("id"))
        require(isinstance(task_id, str), "Phase 5 task id must be a string")
        require(task_id in TASK_IDS and task_id not in by_id, f"invalid or duplicate Phase 5 task id: {task_id}")
        require(task.get("status") in TASK_STATUSES, f"invalid status for {task_id}")
        require(isinstance(task.get("validation"), list) and task["validation"], f"validation commands are missing for {task_id}")
        require(all(isinstance(command, str) and command for command in task["validation"]), f"validation command is invalid for {task_id}")
        require(isinstance(task.get("evidence"), list), f"evidence list is missing for {task_id}")
        require(isinstance(task.get("blockers"), list), f"blocker list is missing for {task_id}")
        by_id[task_id] = task
    require(set(by_id) == set(TASK_IDS), "Phase 5 task IDs are incomplete")

    live = manifest.get("required_live_evidence")
    require(isinstance(live, dict), "required live evidence is missing")
    require(bool(live), "required live evidence is empty")
    live = cast(dict[str, bool], live)
    require(all(isinstance(value, bool) for value in live.values()), "required live evidence values must be boolean")
    required_checks = manifest.get("required_checks")
    require(isinstance(required_checks, list), "required checks are missing")
    require(bool(required_checks), "required checks are empty")
    required_checks = cast(list[str], required_checks)
    require(all(isinstance(command, str) and command for command in required_checks), "required check command is invalid")
    nonclaims = manifest.get("nonclaims")
    require(isinstance(nonclaims, list), "Phase 5 nonclaims are required")
    require(bool(nonclaims), "Phase 5 nonclaims are empty")
    nonclaims = cast(list[str], nonclaims)

    live_ready = dependency["real_chrome_gate"] and all(live.values())
    all_tasks_complete = all(task["status"] == "complete" for task in tasks)
    if status == "complete" or require_complete:
        require(status == "complete", "Phase 5 is not marked complete")
        require(dependency["status"] == "complete" and live_ready, "Phase 5 dependency/live gates are incomplete")
        require(all_tasks_complete, "Phase 5 has incomplete tasks")
        require(manifest.get("release_eligible") is True, "complete Phase 5 must be release eligible")
    else:
        require(not manifest.get("release_eligible"), "non-complete Phase 5 must not be release eligible")

    return {
        "phase": 5,
        "status": status,
        "dependency_status": dependency["status"],
        "live_ready": live_ready,
        "all_tasks_complete": all_tasks_complete,
        "release_eligible": manifest.get("release_eligible"),
        "task_count": len(tasks),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--require-complete", action="store_true")
    args = parser.parse_args()
    try:
        result = check(args.manifest, require_complete=args.require_complete)
    except (OSError, Phase5Error) as exc:
        print(f"phase-5-check: FAIL: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
