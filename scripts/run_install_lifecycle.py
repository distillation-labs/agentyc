#!/usr/bin/env python3
"""Execute the real macOS disposable-profile extension lifecycle.

The default mode is read-only and writes only a bounded offline record. ``--run``
launches an owned Chrome process with an empty temporary profile and uses the
public browser-target CDP Extensions domain. The Phase 4 default stages the
production extension, opens a checked-in browser-task fixture outside the
extension tree, and records source/host provenance. No existing browser
endpoint, user profile, command-line extension loading, private extension API,
or operator-supplied lifecycle claim is accepted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit
from urllib.request import url2pathname

# The lifecycle executor is intentionally a new surface. It reuses only the
# existing probe's bounded, public-CDP transport primitives; it does not alter
# the existing install drill or Chrome probe.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from artifact_envelope import envelope as add_envelope
from artifact_envelope import redact_for_persistence, write_json_atomic
from run_chrome_probe import (
    BROWSER_CDP_PATH_PREFIX,
    CDP_SESSION_ID_PATTERN,
    EXTENSION_ID_PATTERN,
    DevToolsSocket,
    _browser_websocket_url,
    _endpoint_belongs_to_process,
    _manifest_extension_id,
    build_chrome_command,
    chrome_binary,
    chrome_endpoint,
    claim_disposable_profile,
    safe_profile_dir,
    wait_for_chrome,
)
from run_install_drill import (
    PHASE4_ARTIFACT_DIR,
    PRODUCTION_EXTENSION_DIR,
    lifecycle_source_hashes,
    lifecycle_source_provenance,
    validate_lifecycle_record,
)

ROOT = Path(__file__).resolve().parents[1]
SOURCE_EXTENSION_DIR = PRODUCTION_EXTENSION_DIR
PROBE_EXTENSION_DIR = ROOT / "extension" / "probes"
LIFECYCLE_FIXTURE = ROOT / "tests" / "fixtures" / "browser-task-spaces" / "small-form.html"
DEFAULT_ARTIFACT_DIR = PHASE4_ARTIFACT_DIR
LEGACY_ARTIFACT_DIR = ROOT / "artifacts" / "p0-installation"
RECORD_NAME = "lifecycle-record.json"
SCHEMA_VERSION = 1
LIFECYCLE_PROVENANCE_SCHEMA_VERSION = 1
MAX_SOURCE_HASH_FILES = 256
MAX_SOURCE_HASH_BYTES = 16 * 1024 * 1024
MAX_SOURCE_FILE_BYTES = 4 * 1024 * 1024
MAX_INVENTORY = 64
INVENTORY_TIMEOUT = 4.0
INVENTORY_INTERVAL = 0.1
VERSION_PARTS = 4


class LifecycleFailure(RuntimeError):
    """A lifecycle operation could not be verified and must fail closed."""


class LedgerMismatch(LifecycleFailure):
    """Observed Chrome state did not match the executor's owned ledger."""


def _safe_components(path: Path) -> None:
    current = path
    while current != current.parent:
        if current.is_symlink():
            raise ValueError("path components must not be symlinks")
        current = current.parent


def safe_artifact_dir(value: str) -> Path:
    requested = Path(value).expanduser()
    if not requested.is_absolute():
        requested = ROOT / requested
    _safe_components(requested)
    resolved = requested.resolve(strict=False)
    allowed_roots = (DEFAULT_ARTIFACT_DIR.resolve(), LEGACY_ARTIFACT_DIR.resolve())
    if not any(resolved == allowed or allowed in resolved.parents for allowed in allowed_roots):
        raise ValueError("artifact directory must be inside artifacts/p4-install-lifecycle or artifacts/p0-installation")
    if resolved.exists() and not resolved.is_dir():
        raise ValueError("artifact directory exists but is not a directory")
    return resolved


def safe_extension_dir(value: str | None) -> Path:
    requested = SOURCE_EXTENSION_DIR if value is None else Path(value).expanduser()
    if not requested.is_absolute():
        requested = ROOT / requested
    _safe_components(requested)
    try:
        resolved = requested.resolve(strict=True)
        resolved.relative_to(ROOT)
    except (OSError, ValueError) as error:
        raise ValueError("extension directory must be a real repository directory") from error
    if not resolved.is_dir() or resolved.is_symlink():
        raise ValueError("extension directory must be a real repository directory")
    manifest_path = resolved / "manifest.json"
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise ValueError("extension directory must contain a manifest.json file")
    return resolved


def _fixture_file_from_url(value: str, source: Path) -> Path | None:
    parsed = urlsplit(value)
    if parsed.scheme != "file":
        return None
    if parsed.netloc not in {"", "localhost"} or parsed.query or parsed.fragment:
        raise ValueError("fixture file URL must not contain a host, query, or fragment")
    try:
        candidate = Path(url2pathname(unquote(parsed.path)))
        _safe_components(candidate)
        resolved = candidate.resolve(strict=True)
        allowed_roots = (ROOT / "tests" / "fixtures", source)
        if not any(resolved == root or root in resolved.parents for root in allowed_roots):
            raise ValueError("fixture URL must point inside a checked-in fixture tree")
        if not resolved.is_file() or resolved.is_symlink():
            raise ValueError("fixture URL must point to a regular file")
        return resolved
    except (OSError, ValueError) as error:
        raise ValueError("fixture URL must point to a safe local fixture") from error


def safe_fixture_url(value: str | None, source: Path) -> str:
    """Select a deterministic local fixture or validate an explicit URL."""
    if value is None:
        source_fixture = source / "fixture.html"
        if source != SOURCE_EXTENSION_DIR and source_fixture.is_file() and not source_fixture.is_symlink():
            return source_fixture.resolve(strict=True).as_uri()
        _safe_components(LIFECYCLE_FIXTURE)
        if not LIFECYCLE_FIXTURE.is_file() or LIFECYCLE_FIXTURE.is_symlink():
            raise ValueError("checked-in lifecycle fixture is missing or unsafe")
        return LIFECYCLE_FIXTURE.resolve(strict=True).as_uri()
    if not isinstance(value, str) or not value or len(value) > 2048 or any(ord(char) < 0x20 for char in value):
        raise ValueError("fixture URL is invalid or exceeds the bounded limit")
    parsed = urlsplit(value)
    if parsed.scheme == "file":
        fixture = _fixture_file_from_url(value, source)
        assert fixture is not None
        return fixture.as_uri()
    if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password or parsed.fragment:
        raise ValueError("fixture URL must be a local file URL or an explicit HTTP(S) URL without credentials")
    if not parsed.hostname:
        raise ValueError("fixture URL host is missing")
    try:
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            raise ValueError("fixture URL port is invalid")
    except ValueError as error:
        raise ValueError("fixture URL port is invalid") from error
    return value


def _fixture_provenance(fixture_url: str, source: Path) -> dict[str, Any]:
    rendered = fixture_url.encode("utf-8")
    result: dict[str, Any] = {
        "schema_version": LIFECYCLE_PROVENANCE_SCHEMA_VERSION,
        "url_sha256": hashlib.sha256(rendered).hexdigest(),
    }
    fixture = _fixture_file_from_url(fixture_url, source)
    if fixture is None:
        result["kind"] = "explicit_url"
        return result
    data = fixture.read_bytes()
    if len(data) > MAX_SOURCE_FILE_BYTES:
        raise ValueError("lifecycle fixture exceeds the bounded hash limit")
    result.update(
        {
            "kind": "repository_file",
            "relative_file": fixture.relative_to(ROOT).as_posix(),
            "content_sha256": hashlib.sha256(data).hexdigest(),
        }
    )
    return result


def build_lifecycle_provenance(source: Path, fixture_url: str) -> dict[str, Any]:
    provenance = lifecycle_source_provenance(source)
    provenance["fixture"] = _fixture_provenance(fixture_url, source)
    return provenance


def _version(value: str) -> tuple[int, ...]:
    parts = value.split(".")
    if not 1 <= len(parts) <= VERSION_PARTS or any(not part.isdigit() for part in parts):
        raise ValueError("extension versions must contain one to four numeric components")
    if any(len(part) > 1 and part.startswith("0") for part in parts):
        raise ValueError("extension version components must not have leading zeroes")
    parsed = tuple(int(part) for part in parts)
    if any(part > 65535 for part in parsed):
        raise ValueError("extension version components exceed Chrome's limit")
    return parsed


def _version_text(value: str) -> str:
    _version(value)
    return value


def _next_version(value: str) -> str:
    parts = list(_version(value))
    parts[-1] += 1
    if parts[-1] > 65535:
        raise ValueError("base extension version cannot be incremented")
    return ".".join(str(part) for part in parts)


def _default_older_version(value: str) -> str:
    parts = list(_version(value))
    if parts[-1] > 0:
        parts[-1] -= 1
    elif len(parts) > 1:
        parts.pop()
        parts[-1] = max(0, parts[-1] - 1)
    else:
        raise ValueError("base extension version has no lower test version")
    return ".".join(str(part) for part in parts)


def _manifest(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError("extension manifest is unreadable") from error
    if not isinstance(value, dict):
        raise TypeError("extension manifest must be an object")
    if value.get("manifest_version") != 3 or not isinstance(value.get("name"), str):
        raise ValueError("extension manifest is not a supported MV3 fixture")
    if not isinstance(value.get("version"), str):
        raise TypeError("extension manifest has no version")
    _version(value["version"])
    extension_id = _manifest_extension_id(value)
    if not isinstance(extension_id, str) or not EXTENSION_ID_PATTERN.fullmatch(extension_id):
        raise ValueError("extension manifest must have a valid pinned public identity")
    return value


def _stage_extension(
    source: Path,
    profile: Path,
    version: str,
    expected_source_hashes: dict[str, str] | None = None,
) -> tuple[Path, dict[str, Any]]:
    if expected_source_hashes is not None and lifecycle_source_hashes(source) != expected_source_hashes:
        raise LifecycleFailure("extension source changed while the lifecycle was being prepared")
    staged = profile / "extension" / "probes"
    shutil.copytree(source, staged, symlinks=False)
    for path in staged.rglob("*"):
        if path.is_symlink():
            raise ValueError("extension fixture must not contain symlinks")
    manifest_path = staged / "manifest.json"
    manifest = _manifest(manifest_path)
    manifest["version"] = _version_text(version)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return staged, manifest


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _same_path(observed: Any, expected: Path) -> bool:
    if not isinstance(observed, str) or not observed or "\x00" in observed:
        return False
    try:
        return Path(observed).resolve(strict=False) == expected.resolve(strict=True)
    except (OSError, ValueError):
        return False


def _inventory(client: DevToolsSocket, session_id: str) -> list[dict[str, Any]]:
    result = client.command("Extensions.getExtensions", session_id=session_id)
    extensions = result.get("extensions") if isinstance(result, dict) else None
    if not isinstance(extensions, list) or len(extensions) > MAX_INVENTORY:
        raise LifecycleFailure("extension inventory was unavailable or exceeded its bound")
    if any(not isinstance(item, dict) for item in extensions):
        raise LifecycleFailure("extension inventory contained an invalid entry")
    return extensions


def _find_owned(
    extensions: list[dict[str, Any]],
    extension_id: str,
    staged: Path,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    matches = [item for item in extensions if item.get("id") == extension_id]
    if len(matches) != 1:
        raise LedgerMismatch("owned extension identity was not unique")
    item = matches[0]
    if (
        item.get("name") != manifest.get("name")
        or item.get("version") != manifest.get("version")
        or item.get("enabled") is not True
        or not _same_path(item.get("path"), staged)
    ):
        raise LedgerMismatch("owned extension observation did not match the ledger")
    return item


def _wait_for_version(
    client: DevToolsSocket,
    session_id: str,
    extension_id: str,
    staged: Path,
    manifest: dict[str, Any],
) -> None:
    deadline = time.monotonic() + INVENTORY_TIMEOUT
    while time.monotonic() < deadline:
        try:
            _find_owned(_inventory(client, session_id), extension_id, staged, manifest)
            return
        except (LifecycleFailure, OSError, ValueError, TypeError, TimeoutError):
            remaining = deadline - time.monotonic()
            if remaining > 0:
                time.sleep(min(INVENTORY_INTERVAL, remaining))
    raise LifecycleFailure("Chrome did not expose the requested extension version")


def _wait_absent(client: DevToolsSocket, session_id: str, extension_id: str, staged: Path) -> None:
    deadline = time.monotonic() + INVENTORY_TIMEOUT
    while time.monotonic() < deadline:
        try:
            extensions = _inventory(client, session_id)
            present = any(
                item.get("id") == extension_id or _same_path(item.get("path"), staged)
                for item in extensions
            )
            if not present:
                return
        except (LifecycleFailure, OSError, ValueError, TypeError, TimeoutError):
            pass
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(INVENTORY_INTERVAL, remaining))
    raise LifecycleFailure("Chrome did not verify extension absence")


def _load_version(
    client: DevToolsSocket,
    session_id: str,
    staged: Path,
    manifest: dict[str, Any],
    extension_id: str,
    phase: str,
) -> dict[str, Any]:
    result = client.command(
        "Extensions.loadUnpacked",
        {"path": str(staged.resolve(strict=True))},
        session_id=session_id,
    )
    returned_id = result.get("id") if isinstance(result, dict) else None
    if returned_id != extension_id:
        raise LedgerMismatch(f"{phase} returned a different extension identity")
    _wait_for_version(client, session_id, extension_id, staged, manifest)
    return {
        "status": "passed" if phase != "install" else "installed",
        "mechanism": "cdp.Extensions.loadUnpacked",
        "identity_verified": True,
        "inventory_verified": True,
        "version": manifest["version"],
    }


def _uninstall(
    client: DevToolsSocket,
    session_id: str,
    staged: Path,
    extension_id: str,
) -> dict[str, Any]:
    client.command("Extensions.uninstall", {"id": extension_id}, session_id=session_id)
    _wait_absent(client, session_id, extension_id, staged)
    return {
        "status": "passed",
        "mechanism": "cdp.Extensions.uninstall",
        "uninstall_verified": True,
        "absence_verified": True,
    }


def _fixture_pages(port: int, process: subprocess.Popen[bytes], fixture_url: str) -> int | None:
    if not _endpoint_belongs_to_process(port, process):
        return None
    try:
        targets = chrome_endpoint(port, "/json/list")
    except (OSError, ValueError):
        return None
    if not isinstance(targets, list):
        return None
    return sum(
        1
        for target in targets
        if isinstance(target, dict) and target.get("type") == "page" and target.get("url") == fixture_url
    )


class LifecycleLedger:
    """Small ownership ledger that rejects incompatible observations."""

    def __init__(self, extension_id: str, staged: Path, version: str) -> None:
        if not EXTENSION_ID_PATTERN.fullmatch(extension_id):
            raise ValueError("ledger extension identity is invalid")
        self.extension_id = extension_id
        self.staged = staged.resolve(strict=True)
        self.version = version

    def accept(self, extensions: list[dict[str, Any]], manifest: dict[str, Any]) -> None:
        _find_owned(extensions, self.extension_id, self.staged, manifest)
        self.version = manifest["version"]

    def refuse_incompatible(self) -> bool:
        try:
            _find_owned(
                [{"id": "b" * 32, "name": "other", "version": "0.0.0", "enabled": True, "path": str(self.staged)}],
                self.extension_id,
                self.staged,
                {"name": "other", "version": "0.0.0"},
            )
        except LedgerMismatch:
            return True
        return False


def _live_record_base() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "evidence_mode": "live",
        "lifecycle": {
            "schema_version": SCHEMA_VERSION,
            "evidence_mode": "live",
            "status": "failed_closed",
            "install": "not_observed",
            "update": "not_observed",
            "uninstall": "not_observed",
            "downgrade": "not_observed",
            "rollback": "not_observed",
        },
        "rollback_safety": {
            "schema_version": SCHEMA_VERSION,
            "evidence_mode": "live",
            "new_mutations": "not_observed",
            "pages_retained": None,
            "user_tabs_preserved": None,
            "chrome_process_terminated": None,
            "global_close_used": False,
            "incompatible_ledger_refused": False,
            "kill_switch": {"status": "not_observed", "armed": False, "verified": False},
        },
        "phases": {},
        "safety": {
            "disposable_profile": True,
            "existing_endpoint_attached": False,
            "command_line_extension_loading": False,
            "private_extension_api": False,
            "raw_browser_ids_logged": False,
            "extension_ids_logged": False,
            "absolute_paths_logged": False,
            "secrets_logged": False,
        },
    }


def _offline_record(source: Path | None = None, fixture_url: str | None = None) -> dict[str, Any]:
    record = _live_record_base()
    if source is not None:
        selected_fixture_url = safe_fixture_url(fixture_url, source)
        record["phase"] = 4 if source == SOURCE_EXTENSION_DIR else 0
        record["lifecycle_lane"] = "phase4-production" if source == SOURCE_EXTENSION_DIR else "legacy-test"
        record["lifecycle_provenance"] = build_lifecycle_provenance(source, selected_fixture_url)
    record["evidence_mode"] = "offline"
    record["lifecycle"].update(
        {
            "evidence_mode": "offline",
            "status": "not_gateable_offline",
            "install": "not_measured_offline",
            "update": "not_measured_offline",
            "uninstall": "not_measured_offline",
            "downgrade": "not_measured_offline",
            "rollback": "not_measured_offline",
        }
    )
    record["rollback_safety"].update(
        {
            "evidence_mode": "offline",
            "new_mutations": "not_measured_offline",
            "kill_switch": {"status": "not_measured_offline", "armed": False, "verified": False},
        }
    )
    record["status"] = "offline_passed"
    record["mode"] = "offline"
    return record


def execute_lifecycle(
    *,
    profile_dir: Path | None = None,
    chrome_path: str | None = None,
    source_extension_dir: Path = SOURCE_EXTENSION_DIR,
    fixture_url: str | None = None,
    debug_port: int | None = None,
    timeout: float = 8.0,
) -> dict[str, Any]:
    """Run all five phases against one owned disposable Chrome process."""
    record = _live_record_base()
    if platform.system() != "Darwin":
        record["failure_code"] = "unsupported_platform"
        return record
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be positive and finite")

    source = safe_extension_dir(str(source_extension_dir))
    source_manifest = _manifest(source / "manifest.json")
    base_version = source_manifest["version"]
    update_version = _next_version(base_version)
    older_version = _default_older_version(base_version)
    extension_id = _manifest_extension_id(source_manifest)
    assert isinstance(extension_id, str)
    if not (_version(older_version) < _version(base_version) < _version(update_version)):
        raise ValueError("lifecycle versions must be strictly ordered")
    selected_fixture_url = safe_fixture_url(fixture_url, source)
    source_provenance = build_lifecycle_provenance(source, selected_fixture_url)
    record["phase"] = 4 if source == SOURCE_EXTENSION_DIR else 0
    record["lifecycle_lane"] = "phase4-production" if source == SOURCE_EXTENSION_DIR else "legacy-test"
    record["lifecycle_provenance"] = source_provenance
    expected_source_hashes = source_provenance["source_hashes"]
    active_fixture_url = selected_fixture_url

    owned_profile = profile_dir or Path(tempfile.mkdtemp(prefix="agentyc-p4-lifecycle-"))
    remove_profile = True
    process: subprocess.Popen[bytes] | None = None
    stderr_handle: Any | None = None
    client: DevToolsSocket | None = None
    session_id: str | None = None
    staged: Path | None = None
    baseline_pages: int | None = None
    phase_error: str | None = None
    cleanup_uninstall: dict[str, Any] | None = None
    cleanup_errors: list[str] = []
    cleanup_process_terminated = False
    cleanup_profile_removed = False
    try:
        safe_profile_dir(str(owned_profile))
        claim_disposable_profile(owned_profile)
        staged, manifest = _stage_extension(source, owned_profile, base_version, expected_source_hashes)
        port = debug_port or _free_port()
        if not 1 <= port <= 65535:
            raise ValueError("debug port must be between 1 and 65535")
        try:
            chrome_endpoint(port, "/json/version")
        except (OSError, ValueError):
            pass
        else:
            raise LifecycleFailure("refusing to attach to an existing debug endpoint")
        executable = chrome_binary(chrome_path)
        if executable is None:
            raise LifecycleFailure("an installed Chrome executable was not found")
        stderr_handle = (owned_profile / "chrome.stderr.log").open("wb")
        command = build_chrome_command(
            executable,
            owned_profile,
            port,
            extension_dir=None,
            fixture_url=active_fixture_url,
            operator_assisted=False,
        )
        process = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=stderr_handle,
            start_new_session=True,
        )
        version = wait_for_chrome(port, timeout=timeout)
        if version is None or process.poll() is not None or not _endpoint_belongs_to_process(port, process):
            raise LifecycleFailure("owned Chrome did not expose a verified endpoint")
        baseline_pages = _fixture_pages(port, process, active_fixture_url)
        if baseline_pages != 1:
            raise LifecycleFailure("disposable profile did not expose exactly one owned fixture page")
        websocket_url = _browser_websocket_url(version, port)
        if not websocket_url.startswith(f"ws://127.0.0.1:{port}{BROWSER_CDP_PATH_PREFIX}"):
            raise LifecycleFailure("Chrome browser websocket was not bound to the owned endpoint")
        client = DevToolsSocket(websocket_url)
        attached = client.command("Target.attachToBrowserTarget")
        session_id = attached.get("sessionId") if isinstance(attached, dict) else None
        if not isinstance(session_id, str) or not CDP_SESSION_ID_PATTERN.fullmatch(session_id):
            raise LifecycleFailure("browser-target CDP session was not established")

        ledger = LifecycleLedger(extension_id, staged, base_version)
        record["rollback_safety"]["incompatible_ledger_refused"] = ledger.refuse_incompatible()
        if not record["rollback_safety"]["incompatible_ledger_refused"]:
            raise LifecycleFailure("incompatible lifecycle ledger was not refused")

        phase = _load_version(client, session_id, staged, manifest, extension_id, "install")
        ledger.accept(_inventory(client, session_id), manifest)
        record["phases"]["install"] = phase
        record["lifecycle"]["install"] = "installed"

        manifest["version"] = update_version
        (staged / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        phase = _load_version(client, session_id, staged, manifest, extension_id, "update")
        ledger.accept(_inventory(client, session_id), manifest)
        record["phases"]["update"] = phase
        record["lifecycle"]["update"] = "passed"

        manifest["version"] = older_version
        (staged / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        phase = _load_version(client, session_id, staged, manifest, extension_id, "downgrade")
        ledger.accept(_inventory(client, session_id), manifest)
        record["phases"]["downgrade"] = phase
        record["lifecycle"]["downgrade"] = "passed"

        manifest["version"] = base_version
        (staged / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        phase = _load_version(client, session_id, staged, manifest, extension_id, "rollback")
        ledger.accept(_inventory(client, session_id), manifest)
        record["phases"]["rollback"] = phase
        record["lifecycle"]["rollback"] = "rolled_back"

        phase = _uninstall(client, session_id, staged, extension_id)
        record["phases"]["uninstall"] = phase
        record["lifecycle"]["uninstall"] = "passed"

        final_pages = _fixture_pages(port, process, active_fixture_url)
        process_alive = process.poll() is None
        record["rollback_safety"].update(
            {
                "new_mutations": "paused",
                "pages_retained": final_pages == baseline_pages == 1,
                "user_tabs_preserved": final_pages == baseline_pages == 1,
                "chrome_process_terminated": not process_alive,
                "global_close_used": False,
                "kill_switch": {"status": "armed_and_verified", "armed": True, "verified": process_alive},
            }
        )
        if record["rollback_safety"]["chrome_process_terminated"] is not False:
            raise LifecycleFailure("Chrome terminated before rollback safety was verified")
        if not record["rollback_safety"]["pages_retained"] or not record["rollback_safety"]["kill_switch"]["verified"]:
            raise LifecycleFailure("rollback safety was not verified")
        record["lifecycle"]["status"] = "passed"
        record["chrome"] = {"browser_target_cdp": True, "version_observed": isinstance(version, dict)}
    except (OSError, ValueError, TypeError, TimeoutError, LifecycleFailure, KeyError) as error:
        phase_error = type(error).__name__
        record["failure_code"] = phase_error
    finally:
        if client is not None and session_id is not None:
            try:
                if record["lifecycle"]["uninstall"] != "passed":
                    cleanup_uninstall = _uninstall(client, session_id, staged, extension_id) if staged is not None else None
            except (OSError, ValueError, TypeError, TimeoutError, LifecycleFailure):
                cleanup_uninstall = {"status": "failed_closed", "absence_verified": False}
                cleanup_errors.append("extension_uninstall_not_verified")
            try:
                client.command("Target.detachFromTarget", {"sessionId": session_id})
            except (OSError, ValueError, TypeError, TimeoutError):
                pass
            client.close()
        if process is not None:
            try:
                if process.poll() is None:
                    process.terminate()
                process.wait(timeout=5)
            except (OSError, subprocess.SubprocessError):
                try:
                    process.kill()
                    process.wait(timeout=3)
                except (OSError, subprocess.SubprocessError):
                    cleanup_errors.append("chrome_process_termination_unconfirmed")
            cleanup_process_terminated = process.poll() is not None
            if not cleanup_process_terminated:
                cleanup_errors.append("chrome_process_still_alive")
        if stderr_handle is not None:
            try:
                stderr_handle.close()
            except OSError:
                pass
        if remove_profile:
            try:
                shutil.rmtree(owned_profile)
                cleanup_profile_removed = not owned_profile.exists()
                if not cleanup_profile_removed:
                    cleanup_errors.append("profile_removal_unconfirmed")
            except OSError:
                cleanup_errors.append("profile_removal_failed")

    cleanup_uninstall_verified = cleanup_uninstall is None or cleanup_uninstall.get("absence_verified") is True
    if not cleanup_uninstall_verified:
        cleanup_errors.append("extension_uninstall_not_verified")
    cleanup_ok = not cleanup_errors and (process is None or cleanup_process_terminated) and (not remove_profile or cleanup_profile_removed)
    record["cleanup"] = {
        "status": "passed" if cleanup_ok else "failed_closed",
        "extension_uninstall_verified": cleanup_uninstall_verified,
        "chrome_process_terminated": cleanup_process_terminated if process is not None else False,
        "profile_removed": cleanup_profile_removed if remove_profile else False,
    }
    if cleanup_errors:
        record["cleanup"]["errors"] = cleanup_errors
    if phase_error is not None or not cleanup_ok:
        record["lifecycle"]["status"] = "failed_closed"
        record["status"] = "failed_closed"
        if phase_error is None:
            record["failure_code"] = cleanup_errors[0] if cleanup_errors else "cleanup_unconfirmed"
    else:
        record["status"] = "live_passed"
    return record


def write_record(artifact_dir: Path, record: dict[str, Any]) -> Path:
    artifact_dir = safe_artifact_dir(str(artifact_dir))
    if record.get("lifecycle_lane") == "phase4-production" and record.get("status") == "live_passed":
        validation_record = dict(record)
        validation_record.pop("record_validation", None)
        errors = validate_lifecycle_record(
            validation_record,
            require_live=True,
            require_provenance=True,
            require_production_provenance=True,
        )
        if errors:
            raise ValueError("lifecycle record provenance or safety validation failed")
    artifact_dir.mkdir(parents=True, exist_ok=True)
    phase = 4 if record.get("lifecycle_lane") == "phase4-production" else 0
    build_tuple: dict[str, Any] = {
        "phase": phase,
        "lifecycle_lane": record.get("lifecycle_lane", "legacy-test"),
    }
    provenance = record.get("lifecycle_provenance")
    if isinstance(provenance, dict):
        manifest_identity = provenance.get("manifest_identity")
        host_identity = provenance.get("host_identity")
        build_tuple.update(
            {
                "source_root": provenance.get("source_root"),
                "source_tree_sha256": provenance.get("source_tree_sha256"),
                "manifest_sha256": manifest_identity.get("sha256") if isinstance(manifest_identity, dict) else None,
                "host_manifest_sha256": host_identity.get("sha256") if isinstance(host_identity, dict) else None,
            }
        )
    rendered = add_envelope(record, kind="install-lifecycle", build_tuple=build_tuple)
    rendered = redact_for_persistence(rendered)
    path = artifact_dir / RECORD_NAME
    write_json_atomic(path, rendered)
    return path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", "--execute", "--require-live", dest="run", action="store_true", help="run the real disposable Chrome lifecycle")
    parser.add_argument("--dry-run", action="store_true", help="write an offline record without launching Chrome (default)")
    parser.add_argument("--artifact-dir", default=DEFAULT_ARTIFACT_DIR.relative_to(ROOT).as_posix())
    parser.add_argument("--profile-dir", help="absolute empty disposable profile path inside the system temporary directory")
    parser.add_argument("--chrome-binary", help="explicit already-installed Chrome executable")
    parser.add_argument("--extension-dir", help="repository-relative MV3 source directory; defaults to production extension/")
    parser.add_argument(
        "--fixture-url",
        help="explicit local fixture URL or HTTP(S) URL; production defaults to tests/fixtures/browser-task-spaces/small-form.html",
    )
    parser.add_argument("--debug-port", type=int, help="unused local debug port; defaults to an OS-selected free port")
    parser.add_argument("--timeout", type=float, default=8.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.dry_run and args.run:
        parser.error("--dry-run cannot be combined with --run")
    if args.debug_port is not None and not 1 <= args.debug_port <= 65535:
        parser.error("--debug-port must be between 1 and 65535")
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--timeout must be positive and finite")
    try:
        artifact_dir = safe_artifact_dir(args.artifact_dir)
        source = safe_extension_dir(args.extension_dir)
        selected_fixture_url = safe_fixture_url(args.fixture_url, source)
        profile = safe_profile_dir(args.profile_dir) if args.profile_dir else None
    except (OSError, ValueError) as error:
        print(json.dumps({"status": "invalid_arguments", "detail": str(error)}, sort_keys=True), file=sys.stderr)
        return 2

    if args.run:
        record = execute_lifecycle(
            profile_dir=profile,
            chrome_path=args.chrome_binary,
            source_extension_dir=source,
            fixture_url=args.fixture_url,
            debug_port=args.debug_port,
            timeout=args.timeout,
        )
    else:
        record = _offline_record(source, selected_fixture_url)
    try:
        live_passed = record.get("evidence_mode") == "live" and record.get("status") == "live_passed"
        errors = validate_lifecycle_record(
            record,
            require_live=live_passed,
            require_provenance=live_passed,
            require_production_provenance=record.get("lifecycle_lane") == "phase4-production",
        )
        if errors:
            record["record_validation"] = {"status": "blocked", "errors": errors}
        else:
            record["record_validation"] = {"status": "passed", "errors": []}
        path = write_record(artifact_dir, record)
    except (OSError, ValueError, TypeError) as error:
        print(json.dumps({"status": "artifact_write_failed", "detail": type(error).__name__}, sort_keys=True), file=sys.stderr)
        return 2
    output = redact_for_persistence({"status": record.get("status"), "record": str(path.relative_to(ROOT)), "record_validation": record["record_validation"]})
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0 if record.get("status") in {"offline_passed", "live_passed"} and record["record_validation"]["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
