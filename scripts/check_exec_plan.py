#!/usr/bin/env python3
"""Validate the canonical execution-plan registry without external dependencies."""

from __future__ import annotations

import re
import sys
from pathlib import Path

CANONICAL_PHASES = {
    0: "phase-0-discovery.md",
    1: "phase-1-architecture.md",
    2: "phase-2-contracts.md",
    3: "phase-3-core-implementation.md",
    4: "phase-4-extension.md",
    5: "phase-5-context-and-automation.md",
    6: "phase-6-direct-cli-sdk.md",
    7: "phase-7-hardening.md",
    8: "phase-8-mcp-compatibility.md",
}
STATUSES = {"pending", "active", "complete", "blocked"}


class PlanError(ValueError):
    """The plan is not structurally executable."""


def _front_matter(path: Path) -> dict[str, str]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PlanError(f"cannot read {path.name}") from exc
    lines = text.splitlines()
    if len(lines) < 3 or lines[0].strip() != "---":
        raise PlanError(f"{path.name} has no front matter")
    try:
        end = lines.index("---", 1)
    except ValueError as exc:
        raise PlanError(f"{path.name} has unterminated front matter") from exc
    values: dict[str, str] = {}
    for line in lines[1:end]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line or line[: len(line) - len(line.lstrip())] != "":
            raise PlanError(f"{path.name} has invalid front matter")
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip().strip('"\'')
        if not key or not value:
            raise PlanError(f"{path.name} has incomplete front matter")
        if key in values:
            raise PlanError(f"{path.name} repeats front-matter key {key}")
        values[key] = value
    return values


def _index_rows(text: str) -> dict[int, tuple[str, str, str]]:
    rows: dict[int, tuple[str, str, str]] = {}
    pattern = re.compile(
        r"^\|\s*(\d+)\s*\|\s*`([^`]+)`\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|"
    )
    for line in text.splitlines():
        match = pattern.match(line)
        if match:
            number = int(match.group(1))
            row = (match.group(2), match.group(4).strip(), match.group(3).strip())
            if number in rows and rows[number] != row:
                raise PlanError(f"PLAN_INDEX disagrees about phase {number}")
            rows[number] = row
    return rows


def validate(plan_dir: Path) -> None:
    if not plan_dir.is_dir():
        raise PlanError("plan directory is missing")
    if not (plan_dir / "PLAN_INDEX.md").is_file() and (plan_dir / "plans").is_dir():
        plan_dir = plan_dir / "plans"
    index_path = plan_dir / "PLAN_INDEX.md"
    if not index_path.is_file():
        raise PlanError("PLAN_INDEX.md is missing")
    try:
        index_text = index_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PlanError("PLAN_INDEX.md cannot be read") from exc

    rows = _index_rows(index_text)
    if set(rows) != set(CANONICAL_PHASES):
        raise PlanError("PLAN_INDEX.md must contain exactly phases 0 through 8")

    phase_files = {path.name for path in plan_dir.glob("phase-*.md") if path.is_file()}
    expected_files = set(CANONICAL_PHASES.values())
    if phase_files != expected_files:
        missing = sorted(expected_files - phase_files)
        extra = sorted(phase_files - expected_files)
        detail = ", ".join([f"missing: {', '.join(missing)}"] if missing else [])
        if extra:
            detail = f"{detail}; " if detail else ""
            detail += f"unexpected: {', '.join(extra)}"
        raise PlanError(f"canonical phase files mismatch ({detail})")

    metadata: dict[int, dict[str, str]] = {}
    for number, filename in CANONICAL_PHASES.items():
        path = plan_dir / filename
        values = _front_matter(path)
        if values.get("phase") != str(number):
            raise PlanError(f"{filename} phase metadata does not match its filename")
        for required in ("name", "status", "owner", "primary_outcome", "depends_on"):
            if not values.get(required):
                raise PlanError(f"{filename} is missing {required}")
        if values["status"] not in STATUSES:
            raise PlanError(f"{filename} has invalid status")
        expected_dependency = "none" if number == 0 else f"phase-{number - 1}"
        if values["depends_on"] != expected_dependency:
            raise PlanError(f"{filename} has invalid dependency")
        text = path.read_text(encoding="utf-8")
        for heading in ("## Objective", "## Tasks", "## Exit gate"):
            if heading not in text:
                raise PlanError(f"{filename} is missing {heading}")
        metadata[number] = values

        index_filename, index_status, index_dependency = rows[number]
        if index_filename != filename:
            raise PlanError(f"PLAN_INDEX points phase {number} at the wrong file")
        if index_status != metadata[number]["status"]:
            raise PlanError(f"PLAN_INDEX status disagrees for phase {number}")
        if index_dependency != ("none" if number == 0 else str(number - 1)):
            raise PlanError(f"PLAN_INDEX dependency disagrees for phase {number}")

    active = [number for number, values in metadata.items() if values["status"] == "active"]
    if len(active) != 1:
        raise PlanError("exactly one phase must be active")
    for number in range(1, 9):
        predecessor = metadata[number - 1]["status"]
        current = metadata[number]["status"]
        if current in {"complete", "active"} and predecessor not in {"complete", "active"}:
            raise PlanError(f"phase {number} starts before its predecessor is active or complete")


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if len(argv) != 1:
        print("usage: check_exec_plan.py PLAN_DIRECTORY", file=sys.stderr)
        return 2
    try:
        validate(Path(argv[0]))
    except (OSError, PlanError) as exc:
        print(f"check_exec_plan: FAIL: {exc}", file=sys.stderr)
        return 1
    print("check_exec_plan: PASS (9 canonical phases; one active phase)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
