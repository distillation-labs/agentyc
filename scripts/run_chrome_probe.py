#!/usr/bin/env python3
"""Run the test-only MV3 P0-T2 probe.

Offline mode is the default and performs only deterministic fixture checks.
`--headed` requests live inspection of an already running debug endpoint;
`--require-live` makes that live inspection required and fails when Chrome is
unavailable. `--launch-chrome` is a separate explicit opt-in, is always
required when used, and always uses the supplied isolated profile directory.
The script never touches the default Chrome profile.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import signal
import socket
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from artifact_envelope import envelope as add_envelope
from artifact_envelope import write_json_atomic

ROOT = Path(__file__).resolve().parents[1]
EXTENSION_DIR = ROOT / "extension" / "probes"
DEFAULT_ARTIFACT_DIR = ROOT / "artifacts" / "p0-extension"
REQUIRED_PERMISSIONS = {"debugger", "nativeMessaging", "storage", "tabGroups", "tabs"}


def load_manifest() -> dict[str, Any]:
    manifest = json.loads((EXTENSION_DIR / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("manifest_version") != 3:
        raise ValueError("probe manifest is not MV3")
    permissions = set(manifest.get("permissions", []))
    missing = REQUIRED_PERMISSIONS - permissions
    if missing:
        raise ValueError(f"probe manifest is missing permissions: {sorted(missing)}")
    for filename in ("service_worker.js", "probe.html", "probe.js", "fixture.html"):
        if not (EXTENSION_DIR / filename).is_file():
            raise ValueError(f"probe fixture is missing {filename}")
    return manifest


def safe_artifact_dir(value: str) -> Path:
    requested = Path(value)
    if not requested.is_absolute():
        requested = ROOT / requested
    current = requested
    while current != current.parent:
        if current.is_symlink():
            raise SystemExit("artifact path components must not be symlinks")
        current = current.parent
    requested = requested.resolve()
    allowed = DEFAULT_ARTIFACT_DIR.resolve()
    if requested != allowed and allowed not in requested.parents:
        raise SystemExit("artifact directory must be inside artifacts/p0-extension/")
    return requested


def safe_profile_dir(value: str) -> Path:
    requested = Path(value)
    if not requested.is_absolute():
        requested = ROOT / requested
    current = requested
    while current != current.parent:
        if current.is_symlink():
            raise SystemExit("profile path components must not be symlinks")
        current = current.parent
    requested = requested.resolve()
    allowed = DEFAULT_ARTIFACT_DIR.resolve()
    if allowed not in requested.parents and requested != allowed:
        raise SystemExit("profile directory must be inside artifacts/p0-extension/")
    return requested


def chrome_endpoint(port: int, path: str) -> Any:
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}")
    with urllib.request.urlopen(request, timeout=0.75) as response:
        return json.loads(response.read().decode("utf-8"))


class DevToolsSocket:
    """Minimal bounded WebSocket client for the local Chrome debugging endpoint."""

    def __init__(self, url: str, timeout: float = 2.0) -> None:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "ws" or parsed.hostname not in {"127.0.0.1", "localhost"}:
            raise ValueError("debugger websocket must be local ws")
        self.socket = socket.create_connection((parsed.hostname, parsed.port or 80), timeout=timeout)
        self.socket.settimeout(timeout)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            f"GET {parsed.path or '/'} HTTP/1.1\\r\\n"
            f"Host: {parsed.hostname}:{parsed.port or 80}\\r\\n"
            "Upgrade: websocket\\r\\nConnection: Upgrade\\r\\n"
            f"Sec-WebSocket-Key: {key}\\r\\nSec-WebSocket-Version: 13\\r\\n\\r\\n"
        ).encode("ascii")
        self.socket.sendall(request)
        response = self._read_http_headers()
        if not response.startswith(b"HTTP/1.1 101"):
            raise OSError("debugger websocket handshake failed")
        self.next_id = 0

    def _read_http_headers(self) -> bytes:
        data = bytearray()
        while b"\\r\\n\\r\\n" not in data and len(data) <= 16 * 1024:
            chunk = self.socket.recv(1024)
            if not chunk:
                break
            data.extend(chunk)
        return bytes(data)

    def _frame(self, payload: bytes, opcode: int = 1) -> bytes:
        length = len(payload)
        if length > 64 * 1024:
            raise ValueError("debugger message exceeds the probe bound")
        mask = os.urandom(4)
        if length < 126:
            header = bytes([0x80 | opcode, 0x80 | length])
        else:
            header = bytes([0x80 | opcode, 0x80 | 126]) + struct.pack("!H", length)
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        return header + mask + masked

    def _receive(self) -> tuple[int, bytes]:
        header = self._receive_exact(2)
        first, second = header
        opcode = first & 0x0F
        length = second & 0x7F
        if length == 126:
            length = struct.unpack("!H", self._receive_exact(2))[0]
        elif length == 127:
            raise ValueError("large debugger websocket frames are not supported")
        if length > 64 * 1024:
            raise ValueError("debugger frame exceeds the probe bound")
        masked = bool(second & 0x80)
        mask = self._receive_exact(4) if masked else b""
        payload = self._receive_exact(length)
        if masked:
            payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        return opcode, payload

    def _receive_exact(self, size: int) -> bytes:
        result = bytearray()
        while len(result) < size:
            chunk = self.socket.recv(size - len(result))
            if not chunk:
                raise OSError("debugger websocket closed")
            result.extend(chunk)
        return bytes(result)

    def command(self, method: str, params: dict[str, Any] | None = None) -> Any:
        self.next_id += 1
        command_id = self.next_id
        payload = json.dumps({"id": command_id, "method": method, "params": params or {}}, separators=(",", ":")).encode("utf-8")
        self.socket.sendall(self._frame(payload))
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            opcode, frame = self._receive()
            if opcode == 9:
                self.socket.sendall(self._frame(frame, opcode=10))
                continue
            if opcode == 8:
                raise OSError("debugger websocket closed")
            if opcode != 1:
                continue
            message = json.loads(frame.decode("utf-8"))
            if message.get("id") == command_id:
                if "error" in message:
                    raise OSError("debugger command rejected")
                return message.get("result")
        raise TimeoutError("debugger command timed out")

    def close(self) -> None:
        try:
            self.socket.close()
        except OSError:
            pass


def extension_probe_result(port: int, targets: list[dict[str, Any]]) -> dict[str, Any]:
    workers = [target for target in targets if target.get("type") == "service_worker" and str(target.get("url", "")).startswith("chrome-extension://")]
    if not workers or not workers[0].get("webSocketDebuggerUrl"):
        return {"status": "live_unavailable", "limitation": "probe extension service worker was not observed"}
    request_id = "p0-live-probe"
    socket_client: DevToolsSocket | None = None
    try:
        socket_client = DevToolsSocket(workers[0]["webSocketDebuggerUrl"])
        socket_client.command("Runtime.enable")
        socket_client.command(
            "Runtime.evaluate",
            {
                "expression": "chrome.storage.local.set({run_probe:{request_id:'p0-live-probe'}})",
                "awaitPromise": True,
                "returnByValue": True,
            },
        )
        deadline = time.monotonic() + 8.0
        while time.monotonic() < deadline:
            result = socket_client.command(
                "Runtime.evaluate",
                {
                    "expression": "chrome.storage.local.get('last_probe')",
                    "awaitPromise": True,
                    "returnByValue": True,
                },
            )
            value = (((result or {}).get("result") or {}).get("value") or {}).get("last_probe")
            if isinstance(value, dict) and value.get("request_id") == request_id:
                required = {
                    "extension_loaded": value.get("extension_loaded") is True,
                    "debugger_command_passed": value.get("debugger_command_passed") is True,
                    "debugger_event_received": value.get("debugger_event_received") is True,
                    "tab_group_created": value.get("tab_group_created") is True,
                    "native_messaging_passed": value.get("native_messaging_passed") is True,
                    "cleanup_passed": value.get("cleanup_passed") is True,
                }
                transcript = value.get("handshake_transcript")
                if all(required.values()) and isinstance(transcript, list) and transcript:
                    return {"status": "live_passed", **required, "handshake_transcript": transcript[:8]}
                return {"status": "live_unavailable", **required, "handshake_transcript": transcript if isinstance(transcript, list) else [], "limitation": "extension probe returned incomplete evidence"}
            time.sleep(0.1)
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError, TimeoutError):
        return {"status": "live_unavailable", "limitation": "extension result handoff failed closed"}
    finally:
        if socket_client is not None:
            socket_client.close()
    return {"status": "live_unavailable", "limitation": "extension probe result was not received before the deadline"}


def wait_for_chrome(port: int, timeout: float = 8.0) -> dict[str, Any] | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            return chrome_endpoint(port, "/json/version")
        except (OSError, urllib.error.URLError, ValueError):
            time.sleep(0.1)
    return None


def chrome_binary(value: str | None) -> str | None:
    if value:
        return value
    candidates = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary",
        "google-chrome",
        "google-chrome-stable",
        "chromium",
        "chromium-browser",
    ]
    for candidate in candidates:
        if os.path.isabs(candidate) and Path(candidate).is_file():
            return candidate
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    return None


def inspect_live(port: int, profile_dir: Path | None, launch: bool, binary: str | None, artifact_dir: Path) -> dict[str, Any]:
    if launch and profile_dir is None:
        return {"status": "live_unavailable", "limitation": "--launch-chrome requires --profile-dir inside the artifact directory."}
    if launch:
        try:
            chrome_endpoint(port, "/json/version")
        except (OSError, urllib.error.URLError, ValueError):
            pass
        else:
            return {"status": "live_unavailable", "limitation": f"debug port {port} is already in use; refusing to attach or launch ambiguously."}
    process: subprocess.Popen[bytes] | None = None
    launched = False
    try:
        if launch:
            executable = chrome_binary(binary)
            if executable is None:
                return {"status": "live_unavailable", "limitation": "Chrome binary was not found; install Chrome or pass --chrome-binary."}
            assert profile_dir is not None
            profile_dir.mkdir(parents=True, exist_ok=True)
            fixture = (EXTENSION_DIR / "fixture.html").resolve().as_uri() + "?agentyc_p0_probe=1&agentyc_p0_fixture_sha256=fc8ff011514dc69192ec3f383821a38d9c6f584d2756d8013446cbfe80b902e6"
            command = [
                executable,
                f"--user-data-dir={profile_dir}",
                f"--load-extension={EXTENSION_DIR}",
                f"--remote-debugging-port={port}",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-background-networking",
                "--new-window",
                fixture,
            ]
            process = subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            launched = True
        version = wait_for_chrome(port)
        if version is None:
            return {"status": "live_unavailable", "limitation": "Chrome did not expose the requested debug endpoint."}
        try:
            targets = chrome_endpoint(port, "/json/list")
            target_count = len(targets) if isinstance(targets, list) else None
        except (OSError, urllib.error.URLError, ValueError):
            targets = []
            target_count = None
        extension = extension_probe_result(port, targets if isinstance(targets, list) else [])
        return {
            "status": extension.get("status", "live_unavailable"),
            "chrome_version": version.get("Browser", "unknown"),
            "target_count": target_count,
            "launched_by_probe": launched,
            "extension_loaded": extension.get("extension_loaded", False),
            "debugger_command_passed": extension.get("debugger_command_passed", False),
            "debugger_event_received": extension.get("debugger_event_received", False),
            "event_received": extension.get("debugger_event_received", False),
            "tab_group_created": extension.get("tab_group_created", False),
            "native_messaging_passed": extension.get("native_messaging_passed", False),
            "cleanup_passed": extension.get("cleanup_passed", False),
            "handshake_transcript": extension.get("handshake_transcript", []),
            "permission_prompts": "not recorded; Chrome UI prompts require explicit user handling",
            "limitation": extension.get("limitation"),
        }
    finally:
        if process is not None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except (OSError, AttributeError):
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except (OSError, AttributeError):
                    process.kill()
                process.wait(timeout=5)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--headed", action="store_true", help="request optional live Chrome inspection")
    parser.add_argument(
        "--require-live",
        "--required-live",
        dest="require_live",
        action="store_true",
        help="require live Chrome inspection and exit nonzero when unavailable",
    )
    parser.add_argument("--launch-chrome", action="store_true", help="explicitly launch isolated Chrome; never implied by default")
    parser.add_argument("--profile-dir", help="isolated profile path, required for --launch-chrome")
    parser.add_argument("--chrome-binary")
    parser.add_argument("--debug-port", type=int, default=9222)
    parser.add_argument("--artifact-dir", default="artifacts/p0-extension")
    args = parser.parse_args()
    if args.launch_chrome and not args.headed:
        parser.error("--launch-chrome requires --headed")
    if args.require_live and not args.headed:
        parser.error("--require-live requires --headed")
    artifact_dir = safe_artifact_dir(args.artifact_dir)
    profile_dir = safe_profile_dir(args.profile_dir) if args.profile_dir else None
    manifest = load_manifest()
    live_required = bool(args.require_live or args.launch_chrome)
    report: dict[str, Any] = {
        "probe": "P0-T2",
        "status": "offline_passed",
        "manifest_version": manifest["manifest_version"],
        "extension_version": manifest["version"],
        "permissions": manifest["permissions"],
        "mode": "headed" if args.headed else "offline",
        "live": {
            "requested": bool(args.headed),
            "required": live_required,
            "status": "not_requested",
        },
        "safety": {
            "default_chrome_launch": False,
            "default_profile_mutation": False,
            "raw_ids_logged": False,
            "secrets_logged": False,
            "fixture_only_mutation": True,
        },
        "handshake_transcript": [],
        "screenshots": [],
        "limitations": [],
    }
    if args.headed:
        live = inspect_live(args.debug_port, profile_dir, args.launch_chrome, args.chrome_binary, artifact_dir)
        report["live"] = {"requested": True, "required": live_required, **live}
        if live["status"] == "live_unavailable":
            report["status"] = "live_required_unavailable" if live_required else "live_optional_unavailable"
            report["limitations"].append(live["limitation"])
        elif live_required:
            # /json/version proves only that a debugging endpoint is reachable.
            # It does not prove that the MV3 extension, debugger permission,
            # tab-group mutation, and Native Messaging handshake succeeded.
            report["status"] = "live_required_unavailable"
            report["limitations"].append(
                "Chrome endpoint was observed, but no extension probe result was supplied; "
                "an endpoint alone cannot close the live gate."
            )
        else:
            report["status"] = "live_observed"
            report["limitations"].append(live["limitation"])
    else:
        report["limitations"].append("Real Chrome, extension installation, and Native Messaging registration were not requested.")
    artifact_dir.mkdir(parents=True, exist_ok=True)
    add_envelope(report, kind="chrome-extension-probe")
    output = artifact_dir / "report.json"
    try:
        write_json_atomic(output, report)
    except (OSError, ValueError) as error:
        print(f"chrome probe error: {type(error).__name__}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 1 if report["status"] == "live_required_unavailable" else 0


if __name__ == "__main__":
    raise SystemExit(main())
