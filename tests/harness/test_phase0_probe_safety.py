"""Executable failure-path checks for the Phase 0 installation probe."""

from __future__ import annotations

import base64
import copy
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
SCRIPT = ROOT / "scripts" / "run_install_drill.py"
_spec = importlib.util.spec_from_file_location("phase0_install_drill", SCRIPT)
if _spec is None or _spec.loader is None:
    raise RuntimeError("could not load installation drill")
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
REGISTER_SCRIPT = ROOT / "scripts" / "register_native_messaging_probe.py"
_register_spec = importlib.util.spec_from_file_location("phase0_register_probe", REGISTER_SCRIPT)
if _register_spec is None or _register_spec.loader is None:
    raise RuntimeError("could not load registration probe")
_register_module = importlib.util.module_from_spec(_register_spec)
_register_spec.loader.exec_module(_register_module)

CHROME_SCRIPT = ROOT / "scripts" / "run_chrome_probe.py"
_chrome_spec = importlib.util.spec_from_file_location("phase0_chrome_probe", CHROME_SCRIPT)
if _chrome_spec is None or _chrome_spec.loader is None:
    raise RuntimeError("could not load Chrome probe")
_chrome = importlib.util.module_from_spec(_chrome_spec)
_chrome_spec.loader.exec_module(_chrome)

CHECKER_SCRIPT = ROOT / "scripts" / "check_phase_0_baseline.py"
_checker_spec = importlib.util.spec_from_file_location("phase0_baseline_checker", CHECKER_SCRIPT)
if _checker_spec is None or _checker_spec.loader is None:
    raise RuntimeError("could not load baseline checker")
_checker = importlib.util.module_from_spec(_checker_spec)
_checker_spec.loader.exec_module(_checker)


def _strict_performance_report() -> dict[str, Any]:
    fixture = {
        "name": "fixture",
        "file": "fixture.html",
        "sha256": "a" * 64,
        "bytes": 1,
    }
    nonce = "d" * 32
    timestamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    command = ["scripts/live-benchmark.py", "--artifact-dir", "<redacted>"]
    build_tuple = {
        "artifact_kind": "direct-benchmark",
        "benchmark_kind": "direct-benchmark-baseline",
        "fixture_manifest": "tests/fixtures/browser-task-spaces/manifest.json",
        "fixture_manifest_sha256": "b" * 64,
        "version": "test",
    }
    latency = {
        "p50": 1.0,
        "p95": 2.0,
        "p99": 3.0,
        "mean": 1.5,
        "mean_confidence_interval": {
            "confidence_level": 0.95,
            "lower": 1.0,
            "upper": 2.0,
            "sample_count": 1000,
        },
        "measurement_status": "measured",
    }
    live_only = {
        "chrome_cpu_percent": 10.0,
        "chrome_rss_bytes": 100.0,
        "host_rss_bytes": 200.0,
        "event_lag_ms": 1.0,
        "reconnect_ms": 2.0,
        "stale_ref_rate": 0.0,
        "unknown_outcome_rate": 0.0,
        "human_tab_responsiveness_ms": 4.0,
        "status": "measured",
    }
    row = {
        "fixture": "fixture",
        "fixture_sha256": fixture["sha256"],
        "cache_state": "clean",
        "spaces": 1,
        "samples": {"attempted": 1000, "valid": 1000, "errors": 0, "invalid": 0},
        "latency_ms": {key: copy.deepcopy(latency) for key in ("read_ms", "first_useful_action_ms", "metadata_ms", "action_ms", "synthetic_action_ms", "wait_ms", "total_ms")},
        "tail_gates": {
            "p95": {"status": "gateable", "minimum_samples": 200},
            "p99": {"status": "gateable", "minimum_samples": 1000},
        },
        "live_only": live_only,
        "reliability_gates": {
            "stale_ref_rate": {"status": "gateable", "value": 0.0},
            "unknown_outcome_rate": {"status": "gateable", "value": 0.0},
            "reconnect_ms": {"status": "gateable", "value": 2.0},
        },
        "human_tab_gate": {"status": "gateable", "responsiveness_ms": 4.0},
    }
    fixture_set_sha256 = _checker._fixture_set_sha256([fixture])
    report = {
        "schema_version": 1,
        "phase": 0,
        "kind": "direct-benchmark-baseline",
        "mode": "headed",
        "status": "live_passed",
        "smoke": False,
        "nonce": nonce,
        "timestamp": timestamp,
        "command": command,
        "build_tuple": build_tuple,
        "provenance": {
            "nonce": nonce,
            "timestamp": timestamp,
            "command": command,
            "build_tuple": build_tuple,
        },
        "baseline_manifest": {
            "path": "tests/fixtures/browser-task-spaces/manifest.json",
            "sha256": "b" * 64,
        },
        "manifest_sha256": "b" * 64,
        "fixture_set_sha256": fixture_set_sha256,
        "fixture_binding": {
            "manifest_path": "tests/fixtures/browser-task-spaces/manifest.json",
            "manifest_sha256": "b" * 64,
            "fixture_set_sha256": fixture_set_sha256,
            "fixtures": [fixture],
        },
        "fixtures": [fixture],
        "cache_states": ["clean"],
        "spaces": [1],
        "samples_per_cell": 1000,
        "rows": [row],
        "sample_accounting": {"attempted": 1000, "valid": 1000, "errors": 0, "invalid": 0},
        "raw_samples_files": ["raw_samples.jsonl"],
        "raw_sample_declarations": {
            "files": [{"name": "raw_samples.jsonl", "sha256": "c" * 64, "bytes": 1000, "sample_count": 1000}],
            "total_samples": 1000,
            "required_metrics": [
                "read_ms",
                "metadata_ms",
                "action_ms",
                "first_useful_action_ms",
                "synthetic_action_ms",
                "wait_ms",
                "total_ms",
            ],
        },
        "result": {"kind": "direct-benchmark", "status": "live_passed"},
    }
    return report


class InstallationProbeSafetyTests(unittest.TestCase):
    extension_id = "a" * 32

    def test_install_is_owned_and_already_installed_is_not_a_new_pass(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifact_dir = root / "artifacts"
            registration = root / "native-host.json"
            first = _module.install_registration(registration, self.extension_id, artifact_dir)
            self.assertEqual(first["status"], "installed")
            second = _module.install_registration(registration, self.extension_id, artifact_dir)
            self.assertEqual(second["status"], "already_installed")
            self.assertFalse(second["mutated"])
            rollback = _module.rollback_registration(registration, self.extension_id, artifact_dir)
            self.assertEqual(rollback["status"], "rolled_back")
            self.assertFalse(registration.exists())

    def test_rollback_refuses_a_changed_registration(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifact_dir = root / "artifacts"
            registration = root / "native-host.json"
            self.assertEqual(
                _module.install_registration(registration, self.extension_id, artifact_dir)["status"],
                "installed",
            )
            registration.write_text("{}\n", encoding="utf-8")
            result = _module.rollback_registration(registration, self.extension_id, artifact_dir)
            self.assertEqual(result["status"], "refusing_changed_registration")
            self.assertTrue(registration.exists())

    def test_invalid_extension_ids_fail_before_registration(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaises(ValueError):
                _module.expected_manifest("not-a-chrome-id")
            self.assertFalse((root / "native-host.json").exists())

    def test_non_object_registration_and_symlink_escape_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifact_dir = root / "artifacts"
            artifact_dir.mkdir()
            registration = root / "native-host.json"
            registration.write_text("[]", encoding="utf-8")
            result = _module.install_registration(registration, self.extension_id, artifact_dir)
            self.assertEqual(result["status"], "rejected_non_object_manifest")

            outside = root / "outside"
            outside.mkdir()
            escaped_parent = artifact_dir / "escaped"
            escaped_parent.symlink_to(outside, target_is_directory=True)
            result = _module.install_registration(escaped_parent / "native-host.json", self.extension_id, artifact_dir)
            self.assertNotEqual(result["status"], "installed")
            self.assertFalse((outside / "native-host.json").exists())

    def test_prepared_journal_is_reconciled_after_a_crash(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifact_dir = root / "artifacts"
            artifact_dir.mkdir()
            registration = root / "native-host.json"
            payload = _module.canonical_json(_module.expected_manifest(self.extension_id)).encode("utf-8")
            payload_hash = _module.hashlib.sha256(payload).hexdigest()
            temp_path = registration.parent / ".native-host.json.install-crash"
            temp_path.write_bytes(payload)
            owner_token = _module._owner_token(artifact_dir, create=True)
            journal = _module._new_install_journal(registration, artifact_dir, payload_hash, temp_path.name, owner_token)
            journal["payload_bytes"] = len(payload)
            _module._atomic_json(artifact_dir / _module.INSTALL_RECORD, journal)

            result = _module.install_registration(registration, self.extension_id, artifact_dir)
            self.assertEqual(result["status"], "installed")
            self.assertFalse(temp_path.exists())
            self.assertTrue((artifact_dir / _module.INSTALL_RECORD).exists())

    def test_forged_journal_cannot_authorize_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifact_dir = root / "artifacts"
            artifact_dir.mkdir()
            registration = root / "native-host.json"
            installed = _module.install_registration(registration, self.extension_id, artifact_dir)
            self.assertEqual(installed["status"], "installed")
            journal_path = artifact_dir / _module.INSTALL_RECORD
            original_journal = journal_path.read_bytes()
            journal = json.loads(original_journal)
            journal["ownership_digest"] = "0" * 64
            _module._atomic_json(journal_path, journal)
            rollback = _module.rollback_registration(registration, self.extension_id, artifact_dir)
            self.assertNotEqual(rollback["status"], "rolled_back")
            self.assertTrue(registration.exists())

            other_artifact_dir = root / "other-artifacts"
            other_artifact_dir.mkdir()
            shutil.copy2(artifact_dir / _module.INSTALL_OWNER, other_artifact_dir / _module.INSTALL_OWNER)
            (other_artifact_dir / _module.INSTALL_RECORD).write_bytes(original_journal)
            replay = _module.rollback_registration(registration, self.extension_id, other_artifact_dir)
            self.assertNotEqual(replay["status"], "rolled_back")
            self.assertTrue(registration.exists())

    def test_install_lock_rejects_concurrent_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifact_dir = root / "artifacts"
            registration = root / "native-host.json"
            with _module.installation_lock(artifact_dir / _module.INSTALL_LOCK):
                result = _module.install_registration(registration, self.extension_id, artifact_dir)
            self.assertEqual(result["status"], "installation_busy")
            self.assertFalse(registration.exists())

    def test_registration_checker_rejects_non_object_and_symlink_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifact_dir = root / "artifacts"
            artifact_dir.mkdir()
            manifest = artifact_dir / "native-host.json"
            manifest.write_text("[]", encoding="utf-8")
            result = _register_module.check_registration(
                "chrome-extension://" + self.extension_id,
                manifest_path=manifest,
            )
            self.assertEqual(result["status"], "rejected")
            self.assertEqual(
                _register_module.install_registration(
                    "chrome-extension://" + self.extension_id,
                    manifest_path=manifest,
                )["status"],
                "rejected",
            )

            outside = root / "outside"
            outside.mkdir()
            escaped = artifact_dir / "escaped"
            escaped.symlink_to(outside, target_is_directory=True)
            result = _register_module.check_registration(
                "chrome-extension://" + self.extension_id,
                manifest_path=escaped / "native-host.json",
            )
            self.assertEqual(result["status"], "rejected")
            self.assertFalse((outside / "native-host.json").exists())


class ChromeProbeSafetyTests(unittest.TestCase):
    def test_request_ids_are_v4_and_not_reused(self) -> None:
        request_ids = {_chrome.new_request_id() for _ in range(8)}
        self.assertEqual(len(request_ids), 8)
        for request_id in request_ids:
            self.assertEqual(uuid.UUID(request_id).version, 4)

    def test_headed_without_launch_refuses_existing_debug_endpoint(self) -> None:
        with mock.patch.object(
            _chrome,
            "chrome_endpoint",
            side_effect=AssertionError("existing endpoint must not be inspected"),
        ):
            result = _chrome.inspect_live(
                9222,
                profile_dir=None,
                launch=False,
                binary=None,
                artifact_dir=ROOT / "artifacts" / "p0-extension",
            )
        self.assertEqual(result["status"], "live_unavailable")
        self.assertIn("existing-endpoint-not-isolated", result["limitation"])

    def test_service_worker_preserves_only_the_latest_trigger_result(self) -> None:
        script = r'''
const fs = require("fs");
const vm = require("vm");
const { webcrypto } = require("crypto");
const source = fs.readFileSync("extension/probes/service_worker.js", "utf8");
const store = {};
const changed = { listeners: [], addListener(fn) { this.listeners.push(fn); } };
const chrome = {
  runtime: {
    id: "a".repeat(32),
    getManifest: () => ({ version: "0.0.1", permissions: [] }),
    onMessage: { addListener() {} },
  },
  storage: {
    onChanged: changed,
    local: { async set(values) { Object.assign(store, values); } },
  },
};
const context = {
  chrome,
  crypto: webcrypto,
  TextEncoder,
  URL,
  setTimeout,
  clearTimeout,
};
vm.createContext(context);
vm.runInContext(source, context);
let runs = 0;
context.runProbe = async (requestId) => {
  runs += 1;
  return { request_id: requestId };
};
const first = webcrypto.randomUUID();
const second = webcrypto.randomUUID();
changed.listeners[0]({ run_probe: { newValue: { request_id: first } } }, "local");
changed.listeners[0]({ run_probe: { newValue: { request_id: second } } }, "local");
setTimeout(() => {
  if (runs !== 2 || store.last_probe?.request_id !== second) process.exit(1);
}, 50);
'''
        result = subprocess.run(
            ["node", "-e", script],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_worker_selection_requires_exact_probe_worker_url(self) -> None:
        base = {
            "type": "service_worker",
            "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/worker",
        }
        wrong_script = {
            **base,
            "url": "chrome-extension://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/other.js",
        }
        self.assertIsNone(_chrome._worker_candidate(wrong_script))
        expected = {
            **base,
            "url": "chrome-extension://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/service_worker.js",
        }
        self.assertEqual(
            _chrome._worker_candidate(expected),
            ("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", base["webSocketDebuggerUrl"]),
        )

    def test_websocket_handshake_accept_and_server_frame_validation(self) -> None:
        key = base64.b64encode(b"0123456789abcdef").decode("ascii")
        accept = base64.b64encode(
            hashlib.sha1((key + _chrome.WEBSOCKET_GUID).encode("ascii")).digest()
        ).decode("ascii")
        client = _chrome.DevToolsSocket.__new__(_chrome.DevToolsSocket)
        client._validate_handshake(
            (
                "HTTP/1.1 101 Switching Protocols\r\n"
                "Upgrade: websocket\r\n"
                "Connection: keep-alive, Upgrade\r\n"
                f"Sec-WebSocket-Accept: {accept}\r\n\r\n"
            ).encode("ascii"),
            key,
        )
        with self.assertRaises(ValueError):
            client._validate_handshake(
                b"HTTP/1.1 101 Switching Protocols\r\n\r\n", key
            )

        payload = b"{}"
        client.socket = _BufferedSocket(b"\x81" + bytes([len(payload)]) + payload)
        client._receive_buffer = bytearray()
        self.assertEqual(client._receive_message(), payload)
        masked = b"\x81\x81" + b"abcd" + bytes(
            byte ^ b"abcd"[index % 4] for index, byte in enumerate(payload)
        )
        client.socket = _BufferedSocket(masked)
        with self.assertRaises(ValueError):
            client._receive_message()


class BaselineCheckerSafetyTests(unittest.TestCase):
    def test_success_markers_cannot_be_forged_in_nested_transcript_data(self) -> None:
        forged = {"transcript": [{"extension_loaded": True}]}
        self.assertFalse(_checker.has_marker(forged, ("extension_loaded",)))
        self.assertTrue(_checker.has_marker({"extension_loaded": True}, ("extension_loaded",)))

    def test_strict_performance_report_requires_non_smoke_complete_matrix(self) -> None:
        report = _strict_performance_report()
        self.assertTrue(_checker.performance_report_passed(report))
        smoke = copy.deepcopy(report)
        smoke["smoke"] = True
        self.assertFalse(_checker.performance_report_passed(smoke))
        missing_cell = copy.deepcopy(report)
        missing_cell["rows"] = []
        self.assertFalse(_checker.performance_report_passed(missing_cell))

    def test_strict_performance_report_rejects_nonfinite_ranges_and_missing_gates(self) -> None:
        report = _strict_performance_report()
        nonfinite = copy.deepcopy(report)
        nonfinite["rows"][0]["live_only"]["chrome_cpu_percent"] = float("nan")
        self.assertFalse(_checker.performance_report_passed(nonfinite))

        out_of_range = copy.deepcopy(report)
        out_of_range["rows"][0]["live_only"]["stale_ref_rate"] = 2.0
        self.assertFalse(_checker.performance_report_passed(out_of_range))

        missing_reliability = copy.deepcopy(report)
        del missing_reliability["rows"][0]["reliability_gates"]
        self.assertFalse(_checker.performance_report_passed(missing_reliability))

        missing_human_tab = copy.deepcopy(report)
        del missing_human_tab["rows"][0]["human_tab_gate"]
        self.assertFalse(_checker.performance_report_passed(missing_human_tab))


class _BufferedSocket:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def recv(self, size: int) -> bytes:
        chunk, self.payload = self.payload[:size], self.payload[size:]
        return chunk

    def sendall(self, payload: bytes) -> None:
        del payload

    def settimeout(self, timeout: float) -> None:
        del timeout

    def close(self) -> None:
        pass


if __name__ == "__main__":
    unittest.main()
