from __future__ import annotations

import importlib.util
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError
from urllib.request import urlopen

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

    def test_direct_cli_passes_explicit_profile_binding_without_unsafe_modes(self) -> None:
        response = b'{"ok":true,"result":{}}\n'
        with patch.object(
            _runner,
            "_run_bounded_process",
            return_value=("completed", 0, response, b""),
        ) as process:
            client = _runner.DirectCli(
                "agentyc",
                state_dir=None,
                timeout=1.0,
                profile_binding_id="profile_explicit",
            )
            self.assertTrue(client.call(["host", "status"], principal="agent-a").ok)
        argv = process.call_args.args[0]
        self.assertIn("--profile-binding-id", argv)
        self.assertEqual(argv[argv.index("--profile-binding-id") + 1], "profile_explicit")
        self.assertNotIn("--offline", argv)
        self.assertNotIn("--cdp-url", argv)

    def test_direct_cli_uses_profile_binding_environment_fallback(self) -> None:
        response = b'{"ok":true,"result":{}}\n'
        with (
            patch.dict(os.environ, {"AGENTYC_PROFILE_BINDING": "profile_from_env"}, clear=False),
            patch.object(
                _runner,
                "_run_bounded_process",
                return_value=("completed", 0, response, b""),
            ) as process,
        ):
            client = _runner.DirectCli("agentyc", state_dir=None, timeout=1.0)
            self.assertTrue(client.call(["host", "status"], principal="agent-a").ok)
        argv = process.call_args.args[0]
        self.assertEqual(argv[argv.index("--profile-binding-id") + 1], "profile_from_env")

    def test_invalid_profile_binding_fails_closed(self) -> None:
        with self.assertRaises(_runner.ProbeError):
            _runner.DirectCli(
                "agentyc",
                state_dir=None,
                timeout=1.0,
                profile_binding_id="not-a-profile-id",
            )

    def test_missing_profile_binding_stops_before_live_mutation(self) -> None:
        response = json.dumps(
            {
                "ok": True,
                "result": {
                    "lifecycle": "ready",
                    "broker_epoch": 1,
                    "bridge": {
                        "mode": "extension",
                        "connected": True,
                        "test_seam": False,
                        "capabilities": ["action"],
                    },
                    "direct_path": {
                        "browser_auto_launch": False,
                        "copied_debug_endpoint": False,
                        "logical_ids_only": True,
                    },
                },
            }
        ).encode()
        with (
            patch.object(_runner, "resolve_direct_cli", return_value=("/cli", "configured")),
            patch.object(
                _runner,
                "_run_bounded_process",
                return_value=("completed", 0, response, b""),
            ) as process,
        ):
            live = _runner.orchestrate_live(
                cli_path=None,
                state_dir=None,
                cli_timeout=1.0,
                operator_checkpoint=False,
                checkpoint_timeout=1.0,
            )
        self.assertEqual(live["reason_code"], "host_status_profile_instance_id_missing")
        self.assertEqual(len(process.call_args_list), 1)
        self.assertEqual(len(live["scenarios"]), 10)

    def test_host_status_requires_current_profile_instance_observation(self) -> None:
        result = {
            "lifecycle": "ready",
            "broker_epoch": 3,
            "profile_instance_id": "profile_live",
            "bridge": {
                "mode": "extension",
                "connected": True,
                "test_seam": False,
                "capabilities": ["action", "snapshot"],
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
        self.assertIsNone(reason)
        self.assertIsNotNone(observation)
        assert observation is not None
        self.assertTrue(observation["profile_binding_observed"])
        self.assertEqual(observation["profile_instance_id"], "profile_live")
        self.assertEqual(observation["profile_scope"], "existing_user_profile")

        missing = json.loads(json.dumps(result))
        missing.pop("profile_instance_id")
        observation, reason = _runner._host_status_observation(
            _runner.DirectCliResponse("complete", ok=True, result=missing)
        )
        self.assertIsNone(observation)
        self.assertEqual(reason, "host_status_profile_instance_id_missing")

    def test_snapshot_result_is_only_a_bounded_observation(self) -> None:
        response = _runner.DirectCliResponse(
            "complete",
            ok=True,
            result={
                "space_id": "space_research",
                "page_id": "page_results",
                "snapshot": {
                    "space_id": "space_research",
                    "page_id": "page_results",
                    "snapshot_hash": "sha256:fixture",
                    "document_generation": 2,
                    "elements": [{"text": "private body must not persist"}],
                },
                "cache_state": "fresh",
                "scan_performed": True,
            },
        )
        self.assertTrue(_runner._snapshot_result(response, "space_research", "page_results"))
        response.result["snapshot"].pop("snapshot_hash")
        self.assertFalse(_runner._snapshot_result(response, "space_research", "page_results"))

    def test_return_reclaim_ticket_is_bounded_and_memory_only(self) -> None:
        response = _runner.DirectCliResponse(
            "complete",
            ok=True,
            result={
                "space_id": "space_testing",
                "released_epoch": 4,
                "fence_epoch": 5,
                "lifecycle": "user_owned",
                "control_ticket": {
                    "space_id": "space_testing",
                    "broker_epoch": 9,
                    "fence_epoch": 5,
                    "token": "ticket-control-abc",
                },
            },
        )
        returned_epoch, ticket = _runner._return_control_result(response, "space_testing", 4)  # type: ignore[misc]
        self.assertEqual(returned_epoch, 4)
        self.assertEqual(ticket["token"], "ticket-control-abc")
        self.assertNotIn("opaque", ticket)

        malformed = json.loads(json.dumps(response.result))
        malformed["control_ticket"]["token"] = "not valid token!"
        self.assertIsNone(
            _runner._return_control_result(
                _runner.DirectCliResponse("complete", ok=True, result=malformed),
                "space_testing",
                4,
            )
        )

    def test_loopback_fixture_server_serves_checked_in_fixture_and_stops(self) -> None:
        with _runner.fixture_server() as base_url:
            with urlopen(f"{base_url}/dynamic-feed.html", timeout=1.0) as response:
                body = response.read().decode("utf-8")
            self.assertIn("append-item", body)
        with self.assertRaises(URLError):
            urlopen(f"{base_url}/dynamic-feed.html", timeout=1.0)

    def test_managed_inventory_requires_extension_records_visual_groups_and_user_focus(self) -> None:
        result = {
            "space_id": "space_research",
            "pages": [
                {
                    "page_id": "page_research",
                    "space_id": "space_research",
                    "ownership": "agent",
                    "lifecycle": "managed",
                    "binding_state": "bound",
                    "lease_epoch": 1,
                    "target_generation": 1,
                    "browser_session_epoch": 4,
                    "active": False,
                }
            ],
            "groups": [
                {
                    "space_id": "space_research",
                    "present": True,
                    "drift": False,
                    "member_count": 1,
                }
            ],
            "unmanaged_pages": [
                {
                    "ownership": "unmanaged",
                    "lifecycle": "unmanaged",
                    "binding_state": "unbound",
                    "tab_hint": "user-tab-hint",
                    "focus_hint": "user-focus-hint",
                    "active": True,
                    "incognito": False,
                }
            ],
            "active_focus": {"tab_hint": "user-tab-hint", "ownership": "unmanaged", "active": True},
        }
        response = _runner.DirectCliResponse("complete", ok=True, result=result)
        observation = _runner._managed_inventory_observation(
            response,
            "space_research",
            {"page_research": 1},
        )
        self.assertIsNotNone(observation)
        assert observation is not None
        self.assertTrue(observation["extension_backed"])
        self.assertEqual(observation["visual_groups"]["spaces"], 1)
        self.assertTrue(observation["user_focus"]["unmanaged"])

        without_group = json.loads(json.dumps(result))
        without_group.pop("groups")
        self.assertIsNone(
            _runner._managed_inventory_observation(
                _runner.DirectCliResponse("complete", ok=True, result=without_group),
                "space_research",
                {"page_research": 1},
            )
        )
        without_focus = json.loads(json.dumps(result))
        without_focus.pop("unmanaged_pages")
        without_focus.pop("active_focus")
        self.assertIsNone(
            _runner._managed_inventory_observation(
                _runner.DirectCliResponse("complete", ok=True, result=without_focus),
                "space_research",
                {"page_research": 1},
            )
        )

    def test_action_execute_receipt_must_be_succeeded_for_the_managed_page(self) -> None:
        response = _runner.DirectCliResponse(
            "complete",
            ok=True,
            result={
                "action_id": "action_research",
                "receipt": {
                    "action_id": "action_research",
                    "space_id": "space_research",
                    "page_id": "page_research",
                    "lease_epoch": 1,
                    "status": "succeeded",
                    "completion_source": "extension",
                    "dispatch_state": "dispatched",
                },
            },
        )
        self.assertTrue(
            _runner._action_execute_result(
                response,
                "space_research",
                "page_research",
                "action_research",
                1,
            )
        )
        response.result["receipt"]["status"] = "queued"
        self.assertFalse(
            _runner._action_execute_result(
                response,
                "space_research",
                "page_research",
                "action_research",
                1,
            )
        )

    def test_direct_cli_constructs_managed_inventory_and_action_commands(self) -> None:
        response = b'{"ok":true,"result":{}}\n'
        commands = [
            [
                "page",
                "create-managed",
                "--space-id",
                "space_research",
                "--lease-epoch",
                "1",
                "--label",
                "results",
            ],
            ["page", "inventory", "--space-id", "space_research"],
            [
                "snapshot",
                "--space-id",
                "space_research",
                "--page-id",
                "page_results",
                "--lease-epoch",
                "1",
            ],
            [
                "space",
                "return",
                "--space-id",
                "space_testing",
                "--lease-epoch",
                "1",
            ],
            [
                "space",
                "reclaim",
                "--space-id",
                "space_testing",
                "--control-ticket",
                '{"space_id":"space_testing","broker_epoch":1,"fence_epoch":2,"token":"ticket-control-abc"}'
            ],
            [
                "action",
                "execute",
                "--request-id",
                "req_probe",
                "--action-id",
                "action_probe",
                "--idempotency-key",
                "idem_probe",
                "--space-id",
                "space_research",
                "--page-id",
                "page_results",
                "--lease-epoch",
                "1",
                "--operation",
                "screenshot",
            ],
        ]
        with patch.object(
            _runner,
            "_run_bounded_process",
            return_value=("completed", 0, response, b""),
        ) as process:
            client = _runner.DirectCli("agentyc", state_dir=None, timeout=1.0)
            for command in commands:
                result = client.call(command, principal="agent-a")
                self.assertTrue(result.ok)
        observed_commands = [call.args[0][call.args[0].index("--json") + 1 :] for call in process.call_args_list]
        self.assertEqual(observed_commands, commands)

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

    def test_headed_lane_ignores_host_probe_and_uses_direct_cli_only(self) -> None:
        direct_response = json.dumps(
            {
                "ok": True,
                "result": {
                    "lifecycle": "ready",
                    "broker_epoch": 1,
                    "profile_instance_id": "profile_live",
                    "bridge": {
                        "mode": "extension",
                        "connected": True,
                        "test_seam": False,
                        "capabilities": ["action"],
                    },
                    "direct_path": {
                        "browser_auto_launch": False,
                        "copied_debug_endpoint": False,
                        "logical_ids_only": True,
                    },
                },
            }
        ).encode()
        with (
            patch.object(_runner, "resolve_host_probe") as host_probe,
            patch.object(_runner, "resolve_direct_cli", return_value=("/cli", "configured")),
            patch.object(
                _runner,
                "_run_bounded_process",
                return_value=("completed", 0, direct_response, b""),
            ) as process,
        ):
            live = _runner.orchestrate_live(
                cli_path=None,
                state_dir=None,
                cli_timeout=1.0,
                operator_checkpoint=False,
                checkpoint_timeout=1.0,
            )
        host_probe.assert_not_called()
        self.assertFalse(live["host_probe_used"])
        self.assertEqual(live["status"], "live_observation_incomplete")
        self.assertFalse(_runner._live_evidence_is_complete(live))
        self.assertEqual(len(live["scenarios"]), 10)
        argv_values = [call.args[0] for call in process.call_args_list]
        self.assertTrue(all(argv[0] == "/cli" for argv in argv_values))
        self.assertTrue(any("host" in argv and "status" in argv for argv in argv_values))
        self.assertTrue(any("--profile-binding-id" in argv for argv in argv_values[1:]))

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
        self.assertEqual(
            tuple(item["name"] for item in report["scenarios"]),
            _runner.REQUIRED_LIVE_SCENARIOS,
        )
        self.assertTrue(all(item["status"] != "live_passed" for item in report["scenarios"]))
        self.assertEqual(set(report["enrollment"]), {"profile", "host", "extension"})
        self.assertTrue(all(not item["enrolled"] for item in report["enrollment"].values()))
        self.assertNotIn("/Users/private", json.dumps(report, sort_keys=True))

    def test_report_redacts_profile_ticket_url_and_absolute_path_values(self) -> None:
        live = _runner._live_unavailable("test")
        live.update(
            {
                "profile_instance_id": "profile_sensitive",
                "control_ticket": {"token": "ticket-sensitive"},
                "fixture_url": "http://127.0.0.1:43123/dynamic-feed.html",
                "reason": "/Users/private/report.json",
            }
        )
        report = _runner.safe_report(
            mode="headed",
            manifest=_runner.validate_manifest(),
            contract=_runner.validate_scenario(2, 2),
            live=live,
        )
        rendered = json.dumps(report, sort_keys=True)
        self.assertNotIn("profile_sensitive", rendered)
        self.assertNotIn("ticket-sensitive", rendered)
        self.assertNotIn("127.0.0.1:43123", rendered)
        self.assertNotIn("/Users/private/report.json", rendered)

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
