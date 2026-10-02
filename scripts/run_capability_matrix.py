
"""Generate the Phase 0 capability matrix without launching a browser.

The default is an offline catalog report: operation statuses are catalog claims,
not observed browser support, and operation permissions/behavior remain unknown.
Target/headed/managed modes are explicit live-probe lanes and fail closed when
their prerequisites or probe implementation are unavailable; this script never
downloads or launches Chrome and never requires a CDP URL for an existing target.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from artifact_envelope import envelope as add_envelope
from artifact_envelope import write_json_atomic

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "browser-task-spaces"
MCP_ROOT = ROOT / "tests" / "fixtures" / "mcp"
CATALOG_PATH = MCP_ROOT / "tool_catalog.json"
MANIFEST_PATH = FIXTURE_ROOT / "manifest.json"
VALID_STATUSES = {"supported", "partial", "unsupported", "legacy-only"}
LIVE_MODES = {"target", "headed", "managed"}
CATALOG_SOURCE = CATALOG_PATH.relative_to(ROOT).as_posix()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"missing fixture: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON fixture {path}: {exc}") from exc


def load_fixtures() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    manifest = read_json(MANIFEST_PATH)
    fixtures = manifest.get("fixtures")
    if not isinstance(fixtures, list) or not fixtures:
        raise ValueError("browser fixture manifest has no fixtures")
    normalized: list[dict[str, Any]] = []
    for fixture in fixtures:
        name = fixture.get("name")
        relative = fixture.get("file")
        if not isinstance(name, str) or not isinstance(relative, str):
            raise TypeError("each browser fixture needs string name and file")
        path = FIXTURE_ROOT / relative
        if path.parent != FIXTURE_ROOT or not path.is_file():
            raise ValueError(f"fixture {name!r} is not a local file")
        record = dict(fixture)
        record["sha256"] = sha256_file(path)
        record["bytes"] = path.stat().st_size
        normalized.append(record)
    return manifest, normalized


def load_catalog() -> list[dict[str, str]]:
    catalog = read_json(CATALOG_PATH)
    tools = catalog.get("tools")
    if not isinstance(tools, list) or not tools:
        raise ValueError("MCP capability catalog has no tools")
    seen: set[str] = set()
    result: list[dict[str, str]] = []
    for item in tools:
        name = item.get("name")
        category = item.get("category")
        status = item.get("status")
        if not all(isinstance(value, str) for value in (name, category, status)):
            raise ValueError("each MCP catalog item needs name, category, and status")
        if name in seen:
            raise ValueError(f"duplicate MCP operation: {name}")
        if status not in VALID_STATUSES:
            raise ValueError(f"invalid status {status!r} for {name}")
        seen.add(name)
        result.append({"name": name, "category": category, "status": status})
    return result


def browser_candidates() -> list[Path]:
    candidates = [
        os.environ.get("AGENTYC_CHROME_PATH", ""),
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Google Chrome Beta.app/Contents/MacOS/Google Chrome Beta",
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
    ]
    paths = [Path(value) for value in candidates if value]
    for command in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        found = shutil.which(command)
        if found:
            paths.append(Path(found))
    unique: list[Path] = []
    for path in paths:
        if path not in unique:
            unique.append(path)
    return unique


def running_chrome() -> bool:
    if platform.system() == "Windows":
        return False
    try:
        result = subprocess.run(
            ["ps", "-axo", "comm="],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    names = {Path(line.strip()).name.lower() for line in result.stdout.splitlines() if line.strip()}
    return bool(names & {"google chrome", "google-chrome", "google-chrome-stable", "chromium", "chromium-browser"})


def live_prerequisites(mode: str, browser_executable: str | None, profile_dir: str | None) -> list[str]:
    missing: list[str] = []
    if mode in {"target", "headed"}:
        executable = Path(browser_executable) if browser_executable else next(
            (candidate for candidate in browser_candidates() if candidate.is_file()), None
        )
        if executable is None or not executable.is_file():
            missing.append("an installed Chrome/Chromium executable")
        if platform.system() == "Linux" and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
            missing.append("a headed display (DISPLAY or WAYLAND_DISPLAY)")
        if not running_chrome():
            missing.append("an already running headed Chrome target")
    elif mode == "managed":
        if not browser_executable:
            missing.append("--browser-executable for managed mode")
        elif not Path(browser_executable).is_file():
            missing.append(f"browser executable {browser_executable!r}")
        if not profile_dir:
            missing.append("--profile-dir for managed mode")
        elif not Path(profile_dir).is_dir():
            missing.append(f"existing profile directory {profile_dir!r}")
    return missing


def build_matrix(mode: str, fixtures: list[dict[str, Any]], tools: list[dict[str, str]]) -> dict[str, Any]:
    offline = mode == "offline"
    probe_status = "not-run" if offline else "not-available"
    probe_reason = (
        "offline mode reads the local catalog and fixtures only; no operation was probed"
        if offline
        else "live capability probe is not implemented by this scaffold"
    )
    entries: list[dict[str, Any]] = []
    for tool in tools:
        entries.append(
            {
                "name": tool["name"],
                "catalog_claim": {
                    "category": tool["category"],
                    "status": tool["status"],
                    "source": CATALOG_SOURCE,
                },
                "observed_probe": {
                    "status": "not-observed",
                    "reason": probe_reason,
                },
            }
        )
    return {
        "schema_version": 1,
        "phase": 0,
        "kind": "capability-matrix",
        "mode": mode,
        "status": "offline-catalog-only" if offline else "live-probe-required",
        "evidence": {
            "catalog": {
                "source": CATALOG_SOURCE,
                "status": "loaded",
                "claim_count": len(tools),
            },
            "probe": {
                "status": probe_status,
                "observed_operation_count": 0,
                "reason": probe_reason,
            },
        },
        "browser_policy": {
            "automatic_launch": False,
            "automatic_download": False,
            "cdp_url_required_for_target": False,
            "target_description": "existing user-approved headed Chrome",
            "managed_description": "explicit temporary test lane; not a product fallback",
        },
        "probe": {
            "required": mode in LIVE_MODES,
            "status": probe_status,
            "reason": probe_reason,
        },
        "fixtures": fixtures,
        "operations": entries,
        "coverage": {
            "basis": "catalog_claims",
            "operation_count": len(entries),
            "observed_operation_count": 0,
            "statuses": {
                status: sum(item["catalog_claim"]["status"] == status for item in entries)
                for status in sorted(VALID_STATUSES)
            },
            "categories": sorted({item["catalog_claim"]["category"] for item in entries}),
        },
    }


def safe_matrix_path(value: Path) -> Path:
    requested = value if value.is_absolute() else ROOT / value
    current = requested
    while current != current.parent:
        if current.is_symlink():
            raise ValueError("matrix path components must not be symlinks")
        current = current.parent
    resolved = requested.resolve()
    artifacts = (ROOT / "artifacts").resolve()
    try:
        resolved.relative_to(artifacts)
    except ValueError as error:
        raise ValueError("matrix path must be inside artifacts/") from error
    return resolved


def write_matrix(path: Path, matrix: dict[str, Any]) -> None:
    add_envelope(matrix, kind="capability-matrix")
    write_json_atomic(safe_matrix_path(path), matrix)


def parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build the Phase 0 capability matrix from local deterministic fixtures.",
        epilog="offline is safe by default. target/headed inspect existing Chrome only; managed requires explicit executable/profile. No mode launches or downloads Chrome.",
    )
    parser.add_argument("--mode", choices=("offline", "target", "headed", "managed"), default="offline")
    parser.add_argument("--matrix", type=Path, help="write JSON to this path; stdout is used when omitted")
    parser.add_argument("--browser-executable", help="explicit executable for target/headed or required managed lane")
    parser.add_argument("--profile-dir", help="existing profile directory required by managed lane")
    parser.add_argument("--dry-run", action="store_true", help="validate inputs and print the plan without probing or writing")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        _, fixtures = load_fixtures()
        tools = load_catalog()
    except (TypeError, ValueError) as exc:
        print(f"capability matrix error: {exc}", file=sys.stderr)
        return 2

    if args.dry_run:
        if args.matrix:
            try:
                args.matrix = safe_matrix_path(args.matrix)
            except ValueError as exc:
                print(f"capability matrix error: {exc}", file=sys.stderr)
                return 2
        print(json.dumps({
            "mode": args.mode,
            "action": "validate local catalog and fixtures",
            "would_probe_browser": False,
            "would_launch_browser": False,
            "would_download_browser": False,
            "would_write": str(args.matrix) if args.matrix else None,
            "fixture_count": len(fixtures),
            "operation_count": len(tools),
        }, indent=2, sort_keys=True))
        return 0

    if args.mode in LIVE_MODES:
        missing = live_prerequisites(args.mode, args.browser_executable, args.profile_dir)
        if missing:
            print(
                "required live capability probe unavailable: " + "; ".join(missing) + ". "
                "No browser was launched or downloaded; use --mode offline for the local baseline.",
                file=sys.stderr,
            )
            return 2
        print(
            "required live capability probe unavailable: this scaffolding does not drive Chrome. "
            "Use an explicit probe implementation; no CDP URL is required for target mode.",
            file=sys.stderr,
        )
        return 2

    matrix = build_matrix(args.mode, fixtures, tools)
    rendered = json.dumps(matrix, indent=2, sort_keys=True) + "\n"
    if args.matrix:
        try:
            write_matrix(args.matrix, matrix)
        except (OSError, ValueError) as exc:
            print(f"capability matrix error: {type(exc).__name__}", file=sys.stderr)
            return 2
        print(f"wrote offline capability matrix: {args.matrix} ({len(tools)} operations)")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
