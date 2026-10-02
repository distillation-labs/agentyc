#!/usr/bin/env python3
"""Explicitly install or check the Phase 0 Native Messaging host registration.

This script never launches or downloads Chrome. Installation is only performed
when the explicit ``install``/``--install`` action is requested; ``check`` is
read-only.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
HOST_NAME = "com.agentyc.p0_probe"
HOST_PATH = ROOT / "tests" / "probes" / "native_probe"
TEMPLATE_PATH = ROOT / "extension" / "probes" / "native_host_manifest.macos.json"
ORIGIN_PATTERN = re.compile(r"^chrome-extension://[a-p]{32}$")


def validate_origin(value: str) -> str:
    if not ORIGIN_PATTERN.fullmatch(value):
        raise ValueError("extension origin must be an exact chrome-extension://... origin without a trailing slash")
    return value


def default_manifest_path() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Google" / "Chrome" / "NativeMessagingHosts" / f"{HOST_NAME}.json"
    if sys.platform.startswith("linux"):
        return Path.home() / ".config" / "google-chrome" / "NativeMessagingHosts" / f"{HOST_NAME}.json"
    raise RuntimeError("automatic registration path is unsupported on this platform")


def safe_host_path(value: str) -> Path:
    requested = Path(value).expanduser().resolve()
    expected = HOST_PATH.resolve()
    if requested != expected:
        raise ValueError("host path must be the repository test probe")
    if not requested.is_file() or not os.access(requested, os.X_OK):
        raise ValueError("host path is missing or not executable")
    return requested


def safe_manifest_path(value: str | None) -> Path | None:
    if value is None:
        return None
    requested = Path(value).expanduser()
    if not requested.is_absolute():
        requested = ROOT / requested
    resolved = requested.resolve()
    artifact_root = (ROOT / "artifacts").resolve()
    try:
        resolved.relative_to(artifact_root)
    except ValueError as error:
        raise ValueError("custom manifest path must be inside artifacts/") from error
    current = requested
    while current != current.parent:
        if current.is_symlink():
            raise ValueError("custom manifest path cannot contain symlinks")
        current = current.parent
    return resolved


def _manifest(host_path: Path, extension_origin: str) -> dict[str, Any]:
    validate_origin(extension_origin)
    template = json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))
    template["path"] = str(host_path.resolve())
    template["allowed_origins"] = [f"{extension_origin}/"]
    return template


def _redacted_result(status: str, manifest_path: Path, *, manifest_present: bool = False, host_present: bool = False, origin_matches: bool = False, host_path_matches: bool = False, detail: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": status,
        "manifest_filename": manifest_path.name,
        "manifest_present": manifest_present,
        "host_present": host_present,
        "origin_matches": origin_matches,
        "host_path_matches": host_path_matches,
        "chrome_launch": "never",
        "chrome_download": "never",
        "secrets_logged": False,
    }
    if detail:
        result["detail"] = detail
    return result


def check_registration(extension_origin: str, host_path: Path = HOST_PATH, manifest_path: Path | None = None) -> dict[str, Any]:
    """Check one exact registration without changing the filesystem."""
    try:
        validate_origin(extension_origin)
        host_path = safe_host_path(str(host_path))
        destination = safe_manifest_path(str(manifest_path)) if manifest_path is not None else default_manifest_path()
    except (ValueError, RuntimeError) as error:
        return _redacted_result("rejected", Path("native-host-manifest.json"), detail=str(error))

    if not destination.is_file():
        return _redacted_result("unavailable", destination, host_present=host_path.is_file(), detail="registration manifest is not installed")
    try:
        actual = json.loads(destination.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return _redacted_result("rejected", destination, manifest_present=True, host_present=host_path.is_file(), detail="registration manifest is unreadable")

    origin_matches = actual.get("allowed_origins") == [f"{extension_origin}/"]
    host_path_matches = actual.get("path") == str(host_path.resolve())
    host_present = host_path.is_file() and os.access(host_path, os.X_OK)
    valid = (
        actual.get("name") == HOST_NAME
        and actual.get("type") == "stdio"
        and origin_matches
        and host_path_matches
        and host_present
    )
    return _redacted_result(
        "installed" if valid else "rejected",
        destination,
        manifest_present=True,
        host_present=host_present,
        origin_matches=origin_matches,
        host_path_matches=host_path_matches,
        detail=None if valid else "registration does not match the requested exact origin and host",
    )


def install_registration(extension_origin: str, host_path: Path = HOST_PATH, manifest_path: Path | None = None, replace: bool = False) -> dict[str, Any]:
    """Install one exact registration; this function is never called implicitly."""
    validate_origin(extension_origin)
    try:
        host_path = safe_host_path(str(host_path))
    except ValueError as error:
        return _redacted_result("rejected", manifest_path or Path("native-host-manifest.json"), detail=str(error))
    destination = safe_manifest_path(str(manifest_path)) if manifest_path is not None else default_manifest_path()
    if destination.exists() and destination.is_symlink():
        return _redacted_result("rejected", destination, detail="registration manifest must not be a symlink")
    payload = json.dumps(_manifest(host_path, extension_origin), indent=2, sort_keys=True) + "\n"
    if destination.exists() and not replace:
        current = destination.read_text(encoding="utf-8")
        if current != payload:
            return _redacted_result("rejected", destination, detail="manifest exists; pass --replace for explicit replacement")
    temporary_path: Path | None = None
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=destination.parent, delete=False) as temporary:
            temporary.write(payload)
            temporary_path = Path(temporary.name)
        temporary_path.chmod(0o644)
        os.replace(temporary_path, destination)
    except OSError:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
        return _redacted_result("unavailable", destination, detail="registration manifest could not be written")
    return check_registration(extension_origin, host_path, destination)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", nargs="?", choices=("install", "check"))
    action_group = parser.add_mutually_exclusive_group()
    action_group.add_argument("--install", action="store_true", help="explicitly write the user-level host manifest")
    action_group.add_argument("--check", action="store_true", help="read and validate the user-level host manifest")
    parser.add_argument("--extension-origin", required=True, help="exact chrome-extension://.../ origin")
    parser.add_argument("--host-path", default=str(HOST_PATH), help=argparse.SUPPRESS)
    parser.add_argument("--manifest-path", help="override the platform manifest path for an explicit test")
    parser.add_argument("--replace", action="store_true", help="allow explicit install to replace a different manifest")
    args = parser.parse_args()
    selected = args.action or ("install" if args.install else "check" if args.check else None)
    if selected is None:
        parser.error("choose the explicit install or check action")
    try:
        host_path = safe_host_path(args.host_path)
        manifest_path = safe_manifest_path(args.manifest_path)
        result = check_registration(args.extension_origin, host_path, manifest_path) if selected == "check" else install_registration(args.extension_origin, host_path, manifest_path, args.replace)
    except (OSError, UnicodeError, ValueError, RuntimeError) as error:
        result = _redacted_result("rejected", manifest_path or Path("native-host-manifest.json"), detail=str(error))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "installed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
