from __future__ import annotations

import importlib.util
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

_spec = importlib.util.spec_from_file_location(
    "harness_existing_chrome_host_lane",
    SCRIPTS / "run_existing_chrome.py",
)
if _spec is None or _spec.loader is None:
    raise RuntimeError("could not load existing-Chrome runner")
_runner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_runner)


class ExistingChromeHostLaneTests(unittest.TestCase):
    def test_fake_host_status_is_not_a_live_preflight(self) -> None:
        result = {
            "lifecycle": "ready",
            "broker_epoch": 1,
            "bridge": {
                "mode": "fake",
                "connected": True,
                "test_seam": True,
                "capabilities": ["action"],
            },
            "direct_path": {
                "browser_auto_launch": False,
                "copied_debug_endpoint": False,
                "logical_ids_only": True,
            },
        }
        observation, reason = _runner._host_status_observation(
            _runner.DirectCliResponse("complete", ok=True, result=result)
        )
        self.assertIsNone(observation)
        self.assertEqual(reason, "host_status_does_not_prove_safe_extension_bridge")

    def test_direct_response_requires_one_json_value_and_matching_exit_code(self) -> None:
        valid = _runner._parse_direct_response(b'{"ok":true,"result":{}}\n', 0)
        self.assertEqual(valid.transport, "complete")
        self.assertTrue(valid.ok)

        extra = _runner._parse_direct_response(b'{"ok":true,"result":{}} {}', 0)
        self.assertEqual(extra.reason_code, "multiple_stdout_values")

        mismatch = _runner._parse_direct_response(b'{"ok":true,"result":{}}', 3)
        self.assertEqual(mismatch.reason_code, "success_exit_code_mismatch")

    def test_executor_command_has_no_offline_or_cdp_mode(self) -> None:
        response = b'{"ok":true,"result":{}}\n'
        with patch.object(
            _runner,
            "_run_bounded_process",
            return_value=("completed", 0, response, b""),
        ) as process:
            client = _runner.DirectCli("agentyc", state_dir=None, timeout=1.0)
            result = client.call(["host", "status"], principal="agent-a")
        self.assertTrue(result.ok)
        argv = process.call_args.args[0]
        self.assertNotIn("--offline", argv)
        self.assertNotIn("--cdp-url", argv)
        self.assertNotIn("--websocket-url", argv)

    def _host_probe_output(self, *, success: bool = False) -> bytes:
        return json.dumps(
            {
                "success": success,
                "socket_path": "[REDACTED]",
                "broker_epoch": 1,
                "connection_epoch": 2,
                "checkpoints": [
                    {"name": name, "status": "passed" if success else "skipped", "detail": "/Users/private-id"}
                    for name in _runner.HOST_PROBE_CHECKPOINTS
                ],
                "limitations": ["host-only logical probe"],
            }
        ).encode("utf-8")

    def test_host_probe_parser_requires_redacted_path_and_exact_ten_checkpoints(self) -> None:
        response = _runner._parse_host_probe_response(self._host_probe_output(), 1)
        self.assertEqual(response.transport, "complete")
        self.assertFalse(response.success)
        self.assertEqual(len(response.checkpoints), 10)
        self.assertNotIn("/Users/private-id", json.dumps(response.__dict__, sort_keys=True))

        raw = json.loads(self._host_probe_output())
        raw["socket_path"] = "/Users/private/socket"
        accepted = _runner._parse_host_probe_response(json.dumps(raw).encode("utf-8"), 1)
        self.assertEqual(accepted.transport, "complete")
        self.assertNotIn("/Users/private/socket", json.dumps(accepted.__dict__, sort_keys=True))

    def test_real_host_probe_is_preferred_but_cannot_close_browser_gate(self) -> None:
        with (
            patch.object(_runner, "resolve_host_probe", return_value=("/probe", "configured")),
            patch.object(
                _runner,
                "_run_bounded_process",
                return_value=("completed", 1, self._host_probe_output(), b""),
            ) as process,
        ):
            live = _runner.orchestrate_live(
                cli_path=None,
                state_dir=None,
                cli_timeout=1.0,
                operator_checkpoint=False,
                checkpoint_timeout=1.0,
            )
        self.assertTrue(live["host_probe_used"])
        self.assertEqual(live["status"], "live_observation_incomplete")
        self.assertFalse(_runner._live_evidence_is_complete(live))
        self.assertEqual(len(live["scenarios"]), 10)
        self.assertEqual(len(process.call_args.args[0]), 1)

    def test_offline_report_always_has_ten_non_live_scenarios_and_enrollment(self) -> None:
        live = {
            "status": "live_passed",
            "executed": True,
            "reason": "/Users/private/report.json",
            "scenarios": [
                {"name": name, "status": "live_passed"}
                for name in _runner.REQUIRED_LIVE_SCENARIOS
            ],
        }
        report = _runner.safe_report(
            mode="offline",
            manifest=_runner.validate_manifest(),
            contract=_runner.validate_scenario(2, 2),
            live=live,
        )
        self.assertEqual(report["evidence_mode"], "offline")
        self.assertEqual(report["status"], "offline_passed")
        self.assertEqual(len(report["scenarios"]), 10)
        self.assertTrue(all(item["status"] != "live_passed" for item in report["scenarios"]))
        self.assertEqual(set(report["enrollment"]), {"profile", "host", "extension"})
        self.assertTrue(all(not item["enrolled"] for item in report["enrollment"].values()))
        self.assertNotIn("/Users/private", json.dumps(report, sort_keys=True))

    def test_descriptor_shaped_claim_cannot_be_complete_live_evidence(self) -> None:
        live = {
            "executed": True,
            "status": "live_passed",
            "evidence_status": "live_passed",
            "provenance": "direct_cli_current_run",
            "descriptor_policy": "descriptors_not_accepted_as_live_evidence",
            "preflight": {"observed": True, "source": "direct_cli"},
            "browser": {"launch": False, "download": False, "cdp_url_used": False, "attached": False},
            "receipts": [],
            "scenarios": [
                {"name": name, "status": "live_passed"}
                for name in _runner.REQUIRED_LIVE_SCENARIOS
            ],
        }
        self.assertFalse(_runner._live_evidence_is_complete(live))

    def test_fake_host_environment_fails_before_cli_resolution(self) -> None:
        with patch.dict(os.environ, {"AGENTYC_FAKE_HOST": "1"}, clear=False):
            live = _runner.orchestrate_live(
                cli_path="definitely-not-used",
                state_dir=None,
                cli_timeout=1.0,
                operator_checkpoint=False,
                checkpoint_timeout=1.0,
            )
        self.assertEqual(live["status"], "live_required_unavailable")
        self.assertEqual(live["reason_code"], "fake_host_environment_forbidden")
        self.assertFalse(live["executed"])


if __name__ == "__main__":
    unittest.main()
