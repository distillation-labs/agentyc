"""Failure-path checks for the bounded direct Native Messaging host smoke."""

from __future__ import annotations

import importlib.util
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
SCRIPT = ROOT / "scripts" / "run_native_messaging_probe.py"
_spec = importlib.util.spec_from_file_location("phase0_native_probe_runner", SCRIPT)
if _spec is None or _spec.loader is None:
    raise RuntimeError("could not load native messaging probe runner")
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)

ORIGIN = "chrome-extension://" + "a" * 32


def _host_script(directory: Path, mode: str) -> Path:
    path = directory / f"host-{mode}"
    source = f"""#!/usr/bin/env python3
import json
import struct
import sys
import time

origin = sys.argv[1]
def frame(value):
    payload = json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return struct.pack("<I", len(payload)) + payload

def response(message_id, phase, nonce="n-live", version=1, kind="ack"):
    return {{
        "accepted": True,
        "kind": kind,
        "message_id": message_id,
        "phase": phase,
        "nonce": nonce,
        "version": version,
    }}

if {mode!r} == "timeout":
    time.sleep(5)
elif {mode!r} == "oversized":
    sys.stdout.buffer.write(struct.pack("<I", {int(_module.MAX_ENVELOPE_BYTES) + 1}) + b"x" * ({int(_module.MAX_ENVELOPE_BYTES) + 1}))
elif {mode!r} == "malformed":
    sys.stdout.buffer.write(frame([]))
elif {mode!r} == "wrong_nonce":
    sys.stdout.buffer.write(frame(response("m-hello", "hello", nonce="wrong")) + frame(response("m-probe", "probe", nonce="wrong")))
elif {mode!r} == "wrong_version":
    sys.stdout.buffer.write(frame(response("m-hello", "hello", version=2)) + frame(response("m-probe", "probe")))
elif {mode!r} == "extra":
    sys.stdout.buffer.write(
        frame(response("m-hello", "hello"))
        + frame(response("m-probe", "probe"))
        + frame(response("m-extra", "probe"))
    )
else:
    sys.stdout.buffer.write(frame(response("m-hello", "hello")) + frame(response("m-probe", "probe")))
sys.stdout.buffer.flush()
"""
    path.write_text(source, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


class NativeMessagingRunnerTests(unittest.TestCase):
    def test_valid_host_is_host_smoke_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = _module.framed_host_smoke(_host_script(Path(temporary), "valid"), ORIGIN, 1.0)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["disconnect"], "clean_eof")

    def test_real_host_accepts_chrome_origin_serialization_with_trailing_slash(self) -> None:
        result = _module.framed_host_smoke(
            _module.HOST_PATH,
            ORIGIN,
            1.0,
            host_argument_origin=f"{ORIGIN}/",
        )
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["disconnect"], "clean_eof")

    def test_real_host_processes_frames_before_stdin_eof(self) -> None:
        result = _module.framed_host_smoke(
            _module.HOST_PATH,
            ORIGIN,
            1.0,
            host_argument_origin=f"{ORIGIN}/",
            keep_stdin_open=True,
        )
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["messages"], 2)

    def test_malformed_response_shape_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = _module.framed_host_smoke(_host_script(Path(temporary), "malformed"), ORIGIN, 1.0)
        self.assertEqual(result["status"], "rejected")

    def test_oversized_response_is_rejected_before_acceptance(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = _module.framed_host_smoke(_host_script(Path(temporary), "oversized"), ORIGIN, 1.0)
        self.assertEqual(result, {"status": "rejected", "reason": "host_response_oversized"})

    def test_wrong_nonce_and_version_are_rejected(self) -> None:
        for mode in ("wrong_nonce", "wrong_version"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                result = _module.framed_host_smoke(_host_script(Path(temporary), mode), ORIGIN, 1.0)
            self.assertEqual(result["status"], "rejected")

    def test_extra_frame_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = _module.framed_host_smoke(_host_script(Path(temporary), "extra"), ORIGIN, 1.0)
        self.assertEqual(result, {"status": "rejected", "reason": "extra_host_response"})

    def test_timeout_returns_without_waiting_for_host_exit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            started = time.monotonic()
            result = _module.framed_host_smoke(_host_script(Path(temporary), "timeout"), ORIGIN, 0.05)
            elapsed = time.monotonic() - started
        self.assertEqual(result["status"], "timeout")
        self.assertLess(elapsed, 1.0)


if __name__ == "__main__":
    unittest.main()
