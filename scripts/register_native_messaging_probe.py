#!/usr/bin/env python3
"""Explicitly install or check the Phase 0 Native Messaging host registration.

This script never launches or downloads Chrome. Installation is only performed
when the explicit ``install``/``--install`` action is requested; ``check`` is
read-only.
"""

from __future__ import annotations

import argparse
import contextlib
import errno
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows has no fcntl
    fcntl = None

ROOT = Path(__file__).resolve().parents[1]
HOST_NAME = "com.agentyc.p0_probe"
DISPLAY_MANIFEST_FILENAME = "native-host-manifest.json"
HOST_PATH = ROOT / "tests" / "probes" / "native_probe"
TEMPLATE_PATH = ROOT / "extension" / "probes" / "native_host_manifest.macos.json"
ORIGIN_PATTERN = re.compile(r"^chrome-extension://[a-p]{32}$")
INSTALL_LOCK_NAME = f".{HOST_NAME}.lock"


class RegistrationBusy(RuntimeError):
    """Another explicit registration mutation owns the lock."""


def _assert_no_symlink_components(path: Path, *, message: str) -> None:
    current = path
    while current != current.parent:
        try:
            if current.is_symlink():
                raise ValueError(message)
        except OSError as error:
            raise ValueError(message) from error
        current = current.parent


def _fsync_directory(directory: Path) -> None:
    descriptor = os.open(str(directory), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@contextlib.contextmanager
def registration_lock(lock_path: Path):
    target = Path(lock_path)
    _assert_no_symlink_components(target, message="registration lock components must not be symlinks")
    target.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if fcntl is None:
        flags |= os.O_EXCL
    descriptor: int | None = None
    remove_lock = False
    try:
        descriptor = os.open(str(target), flags, 0o600)
        handle = os.fdopen(descriptor, "a+b")
        descriptor = None
        remove_lock = fcntl is None
        try:
            if fcntl is not None:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError as error:
                    if error.errno in {errno.EACCES, errno.EAGAIN}:
                        raise RegistrationBusy("registration lock is held") from error
                    raise
            yield handle
        finally:
            if fcntl is not None:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
            handle.close()
    except FileExistsError as error:
        raise RegistrationBusy("registration lock is held") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if remove_lock:
            try:
                target.unlink()
            except OSError:
                pass


def _atomic_write(path: Path, payload: bytes, *, replace: bool = True) -> None:
    target = Path(path)
    _assert_no_symlink_components(target, message="registration target components must not be symlinks")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("wb", dir=target.parent, prefix=f".{target.name}.tmp-", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
            os.fchmod(handle.fileno(), 0o644)
        if replace:
            os.replace(temporary, target)
        else:
            os.link(temporary, target, follow_symlinks=False)
            temporary.unlink()
        temporary = None
        _fsync_directory(target.parent)
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def _safe_default_manifest_path() -> Path:
    candidate = default_manifest_path()
    _assert_no_symlink_components(candidate, message="user registration path cannot contain symlinks")
    return candidate


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
    raw = Path(value).expanduser()
    current = raw
    while current != current.parent:
        if current.is_symlink():
            raise ValueError("host path cannot contain symlinks")
        current = current.parent
    requested = raw.resolve()
    expected = HOST_PATH.resolve()
    if requested != expected:
        raise ValueError("host path must be the repository test probe")
    if not requested.is_file() or not os.access(requested, os.X_OK):
        raise ValueError("host path is missing or not executable")
    return requested


def safe_manifest_path(value: str) -> Path:
    requested = Path(value).expanduser()
    if not requested.is_absolute():
        requested = ROOT / requested
    _assert_no_symlink_components(requested, message="custom manifest path cannot contain symlinks")
    resolved = requested.resolve(strict=False)
    artifact_root = (ROOT / "artifacts").resolve()
    try:
        resolved.relative_to(artifact_root)
    except ValueError as error:
        raise ValueError("custom manifest path must be inside artifacts/") from error
    if resolved == artifact_root:
        raise ValueError("custom manifest path must be a file under artifacts/")
    if resolved.exists() and resolved.is_dir():
        raise ValueError("custom manifest path must be a file")
    return resolved


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant: {value}")


def _manifest(host_path: Path, extension_origin: str) -> dict[str, Any]:
    validate_origin(extension_origin)
    try:
        template = json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"), parse_constant=_reject_json_constant)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError("Native Messaging template is unreadable") from error
    if not isinstance(template, dict):
        raise TypeError("Native Messaging template must be an object")
    template["path"] = str(host_path.resolve())
    template["allowed_origins"] = [f"{extension_origin}/"]
    return template


def _resolve_manifest_path(manifest_path: Path | None) -> Path:
    if manifest_path is None:
        return _safe_default_manifest_path()
    requested = Path(manifest_path).expanduser()
    _assert_no_symlink_components(requested, message="manifest path cannot contain symlinks")
    if sys.platform in {"darwin"} or sys.platform.startswith("linux"):
        default = default_manifest_path()
        if requested.resolve(strict=False) == default.resolve(strict=False):
            return _safe_default_manifest_path()
    return safe_manifest_path(str(requested))


def _redacted_result(status: str, manifest_path: Path, *, manifest_present: bool = False, host_present: bool = False, origin_matches: bool = False, host_path_matches: bool = False, detail: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": status,
        "manifest_filename": DISPLAY_MANIFEST_FILENAME,
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
        destination = _resolve_manifest_path(manifest_path)
    except (ValueError, RuntimeError) as error:
        return _redacted_result("rejected", Path("native-host-manifest.json"), detail=str(error))

    if destination.is_symlink():
        return _redacted_result("rejected", destination, manifest_present=True, host_present=host_path.is_file(), detail="registration manifest must not be a symlink")
    if not destination.is_file():
        return _redacted_result("unavailable", destination, host_present=host_path.is_file(), detail="registration manifest is not installed")
    try:
        actual = json.loads(destination.read_text(encoding="utf-8"), parse_constant=_reject_json_constant)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        return _redacted_result("rejected", destination, manifest_present=True, host_present=host_path.is_file(), detail="registration manifest is unreadable")
    if not isinstance(actual, dict):
        return _redacted_result("rejected", destination, manifest_present=True, host_present=host_path.is_file(), detail="registration JSON must be an object")

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
        destination = _resolve_manifest_path(manifest_path)
    except ValueError as error:
        return _redacted_result("rejected", manifest_path or Path("native-host-manifest.json"), detail=str(error))
    try:
        payload = (json.dumps(_manifest(host_path, extension_origin), indent=2, sort_keys=True) + "\n").encode("utf-8")
    except (TypeError, ValueError) as error:
        return _redacted_result("rejected", destination, detail=str(error))
    try:
        with registration_lock(destination.parent / INSTALL_LOCK_NAME):
            _assert_no_symlink_components(destination, message="registration manifest must not be a symlink")
            if destination.exists():
                try:
                    current = json.loads(destination.read_text(encoding="utf-8"), parse_constant=_reject_json_constant)
                except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
                    return _redacted_result("rejected", destination, manifest_present=True, detail="registration manifest is unreadable")
                if not isinstance(current, dict):
                    return _redacted_result("rejected", destination, manifest_present=True, detail="registration JSON must be an object")
                if current == json.loads(payload.decode("utf-8")):
                    return check_registration(extension_origin, host_path, destination)
                if not replace:
                    return _redacted_result("rejected", destination, manifest_present=True, detail="manifest exists; pass --replace for explicit replacement")
            _atomic_write(destination, payload, replace=replace)
    except RegistrationBusy:
        return _redacted_result("busy", destination, detail="another registration mutation is in progress")
    except (OSError, ValueError):
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
    manifest_path: Path | None = None
    try:
        host_path = safe_host_path(args.host_path)
        manifest_path = _resolve_manifest_path(Path(args.manifest_path).expanduser()) if args.manifest_path else None
        result = check_registration(args.extension_origin, host_path, manifest_path) if selected == "check" else install_registration(args.extension_origin, host_path, manifest_path, args.replace)
    except (OSError, UnicodeError, ValueError, RuntimeError) as error:
        result = _redacted_result("rejected", manifest_path or Path("native-host-manifest.json"), detail=str(error))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "installed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
