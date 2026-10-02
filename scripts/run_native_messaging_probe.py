#!/usr/bin/env python3
"""Run the Phase 0 Native Messaging probe.

Offline mode is the default and never starts Chrome or a host. Direct host-smoke mode is
opt-in, uses an already-installed executable, and never launches or downloads
Chrome. It is not Chrome-mediated evidence. Registration is a separate explicit action in
``register_native_messaging_probe.py``; ``--check-install`` is read-only.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import selectors
import signal
import subprocess
import sys
import time
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
ProtocolError = _module.ProtocolError
MAX_CHUNK_BYTES = _module.MAX_CHUNK_BYTES
MAX_CUMULATIVE_FRAME_BYTES = _module.MAX_CUMULATIVE_FRAME_BYTES
MAX_ENVELOPE_BYTES = _module.MAX_ENVELOPE_BYTES
validate_host_response = _module.validate_host_response
encode_json = _module.encode_json
run_deterministic_suite = _module.run_deterministic_suite

MAX_HOST_OUTPUT_BYTES = MAX_CUMULATIVE_FRAME_BYTES


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


def _terminate_process_group(process: subprocess.Popen[bytes], *, force: bool) -> None:
    """Stop the probe and descendants without ever targeting the caller group."""
    group_signal_sent = False
    if force and os.name == "posix" and process.pid != os.getpid():
        try:
            os.killpg(process.pid, signal.SIGTERM)
            group_signal_sent = True
        except (OSError, ProcessLookupError):
            pass
        try:
            process.wait(timeout=0.25)
        except subprocess.TimeoutExpired:
            pass
        if group_signal_sent:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass
    try:
        process.wait(timeout=0.25)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except OSError:
            pass
        try:
            process.wait(timeout=0.25)
        except subprocess.TimeoutExpired:
            pass
    for stream in (process.stdin, process.stdout, process.stderr):
        if stream is not None:
            try:
                stream.close()
            except OSError:
                pass


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant: {value}")


def framed_host_smoke(host_path: Path, extension_origin: str, timeout: float) -> dict[str, Any]:
    """Run a bounded direct host handshake; this is not a Chrome-mediated test."""
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
            start_new_session=os.name == "posix",
        )
    except OSError:
        return {"status": "unavailable", "reason": "host_could_not_start"}

    selector: selectors.BaseSelector | None = None
    terminate = False
    output_bytes = 0
    response_count = 0
    response_ids: set[str] = set()
    decoder = FrameDecoder()
    expected = (("m-hello", "hello"), ("m-probe", "probe"))
    deadline = time.monotonic() + timeout
    stdout_closed = False
    input_write_ok = True
    try:
        try:
            assert process.stdin is not None
            process.stdin.write(wire)
            process.stdin.flush()
        except (BrokenPipeError, OSError):
            input_write_ok = False
        finally:
            if process.stdin is not None:
                try:
                    process.stdin.close()
                except OSError:
                    pass

        assert process.stdout is not None
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                terminate = True
                return {"status": "timeout", "reason": "host_handshake_timeout"}
            if stdout_closed:
                if process.poll() is not None:
                    break
                time.sleep(min(0.01, remaining))
                continue
            events = selector.select(min(remaining, 0.05))
            if not events:
                continue
            for key, _ in events:
                chunk = os.read(key.fd, MAX_CHUNK_BYTES)
                if not chunk:
                    selector.unregister(key.fileobj)
                    stdout_closed = True
                    continue
                output_bytes += len(chunk)
                if output_bytes > MAX_HOST_OUTPUT_BYTES:
                    terminate = True
                    return {"status": "rejected", "reason": "host_output_exceeded"}
                try:
                    frames = decoder.feed(chunk)
                except ProtocolError:
                    terminate = True
                    return {"status": "rejected", "reason": "invalid_host_response"}
                for frame in frames:
                    if response_count >= len(expected):
                        terminate = True
                        return {"status": "rejected", "reason": "extra_host_response"}
                    message_id, phase = expected[response_count]
                    if frame.size > MAX_ENVELOPE_BYTES:
                        terminate = True
                        return {"status": "rejected", "reason": "host_response_oversized"}
                    try:
                        text = frame.payload.decode("utf-8")
                        response = json.loads(text, parse_constant=_reject_json_constant)
                        validate_host_response(
                            response,
                            expected_message_id=message_id,
                            expected_phase=phase,
                            expected_nonce="n-live",
                            seen_message_ids=response_ids,
                        )
                    except (UnicodeDecodeError, ValueError, ProtocolError):
                        terminate = True
                        return {"status": "rejected", "reason": "handshake_response_mismatch"}
                    response_count += 1

        try:
            decoder.finish()
        except ProtocolError:
            terminate = True
            return {"status": "rejected", "reason": "invalid_host_response"}
        disconnect = "clean_eof" if process.returncode == 0 else "host_crash"
        if process.returncode != 0:
            terminate = True
            return {"status": "rejected", "reason": "host_rejected_handshake", "disconnect": disconnect}
        if response_count != len(expected):
            terminate = True
            return {"status": "rejected", "reason": "host_disconnected_early", "disconnect": disconnect}
        if not input_write_ok:
            terminate = True
            return {"status": "rejected", "reason": "host_input_disconnect", "disconnect": disconnect}
        return {"status": "passed", "messages": response_count, "bytes": output_bytes, "disconnect": disconnect}
    finally:
        if selector is not None:
            try:
                selector.close()
            except OSError:
                pass
        _terminate_process_group(process, force=terminate)


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
    parser.add_argument("--live-host", action="store_true", help="opt in to a direct framed host smoke; never claims Chrome-mediated evidence")
    parser.add_argument(
        "--require-host-smoke",
        action="store_true",
        help="require the direct host smoke to pass",
    )
    parser.add_argument(
        "--require-live",
        "--required-live",
        dest="require_live",
        action="store_true",
        help="require Chrome-mediated Native Messaging evidence; this direct runner fails closed",
    )
    parser.add_argument("--check-install", action="store_true", help="explicitly check the installed host manifest; never installs it")
    parser.add_argument("--extension-origin", help="exact chrome-extension://.../ origin passed as host argv[1]")
    parser.add_argument("--host-path", default=str(HOST_PATH), help=argparse.SUPPRESS)
    parser.add_argument("--timeout", type=float, default=2.0, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.require_live or args.require_host_smoke:
        args.live_host = True
    if args.require_live:
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
        "live": {
            "requested": bool(args.live_host),
            "required": bool(args.require_live or args.require_host_smoke),
            "chrome_mediated": False,
            "status": "not_requested",
        },
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
        report["live"] = {
            "requested": True,
            "required": bool(args.require_live or args.require_host_smoke),
            "chrome_mediated": False,
            **live,
        }
        if live["status"] == "passed":
            # This path launches only the registered host directly; Chrome-mediated
            # evidence must never be inferred from a host-only handshake.
            if args.require_live:
                report["status"] = "live_required_unavailable"
                report["limitations"].append(
                    "Direct host smoke passed, but this runner did not use Chrome connectNative; Chrome-mediated evidence remains unavailable."
                )
            else:
                report["status"] = "host_smoke_passed"
        else:
            report["status"] = "live_required_unavailable" if (args.require_live or args.require_host_smoke) else "live_optional_unavailable"
            report["limitations"].append("Direct host handshake did not pass; Chrome was not launched.")
    elif not args.live_host:
        report["limitations"].append("Live host handshake was not requested; Chrome was not launched.")

    add_envelope(report, kind="native-messaging-probe")
    try:
        write_json_atomic(artifact, report)
    except (OSError, ValueError) as error:
        print(f"native messaging probe error: {type(error).__name__}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 1 if args.require_live or (args.require_host_smoke and report["status"] != "host_smoke_passed") else 0


if __name__ == "__main__":
    raise SystemExit(main())
