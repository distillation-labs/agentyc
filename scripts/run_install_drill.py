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
import hashlib
import json
import os
import platform
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

from artifact_envelope import envelope as add_envelope
from artifact_envelope import write_json_atomic

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_ROOT = (ROOT / "artifacts").resolve()
DEFAULT_ARTIFACT_DIR = ARTIFACT_ROOT / "p0-installation"
HOST_NAME = "com.agentyc.p0_probe"
HOST_PATH = ROOT / "tests" / "probes" / "native_probe"
EXTENSION_DIR = ROOT / "extension" / "probes"
EXTENSION_MANIFEST = EXTENSION_DIR / "manifest.json"
MACOS_TEMPLATE = EXTENSION_DIR / "native_host_manifest.macos.json"
INSTALL_RECORD = ".install-record.json"
REPORT_NAME = "report.json"
EXTENSION_ID_PATTERN = re.compile(r"^[a-p]{32}$")
PROFILE_MARKERS = {
    "Default",
    "Local State",
    "Preferences",
    "SingletonCookie",
    "SingletonLock",
    "SingletonSocket",
}


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
        return (
            Path.home()
            / "Library"
            / "Application Support"
            / "Google"
            / "Chrome"
            / "NativeMessagingHosts"
            / f"{HOST_NAME}.json",
            "user-level",
        )
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
        return f"artifact/{resolved.relative_to(artifact_dir).as_posix()}"
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
        if path.name == REPORT_NAME or path.is_symlink():
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
    return {
        "name": HOST_NAME,
        "description": "Test-only agentyc Phase 0 Native Messaging host fixture",
        "path": str(HOST_PATH.resolve()),
        "type": "stdio",
        "allowed_origins": [f"chrome-extension://{extension_id}/"],
    }


def canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def read_registration(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def registration_state(path: Path, extension_id: str | None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": "not_checked",
        "location": "user-level" if path.parts and "unsupported" not in path.parts else "unsupported",
        "filename": path.name,
        "mutated": False,
    }
    if extension_id is None:
        result.update({"status": "extension_id_required"})
        return result
    actual = read_registration(path)
    if actual is None:
        result.update({"status": "absent"})
        return result
    try:
        expected = expected_manifest(extension_id)
    except ValueError:
        result.update({"status": "invalid_extension_id"})
        return result
    matches = all(actual.get(key) == value for key, value in expected.items())
    result.update({"status": "installed" if matches else "rejected"})
    return result


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
            "status": "installed_and_executable" if HOST_PATH.is_file() and os.access(HOST_PATH, os.X_OK) else "absent_or_not_executable",
            "executable_present": HOST_PATH.is_file() and os.access(HOST_PATH, os.X_OK),
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
        "evidence": evidence,
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


def install_registration(path: Path, extension_id: str, artifact_dir: Path) -> dict[str, Any]:
    expected = expected_manifest(extension_id)
    payload = canonical_json(expected)
    existing = read_registration(path) if path.exists() else None
    if existing is not None:
        if existing == expected:
            return {"status": "already_installed", "mutated": False, "filename": path.name}
        return {"status": "rejected_existing_different_manifest", "mutated": False, "filename": path.name}
    if path.exists():
        return {"status": "rejected_unreadable_existing_manifest", "mutated": False, "filename": path.name}
    temporary_path: Path | None = None
    registration_written = False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as temporary:
            temporary.write(payload)
            temporary_path = Path(temporary.name)
        temporary_path.chmod(0o644)
        os.replace(temporary_path, path)
        registration_written = True
        record = {
            "schema": 1,
            "filename": path.name,
            "payload_sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        }
        write_json_atomic(artifact_dir / INSTALL_RECORD, record)
    except (OSError, ValueError):
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
        if registration_written:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        return {"status": "install_failed", "mutated": registration_written, "filename": path.name, "detail": "registration could not be written"}
    return {"status": "installed", "mutated": True, "filename": path.name}


def rollback_registration(path: Path, extension_id: str, artifact_dir: Path) -> dict[str, Any]:
    record_path = artifact_dir / INSTALL_RECORD
    if not record_path.is_file():
        return {"status": "no_drill_record", "mutated": False, "filename": path.name}
    try:
        record = load_json(record_path)
    except (ValueError, TypeError) as error:
        return {"status": "invalid_drill_record", "mutated": False, "filename": path.name, "detail": str(error)}
    expected_payload = canonical_json(expected_manifest(extension_id)).encode("utf-8")
    expected_hash = hashlib.sha256(expected_payload).hexdigest()
    if record.get("filename") != path.name or record.get("payload_sha256") != expected_hash:
        return {"status": "record_target_mismatch", "mutated": False, "filename": path.name}
    try:
        actual_payload = path.read_bytes()
    except OSError:
        return {"status": "registration_missing", "mutated": False, "filename": path.name}
    if hashlib.sha256(actual_payload).hexdigest() != expected_hash:
        return {"status": "refusing_changed_registration", "mutated": False, "filename": path.name}
    registration_removed = False
    try:
        path.unlink()
        registration_removed = True
        record_path.unlink()
    except OSError:
        if registration_removed:
            temporary_path: Path | None = None
            try:
                with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as temporary:
                    temporary.write(actual_payload)
                    temporary_path = Path(temporary.name)
                os.replace(temporary_path, path)
            except OSError:
                if temporary_path is not None:
                    temporary_path.unlink(missing_ok=True)
        return {"status": "rollback_failed", "mutated": False, "filename": path.name, "detail": "rollback was restored after cleanup failure"}
    return {"status": "rolled_back", "mutated": True, "filename": path.name}


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
            "kind": "installation-preflight",
            "action": action,
            "required": bool(args.required or args.clean_profile is not None or explicit_action),
            "status": report["status"],
            "registration_filename": registration_path.name,
        }
    )

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
            report["status"] = "drill_passed" if report["installation"]["status"] == "installed" else "drill_failed"
        elif action == "rollback":
            report["rollback"] = rollback_registration(registration_path, extension_id, artifact_dir)
            report["status"] = "drill_passed" if report["rollback"]["status"] == "rolled_back" else "drill_failed"
        else:
            installation = install_registration(registration_path, extension_id, artifact_dir)
            report["installation"] = installation
            if installation["status"] in {"installed", "already_installed"} and installation["status"] == "installed":
                rollback = rollback_registration(registration_path, extension_id, artifact_dir)
            elif installation["status"] == "already_installed":
                rollback = {"status": "not_owned", "mutated": False, "filename": registration_path.name}
            else:
                rollback = {"status": "not_run", "mutated": False, "filename": registration_path.name}
            report["rollback"] = rollback
            report["status"] = "drill_passed" if installation["status"] == "installed" and rollback["status"] == "rolled_back" else "drill_failed"
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
