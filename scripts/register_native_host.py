#!/usr/bin/env python3
"""Explicitly install or check the production agentyc Native Messaging host.

This script never launches Chrome and never automates Chrome's extension UI.
Writing a user-level registration requires an explicit ``--install`` action and
an exact extension ID. The Chrome manifest's ``allowed_origins`` is the primary
origin allowlist; the host validates the transport-supplied origin format.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import os
import stat
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOST_NAME = "com.agentyc.host"
PRODUCTION_EXTENSION_DIR = ROOT / "extension"
EXPECTED_EXTENSION_NAME = "Agentyc"
MANIFEST_MODE = 0o644
EXTENSION_ID_LENGTH = 32
EXTENSION_ID_ALPHABET = "abcdefghijklmnop"


def extension_origin(extension_id: str) -> str:
    if len(extension_id) != EXTENSION_ID_LENGTH or any(char < "a" or char > "p" for char in extension_id):
        raise TypeError("extension ID must contain exactly 32 characters in a-p")
    return f"chrome-extension://{extension_id}"


def extension_id_from_manifest(directory: Path) -> str:
    """Derive the stable unpacked-extension ID from the public manifest key."""
    assert_no_symlinks(directory)
    directory = directory.expanduser().resolve()
    if not directory.is_dir() or directory.is_symlink():
        raise ValueError("production extension directory is missing or unsafe")
    manifest_path = directory / "manifest.json"
    assert_no_symlinks(manifest_path)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError("production extension manifest is unreadable") from error
    if not isinstance(manifest, dict) or manifest.get("name") != EXPECTED_EXTENSION_NAME:
        raise ValueError("extension directory is not the production agentyc extension")
    key = manifest.get("key")
    if not isinstance(key, str) or not key:
        raise ValueError("production extension manifest must carry a stable public key")
    try:
        public_key = base64.b64decode(key, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ValueError("production extension public key is invalid") from error
    if not public_key:
        raise ValueError("production extension public key is empty")
    digest = hashlib.sha256(public_key).digest()
    extension_id = "".join(
        EXTENSION_ID_ALPHABET[byte >> 4] + EXTENSION_ID_ALPHABET[byte & 0x0F]
        for byte in digest[:16]
    )
    extension_origin(extension_id)
    return extension_id


def default_host_path() -> Path:
    release = ROOT / "target" / "release" / "agentyc-native-host"
    debug = ROOT / "target" / "debug" / "agentyc-native-host"
    return release if release.is_file() else debug


def manifest_path() -> Path:
    if sys.platform != "darwin":
        raise ValueError("production registration currently supports macOS only")
    return Path.home() / "Library" / "Application Support" / "Google" / "Chrome" / "NativeMessagingHosts" / f"{HOST_NAME}.json"


def assert_no_symlinks(path: Path) -> None:
    current = path
    while current != current.parent:
        if current.is_symlink():
            raise ValueError("registration path contains a symlink")
        current = current.parent


def validate_host_path(value: str | None) -> Path:
    path = Path(value).expanduser() if value else default_host_path()
    assert_no_symlinks(path)
    path = path.resolve()
    if not path.is_file() or not os.access(path, os.X_OK):
        raise ValueError("production Native Messaging host is missing or not executable")
    return path


def expected_manifest(host_path: Path, origin: str) -> dict[str, object]:
    return {
        "name": HOST_NAME,
        "description": "agentyc production Chrome Native Messaging host",
        "path": str(host_path),
        "type": "stdio",
        "allowed_origins": [f"{origin}/"],
    }


def read_manifest(path: Path) -> dict[str, object] | None:
    if not path.is_file() or path.is_symlink():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError("existing Native Messaging manifest is unreadable") from error
    if not isinstance(value, dict):
        raise TypeError("existing Native Messaging manifest is not an object")
    return value


def check(path: Path, host_path: Path, origin: str) -> dict[str, object]:
    actual = read_manifest(path)
    expected = expected_manifest(host_path, origin)
    installed = actual == expected and stat.S_IMODE(path.stat().st_mode) == MANIFEST_MODE
    return {
        "status": "installed" if installed else "not_installed",
        "manifest_filename": path.name,
        "host_present": host_path.is_file() and os.access(host_path, os.X_OK),
        "origin_matches": bool(actual and actual.get("allowed_origins") == expected["allowed_origins"]),
        "host_path_matches": bool(actual and actual.get("path") == expected["path"]),
        "chrome_launch": "never",
        "chrome_download": "never",
        "secrets_logged": False,
    }


def atomic_write(path: Path, value: dict[str, object]) -> None:
    assert_no_symlinks(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("wb", dir=path.parent, prefix=f".{path.name}.tmp-", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
            os.fchmod(handle.fileno(), MANIFEST_MODE)
        os.replace(temporary, path)
        temporary = None
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--install", action="store_true")
    actions.add_argument("--check", action="store_true")
    actions.add_argument("--remove", action="store_true")
    identity = parser.add_mutually_exclusive_group()
    identity.add_argument("--extension-id", help="stable 32-character Chrome extension ID")
    identity.add_argument(
        "--extension-dir",
        default=str(PRODUCTION_EXTENSION_DIR),
        help="production unpacked extension directory used to derive its ID",
    )
    parser.add_argument("--host-path")
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args(argv)

    try:
        extension_id = args.extension_id or extension_id_from_manifest(Path(args.extension_dir))
        origin = extension_origin(extension_id)
        host_path = validate_host_path(args.host_path)
        destination = manifest_path()
        if args.remove:
            actual = read_manifest(destination)
            expected = expected_manifest(host_path, origin)
            if actual != expected:
                raise ValueError("refusing to remove a manifest not owned by this exact host/origin")
            destination.unlink()
            result = {"status": "removed", "manifest_filename": destination.name, "chrome_launch": "never", "chrome_download": "never", "secrets_logged": False}
        elif args.check:
            result = check(destination, host_path, origin)
        else:
            actual = read_manifest(destination)
            expected = expected_manifest(host_path, origin)
            if actual is not None and actual != expected and not args.replace:
                raise ValueError("manifest exists; pass --replace for explicit replacement")
            atomic_write(destination, expected)
            result = check(destination, host_path, origin)
    except (OSError, UnicodeError, TypeError, ValueError, RuntimeError) as error:
        print(json.dumps({"status": "rejected", "reason": str(error), "chrome_launch": "never", "chrome_download": "never", "secrets_logged": False}, sort_keys=True))
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] in {"installed", "removed"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
