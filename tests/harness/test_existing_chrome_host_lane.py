from __future__ import annotations

import importlib.util
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
