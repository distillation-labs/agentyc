"""Reproduce the historical Phase 0 capability matrix without launching Chrome.

The bundled tool catalog is an archived direct-CDP MCP baseline, not the current
host-backed logical MCP inventory. Offline output records catalog claims only,
not browser support; permissions and behavior remain unknown. This scaffold's
live-probe modes are not implemented and fail closed. No output from this script
is current MCP release evidence.
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
from artifact_envelope import (
    new_nonce,
    redact_for_persistence,
    repository_relative,
    sha256_bytes,
    write_json_atomic,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "browser-task-spaces"
MCP_ROOT = ROOT / "tests" / "fixtures" / "mcp"
CATALOG_PATH = MCP_ROOT / "tool_catalog.json"
MANIFEST_PATH = FIXTURE_ROOT / "manifest.json"
VALID_STATUSES = {"supported", "partial", "unsupported", "legacy-only"}
LIVE_MODES = {"target", "headed", "managed"}
CATALOG_SOURCE = CATALOG_PATH.relative_to(ROOT).as_posix()
MAX_MANIFEST_BYTES = 512 * 1024
MAX_FIXTURE_BYTES = 2 * 1024 * 1024
MAX_JSON_ITEMS = 512
MAX_JSON_DEPTH = 16
MAX_JSON_NODES = 20_000


def sha256_file(path: Path, *, max_bytes: int | None = None) -> str:
    digest = hashlib.sha256()
    total = 0
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if max_bytes is not None and total > max_bytes:
                raise ValueError("fixture exceeds the bounded read limit")
            digest.update(chunk)
    return digest.hexdigest()


def _validate_json_shape(value: Any, *, depth: int = 0, nodes: list[int] | None = None) -> None:
    counters = nodes if nodes is not None else [0]
    counters[0] += 1
    if counters[0] > MAX_JSON_NODES or depth > MAX_JSON_DEPTH:
        raise ValueError("fixture exceeds the bounded JSON limit")
    if isinstance(value, dict):
        if len(value) > MAX_JSON_ITEMS:
            raise ValueError("fixture object exceeds the bounded item limit")
        for key, child in value.items():
            if not isinstance(key, str) or len(key) > 512:
                raise ValueError("fixture key is invalid or too long")
            _validate_json_shape(child, depth=depth + 1, nodes=counters)
    elif isinstance(value, list):
        if len(value) > MAX_JSON_ITEMS:
            raise ValueError("fixture list exceeds the bounded item limit")
        for child in value:
            _validate_json_shape(child, depth=depth + 1, nodes=counters)
    elif isinstance(value, str) and len(value) > MAX_FIXTURE_BYTES:
        raise ValueError("fixture string exceeds the bounded text limit")


def read_json(path: Path) -> Any:
    try:
        raw = path.read_bytes()
        if len(raw) > MAX_MANIFEST_BYTES:
            raise ValueError("fixture exceeds the bounded read limit")
        value = json.loads(raw.decode("utf-8"))
        _validate_json_shape(value)
        return value
    except (FileNotFoundError, json.JSONDecodeError, UnicodeError, ValueError) as exc:
        if isinstance(exc, ValueError) and str(exc).startswith("fixture"):
            raise
        raise ValueError(f"invalid JSON fixture {repository_relative(path)}") from exc


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
        raise ValueError("fixture is not a local file")
    try:
        current.resolve().relative_to(FIXTURE_ROOT.resolve())
    except (OSError, ValueError) as exc:
        raise ValueError("fixture path is outside the fixture root") from exc
    if current.stat().st_size > MAX_FIXTURE_BYTES:
        raise ValueError("fixture exceeds the bounded read limit")
    return current


def load_fixtures() -> tuple[dict[str, Any], list[dict[str, Any]]]:
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
        raise ValueError(f"invalid JSON fixture {repository_relative(MANIFEST_PATH)}") from exc
    _validate_json_shape(manifest)
    if not isinstance(manifest, dict):
        raise TypeError("browser fixture manifest must be an object")
    fixtures = manifest.get("fixtures")
    if not isinstance(fixtures, list) or not fixtures:
        raise ValueError("browser fixture manifest has no fixtures")
    normalized: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    seen_files: set[str] = set()
    for fixture in fixtures:
        if not isinstance(fixture, dict):
            raise TypeError("each browser fixture must be an object")
        name = fixture.get("name")
        relative = fixture.get("file")
        if not isinstance(name, str) or not name or len(name) > 128:
            raise TypeError("each browser fixture needs a bounded string name")
        if not isinstance(relative, str):
            raise TypeError("each browser fixture needs a string file")
        if name in seen_names or relative in seen_files:
            raise ValueError("browser fixture names and files must be unique")
        path = _safe_fixture_path(relative)
        seen_names.add(name)
        seen_files.add(relative)
        record = dict(fixture)
        record.update({"sha256": sha256_file(path, max_bytes=MAX_FIXTURE_BYTES), "bytes": path.stat().st_size})
        normalized.append(record)
    manifest = dict(manifest)
    manifest["path"] = MANIFEST_PATH.relative_to(ROOT).as_posix()
    manifest["sha256"] = sha256_bytes(raw_manifest)
    manifest["bytes"] = len(raw_manifest)
    return manifest, normalized


def load_catalog() -> list[dict[str, str]]:
    catalog = read_json(CATALOG_PATH)
    if not isinstance(catalog, dict):
        raise TypeError("MCP capability catalog must be an object")
    tools = catalog.get("tools")
    if not isinstance(tools, list) or not tools:
        raise ValueError("MCP capability catalog has no tools")
    seen: set[str] = set()
    result: list[dict[str, str]] = []
    for item in tools:
        if not isinstance(item, dict):
            raise TypeError("each MCP catalog item must be an object")
        name = item.get("name")
        category = item.get("category")
        status = item.get("status")
        if not isinstance(name, str) or not isinstance(category, str) or not isinstance(status, str):
            raise TypeError("each MCP catalog item needs string fields")
        if name in seen:
            raise ValueError(f"duplicate MCP operation: {name}")
        if status not in VALID_STATUSES:
            raise ValueError(f"invalid status {status!r} for {name}")
        seen.add(name)
        result.append({"name": name, "category": category, "status": status})
    return result


def observed_metadata(mode: str) -> dict[str, Any]:
    """Return explicit per-operation metadata without inventing live observations."""
    if mode == "offline":
        status = "not-observed"
        reason = "offline mode does not inspect Chrome permissions, URL domains, version behavior, or error responses"
    else:
        status = "not-available"
        reason = "this lane has no implemented live operation probe"
    return {
        "status": status,
        "permissions": {"status": status, "values": [], "reason": reason},
        "domains": {"status": status, "values": [], "reason": reason},
        "chrome_versions": {"status": status, "values": [], "reason": reason},
        "error_behavior": {"status": status, "values": [], "reason": reason},
    }


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
            missing.append("the supplied browser executable")
        if not profile_dir:
            missing.append("--profile-dir for managed mode")
        elif not Path(profile_dir).is_dir():
            missing.append("the supplied existing profile directory")
    return missing


def _fixture_set_hash(fixtures: list[dict[str, Any]]) -> str:
    return hashlib.sha256(
        "\n".join(f"{item['name']}:{item['sha256']}" for item in fixtures).encode("utf-8")
    ).hexdigest()


def build_matrix(
    mode: str,
    fixtures: list[dict[str, Any]],
    tools: list[dict[str, str]],
    manifest_metadata: dict[str, Any] | None = None,
    *,
    nonce: str | None = None,
) -> dict[str, Any]:
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
                "operation_metadata": observed_metadata(mode),
            }
        )
    fixture_set_sha256 = _fixture_set_hash(fixtures)
    fixture_records = [
        {
            "name": item["name"],
            "file": item["file"],
            "sha256": item["sha256"],
            "bytes": item["bytes"],
            **{key: item[key] for key in ("purpose", "expected_controls") if key in item},
        }
        for item in fixtures
    ]
    return {
        "schema_version": 1,
        "phase": 0,
        "kind": "capability-matrix",
        "mode": mode,
        "status": "offline-catalog-only" if offline else "live-probe-required",
        "nonce": nonce,
        "manifest_sha256": manifest_metadata.get("sha256") if manifest_metadata else None,
        "baseline_manifest": {
            "path": manifest_metadata.get("path") if manifest_metadata else MANIFEST_PATH.relative_to(ROOT).as_posix(),
            "sha256": manifest_metadata.get("sha256") if manifest_metadata else None,
            "fixture_set": manifest_metadata.get("fixture_set") if manifest_metadata else None,
        },
        "fixture_set_sha256": fixture_set_sha256,
        "fixture_binding": {
            "manifest_path": manifest_metadata.get("path") if manifest_metadata else MANIFEST_PATH.relative_to(ROOT).as_posix(),
            "manifest_sha256": manifest_metadata.get("sha256") if manifest_metadata else None,
            "fixture_set_sha256": fixture_set_sha256,
            "fixtures": fixture_records,
        },
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
        "metadata_policy": {
            "required_per_operation": [
                "permissions",
                "domains",
                "chrome_versions",
                "error_behavior",
            ],
            "unknown_is_explicit": True,
            "live_observation_required_for_claims": True,
            "source": "scripts/run_capability_matrix.py",
        },
        "probe": {
            "required": mode in LIVE_MODES,
            "status": probe_status,
            "reason": probe_reason,
        },
        "fixtures": fixture_records,
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
    if requested.exists() and requested.is_dir():
        raise ValueError("matrix output must be a file")
    resolved = requested.resolve()
    artifacts = (ROOT / "artifacts").resolve()
    try:
        resolved.relative_to(artifacts)
    except ValueError as error:
        raise ValueError("matrix path must be inside artifacts/") from error
    if resolved == artifacts:
        raise ValueError("matrix path must be a child of artifacts/")
    return resolved


def add_matrix_envelope(matrix: dict[str, Any]) -> dict[str, Any]:
    nonce = matrix.get("nonce") if isinstance(matrix.get("nonce"), str) else None
    add_envelope(
        matrix,
        kind="capability-matrix",
        nonce=nonce,
        build_tuple={
            "matrix_script": repository_relative(Path(__file__)),
            "matrix_script_sha256": sha256_file(Path(__file__)),
            "fixture_manifest": repository_relative(MANIFEST_PATH),
            "fixture_manifest_sha256": matrix.get("manifest_sha256"),
        },
    )
    return matrix


def write_matrix(path: Path, matrix: dict[str, Any]) -> None:
    add_matrix_envelope(matrix)
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
        manifest, fixtures = load_fixtures()
        tools = load_catalog()
    except (TypeError, ValueError, OSError) as exc:
        print(f"capability matrix error: {type(exc).__name__}", file=sys.stderr)
        return 2

    if args.dry_run:
        if args.matrix:
            try:
                args.matrix = safe_matrix_path(args.matrix)
            except ValueError as exc:
                print(f"capability matrix error: {type(exc).__name__}", file=sys.stderr)
                return 2
        print(
            json.dumps(
                {
                    "mode": args.mode,
                    "action": "validate local catalog and fixtures",
                    "would_probe_browser": False,
                    "would_launch_browser": False,
                    "would_download_browser": False,
                    "would_write": repository_relative(args.matrix) if args.matrix else None,
                    "fixture_count": len(fixtures),
                    "operation_count": len(tools),
                    "manifest_sha256": manifest.get("sha256"),
                },
                indent=2,
                sort_keys=True,
            )
        )
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

    matrix = build_matrix(args.mode, fixtures, tools, manifest, nonce=new_nonce())
    matrix = redact_for_persistence(matrix)
    if args.matrix:
        try:
            write_matrix(args.matrix, matrix)
        except (OSError, TypeError, ValueError) as exc:
            print(f"capability matrix error: {type(exc).__name__}", file=sys.stderr)
            return 2
        print(f"wrote offline capability matrix: {repository_relative(args.matrix)} ({len(tools)} operations)")
    else:
        add_matrix_envelope(matrix)
        print(json.dumps(matrix, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
