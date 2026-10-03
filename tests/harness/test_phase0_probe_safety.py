"""Executable failure-path checks for the Phase 0 installation probe."""

from __future__ import annotations

import base64
import copy
import hashlib
import importlib.util
import json
import shutil
import stat
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
REGISTER_HOST_SCRIPT = ROOT / "scripts" / "register_native_host.py"
_register_host_spec = importlib.util.spec_from_file_location("phase0_register_host", REGISTER_HOST_SCRIPT)
if _register_host_spec is None or _register_host_spec.loader is None:
    raise RuntimeError("could not load production host registration")
_register_host_module = importlib.util.module_from_spec(_register_host_spec)
_register_host_spec.loader.exec_module(_register_host_module)

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
            "method": "normal_approximation",
            "status": "approximate",
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
        "evidence_mode": "live",
        "release_eligible": True,
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
            self.assertEqual(stat.S_IMODE(registration.stat().st_mode), _module.REGISTRATION_MODE)
            self.assertEqual(
                stat.S_IMODE((artifact_dir / _module.INSTALL_RECORD).stat().st_mode),
                _module.PRIVATE_TRANSACTION_MODE,
            )
            second = _module.install_registration(registration, self.extension_id, artifact_dir)
            self.assertEqual(second["status"], "already_installed")
            self.assertFalse(second["mutated"])
            rollback = _module.rollback_registration(registration, self.extension_id, artifact_dir)
            self.assertEqual(rollback["status"], "rolled_back")
            self.assertFalse(registration.exists())

    def test_existing_identical_manifest_with_incorrect_mode_is_repaired(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifact_dir = root / "artifacts"
            artifact_dir.mkdir()
            registration = root / "native-host.json"
            registration.write_text(_module.canonical_json(_module.expected_manifest(self.extension_id)), encoding="utf-8")
            registration.chmod(_module.PRIVATE_TRANSACTION_MODE)

            result = _module.install_registration(registration, self.extension_id, artifact_dir)

            self.assertEqual(result["status"], "already_installed")
            self.assertTrue(result["mutated"])
            self.assertTrue(result["mode_repaired"])
            self.assertEqual(stat.S_IMODE(registration.stat().st_mode), _module.REGISTRATION_MODE)

    def test_registration_script_repairs_identical_manifest_with_incorrect_mode(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / "artifacts") as temporary:
            manifest = Path(temporary) / "native-host.json"
            expected = _register_module._manifest(
                _register_module.HOST_PATH,
                "chrome-extension://" + self.extension_id,
            )
            manifest.write_text(json.dumps(expected, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            manifest.chmod(_register_module.MANIFEST_MODE ^ 0o200)

            result = _register_module.install_registration(
                "chrome-extension://" + self.extension_id,
                manifest_path=manifest,
            )

            self.assertEqual(result["status"], "installed")
            self.assertTrue(result["mode_matches"])
            self.assertEqual(stat.S_IMODE(manifest.stat().st_mode), _register_module.MANIFEST_MODE)

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

    def test_production_manifest_has_a_stable_identity_and_probe_identity_is_rejected(self) -> None:
        self.assertEqual(
            _register_host_module.extension_id_from_manifest(ROOT / "extension"),
            "jgbllikljnllangilfgkhncepiockppj",
        )
        with self.assertRaises(ValueError):
            _register_host_module.extension_id_from_manifest(ROOT / "extension" / "probes")


class ChromeProbeSafetyTests(unittest.TestCase):
    def test_request_ids_are_v4_and_not_reused(self) -> None:
        request_ids = {_chrome.new_request_id() for _ in range(8)}
        self.assertEqual(len(request_ids), 8)
        for request_id in request_ids:
            self.assertEqual(uuid.UUID(request_id).version, 4)

    def test_operator_ack_uses_controlling_tty_when_stdin_is_not_a_tty(self) -> None:
        stdin = mock.Mock()
        stdin.isatty.return_value = False
        tty = mock.Mock()
        tty.readline.return_value = "none_observed\n"
        tty_context = mock.MagicMock()
        tty_context.__enter__.return_value = tty
        with (
            mock.patch.object(_chrome.sys, "stdin", stdin),
            mock.patch.object(_chrome, "open", return_value=tty_context, create=True) as open_tty,
        ):
            self.assertEqual(_chrome.collect_operator_permission_status(), "none_observed")
        open_tty.assert_called_once_with("/dev/tty", "r", encoding="utf-8")
        tty_context.__exit__.assert_called_once()

    def test_pinned_manifest_id_and_identity_diagnostics_reject_same_shaped_worker(self) -> None:
        manifest = _chrome.load_manifest()
        self.assertEqual(
            _chrome._manifest_extension_id(manifest),
            "hlnmcimoechnbccahemchokemgceaffp",
        )
        extension_id = "a" * 32
        identity = {
            "runtime_id": extension_id,
            "manifest_version": 3,
            "name": "Google Network Speech",
            "version": "1.0",
            "service_worker": "service_worker.js",
            "origin": f"chrome-extension://{extension_id}",
            "pathname": "/service_worker.js",
        }
        self.assertFalse(_chrome._is_expected_worker(identity, extension_id, manifest))
        evidence = _chrome._worker_identity_evidence(identity, extension_id, manifest)
        self.assertEqual(evidence["observed_name"], "Google Network Speech")
        self.assertFalse(evidence["manifest_key_id_matches_target"])
        self.assertNotIn(extension_id, json.dumps(evidence, sort_keys=True))

    def test_control_page_identity_retries_with_fresh_target_after_lifecycle_race(self) -> None:
        manifest = _chrome.load_manifest()
        extension_id = _chrome._manifest_extension_id(manifest)
        self.assertIsNotNone(extension_id)
        stale_target = {
            "type": "page",
            "url": f"chrome-extension://{extension_id}/probe.html",
            "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/stale",
        }
        fresh_target = {
            **stale_target,
            "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/fresh",
        }
        identity = {
            "runtime_id": extension_id,
            "manifest_version": 3,
            "name": manifest["name"],
            "version": manifest["version"],
            "service_worker": "service_worker.js",
            "origin": f"chrome-extension://{extension_id}",
            "pathname": "/probe.html",
        }
        request_id = "11111111-1111-4111-8111-111111111111"
        source_hash = "b" * 64
        probe_value = {
            "ok": True,
            "request_id": request_id,
            "binding_nonce": "22222222-2222-4222-8222-222222222222",
            "binding_source_tree_sha256": source_hash,
            "extension_loaded": True,
            "extension_version": manifest["version"],
            "permissions": manifest["permissions"],
            "fixture_identity_passed": True,
            "debugger_command_passed": True,
            "debugger_event_received": True,
            "tab_group_created": True,
            "native_messaging_passed": True,
            "debugger_cleanup_passed": True,
            "cleanup_passed": True,
            "screenshot_captured": True,
            "handshake_transcript": ["hello_accepted", "probe_accepted"],
        }
        stale_client = mock.Mock()
        stale_client.command.side_effect = OSError("target closed")
        fresh_client = mock.Mock()
        fresh_client.command.side_effect = [
            None,
            {"result": {"value": identity}},
            {"result": {"value": True}},
            {"result": {"value": probe_value}},
        ]
        refreshed = []

        def target_provider() -> list[dict[str, Any]]:
            refreshed.append(True)
            return [fresh_target]

        with (
            mock.patch.object(_chrome, "new_request_id", return_value=request_id),
            mock.patch.object(_chrome, "DevToolsSocket", side_effect=[stale_client, fresh_client]),
            mock.patch.object(_chrome.time, "sleep"),
        ):
            result = _chrome.extension_probe_result(
                9222,
                [stale_target],
                expected_manifest=manifest,
                binding_nonce=probe_value["binding_nonce"],
                source_extension_hash=source_hash,
                target_provider=target_provider,
            )

        self.assertEqual(result["status"], "live_passed")
        self.assertGreaterEqual(len(refreshed), 1)
        stale_client.close.assert_called_once_with()
        fresh_client.close.assert_called_once_with()

    def test_control_page_identity_failure_diagnostics_are_bounded_and_redacted(self) -> None:
        manifest = _chrome.load_manifest()
        extension_id = _chrome._manifest_extension_id(manifest)
        target = {
            "type": "page",
            "url": f"chrome-extension://{extension_id}/probe.html",
            "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/private",
        }
        client = mock.Mock()
        client.command.side_effect = OSError("target closed")
        with (
            mock.patch.object(_chrome, "DevToolsSocket", return_value=client),
            mock.patch.object(_chrome.time, "monotonic", side_effect=[0.0, 5.0]),
        ):
            result = _chrome.extension_probe_result(
                9222,
                [target],
                expected_manifest=manifest,
                target_provider=lambda: [target],
            )

        self.assertEqual(result["status"], "live_unavailable")
        self.assertEqual(result["candidate_extension_page_count"], 1)
        self.assertEqual(result["identity_diagnostics"][0]["phase"], "control_page_connect")
        self.assertNotIn(extension_id, json.dumps(result, sort_keys=True))
        self.assertNotIn("ws://", json.dumps(result, sort_keys=True))
        client.close.assert_called_once_with()

    def test_chrome_load_extension_refusal_is_reported_without_raw_log(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            log_path = Path(temporary) / "chrome.stderr.log"
            log_path.write_text(
                "WARNING: --load-extension is not allowed in Google Chrome, ignoring.\\n",
                encoding="utf-8",
            )
            evidence = _chrome._chrome_load_extension_evidence(log_path)
        self.assertEqual(
            evidence,
            {
                "status": "refused",
                "reason": "branded_google_chrome_rejected_load_extension",
                "message": _chrome.CHROME_LOAD_EXTENSION_REFUSAL,
            },
        )

    def test_chrome_native_messaging_logs_are_classified_without_raw_lines(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            log_path = Path(temporary) / "chrome.stderr.log"
            log_path.write_text(
                "[native_messaging] launch_context: host not found at /private/user/secret\\n",
                encoding="utf-8",
            )
            evidence = _chrome._chrome_native_messaging_evidence(log_path)
        self.assertEqual(evidence["status"], "observed")
        self.assertEqual(evidence["channels"], ["launch_context", "native_messaging"])
        self.assertIn("host_not_found", evidence["categories"])
        self.assertIn("host_launch", evidence["categories"])
        self.assertNotIn("/private/user/secret", json.dumps(evidence))

    def test_extension_result_evidence_reports_binding_and_native_failure_codes(self) -> None:
        nonce = "11111111-1111-4111-8111-111111111111"
        source_hash = "a" * 64
        evidence = _chrome._extension_probe_evidence(
            {
                "binding_status": "observed",
                "binding_nonce": nonce,
                "binding_source_tree_sha256": source_hash,
                "native_messaging": "unavailable",
                "native_error": "host_not_found",
            },
            nonce,
            source_hash,
        )
        self.assertTrue(evidence["binding_nonce_observed"])
        self.assertTrue(evidence["binding_source_tree_hash_observed"])
        self.assertTrue(evidence["binding_nonce_matches"])
        self.assertTrue(evidence["binding_source_tree_hash_matches"])
        self.assertEqual(evidence["native_failure_code"], "host_not_found")
        self.assertNotIn(nonce, json.dumps(evidence))
        self.assertNotIn(source_hash, json.dumps(evidence))

    def test_operator_assisted_command_uses_public_extensions_ui_only(self) -> None:
        command = _chrome.build_chrome_command(
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            Path("/tmp/agentyc-p0-profile"),
            9333,
            extension_dir=None,
            fixture_url=None,
            operator_assisted=True,
        )
        self.assertFalse(any("--load-extension" in argument for argument in command))
        self.assertIn("--remote-debugging-port=9333", command)
        self.assertIn("--enable-logging=stderr", command)
        self.assertIn("--log-level=1", command)
        self.assertNotIn("chrome://extensions", command)

        source = CHROME_SCRIPT.read_text(encoding="utf-8")
        for forbidden in (
            "chrome.developerPrivate",
            "Page.setInterceptFileChooserDialog",
            "DOM.setFileInputFiles",
            "extensions-manager",
            "#loadUnpacked",
        ):
            self.assertNotIn(forbidden, source)

    def test_automated_command_requires_explicit_staged_inputs(self) -> None:
        with self.assertRaises(ValueError):
            _chrome.build_chrome_command(
                "chrome",
                Path("/tmp/agentyc-p0-profile"),
                9333,
                extension_dir=None,
                fixture_url=None,
                operator_assisted=False,
            )

    def test_automated_command_uses_fixture_url_without_load_extension_flag(self) -> None:
        fixture_url = Path("/tmp/agentyc-p0-profile/fixture.html").as_uri()
        command = _chrome.build_chrome_command(
            "chrome",
            Path("/tmp/agentyc-p0-profile"),
            9333,
            extension_dir=None,
            fixture_url=fixture_url,
            operator_assisted=False,
        )
        self.assertIn(fixture_url, command)
        self.assertFalse(any(argument.startswith("--load-extension=") for argument in command))

    def test_browser_target_websocket_requires_exact_local_browser_endpoint(self) -> None:
        version = {"webSocketDebuggerUrl": "ws://127.0.0.1:9333/devtools/browser/browser-token"}
        self.assertEqual(_chrome._browser_websocket_url(version, 9333), version["webSocketDebuggerUrl"])
        for url in (
            "ws://127.0.0.1:9333/devtools/page/page-token",
            "ws://127.0.0.1:9334/devtools/browser/browser-token",
            "ws://127.0.0.1:9333/devtools/browser/browser-token?secret=1",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                _chrome._browser_websocket_url({"webSocketDebuggerUrl": url}, 9333)

    def test_extension_inventory_requires_exact_identity_path_and_enabled_state(self) -> None:
        manifest = _chrome.load_manifest()
        extension_id = _chrome._manifest_extension_id(manifest)
        self.assertIsNotNone(extension_id)
        with tempfile.TemporaryDirectory() as temporary:
            staged = Path(temporary) / "probes"
            staged.mkdir()
            inventory = [{
                "id": extension_id,
                "name": manifest["name"],
                "version": manifest["version"],
                "path": str(staged.resolve()),
                "enabled": True,
            }]
            evidence = _chrome._extension_inventory_evidence(inventory, extension_id, manifest, staged)
            self.assertTrue(evidence["inventory_identity_passed"])
            self.assertTrue(evidence["inventory_path_matches"])
            self.assertTrue(evidence["inventory_enabled"])
            self.assertNotIn(extension_id, json.dumps(evidence))
            wrong = copy.deepcopy(inventory)
            wrong[0]["enabled"] = False
            self.assertFalse(
                _chrome._extension_inventory_evidence(wrong, extension_id, manifest, staged)[
                    "inventory_identity_passed"
                ]
            )

    def test_browser_cdp_load_routes_through_attached_browser_session(self) -> None:
        manifest = _chrome.load_manifest()
        extension_id = _chrome._manifest_extension_id(manifest)
        self.assertIsNotNone(extension_id)
        process = mock.Mock()
        client = mock.Mock()
        client.command.side_effect = [
            {"sessionId": "session-token"},
            {"id": extension_id},
            {
                "extensions": [{
                    "id": extension_id,
                    "name": manifest["name"],
                    "version": manifest["version"],
                    "path": "/tmp/staged-probes",
                    "enabled": True,
                }]
            },
        ]
        with tempfile.TemporaryDirectory() as temporary:
            staged = Path(temporary) / "probes"
            staged.mkdir()
            with (
                mock.patch.object(_chrome, "_endpoint_belongs_to_process", return_value=True),
                mock.patch.object(_chrome, "chrome_endpoint", return_value={
                    "webSocketDebuggerUrl": "ws://127.0.0.1:9333/devtools/browser/browser-token"
                }),
                mock.patch.object(_chrome, "DevToolsSocket", return_value=client),
                mock.patch.object(_chrome.Path, "resolve", return_value=staged),
            ):
                loaded_client, session_id, loaded_id, evidence = _chrome.load_extension_via_browser_cdp(
                    9333, process, staged, manifest, extension_id
                )
        self.assertIs(loaded_client, client)
        self.assertEqual(session_id, "session-token")
        self.assertEqual(loaded_id, extension_id)
        self.assertEqual(evidence["status"], "passed")
        self.assertTrue(evidence["browser_target_cdp"])
        self.assertEqual(client.command.call_args_list[1].kwargs["session_id"], "session-token")
        self.assertEqual(client.command.call_args_list[2].kwargs["session_id"], "session-token")
        self.assertNotIn(extension_id, json.dumps(evidence))

    def test_browser_cdp_unload_requires_command_and_absence(self) -> None:
        client = mock.Mock()
        client.command.side_effect = [{}, {"extensions": []}]
        with tempfile.TemporaryDirectory() as temporary:
            evidence = _chrome.unload_extension_via_browser_cdp(
                client,
                "session-token",
                "a" * 32,
                Path(temporary) / "probes",
            )
        self.assertEqual(evidence["status"], "passed")
        self.assertTrue(evidence["uninstall_command_passed"])
        self.assertTrue(evidence["absent_after_uninstall"])

    def test_extension_tree_hash_rejects_symlinks_and_is_stable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "extension"
            root.mkdir()
            (root / "manifest.json").write_text("{}", encoding="utf-8")
            first = _chrome.extension_tree_sha256(root)
            second = _chrome.extension_tree_sha256(root)
            self.assertEqual(first, second)
            outside = Path(temporary) / "outside"
            outside.write_text("unsafe", encoding="utf-8")
            (root / "link").symlink_to(outside)
            with self.assertRaises(ValueError):
                _chrome.extension_tree_sha256(root)

    def test_staged_extension_binding_matches_the_source_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            profile = Path(temporary) / "profile"
            profile.mkdir()
            binding_nonce = str(uuid.uuid4())
            staged, source_hash, staged_hash = _chrome.stage_extension(profile, binding_nonce)
            binding = json.loads((staged / "probe_binding.json").read_text(encoding="utf-8"))
            self.assertEqual(binding, {"nonce": binding_nonce, "source_tree_sha256": source_hash})
            self.assertEqual(
                _chrome.extension_tree_sha256(staged, exclude=frozenset({"probe_binding.json"})),
                source_hash,
            )
            self.assertNotEqual(staged_hash, source_hash)

    def test_disposable_profile_stages_native_host_manifest_for_chrome_lookup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            profile = Path(temporary) / "profile"
            profile.mkdir()
            target = _chrome.stage_native_host_manifest(profile, "a" * 32)
            manifest = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(manifest["name"], _chrome.NATIVE_HOST_NAME)
            self.assertEqual(manifest["type"], "stdio")
            self.assertEqual(manifest["allowed_origins"], [f"chrome-extension://{'a' * 32}/"])
            self.assertEqual(manifest["path"], str(_chrome.NATIVE_HOST_PATH.resolve()))

    def test_owned_profile_cleanup_is_reported_and_complete(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            profile = Path(temporary) / "profile"
            profile.mkdir()
            with mock.patch.object(_chrome, "_LAST_CLEANUP_OK", True):
                self.assertTrue(_chrome._cleanup_owned_profile(profile))
                self.assertFalse(profile.exists())
                self.assertTrue(_chrome._LAST_CLEANUP_OK)

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

    def test_websocket_resolution_accepts_loopback_and_rejects_rebinding(self) -> None:
        fake_socket = mock.Mock()
        with (
            mock.patch.object(
                _chrome.socket,
                "getaddrinfo",
                return_value=[
                    (
                        _chrome.socket.AF_INET,
                        _chrome.socket.SOCK_STREAM,
                        6,
                        "",
                        ("127.0.0.1", 9222),
                    )
                ],
            ),
            mock.patch.object(_chrome.socket, "create_connection", return_value=fake_socket),
            mock.patch.object(_chrome.DevToolsSocket, "_read_http_headers", return_value=b""),
            mock.patch.object(_chrome.DevToolsSocket, "_validate_handshake"),
        ):
            client = _chrome.DevToolsSocket("ws://127.0.0.1:9222/devtools/page/worker")
            client.close()

        for resolved_host in ("192.0.2.1", "::ffff:192.0.2.1"):
            with self.subTest(resolved_host=resolved_host):
                family = _chrome.socket.AF_INET6 if ":" in resolved_host else _chrome.socket.AF_INET
                sockaddr = (resolved_host, 9222, 0, 0) if family == _chrome.socket.AF_INET6 else (resolved_host, 9222)
                with (
                    mock.patch.object(
                        _chrome.socket,
                        "getaddrinfo",
                        return_value=[(family, _chrome.socket.SOCK_STREAM, 6, "", sockaddr)],
                    ),
                    self.assertRaisesRegex(ValueError, "hostname is not loopback"),
                ):
                    _chrome.DevToolsSocket("ws://localhost:9222/devtools/page/worker")

    def test_worker_discovery_polls_until_ready_and_fails_closed_at_deadline(self) -> None:
        process = object()
        worker = {
            "type": "service_worker",
            "url": "chrome-extension://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/service_worker.js",
            "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/worker",
        }
        built_in_worker = {
            **worker,
            "url": "chrome-extension://bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb/service_worker.js",
        }
        clock = [0.0]

        def monotonic() -> float:
            return clock[0]

        def sleep(duration: float) -> None:
            clock[0] += duration

        with (
            mock.patch.object(_chrome, "_endpoint_belongs_to_process", return_value=True) as ownership,
            mock.patch.object(_chrome, "chrome_endpoint", side_effect=[[], [built_in_worker], [worker]]) as endpoint,
            mock.patch.object(_chrome.time, "monotonic", side_effect=monotonic),
            mock.patch.object(_chrome.time, "sleep", side_effect=sleep),
        ):
            self.assertEqual(
                _chrome.wait_for_probe_worker(
                    9222,
                    process,
                    timeout=0.25,
                    expected_extension_id="a" * 32,
                ),
                [worker],
            )

        self.assertEqual(endpoint.call_count, 3)
        self.assertEqual(ownership.call_count, 3)
        endpoint.assert_has_calls([mock.call(9222, "/json/list")] * 3)
        ownership.assert_called_with(9222, process)

        clock[0] = 0.0
        with (
            mock.patch.object(_chrome, "_endpoint_belongs_to_process", return_value=True),
            mock.patch.object(_chrome, "chrome_endpoint", return_value=[]),
            mock.patch.object(_chrome.time, "monotonic", side_effect=monotonic),
            mock.patch.object(_chrome.time, "sleep", side_effect=sleep),
        ):
            self.assertIsNone(_chrome.wait_for_probe_worker(9222, process, timeout=0.25))
            self.assertEqual(clock[0], 0.25)

    def test_owned_page_navigation_waits_for_initial_page(self) -> None:
        process = object()
        target = {
            "type": "page",
            "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/owned",
        }
        with (
            mock.patch.object(_chrome, "_owned_page_target", side_effect=[None, target]),
            mock.patch.object(_chrome, "navigate_page_target") as navigate,
            mock.patch.object(_chrome.time, "monotonic", return_value=0.0),
            mock.patch.object(_chrome.time, "sleep") as sleep,
        ):
            result = _chrome.navigate_owned_page(9222, process, "chrome://extensions/")

        self.assertEqual(result, target["webSocketDebuggerUrl"])
        navigate.assert_called_once_with(target["webSocketDebuggerUrl"], "chrome://extensions/")
        sleep.assert_called_once_with(_chrome.CONTROL_PAGE_RETRY_INTERVAL)

    def test_websocket_handshake_accept_and_server_frame_validation(self) -> None:
        self.assertEqual(
            _chrome.WEBSOCKET_GUID,
            "258EAFA5-E914-47DA-95CA-C5AB0DC85B11",
        )
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
    def test_live_extension_gate_requires_operator_install_provenance_and_hashes(self) -> None:
        live = {
            "requested": True,
            "required": True,
            "status": "live_passed",
            "binding_status": "observed",
            "binding_nonce_observed": True,
            "binding_source_tree_hash_observed": True,
            "binding_nonce_matches": True,
            "binding_source_tree_hash_matches": True,
            "launched_by_probe": True,
            "extension_loaded": True,
            "extension_identity_passed": True,
            "extension_build_binding_passed": True,
            "fixture_identity_passed": True,
            "debugger_command_passed": True,
            "debugger_event_received": True,
            "tab_group_created": True,
            "native_messaging_passed": True,
            "chrome_mediated_native_messaging": True,
            "debugger_cleanup_passed": True,
            "cleanup_passed": True,
            "screenshot_captured": True,
            "extension_unload_passed": True,
            "screenshots": [{"captured": True}],
            "handshake_transcript": ["hello_accepted", "probe_accepted"],
            "permission_prompts": {"status": "not_requested", "required_manual_review": False},
            "extension_load_evidence": {
                "status": "passed",
                "method": "cdp_extensions_load_unpacked",
                "browser_target_cdp": True,
                "load_command_passed": True,
                "returned_id_matches_expected": True,
                "inventory_checked": True,
                "inventory_identity_passed": True,
                "inventory_path_matches": True,
                "inventory_enabled": True,
            },
            "extension_unload_evidence": {
                "status": "passed",
                "uninstall_command_passed": True,
                "inventory_checked": True,
                "absent_after_uninstall": True,
            },
        }
        report = {
            "probe": "P0-T2",
            "mode": "headed",
            "status": "live_passed",
            "handshake_transcript": ["hello_accepted", "probe_accepted"],
            "live": live,
            "safety": {
                "default_chrome_launch": False,
                "default_profile_mutation": False,
                "raw_ids_logged": False,
                "secrets_logged": False,
                "fixture_only_mutation": True,
            },
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "scripts").mkdir(parents=True)
            shutil.copy2(CHROME_SCRIPT, root / "scripts" / "run_chrome_probe.py")
            shutil.copytree(ROOT / "extension" / "probes", root / "extension" / "probes")
            destination = root / "artifacts" / "p0-extension"
            destination.mkdir(parents=True)
            report_path = destination / "report.json"
            report_path.write_text(json.dumps(report), encoding="utf-8")
            checker = _checker.Checker(root)
            self.assertEqual(_checker.validate_extension_gate(checker), "missing")
            codes = {issue.code for issue in checker.issues}
            self.assertIn("live-chrome-install-provenance-missing", codes)
            self.assertIn("live-chrome-build-binding-missing", codes)

            live.update(
                {
                    "operator_assisted": False,
                    "load_method": "cdp_extensions_load_unpacked",
                    "load_extension_flag_used": False,
                    "browser_target_cdp": True,
                    "developer_private_used": False,
                    "extensions_ui_dom_access": False,
                    "runner_sha256": hashlib.sha256(CHROME_SCRIPT.read_bytes()).hexdigest(),
                    "source_extension_tree_sha256": _checker.extension_tree_sha256(ROOT / "extension" / "probes"),
                    "staged_extension_tree_sha256": "c" * 64,
                }
            )
            report_path.write_text(json.dumps(report), encoding="utf-8")
            checker = _checker.Checker(root)
            self.assertEqual(_checker.validate_extension_gate(checker), "passed")
            self.assertEqual(checker.issues, [])

    def test_native_gate_cannot_pass_without_the_chrome_extension_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            native = root / "artifacts" / "p0-native-protocol"
            native.mkdir(parents=True)
            (native / "report.json").write_text(
                json.dumps(
                    {
                        "offline": {"cases": {}, "limits": {}},
                        "status": "live_passed",
                        "live": {"required": True, "status": "passed", "chrome_mediated": True},
                    }
                ),
                encoding="utf-8",
            )
            preflight = root / "artifacts" / "p0-installation-preflight"
            preflight.mkdir(parents=True)
            (preflight / "report.json").write_text(
                json.dumps({"kind": "installation-preflight", "status": "ready", "evidence_mode": "offline"}),
                encoding="utf-8",
            )
            checker = _checker.Checker(root)
            self.assertEqual(_checker.validate_native_protocol_gate(checker, extension_gate_passed=False), "missing")
            self.assertIn("native-protocol-live-missing", {issue.code for issue in checker.issues})

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
