#!/usr/bin/env python3
"""Run the macOS-first installation and rollback preflight.

The default action is a read-only preflight. This script never launches or
downloads Chrome, never installs an extension, and never creates or changes a
Chrome profile. ``--drill`` is the only action that writes the test Native
Messaging registration and then removes exactly what it wrote; it is explicit
and requires an extension ID.

A clean-profile path is only inspected. It must be absent or empty and must be
inside the selected artifact directory. The artifact report contains metadata
only: no absolute paths, extension IDs, browser IDs, secrets, or page data.
"""

from __future__ import annotations

import argparse
import contextlib
import errno
import hashlib
import json
import os
import platform
import re
import secrets
import stat
import sys
import tempfile
from pathlib import Path
from typing import Any

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows has no fcntl
    fcntl = None

from artifact_envelope import envelope as add_envelope
from artifact_envelope import redact_for_persistence, write_json_atomic

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_ROOT = (ROOT / "artifacts").resolve()
DEFAULT_ARTIFACT_DIR = ARTIFACT_ROOT / "p0-installation"
HOST_NAME = "com.agentyc.p0_probe"
HOST_PATH = ROOT / "tests" / "probes" / "native_probe"
EXTENSION_DIR = ROOT / "extension" / "probes"
EXTENSION_MANIFEST = EXTENSION_DIR / "manifest.json"
MACOS_TEMPLATE = EXTENSION_DIR / "native_host_manifest.macos.json"
# Internal transaction state is not a published evidence JSON artifact.
INSTALL_RECORD = ".install-journal"
INSTALL_OWNER = ".install-owner"
INSTALL_LOCK = ".install-lock"
INSTALL_SCHEMA = 2
REPORT_NAME = "report.json"
DISPLAY_FILENAME = "native-host-manifest.json"
REGISTRATION_MODE = 0o644
PRIVATE_TRANSACTION_MODE = 0o600
JOURNAL_STATES = frozenset({"prepared", "temp_written", "installed", "removing", "removed"})
LIFECYCLE_SCHEMA_VERSION = 1
OFFLINE_EVIDENCE_STATUS = "not_measured_offline"
MAX_LIFECYCLE_RECORD_BYTES = 128 * 1024
EXTENSION_ID_PATTERN = re.compile(r"^[a-p]{32}$")
PROFILE_MARKERS = {
    "Default",
    "Local State",
    "Preferences",
    "SingletonCookie",
    "SingletonLock",
    "SingletonSocket",
}


class InstallationBusy(RuntimeError):
    """Another explicit install or rollback owns the registration lock."""


class JournalError(ValueError):
    """An install journal cannot be trusted for reconciliation."""


def _assert_no_symlink_components(path: Path, *, message: str) -> None:
    current = path
    while current != current.parent:
        try:
            if current.is_symlink():
                raise ValueError(message)
        except OSError as error:
            raise ValueError(message) from error
        current = current.parent


def _canonical_target(path: Path) -> Path:
    requested = Path(path).expanduser()
    _assert_no_symlink_components(requested, message="registration target components must not be symlinks")
    resolved = requested.resolve(strict=False)
    if resolved.exists() and resolved.is_dir():
        raise ValueError("registration target must be a file")
    return resolved


def _native_host_available() -> bool:
    try:
        _assert_no_symlink_components(HOST_PATH, message="native host path components must not be symlinks")
    except ValueError:
        return False
    return HOST_PATH.is_file() and os.access(HOST_PATH, os.X_OK)


def _fsync_directory(directory: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(str(directory), flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_bytes(path: Path, rendered: bytes, *, mode: int = PRIVATE_TRANSACTION_MODE) -> None:
    """Write and publish a file with a durable replacement and no symlink follow."""
    target = Path(path)
    _assert_no_symlink_components(target, message="atomic target components must not be symlinks")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    descriptor: int | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.tmp-", dir=target.parent)
        os.close(descriptor)
        descriptor = None
        temporary = Path(temporary_name)
        with temporary.open("wb") as handle:
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
            os.fchmod(handle.fileno(), mode)
        os.replace(temporary, target)
        temporary = None
        _fsync_directory(target.parent)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    rendered = (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n").encode("utf-8")
    _atomic_bytes(path, rendered)


def _owner_token(artifact_dir: Path, *, create: bool) -> bytes | None:
    """Read or create the private transaction token bound to an artifact set."""
    path = artifact_dir / INSTALL_OWNER
    _assert_no_symlink_components(path, message="install owner path components must not be symlinks")
    try:
        metadata = path.stat()
        if not path.is_file() or (hasattr(os, "getuid") and metadata.st_uid != os.getuid()) or metadata.st_mode & 0o077:
            raise JournalError("install owner file ownership or permissions are invalid")
        token = path.read_bytes()
        if len(token) != 32:
            raise JournalError("install owner token is invalid")
        return token
    except FileNotFoundError:
        if not create:
            return None
    if not create:
        return None
    artifact_dir.mkdir(parents=True, exist_ok=True)
    token = secrets.token_bytes(32)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor: int | None = None
    try:
        descriptor = os.open(str(path), flags, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = None
            handle.write(token)
            handle.flush()
            os.fsync(handle.fileno())
        _fsync_directory(artifact_dir)
        return token
    except FileExistsError:
        return _owner_token(artifact_dir, create=False)
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _unlink_durable(path: Path) -> bool:
    target = Path(path)
    _assert_no_symlink_components(target, message="cleanup target components must not be symlinks")
    try:
        target.unlink()
    except FileNotFoundError:
        return False
    _fsync_directory(target.parent)
    return True


@contextlib.contextmanager
def installation_lock(lock_path: Path):
    """Take a non-blocking lock for the whole explicit mutation transaction."""
    target = Path(lock_path)
    _assert_no_symlink_components(target, message="installation lock components must not be symlinks")
    target.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor: int | None = None
    remove_fallback_lock = False
    try:
        if fcntl is None:
            flags |= os.O_EXCL
            remove_fallback_lock = True
        descriptor = os.open(str(target), flags, 0o600)
        handle = os.fdopen(descriptor, "a+b")
        descriptor = None
        try:
            if fcntl is not None:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError as error:
                    if error.errno in {errno.EACCES, errno.EAGAIN}:
                        raise InstallationBusy("installation lock is held") from error
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
        raise InstallationBusy("installation lock is held") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if remove_fallback_lock:
            try:
                target.unlink()
            except OSError:
                pass


@contextlib.contextmanager
def installation_transaction(target: Path, artifact_dir: Path):
    """Lock both the journal and its canonical registration target."""
    paths = sorted(
        {artifact_dir / INSTALL_LOCK, target.parent / INSTALL_LOCK},
        key=lambda path: str(path),
    )
    with contextlib.ExitStack() as stack:
        for path in paths:
            stack.enter_context(installation_lock(path))
        yield


def _path_inside(path: Path, parent: Path, *, allow_parent: bool = False) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return allow_parent or path != parent


def safe_artifact_dir(value: str) -> Path:
    """Resolve an artifact directory without allowing writes outside artifacts/."""
    requested = Path(value).expanduser()
    if not requested.is_absolute():
        requested = ROOT / requested
    current = requested
    while current != current.parent:
        if current.is_symlink():
            raise ValueError("artifact path components must not be symlinks")
        current = current.parent
    resolved = requested.resolve()
    if not _path_inside(resolved, ARTIFACT_ROOT):
        raise ValueError("artifact directory must be inside the repository artifacts/ directory")
    if requested.exists() and requested.is_symlink():
        raise ValueError("artifact directory must not be a symlink")
    if resolved == ARTIFACT_ROOT:
        raise ValueError("artifact directory must be a child of the repository artifacts/ directory")
    if resolved.exists() and not resolved.is_dir():
        raise ValueError("artifact directory exists but is not a directory")
    return resolved


def safe_profile_dir(value: str, artifact_dir: Path) -> Path:
    """Resolve a disposable profile path and reject symlink/path escapes."""
    requested = Path(value).expanduser()
    if not requested.is_absolute():
        repository_relative = (ROOT / requested).resolve()
        requested = repository_relative if _path_inside(repository_relative, artifact_dir) else artifact_dir / requested
    resolved = requested.resolve()
    current = requested
    while current != current.parent:
        if current.is_symlink():
            raise ValueError("clean profile path components must not be symlinks")
        current = current.parent
    if not _path_inside(resolved, artifact_dir):
        raise ValueError("clean profile must be inside the selected artifact directory")
    if requested.exists() and requested.is_symlink():
        raise ValueError("clean profile must not be a symlink")
    if resolved == artifact_dir:
        raise ValueError("clean profile must be a child of the artifact directory")
    if resolved.exists() and not resolved.is_dir():
        raise ValueError("clean profile exists but is not a directory")
    return resolved


def safe_registration_path(value: str | None, artifact_dir: Path) -> tuple[Path, str]:
    """Return a user registration path or a test path under artifacts/."""
    if value is None:
        if platform.system() != "Darwin":
            return Path("unsupported"), "unsupported"
        candidate = (
            Path.home()
            / "Library"
            / "Application Support"
            / "Google"
            / "Chrome"
            / "NativeMessagingHosts"
            / f"{HOST_NAME}.json"
        )
        current = candidate
        while current != current.parent:
            if current.is_symlink():
                raise ValueError("user registration path cannot contain symlinks")
            current = current.parent
        return candidate, "user-level"
    requested = Path(value).expanduser()
    if not requested.is_absolute():
        repository_relative = (ROOT / requested).resolve()
        requested = repository_relative if _path_inside(repository_relative, artifact_dir) else artifact_dir / requested
    resolved = requested.resolve()
    current = requested
    while current != current.parent:
        if current.is_symlink():
            raise ValueError("registration path components must not be symlinks")
        current = current.parent
    if not _path_inside(resolved, artifact_dir):
        raise ValueError("custom registration path must be inside the selected artifact directory")
    if requested.exists() and requested.is_symlink():
        raise ValueError("registration path must not be a symlink")
    if resolved.exists() and resolved.is_dir():
        raise ValueError("registration path must be a file, not a directory")
    return resolved, "artifact-test"


def redacted_path(path: Path, artifact_dir: Path | None = None) -> str:
    """Return a stable non-sensitive label; never serialize an absolute path."""
    resolved = path.resolve()
    if artifact_dir is not None and _path_inside(resolved, artifact_dir, allow_parent=True):
        digest = hashlib.sha256(resolved.relative_to(artifact_dir).as_posix().encode("utf-8")).hexdigest()[:12]
        return f"artifact/item-{digest}"
    if resolved == ROOT or _path_inside(resolved, ROOT, allow_parent=True):
        return f"repo/{resolved.relative_to(ROOT).as_posix()}"
    if resolved == Path.home() or _path_inside(resolved, Path.home(), allow_parent=True):
        return f"home/{resolved.relative_to(Path.home()).as_posix()}"
    return path.name


def validate_extension_id(value: str) -> str:
    if not EXTENSION_ID_PATTERN.fullmatch(value):
        raise ValueError("extension ID must be the 32-character Chrome ID using letters a-p")
    return value


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ValueError(f"missing fixture: {path.name}") from error
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"unreadable JSON fixture: {path.name}") from error
    if not isinstance(value, dict):
        raise TypeError(f"JSON fixture is not an object: {path.name}")
    return value


def validate_fixtures() -> dict[str, Any]:
    manifest = load_json(EXTENSION_MANIFEST)
    if manifest.get("manifest_version") != 3:
        raise ValueError("probe extension is not Manifest V3")
    if not isinstance(manifest.get("version"), str):
        raise TypeError("probe extension has no version")
    for filename in ("service_worker.js", "probe.html", "probe.js", "fixture.html"):
        if not (EXTENSION_DIR / filename).is_file():
            raise ValueError(f"probe extension fixture is missing {filename}")

    template = load_json(MACOS_TEMPLATE)
    if template.get("name") != HOST_NAME or template.get("type") != "stdio":
        raise ValueError("macOS Native Messaging template has the wrong name or type")
    if template.get("path") != "__ABSOLUTE_PATH_TO_REPOSITORY__/tests/probes/native_probe":
        raise ValueError("macOS Native Messaging template must retain its path placeholder")
    if template.get("allowed_origins") != ["chrome-extension://__UNPACKED_EXTENSION_ID__/"]:
        raise ValueError("macOS Native Messaging template must retain its origin placeholder")
    return {"extension_version": manifest["version"], "manifest_version": manifest["manifest_version"]}


def chrome_binary(value: str | None) -> Path | None:
    candidates = [
        value,
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Google Chrome Beta.app/Contents/MacOS/Google Chrome Beta",
        "/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary",
    ]
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate).expanduser()
        if path.is_file() and os.access(path, os.X_OK):
            return path.resolve()
    return None


def clean_profile_state(profile_dir: Path | None) -> dict[str, Any]:
    if profile_dir is None:
        return {
            "requested": False,
            "status": "not_requested",
            "mutated": False,
        }
    if not profile_dir.exists():
        return {
            "requested": True,
            "status": "absent_and_clean",
            "exists": False,
            "mutated": False,
        }
    try:
        entries = sorted(entry.name for entry in profile_dir.iterdir())
    except OSError:
        return {
            "requested": True,
            "status": "unreadable",
            "exists": True,
            "mutated": False,
        }
    return {
        "requested": True,
        "status": "empty" if not entries else "not_clean",
        "exists": True,
        "entry_count": len(entries),
        "has_profile_markers": bool(set(entries) & PROFILE_MARKERS),
        "mutated": False,
    }


def file_metadata(path: Path, artifact_dir: Path) -> dict[str, Any]:
    metadata: dict[str, Any] = {"name": redacted_path(path, artifact_dir), "kind": "file"}
    try:
        data = path.read_bytes()
    except (OSError, UnicodeError):
        metadata.update({"status": "unreadable"})
        return metadata
    metadata.update(
        {
            "status": "present",
            "bytes": len(data),
            "sha256_prefix": hashlib.sha256(data).hexdigest()[:12],
        }
    )
    return metadata


def artifact_metadata(artifact_dir: Path) -> list[dict[str, Any]]:
    if not artifact_dir.is_dir():
        return []
    result: list[dict[str, Any]] = []
    for path in sorted(artifact_dir.iterdir(), key=lambda item: item.name):
        if path.name in {REPORT_NAME, INSTALL_RECORD, INSTALL_OWNER, INSTALL_LOCK} or path.is_symlink():
            continue
        if path.is_file():
            result.append(file_metadata(path, artifact_dir))
        elif path.is_dir():
            result.append(
                {
                    "name": redacted_path(path, artifact_dir),
                    "kind": "directory",
                    "status": "present",
                }
            )
    return result


def expected_manifest(extension_id: str) -> dict[str, Any]:
    validate_extension_id(extension_id)
    if not _native_host_available():
        raise ValueError("the repository Native Messaging host is missing, not executable, or uses a symlink")
    return {
        "name": HOST_NAME,
        "description": "Test-only agentyc Phase 0 Native Messaging host fixture",
        "path": str(HOST_PATH.resolve()),
        "type": "stdio",
        "allowed_origins": [f"chrome-extension://{extension_id}/"],
    }


def canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant: {value}")


def read_registration(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"), parse_constant=_reject_json_constant)
    except (FileNotFoundError, OSError, UnicodeError, ValueError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def registration_file_state(path: Path) -> tuple[str, dict[str, Any] | None]:
    """Return absent, unreadable, non-object, or object without changing the file."""
    try:
        if path.is_symlink():
            return "symlink", None
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return "absent", None
    except (OSError, UnicodeError):
        return "unreadable", None
    try:
        value = json.loads(raw, parse_constant=_reject_json_constant)
    except (ValueError, json.JSONDecodeError):
        return "unreadable", None
    if not isinstance(value, dict):
        return "non_object", None
    return "object", value


def registration_state(path: Path, extension_id: str | None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": "not_checked",
        "location": "user-level" if path.parts and "unsupported" not in path.parts else "unsupported",
        "filename": DISPLAY_FILENAME,
        "mutated": False,
    }
    if extension_id is None:
        result.update({"status": "extension_id_required"})
        return result
    file_status, actual = registration_file_state(path)
    if file_status == "absent":
        result.update({"status": "absent"})
        return result
    if file_status == "symlink":
        result.update({"status": "rejected", "detail": "registration path must not be a symlink"})
        return result
    if file_status == "non_object":
        result.update({"status": "rejected", "detail": "registration JSON must be an object"})
        return result
    if file_status != "object" or actual is None:
        result.update({"status": "rejected", "detail": "registration manifest is unreadable"})
        return result
    try:
        expected = expected_manifest(extension_id)
    except ValueError:
        result.update({"status": "invalid_extension_id"})
        return result
    matches = all(actual.get(key) == value for key, value in expected.items())
    result.update({"status": "installed" if matches else "rejected"})
    return result


def offline_lifecycle_record() -> dict[str, Any]:
    """Describe every lifecycle phase without turning an unrun phase green."""
    return {
        "schema_version": LIFECYCLE_SCHEMA_VERSION,
        "evidence_mode": "offline",
        "status": "not_gateable_offline",
        "install": OFFLINE_EVIDENCE_STATUS,
        "update": OFFLINE_EVIDENCE_STATUS,
        "uninstall": OFFLINE_EVIDENCE_STATUS,
        "downgrade": OFFLINE_EVIDENCE_STATUS,
        "rollback": OFFLINE_EVIDENCE_STATUS,
    }


def offline_rollback_safety() -> dict[str, Any]:
    """Return a complete rollback-safety shape with no fabricated observations."""
    return {
        "schema_version": LIFECYCLE_SCHEMA_VERSION,
        "evidence_mode": "offline",
        "new_mutations": OFFLINE_EVIDENCE_STATUS,
        "pages_retained": None,
        "user_tabs_preserved": None,
        "chrome_process_terminated": None,
        "global_close_used": None,
        "incompatible_ledger_refused": None,
        "kill_switch": {
            "status": OFFLINE_EVIDENCE_STATUS,
            "armed": False,
            "verified": False,
        },
    }


def validate_lifecycle_record(record: dict[str, Any], *, require_live: bool) -> list[str]:
    """Validate an operator-supplied lifecycle record without executing it."""
    errors: list[str] = []
    if not isinstance(record, dict):
        return ["lifecycle record must be an object"]
    lifecycle = record.get("lifecycle")
    if not isinstance(lifecycle, dict):
        errors.append("lifecycle is missing")
    else:
        if lifecycle.get("schema_version") != LIFECYCLE_SCHEMA_VERSION:
            errors.append("lifecycle schema_version must be 1")
        evidence_mode = record.get("evidence_mode", lifecycle.get("evidence_mode"))
        if require_live and evidence_mode != "live":
            errors.append("lifecycle record is not real live evidence")
        if not require_live and evidence_mode not in {"offline", "live"}:
            errors.append("lifecycle evidence_mode is invalid")
        expected = {
            "install": "installed",
            "update": "passed",
            "uninstall": "passed",
            "downgrade": "passed",
            "rollback": "rolled_back",
        }
        allowed_offline = {
            OFFLINE_EVIDENCE_STATUS,
            "not_run",
            "not_observed",
            "not_applicable",
            "installed",
            "already_installed",
            "passed",
            "rolled_back",
        }
        for key, wanted in expected.items():
            value = lifecycle.get(key)
            if not isinstance(value, str):
                errors.append(f"lifecycle.{key} is missing")
            elif require_live and value != wanted:
                errors.append(f"lifecycle.{key} is not {wanted}")
            elif not require_live and value not in allowed_offline:
                errors.append(f"lifecycle.{key} has an invalid status")

    rollback_safety = record.get("rollback_safety")
    required_safety = {
        "new_mutations": "paused",
        "pages_retained": True,
        "user_tabs_preserved": True,
        "chrome_process_terminated": False,
        "global_close_used": False,
        "incompatible_ledger_refused": True,
    }
    if not isinstance(rollback_safety, dict):
        errors.append("rollback_safety is missing")
    else:
        if rollback_safety.get("schema_version") != LIFECYCLE_SCHEMA_VERSION:
            errors.append("rollback_safety.schema_version must be 1")
        safety_mode = record.get("evidence_mode", rollback_safety.get("evidence_mode"))
        if require_live and safety_mode != "live":
            errors.append("rollback_safety is not live evidence")
        if not require_live and safety_mode not in {"offline", "live"}:
            errors.append("rollback_safety evidence_mode is invalid")
        for key, wanted in required_safety.items():
            if key not in rollback_safety:
                errors.append(f"rollback_safety.{key} is missing")
            elif require_live and rollback_safety.get(key) != wanted:
                errors.append(f"rollback_safety.{key} is unsafe or unproven")
        kill_switch = rollback_safety.get("kill_switch")
        if not isinstance(kill_switch, dict):
            errors.append("rollback_safety.kill_switch is missing")
        elif require_live and (
            kill_switch.get("status") != "armed_and_verified"
            or kill_switch.get("armed") is not True
            or kill_switch.get("verified") is not True
        ):
            errors.append("rollback_safety.kill_switch is not armed_and_verified")
        elif not require_live and kill_switch.get("status") not in {OFFLINE_EVIDENCE_STATUS, "armed_and_verified"}:
            errors.append("rollback_safety.kill_switch has an invalid status")

    if redact_for_persistence(record) != record:
        errors.append("lifecycle record is not stable after central redaction")
    return sorted(set(errors))


def load_lifecycle_record(path_value: str, artifact_dir: Path) -> dict[str, Any]:
    """Load a redacted live lifecycle record from the repository artifact tree."""
    path = Path(path_value).expanduser()
    if not path.is_absolute():
        path = ROOT / path
    current = path
    while current != current.parent:
        if current.is_symlink():
            raise ValueError("lifecycle record path components must not be symlinks")
        current = current.parent
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(ROOT)
        resolved.relative_to(Path(artifact_dir).resolve())
        if resolved.stat().st_size > MAX_LIFECYCLE_RECORD_BYTES:
            raise ValueError("lifecycle record exceeds the bounded read limit")
        value = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError("lifecycle record is unreadable") from error
    if not isinstance(value, dict):
        raise ValueError("lifecycle record must be an object")
    errors = validate_lifecycle_record(value, require_live=True)
    if errors:
        raise ValueError("; ".join(errors))
    return redact_for_persistence(value)


def build_preflight(
    *,
    artifact_dir: Path,
    profile_dir: Path | None,
    extension_id: str | None,
    chrome_path: Path | None,
    registration_path: Path,
    registration_scope: str,
) -> dict[str, Any]:
    evidence: dict[str, Any] = {
        "platform": {
            "name": platform.system(),
            "status": "supported" if platform.system() == "Darwin" else "unsupported",
            "supported_first": "macOS",
            "linux": "unsupported_pending_owner",
            "windows": "unsupported_pending_owner",
        },
        "chrome": {
            "status": "installed" if chrome_path else "absent",
            "binary_present": bool(chrome_path),
            "launch": "never",
            "download": "never",
        },
        "fixtures": {"status": "not_checked"},
        "native_host": {
            "status": "installed_and_executable" if _native_host_available() else "absent_or_not_executable",
            "executable_present": _native_host_available(),
        },
        "registration": {"status": "not_checked", "scope": registration_scope, "mutated": False},
        "clean_profile": clean_profile_state(profile_dir),
    }
    try:
        evidence["fixtures"] = {"status": "valid", **validate_fixtures()}
    except (ValueError, TypeError) as error:
        evidence["fixtures"] = {"status": "invalid", "detail": str(error)}

    if platform.system() == "Darwin" and registration_scope != "unsupported":
        evidence["registration"] = registration_state(registration_path, extension_id)
        evidence["registration"]["scope"] = registration_scope
    elif platform.system() != "Darwin":
        evidence["registration"] = {
            "status": "unsupported_platform",
            "scope": "unsupported",
            "filename": f"{HOST_NAME}.json",
            "mutated": False,
        }

    blockers: list[str] = []
    if platform.system() != "Darwin":
        blockers.append("macOS is required; Linux and Windows are unsupported by this drill")
    if not chrome_path:
        blockers.append("an installed macOS Chrome executable was not found; Chrome is never downloaded")
    if evidence["fixtures"]["status"] != "valid":
        blockers.append("the local extension or macOS Native Messaging fixture is invalid")
    if evidence["native_host"]["status"] != "installed_and_executable":
        blockers.append("the local Native Messaging host is missing or not executable")
    if profile_dir is not None and evidence["clean_profile"]["status"] not in {"absent_and_clean", "empty"}:
        blockers.append("the requested clean profile is not empty")
    return {
        "status": "ready" if not blockers else "blocked",
        "blockers": blockers,
        "evidence_mode": "offline",
        "release_eligible": False,
        "evidence": evidence,
        "lifecycle": offline_lifecycle_record(),
        "rollback_safety": offline_rollback_safety(),
        "safety": {
            "dry_run_default": True,
            "chrome_launch": "never",
            "chrome_download": "never",
            "chrome_profile_mutation": False,
            "user_registration_mutation": False,
            "raw_browser_ids_logged": False,
            "extension_id_logged": False,
            "secrets_logged": False,
        },
        "rollback": {
            "status": "not_observed",
            "requires": "explicit --drill or explicit --rollback",
            "user_tabs_or_chrome_changed": "not_observed",
        },
        "artifact_dir": "validated",
        "artifact_metadata": artifact_metadata(artifact_dir),
    }

def _target_state(path: Path) -> tuple[str, str | None]:
    try:
        if path.is_symlink():
            return "symlink", None
        if not path.exists():
            return "absent", None
        if not path.is_file():
            return "other", None
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        return "present", digest
    except (OSError, UnicodeError):
        return "unreadable", None


def _journal_path(artifact_dir: Path) -> Path:
    return artifact_dir / INSTALL_RECORD


def _path_digest(path: Path) -> str:
    """Bind journal state to a canonical path without persisting that path."""
    canonical = str(path.resolve(strict=False)).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _load_install_journal(path: Path) -> dict[str, Any] | None:
    if path.is_symlink():
        raise JournalError("install journal must not be a symlink")
    if not path.exists():
        return None
    try:
        metadata = path.stat()
        if not path.is_file() or (hasattr(os, "getuid") and metadata.st_uid != os.getuid()) or metadata.st_mode & 0o077:
            raise JournalError("install journal ownership or permissions are invalid")
        value = json.loads(path.read_text(encoding="utf-8"), parse_constant=_reject_json_constant)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
        raise JournalError("install journal is unreadable") from error
    if not isinstance(value, dict):
        raise JournalError("install journal must be an object")
    if value.get("schema") != INSTALL_SCHEMA or value.get("filename") != DISPLAY_FILENAME:
        raise JournalError("install journal schema is unsupported")
    if value.get("state") not in JOURNAL_STATES:
        raise JournalError("install journal state is unsupported")
    ownership_digest = value.get("ownership_digest")
    if not isinstance(ownership_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", ownership_digest):
        raise JournalError("install journal ownership digest is invalid")
    for field in ("target_digest", "target_parent_digest", "payload_sha256"):
        if not isinstance(value.get(field), str) or not value[field]:
            raise JournalError(f"install journal {field} is invalid")
    if not re.fullmatch(r"[0-9a-f]{64}", value["payload_sha256"]):
        raise JournalError("install journal payload hash is invalid")
    if type(value.get("payload_bytes")) is not int or value["payload_bytes"] < 0:
        raise JournalError("install journal payload size is invalid")
    if value["state"] in {"prepared", "temp_written"}:
        temp_name = value.get("temp_name")
        if not isinstance(temp_name, str) or not temp_name or Path(temp_name).name != temp_name:
            raise JournalError("install journal temporary target is invalid")
    return value


def _journal_target_matches(
    record: dict[str, Any], target: Path, artifact_dir: Path, payload_hash: str, owner_token: bytes
) -> bool:
    expected_ownership = hashlib.sha256(
        owner_token
        + _path_digest(artifact_dir).encode("ascii")
        + _path_digest(target).encode("ascii")
        + payload_hash.encode("ascii")
    ).hexdigest()
    return (
        record.get("target_digest") == _path_digest(target)
        and record.get("target_parent_digest") == _path_digest(target.parent)
        and record.get("payload_sha256") == payload_hash
        and record.get("ownership_digest") == expected_ownership
    )


def _journal_temp_path(record: dict[str, Any], target: Path) -> Path | None:
    temp_name = record.get("temp_name")
    if not temp_name:
        return None
    if not isinstance(temp_name, str) or Path(temp_name).name != temp_name:
        raise JournalError("install journal temporary target is invalid")
    temporary = target.parent / temp_name
    _assert_no_symlink_components(temporary, message="install temporary target components must not be symlinks")
    return temporary


def _remove_owned_temp(record: dict[str, Any], target: Path) -> None:
    temporary = _journal_temp_path(record, target)
    if temporary is None:
        return
    status, digest = _target_state(temporary)
    if status == "absent":
        return
    if status != "present" or digest != record.get("payload_sha256"):
        raise JournalError("install temporary target is not owned by this journal")
    _unlink_durable(temporary)


def _write_owned_temp(path: Path, payload: bytes, *, mode: int = PRIVATE_TRANSACTION_MODE) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(str(path), flags, PRIVATE_TRANSACTION_MODE)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(payload)
            os.fchmod(handle.fileno(), mode)
            handle.flush()
            os.fsync(handle.fileno())
        _fsync_directory(path.parent)
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _remove_journal(path: Path) -> None:
    if path.exists() or path.is_symlink():
        _unlink_durable(path)


def _registration_mode(path: Path) -> int | None:
    try:
        metadata = path.stat()
    except OSError:
        return None
    if not stat.S_ISREG(metadata.st_mode):
        return None
    return stat.S_IMODE(metadata.st_mode)


def _repair_registration_mode(path: Path) -> None:
    """Repair an exact existing manifest without following a replacement symlink."""
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(str(path), flags)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("registration target is not a regular file")
        os.fchmod(descriptor, REGISTRATION_MODE)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _reconcile_install_journal(
    target: Path,
    artifact_dir: Path,
    payload: bytes,
    payload_hash: str,
    owner_token: bytes | None = None,
) -> str:
    """Reconcile a prior crash without ever acting on a different canonical target."""
    journal_path = _journal_path(artifact_dir)
    record = _load_install_journal(journal_path)
    if record is None:
        return "none"
    owner_token = owner_token or _owner_token(artifact_dir, create=False)
    if owner_token is None or not _journal_target_matches(record, target, artifact_dir, payload_hash, owner_token):
        return "record_target_mismatch"
    state = record["state"]
    target_status, target_hash = _target_state(target)
    if target_status == "symlink":
        return "target_symlink"
    if target_status == "unreadable" or target_status == "other":
        return "target_changed"
    if state in {"prepared", "temp_written"}:
        if target_status == "present" and target_hash == payload_hash:
            _remove_owned_temp(record, target)
            record.update({"state": "installed", "temp_name": ""})
            _atomic_json(journal_path, record)
            return "installed"
        if target_status == "present":
            return "target_changed"
        _remove_owned_temp(record, target)
        _remove_journal(journal_path)
        return "clean"
    if state == "installed":
        if target_status == "absent":
            _remove_journal(journal_path)
            return "clean"
        return "installed" if target_hash == payload_hash else "target_changed"
    if state in {"removing", "removed"}:
        if target_status == "present" and target_hash != payload_hash:
            return "target_changed"
        if target_status == "present" and not _remove_owned_registration(target, payload_hash):
            return "target_changed"
        _remove_journal(journal_path)
        return "clean"
    raise JournalError("install journal state cannot be reconciled")


def _remove_owned_registration(target: Path, expected_hash: str) -> bool:
    """Remove only the exact owned payload without clobbering a concurrent replacement."""
    quarantine = target.parent / f".{target.name}.rollback-{secrets.token_hex(8)}"
    _assert_no_symlink_components(quarantine, message="rollback quarantine components must not be symlinks")
    os.rename(target, quarantine)
    status, digest = _target_state(quarantine)
    if status == "present" and digest == expected_hash:
        _unlink_durable(quarantine)
        return True
    # A concurrent replacement won the target path. Restore the moved object
    # only if nobody has recreated the path; otherwise leave the quarantine for
    # explicit reconciliation rather than deleting an unknown file.
    if not target.exists() and not target.is_symlink():
        os.rename(quarantine, target)
        _fsync_directory(target.parent)
    return False


def _new_install_journal(
    target: Path, artifact_dir: Path, payload_hash: str, temporary_name: str, owner_token: bytes | None = None
) -> dict[str, Any]:
    owner_token = owner_token or secrets.token_bytes(32)
    target_digest = _path_digest(target)
    return {
        "schema": INSTALL_SCHEMA,
        "state": "prepared",
        "filename": DISPLAY_FILENAME,
        "ownership_digest": hashlib.sha256(
            owner_token
            + _path_digest(artifact_dir).encode("ascii")
            + target_digest.encode("ascii")
            + payload_hash.encode("ascii")
        ).hexdigest(),
        "target_digest": target_digest,
        "target_parent_digest": _path_digest(target.parent),
        "payload_sha256": payload_hash,
        "payload_bytes": None,
        "temp_name": temporary_name,
    }


def _install_result(status: str, *, mutated: bool = False, detail: str | None = None, **extra: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": status,
        "mutated": mutated,
        "filename": DISPLAY_FILENAME,
        "user_tabs_or_chrome_changed": False,
        "chrome_process_terminated": False,
        "global_close_used": False,
        **extra,
    }
    if detail:
        result["detail"] = detail
    return result


def install_registration(path: Path, extension_id: str, artifact_dir: Path) -> dict[str, Any]:
    try:
        expected = expected_manifest(extension_id)
    except (OSError, ValueError) as error:
        return _install_result("install_failed", detail=str(error))
    payload = canonical_json(expected).encode("utf-8")
    payload_hash = hashlib.sha256(payload).hexdigest()
    try:
        target = _canonical_target(path)
        artifact_dir = Path(artifact_dir).expanduser()
        _assert_no_symlink_components(artifact_dir, message="artifact directory components must not be symlinks")
        with installation_transaction(target, artifact_dir):
            owner_token = _owner_token(artifact_dir, create=True)
            assert owner_token is not None
            reconciled = _reconcile_install_journal(target, artifact_dir, payload, payload_hash, owner_token)
            if reconciled == "installed":
                return _install_result("already_installed", reconciled=True)
            if reconciled in {"record_target_mismatch", "target_changed", "target_symlink"}:
                return _install_result("rejected_stale_drill_record", detail="owned installation state does not match the requested target")
            target_status, _ = _target_state(target)
            if target_status == "symlink":
                return _install_result("rejected_symlink_target", detail="registration target must not be a symlink")
            if target_status == "present":
                file_status, actual = registration_file_state(target)
                if file_status == "non_object":
                    return _install_result("rejected_non_object_manifest", detail="registration JSON must be an object")
                if file_status != "object" or actual is None:
                    return _install_result("rejected_unreadable_existing_manifest")
                if actual == expected:
                    mode_repaired = False
                    if _registration_mode(target) != REGISTRATION_MODE:
                        try:
                            _repair_registration_mode(target)
                        except (OSError, ValueError):
                            return _install_result("install_failed", detail="existing registration manifest permissions could not be repaired")
                        mode_repaired = True
                    return _install_result("already_installed", mutated=mode_repaired, mode_repaired=mode_repaired)
                return _install_result("rejected_existing_different_manifest")
            if target_status != "absent":
                return _install_result("install_failed", detail="registration target is not writable")

            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.parent / f".{target.name}.install-{secrets.token_hex(8)}"
            record_path = _journal_path(artifact_dir)
            record = _new_install_journal(target, artifact_dir, payload_hash, temporary.name, owner_token)
            record["payload_bytes"] = len(payload)
            _atomic_json(record_path, record)
            try:
                _write_owned_temp(temporary, payload, mode=REGISTRATION_MODE)
                record["state"] = "temp_written"
                _atomic_json(record_path, record)
                # Link the fully written temporary file into place so a
                # concurrent creator cannot be overwritten. A crash before the
                # temporary unlink is reconciled by its content hash.
                os.link(temporary, target, follow_symlinks=False)
                _fsync_directory(target.parent)
                _unlink_durable(temporary)
                record["state"] = "installed"
                record["temp_name"] = ""
                _atomic_json(record_path, record)
            except (OSError, ValueError, JournalError):
                try:
                    reconciled = _reconcile_install_journal(target, artifact_dir, payload, payload_hash, owner_token)
                except (OSError, ValueError, JournalError):
                    reconciled = "uncertain"
                if reconciled == "installed":
                    return _install_result("installed", mutated=True, reconciled=True)
                cleanup_ok = True
                try:
                    if temporary.exists() or temporary.is_symlink():
                        _unlink_durable(temporary)
                    if _target_state(target)[0] == "present":
                        cleanup_ok = False
                    if cleanup_ok:
                        _remove_journal(record_path)
                except (OSError, ValueError):
                    cleanup_ok = False
                return _install_result(
                    "install_uncertain" if not cleanup_ok or reconciled == "uncertain" else "install_failed",
                    mutated=not cleanup_ok,
                    detail="installation state requires reconciliation" if not cleanup_ok or reconciled == "uncertain" else "registration could not be written",
                )
            return _install_result("installed", mutated=True)
    except InstallationBusy:
        return _install_result("installation_busy", detail="another install or rollback is in progress")
    except (OSError, ValueError, JournalError) as error:
        return _install_result("install_failed", detail=str(error))


def rollback_registration(path: Path, extension_id: str, artifact_dir: Path) -> dict[str, Any]:
    try:
        expected_payload = canonical_json(expected_manifest(extension_id)).encode("utf-8")
    except (OSError, ValueError) as error:
        return _install_result("rollback_failed", detail=str(error))
    expected_hash = hashlib.sha256(expected_payload).hexdigest()
    try:
        target = _canonical_target(path)
        artifact_dir = Path(artifact_dir).expanduser()
        _assert_no_symlink_components(artifact_dir, message="artifact directory components must not be symlinks")
        with installation_transaction(target, artifact_dir):
            owner_token = _owner_token(artifact_dir, create=False)
            reconciled = _reconcile_install_journal(target, artifact_dir, expected_payload, expected_hash, owner_token)
            if reconciled == "record_target_mismatch":
                return _install_result("record_target_mismatch")
            if reconciled in {"target_changed", "target_symlink"}:
                return _install_result("refusing_changed_registration")
            record_path = _journal_path(artifact_dir)
            record = _load_install_journal(record_path)
            if record is None:
                return _install_result("no_drill_record")
            if (
                owner_token is None
                or record.get("state") != "installed"
                or not _journal_target_matches(record, target, artifact_dir, expected_hash, owner_token)
            ):
                return _install_result("invalid_drill_record")
            target_status, target_hash = _target_state(target)
            if target_status == "absent":
                _remove_journal(record_path)
                return _install_result("already_rolled_back")
            if target_status != "present" or target_hash != expected_hash:
                return _install_result("refusing_changed_registration")
            record["state"] = "removing"
            _atomic_json(record_path, record)
            try:
                if not _remove_owned_registration(target, expected_hash):
                    return _install_result("refusing_changed_registration", mutated=False)
                record["state"] = "removed"
                _atomic_json(record_path, record)
                _remove_journal(record_path)
            except (OSError, ValueError, JournalError) as error:
                return _install_result("rollback_failed", mutated=True, detail=str(error))
            return _install_result("rolled_back", mutated=True)
    except InstallationBusy:
        return _install_result("installation_busy", detail="another install or rollback is in progress")
    except (OSError, ValueError, JournalError) as error:
        return _install_result("rollback_failed", detail=str(error))


def write_report(artifact_dir: Path, report: dict[str, Any]) -> None:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    report["artifact_metadata"] = artifact_metadata(artifact_dir)
    add_envelope(report, kind="installation-drill")
    write_json_atomic(artifact_dir / REPORT_NAME, report)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the macOS-first, read-only-by-default installation and rollback preflight.",
        epilog="No action launches or downloads Chrome. --drill is the only action that writes a user-level test registration, and it rolls that exact file back.",
    )
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--install", action="store_true", help="explicitly install the test Native Messaging registration")
    action.add_argument("--rollback", action="store_true", help="explicitly remove a registration previously written by this drill")
    action.add_argument("--drill", action="store_true", help="explicitly install and then roll back the test registration")
    parser.add_argument("--dry-run", action="store_true", help="read-only preflight; this is the default")
    parser.add_argument("--required", "--require-real", dest="required", action="store_true", help="fail unless the requested drill has real platform evidence")
    parser.add_argument("--clean-profile", nargs="?", const="", help="validate an absent or empty disposable profile inside --artifact-dir")
    parser.add_argument(
        "--artifact-dir",
        default=DEFAULT_ARTIFACT_DIR.relative_to(ROOT).as_posix(),
        help="artifact directory, restricted to repository artifacts/",
    )
    parser.add_argument("--extension-id", help="exact 32-character unpacked/stable Chrome extension ID; never written to the report")
    parser.add_argument("--chrome-binary", help="explicit already-installed macOS Chrome executable to inspect")
    parser.add_argument("--registration-path", help="test-only registration path inside --artifact-dir; default is the macOS user-level path")
    parser.add_argument(
        "--lifecycle-record",
        help="redacted operator-supplied live install/update/uninstall/downgrade/rollback record inside --artifact-dir",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    action = "drill" if args.drill else "install" if args.install else "rollback" if args.rollback else "preflight"
    explicit_action = action != "preflight"
    if args.dry_run and explicit_action:
        parser.error("--dry-run cannot be combined with --install, --rollback, or --drill")
    if args.required and args.clean_profile is None:
        parser.error("--required requires --clean-profile so the disposable profile is validated")
    if explicit_action and args.clean_profile is None:
        parser.error("an explicit action requires --clean-profile")
    if action in {"install", "rollback", "drill"} and not args.extension_id:
        parser.error(f"--{action} requires --extension-id")

    try:
        artifact_dir = safe_artifact_dir(args.artifact_dir)
        profile_value = None if args.clean_profile is None else (args.clean_profile or "clean-profile")
        profile_dir = safe_profile_dir(profile_value, artifact_dir) if profile_value is not None else None
        registration_path, registration_scope = safe_registration_path(args.registration_path, artifact_dir)
        extension_id = validate_extension_id(args.extension_id) if args.extension_id else None
    except ValueError as error:
        print(json.dumps({"status": "invalid_arguments", "detail": str(error)}, sort_keys=True), file=sys.stderr)
        return 2

    chrome_path = chrome_binary(args.chrome_binary)
    report = build_preflight(
        artifact_dir=artifact_dir,
        profile_dir=profile_dir,
        extension_id=extension_id,
        chrome_path=chrome_path,
        registration_path=registration_path,
        registration_scope=registration_scope,
    )
    report.update(
        {
            "schema_version": 1,
            "phase": 0,
            "rollout_phase": 7,
            "kind": "installation-preflight",
            "action": action,
            "required": bool(args.required or args.clean_profile is not None or explicit_action),
            "status": report["status"],
            "evidence_mode": "offline",
            "release_eligible": False,
            "registration_filename": DISPLAY_FILENAME,
        }
    )

    lifecycle_record: dict[str, Any] | None = None
    lifecycle_record_errors: list[str] = []
    if args.lifecycle_record:
        try:
            lifecycle_record = load_lifecycle_record(args.lifecycle_record, artifact_dir)
            lifecycle_record_errors = validate_lifecycle_record(lifecycle_record, require_live=True)
        except ValueError as error:
            lifecycle_record_errors = [str(error)]
        report["lifecycle_record_validation"] = {
            "status": "passed" if not lifecycle_record_errors else "blocked",
            "evidence_mode": "live",
            "errors": lifecycle_record_errors,
        }
    else:
        report["lifecycle_record_validation"] = {
            "status": "not_supplied",
            "evidence_mode": "not_measured_offline",
            "errors": [],
        }

    if explicit_action:
        assert extension_id is not None
        if platform.system() != "Darwin":
            report["status"] = "unsupported_platform"
            report["limitations"] = ["Linux and Windows are not implemented by this drill; no registration was changed."]
        elif report["status"] != "ready":
            report["status"] = "drill_blocked"
            report["limitations"] = report["blockers"]
        elif action == "install":
            report["installation"] = install_registration(registration_path, extension_id, artifact_dir)
            report["lifecycle"]["install"] = report["installation"]["status"]
            report["status"] = "drill_passed" if report["installation"]["status"] == "installed" else "drill_failed"
        elif action == "rollback":
            report["rollback"] = rollback_registration(registration_path, extension_id, artifact_dir)
            report["lifecycle"]["rollback"] = report["rollback"]["status"]
            report["status"] = "drill_passed" if report["rollback"]["status"] == "rolled_back" else "drill_failed"
        else:
            installation = install_registration(registration_path, extension_id, artifact_dir)
            report["installation"] = installation
            if installation["status"] == "installed":
                rollback = rollback_registration(registration_path, extension_id, artifact_dir)
            elif installation["status"] == "already_installed":
                rollback = {"status": "not_owned", "mutated": False, "filename": DISPLAY_FILENAME, "user_tabs_or_chrome_changed": False}
            else:
                rollback = {"status": "not_run", "mutated": False, "filename": DISPLAY_FILENAME, "user_tabs_or_chrome_changed": False}
            report["rollback"] = rollback
            report["lifecycle"]["install"] = installation["status"]
            report["lifecycle"]["rollback"] = rollback["status"]
            report["status"] = "drill_passed" if installation["status"] == "installed" and rollback["status"] == "rolled_back" else "drill_failed"
        if action == "drill" and report["status"] == "drill_passed":
            report["status"] = "drill_incomplete"
            report.setdefault("limitations", []).append(
                "install/rollback smoke passed; update, uninstall, and downgrade require a separately captured lifecycle record"
            )

        if lifecycle_record_errors:
            report["status"] = "drill_failed"
            report.setdefault("limitations", []).append("the supplied lifecycle record failed validation")
        elif lifecycle_record is not None:
            if action == "drill" and report["status"] == "drill_incomplete":
                report["lifecycle"] = lifecycle_record["lifecycle"]
                report["rollback_safety"] = lifecycle_record["rollback_safety"]
                report["evidence_mode"] = "live"
                report["status"] = "drill_passed"
                report["release_eligible"] = True
            else:
                report.setdefault("limitations", []).append(
                    "the lifecycle record was validated but was not merged because the local registration drill did not pass"
                )
        report["safety"]["user_registration_mutation"] = bool(
            report.get("installation", {}).get("mutated") or report.get("rollback", {}).get("mutated")
        )
    elif report["required"]:
        report["status"] = "unsupported_platform" if platform.system() != "Darwin" else "preflight_blocked" if report["blockers"] else "required_evidence_missing"
        report["limitations"] = [
            *report["blockers"],
            "read-only preflight does not prove installation, extension loading, Native Messaging, or rollback; run explicit --drill on macOS",
        ]
    else:
        if platform.system() != "Darwin":
            report["status"] = "unsupported_platform"
        report["limitations"] = [
            *report["blockers"],
            "preflight only; no installation, extension loading, Native Messaging, Chrome launch, or rollback was attempted",
        ]

    try:
        write_report(artifact_dir, report)
    except (OSError, ValueError) as error:
        print(json.dumps({"status": "artifact_write_failed", "detail": type(error).__name__}, sort_keys=True), file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    if explicit_action or args.required or args.clean_profile is not None:
        return 0 if report["status"] == "drill_passed" else 1
    return 0 if report["status"] == "ready" else 1


if __name__ == "__main__":
    raise SystemExit(main())
