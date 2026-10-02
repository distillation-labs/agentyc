#!/usr/bin/env python3
"""Run the Phase 0 Native Messaging probe.

Offline mode is the default and never starts Chrome or a host. Live host mode is
opt-in, uses an already-installed executable, and never launches or downloads
Chrome. Registration is a separate explicit action in
``register_native_messaging_probe.py``; ``--check-install`` is read-only.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from artifact_envelope import envelope as add_envelope
from artifact_envelope import write_json_atomic

ROOT = Path(__file__).resolve().parents[1]
PROBE_MODULE = ROOT / "tests" / "probes" / "native_messaging.py"
HOST_PATH = ROOT / "tests" / "probes" / "native_probe"
DEFAULT_ORIGIN = "chrome-extension://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
ORIGIN_PATTERN = re.compile(r"^chrome-extension://[a-p]{32}$")
_spec = importlib.util.spec_from_file_location("agentyc_p0_native_messaging", PROBE_MODULE)
if _spec is None or _spec.loader is None:
    raise RuntimeError("cannot load the local native messaging probe")
_module = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _module
_spec.loader.exec_module(_module)
FrameDecoder = _module.FrameDecoder
encode_json = _module.encode_json
run_deterministic_suite = _module.run_deterministic_suite


def safe_artifact_path(value: str) -> Path:
    requested = Path(value)
    if not requested.is_absolute():
        requested = ROOT / requested
    current = requested
    while current != current.parent:
        if current.is_symlink():
            raise SystemExit("artifact path components must not be symlinks")
        current = current.parent
    requested = requested.resolve()
    allowed = (ROOT / "artifacts" / "p0-native-protocol").resolve()
    if requested != allowed and allowed not in requested.parents:
        raise SystemExit("artifact must be inside artifacts/p0-native-protocol/")
    return requested


def safe_host_path(value: str) -> Path:
    requested = Path(value)
    if not requested.is_absolute():
        requested = ROOT / requested
    current = requested
    while current != current.parent:
        if current.is_symlink():
            raise SystemExit("host path components must not be symlinks")
        current = current.parent
    requested = requested.resolve()
    if requested != HOST_PATH.resolve():
        raise SystemExit("host must be the repository test probe")
    return requested


def redacted_metadata(*, host_path: Path, extension_origin_supplied: bool) -> dict[str, Any]:
    return {
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "cwd": "redacted",
        "host_path": "redacted",
        "host_filename": host_path.name,
        "extension_origin": "redacted" if extension_origin_supplied else "not_supplied",
        "chrome_launch": "never",
        "chrome_download": "never",
        "secrets_logged": False,
    }


def envelope(message_id: str, nonce: str, kind: str, origin: str) -> dict[str, Any]:
    return {
        "version": 1,
        "origin": origin,
        "message_id": message_id,
        "nonce": nonce,
        "kind": kind,
        "payload": {"fixture": "agentyc P0 probe fixture"},
    }


def framed_host_smoke(host_path: Path, extension_origin: str, timeout: float) -> dict[str, Any]:
    """Run the host with Chrome-style argv[1] and two framed messages."""
    if not ORIGIN_PATTERN.fullmatch(extension_origin):
        return {"status": "rejected", "reason": "invalid_extension_origin"}
    if not host_path.is_file() or not os.access(host_path, os.X_OK):
        return {"status": "unavailable", "reason": "host_missing_or_not_executable"}
    wire = encode_json(envelope("m-hello", "n-live", "hello", extension_origin))
    wire += encode_json(envelope("m-probe", "n-live", "probe", extension_origin))
    try:
        process = subprocess.Popen(
            [str(host_path), extension_origin],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return {"status": "unavailable", "reason": "host_could_not_start"}
    try:
        stdout, _ = process.communicate(wire, timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
        return {"status": "timeout", "reason": "host_handshake_timeout"}

    decoder = FrameDecoder()
    try:
        frames = decoder.feed(stdout)
        decoder.finish()
    except Exception:
        return {"status": "rejected", "reason": "invalid_host_response"}
    if process.returncode != 0 or len(frames) != 2:
        return {"status": "rejected", "reason": "host_rejected_handshake"}
    try:
        responses = [json.loads(frame.payload.decode("utf-8")) for frame in frames]
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"status": "rejected", "reason": "invalid_host_response"}
    expected = (("m-hello", "hello"), ("m-probe", "probe"))
    for response, (message_id, phase) in zip(responses, expected):
        if (
            not isinstance(response, dict)
            or response.get("accepted") is not True
            or response.get("message_id") != message_id
            or response.get("phase") != phase
            or response.get("nonce") != "n-live"
            or response.get("version") != 1
        ):
            return {"status": "rejected", "reason": "handshake_response_mismatch"}
    return {"status": "passed", "messages": 2}


def check_install(extension_origin: str) -> dict[str, Any]:
    """Call the read-only registration checker without exposing its path data."""
    checker = ROOT / "scripts" / "register_native_messaging_probe.py"
    try:
        completed = subprocess.run(
            [sys.executable, str(checker), "check", "--extension-origin", extension_origin],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=2,
            check=False,
        )
        result = json.loads(completed.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return {"status": "unavailable", "reason": "registration_check_failed"}
    return {
        "status": result.get("status", "rejected"),
        "manifest_present": bool(result.get("manifest_present")),
        "host_present": bool(result.get("host_present")),
        "origin_matches": bool(result.get("origin_matches")),
        "host_path_matches": bool(result.get("host_path_matches")),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--artifact",
        default="artifacts/p0-native-protocol/report.json",
        help="JSON report path under artifacts/p0-native-protocol/",
    )
    parser.add_argument("--live-host", action="store_true", help="opt in to a direct framed host handshake")
    parser.add_argument(
        "--require-live",
        "--required-live",
        dest="require_live",
        action="store_true",
        help="fail when the live host handshake is unavailable, rejected, or times out",
    )
    parser.add_argument("--check-install", action="store_true", help="explicitly check the installed host manifest; never installs it")
    parser.add_argument("--extension-origin", help="exact chrome-extension://.../ origin passed as host argv[1]")
    parser.add_argument("--host-path", default=str(HOST_PATH), help=argparse.SUPPRESS)
    parser.add_argument("--timeout", type=float, default=2.0, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.require_live:
        args.live_host = True
        if not args.check_install:
            parser.error("--require-live also requires --check-install and an explicit registered extension origin")
    if args.check_install and not args.extension_origin:
        parser.error("--check-install requires --extension-origin")
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    if not args.extension_origin:
        args.extension_origin = DEFAULT_ORIGIN
    if args.extension_origin == "chrome-extension://<registered-id>":
        args.extension_origin = os.environ.get("AGENTYC_EXTENSION_ORIGIN", "")
        if not args.extension_origin:
            parser.error("the registered extension origin must be supplied via AGENTYC_EXTENSION_ORIGIN")
    if not ORIGIN_PATTERN.fullmatch(args.extension_origin):
        parser.error("--extension-origin must be chrome-extension://<id> without a trailing slash")
    artifact = safe_artifact_path(args.artifact)
    host_path = safe_host_path(args.host_path)
    report: dict[str, Any] = {
        "probe": "P0-T3",
        "status": "offline_passed",
        "mode": "live-host" if args.live_host else "offline",
        "live": {"requested": bool(args.live_host), "required": bool(args.require_live), "status": "not_requested"},
        "installation": {"checked": False, "status": "not_requested"},
        "environment": redacted_metadata(host_path=host_path, extension_origin_supplied=bool(args.live_host or args.check_install)),
        "offline": run_deterministic_suite(),
        "limitations": [],
    }
    if args.check_install:
        report["installation"] = {"checked": True, **check_install(args.extension_origin)}
        if report["installation"]["status"] != "installed":
            report["limitations"].append("Explicit Native Messaging registration check did not pass.")
            if args.require_live:
                report["status"] = "live_required_unavailable"
    if args.live_host and report["status"] != "live_required_unavailable":
        live = framed_host_smoke(host_path, args.extension_origin, args.timeout)
        report["live"] = {"requested": True, "required": bool(args.require_live), **live}
        if live["status"] == "passed":
            smoke_status = "live_passed" if args.require_live else "host_smoke_passed"
            live["status"] = smoke_status
            report["live"]["status"] = smoke_status
            report["status"] = smoke_status
        else:
            report["status"] = "live_required_unavailable" if args.require_live else "live_optional_unavailable"
            report["limitations"].append("Live host handshake did not pass; Chrome was not launched.")
    elif not args.live_host:
        report["limitations"].append("Live host handshake was not requested; Chrome was not launched.")

    add_envelope(report, kind="native-messaging-probe")
    try:
        write_json_atomic(artifact, report)
    except (OSError, ValueError) as error:
        print(f"native messaging probe error: {type(error).__name__}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 1 if report["status"] == "live_required_unavailable" else 0


if __name__ == "__main__":
    raise SystemExit(main())
