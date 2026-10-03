#!/usr/bin/env python3
"""Run the Phase 0 existing-Chrome coexistence probe.

The default lane is an offline, deterministic fixture-contract check.  Headed
lanes use only the public host-backed direct CLI against an already-running
extension bridge.  This runner never launches Chrome, discovers or attaches to
CDP, accepts a copied debugger endpoint, or treats a descriptor as live proof.
The report contains logical scenario names only; browser IDs, secrets, paths,
and page bodies are never persisted.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import select
import selectors
import shutil
import signal
import subprocess
import sys
import time
from contextlib import ExitStack
from pathlib import Path
from typing import Any

from artifact_envelope import envelope as add_envelope
from artifact_envelope import redact_for_persistence, write_json_atomic

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "browser-task-spaces"
MANIFEST_PATH = FIXTURE_ROOT / "manifest.json"
DEFAULT_ARTIFACT_ROOT = (ROOT / "artifacts" / "p0-coexistence").resolve()
MAX_REPORT_BYTES = 64 * 1024
MAX_ARTIFACT_FILES = 8
MAX_EXISTING_ARTIFACT_BYTES = 512 * 1024
MAX_CLI_STDOUT_BYTES = 128 * 1024
MAX_CLI_STDERR_BYTES = 16 * 1024
MAX_CLI_TIMEOUT_SECONDS = 15.0
MAX_OPERATOR_CHECKPOINT_SECONDS = 60.0
MAX_OPERATOR_LINE_CHARS = 160
MAX_LIVE_RECEIPTS = 96

DESCRIPTOR_SCHEMA_VERSION = 2
DIRECT_CLI_ENV = "AGENTYC_CLI"
DIRECT_CLI_DEFAULT = ROOT / "target" / "debug" / "agentyc"
HOST_PROBE_ENV = "AGENTYC_EXISTING_CHROME_PROBE"
HOST_PROBE_DEFAULT = ROOT / "target" / "debug" / "agentyc-existing-chrome-probe"
CHECKPOINT_ENV = "AGENTYC_EXISTING_CHROME_OPERATOR_CHECKPOINT"
CHECKPOINT_TOKEN_PREFIX = "AGENTYC_EXISTING_CHROME_CHECKPOINT_V1"
LIVE_PRINCIPALS = ("agent-a", "agent-b")
_LIVE_EXECUTION_TOKEN = object()
REQUIRED_LIVE_SCENARIOS = (
    "user-tab-preservation",
    "two-space-isolation",
    "focus-stability",
    "takeover-fence",
    "return-control-fresh-lease",
    "agent-page-cleanup",
    "worker-restart-recovery",
    "host-restart-recovery",
    "chrome-restart-recovery",
    "extension-update-recovery",
)
# Newer probe builds use the browser-scenario names; older available builds
# use the legacy logical checkpoint names below. Both are adapted to the
# runner's exact ten-scenario output and neither is browser evidence.
HOST_PROBE_CHECKPOINTS = REQUIRED_LIVE_SCENARIOS
LEGACY_HOST_PROBE_CHECKPOINTS = (
    "connect.local_socket",
    "connect.host_extension",
    "scenario.two_spaces",
    "scenario.page_create_list",
    "scenario.isolation",
    "scenario.lease_takeover",
    "scenario.return_control",
    "scenario.cleanup",
    "scenario.cleanup_returned_space",
)

EXPECTED_FIXTURES = {
    "small-form": {"file": "small-form.html", "controls": {"name", "role", "save"}},
    "dense-admin-table": {"file": "dense-admin-table.html", "controls": {"search", "row-actions"}},
    "dynamic-feed": {"file": "dynamic-feed.html", "controls": {"append-item", "feed-item"}},
    "nested-frame": {"file": "nested-frame.html", "controls": {"outer-action", "inner-action"}},
}

# These are logical handles, not browser/Chrome identifiers.
SCENARIO_CONTRACT: dict[str, Any] = {
    "spaces": [
        {"space": "research", "agent": "agent-a", "page": "results", "fixture": "dynamic-feed"},
        {"space": "testing", "agent": "agent-b", "page": "app", "fixture": "small-form"},
    ],
    "user_tab": {
        "label": "unrelated-user-tab",
        "agent_may_close": False,
        "agent_may_focus": False,
        "must_remain_open": True,
    },
    "isolation": {
        "same_space_mutation": "allowed",
        "cross_space_mutation": "rejected",
        "user_tab_mutation": "rejected",
    },
    "control": {
        "takeover_fences_queued_actions": True,
        "return_requires_fresh_lease": True,
        "cleanup_closes_only_agent_pages": True,
    },
}

_SECRET_KEY = re.compile(
    r"(?:token|secret|password|passwd|cookie|authorization|credential|private[_-]?key|websocket[_-]?url)",
    re.IGNORECASE,
)
_RAW_ID_KEY = re.compile(
    r"(?:raw[_-]?id|cdp[_-]?id|backend[_-]?node[_-]?id|target[_-]?id|session[_-]?id|tab[_-]?id|group[_-]?id)",
    re.IGNORECASE,
)
_SECRET_TEXT = re.compile(r"(?i)(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]+")
_NETWORK = re.compile(r"(?:https?|wss?)://|\b(?:fetch|XMLHttpRequest|WebSocket)\b", re.IGNORECASE)
_ABSOLUTE_PATH = re.compile(r"(?i)(?:/(?:Users|home|private|tmp|var|etc|opt|Applications)/|[A-Za-z]:[\\\\/])")
_BROWSER_ID = re.compile(r"^[a-p]{32}$")


class ProbeError(ValueError):
    """A deterministic input or fixture contract failure."""


class DirectCliResponse:
    """A bounded, shape-checked response from one public direct-CLI call."""

    def __init__(
        self,
        transport: str,
        ok: bool | None = None,
        result: dict[str, Any] | None = None,
        error_code: str | None = None,
        reason_code: str | None = None,
    ) -> None:
        self.transport = transport
        self.ok = ok
        self.result = result
        self.error_code = error_code
        self.reason_code = reason_code


class HostProbeResponse:
    """A normalized report from the real host-backed probe executable."""

    def __init__(
        self,
        transport: str,
        *,
        success: bool | None = None,
        broker_epoch: int | None = None,
        connection_epoch: int | None = None,
        checkpoints: list[dict[str, str]] | None = None,
        limitations_count: int = 0,
        returncode: int | None = None,
        reason_code: str | None = None,
    ) -> None:
        self.transport = transport
        self.success = success
        self.broker_epoch = broker_epoch
        self.connection_epoch = connection_epoch
        self.checkpoints = checkpoints or []
        self.limitations_count = limitations_count
        self.returncode = returncode
        self.reason_code = reason_code


def _terminate_process(process: subprocess.Popen[bytes]) -> None:
    """Stop only the bounded child process group used for one CLI call."""
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (AttributeError, OSError, ProcessLookupError):
        try:
            process.kill()
        except (OSError, ProcessLookupError):
            pass
    try:
        process.wait(timeout=1.0)
    except (OSError, subprocess.TimeoutExpired):
        pass


def _run_bounded_process(
    argv: list[str],
    *,
    env: dict[str, str],
    timeout: float,
) -> tuple[str, int | None, bytes, bytes]:
    """Run one non-shell child without allowing unbounded pipes or hangs."""
    try:
        process = subprocess.Popen(
            argv,
            cwd=str(ROOT),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            close_fds=True,
            start_new_session=True,
        )
    except FileNotFoundError:
        return "cli_not_found", None, b"", b""
    except PermissionError:
        return "cli_not_executable", None, b"", b""
    except OSError:
        return "cli_unavailable", None, b"", b""

    streams = {
        process.stdout: ("stdout", MAX_CLI_STDOUT_BYTES),
        process.stderr: ("stderr", MAX_CLI_STDERR_BYTES),
    }
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    selector = selectors.DefaultSelector()
    for stream, stream_data in streams.items():
        if stream is not None:
            selector.register(stream, selectors.EVENT_READ, stream_data)

    status = "completed"
    deadline = time.monotonic() + timeout
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                status = "cli_timeout"
                _terminate_process(process)
                break
            ready = selector.select(remaining)
            if not ready:
                status = "cli_timeout"
                _terminate_process(process)
                break
            for key, _ in ready:
                stream_name, maximum = key.data
                try:
                    chunk = os.read(key.fd, 8192)
                except OSError:
                    status = "cli_io_error"
                    _terminate_process(process)
                    chunk = b""
                if not chunk:
                    try:
                        selector.unregister(key.fileobj)
                    except (KeyError, ValueError):
                        pass
                    continue
                buffer = buffers[stream_name]
                if len(buffer) + len(chunk) > maximum:
                    status = f"{stream_name}_oversized"
                    _terminate_process(process)
                    break
                buffer.extend(chunk)
            if status != "completed":
                break

        if status == "completed":
            remaining = max(0.0, deadline - time.monotonic())
            try:
                process.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                status = "cli_timeout"
                _terminate_process(process)
    finally:
        selector.close()
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass

    return status, process.returncode, bytes(buffers["stdout"]), bytes(buffers["stderr"])


def _parse_direct_response(
    stdout: bytes,
    returncode: int | None,
) -> DirectCliResponse:
    """Parse exactly one direct-CLI JSON record and no diagnostic text."""
    try:
        text = stdout.decode("utf-8")
    except UnicodeDecodeError:
        return DirectCliResponse("malformed_response", reason_code="stdout_not_utf8")
    decoder = json.JSONDecoder()
    payload = text.lstrip()
    if not payload:
        return DirectCliResponse("malformed_response", reason_code="stdout_empty")
    try:
        value, end = decoder.raw_decode(payload)
    except json.JSONDecodeError:
        return DirectCliResponse("malformed_response", reason_code="stdout_not_json")
    if payload[end:].strip():
        return DirectCliResponse("malformed_response", reason_code="multiple_stdout_values")
    if not isinstance(value, dict) or type(value.get("ok")) is not bool:
        return DirectCliResponse("malformed_response", reason_code="response_envelope_invalid")

    if value["ok"]:
        result = value.get("result")
        if not isinstance(result, dict):
            return DirectCliResponse("malformed_response", reason_code="success_result_invalid")
        if returncode != 0:
            return DirectCliResponse("malformed_response", reason_code="success_exit_code_mismatch")
        return DirectCliResponse("complete", ok=True, result=result)

    error = value.get("error")
    code = error.get("code") if isinstance(error, dict) else None
    if (
        not isinstance(code, str)
        or not code
        or len(code) > 96
        or re.fullmatch(r"[a-z0-9_]+", code) is None
    ):
        return DirectCliResponse("malformed_response", reason_code="failure_error_invalid")
    if returncode == 0:
        return DirectCliResponse("malformed_response", reason_code="failure_exit_code_mismatch")
    return DirectCliResponse("complete", ok=False, error_code=code)


def _parse_host_probe_response(stdout: bytes, returncode: int | None) -> HostProbeResponse:
    """Parse the host probe without retaining IDs, details, or private paths."""
    try:
        text = stdout.decode("utf-8")
    except UnicodeDecodeError:
        return HostProbeResponse("malformed_response", reason_code="stdout_not_utf8", returncode=returncode)
    payload = text.lstrip()
    if not payload:
        return HostProbeResponse("malformed_response", reason_code="stdout_empty", returncode=returncode)
    decoder = json.JSONDecoder()
    try:
        value, end = decoder.raw_decode(payload)
    except json.JSONDecodeError:
        return HostProbeResponse("malformed_response", reason_code="stdout_not_json", returncode=returncode)
    if payload[end:].strip():
        return HostProbeResponse("malformed_response", reason_code="multiple_stdout_values", returncode=returncode)
    if not isinstance(value, dict) or type(value.get("success")) is not bool:
        return HostProbeResponse("malformed_response", reason_code="response_shape_invalid", returncode=returncode)
    socket_path = value.get("socket_path")
    if not isinstance(socket_path, str) or len(socket_path) > 2048:
        return HostProbeResponse("malformed_response", reason_code="socket_path_invalid", returncode=returncode)

    checkpoints = value.get("checkpoints")
    if not isinstance(checkpoints, list):
        return HostProbeResponse("malformed_response", reason_code="checkpoint_count_invalid", returncode=returncode)
    checkpoint_names = [item.get("name") for item in checkpoints if isinstance(item, dict)]
    if tuple(checkpoint_names) == HOST_PROBE_CHECKPOINTS:
        checkpoint_format = "scenario"
    elif tuple(checkpoint_names) == LEGACY_HOST_PROBE_CHECKPOINTS:
        checkpoint_format = "legacy"
    else:
        return HostProbeResponse("malformed_response", reason_code="checkpoint_order_invalid", returncode=returncode)
    if len(checkpoints) != len(checkpoint_names):
        return HostProbeResponse("malformed_response", reason_code="checkpoint_shape_invalid", returncode=returncode)
    raw_statuses: dict[str, str] = {}
    for checkpoint in checkpoints:
        status = checkpoint.get("status")
        detail = checkpoint.get("detail")
        if status not in {"passed", "failed", "skipped"} or not isinstance(detail, str) or len(detail) > 2048:
            return HostProbeResponse("malformed_response", reason_code="checkpoint_shape_invalid", returncode=returncode)
        # Details and socket_path may contain logical IDs or absolute paths.
        # Neither is retained; only bounded status values become evidence.
        raw_statuses[checkpoint["name"]] = status

    normalized: list[dict[str, str]] = []
    if checkpoint_format == "scenario":
        normalized = [
            {"name": name, "status": raw_statuses[name]}
            for name in HOST_PROBE_CHECKPOINTS
        ]
    else:
        legacy_to_scenario = {
            "scenario.two_spaces": "two-space-isolation",
            "scenario.isolation": "two-space-isolation",
            "scenario.lease_takeover": "takeover-fence",
            "scenario.return_control": "return-control-fresh-lease",
            "scenario.cleanup": "agent-page-cleanup",
            "scenario.cleanup_returned_space": "agent-page-cleanup",
        }
        mapped = {name: "skipped" for name in HOST_PROBE_CHECKPOINTS}
        for legacy_name, scenario_name in legacy_to_scenario.items():
            if raw_statuses.get(legacy_name) == "passed":
                mapped[scenario_name] = "passed"
            elif raw_statuses.get(legacy_name) == "failed" and mapped[scenario_name] != "passed":
                mapped[scenario_name] = "failed"
        normalized = [{"name": name, "status": mapped[name]} for name in HOST_PROBE_CHECKPOINTS]

    limitations = value.get("limitations", [])
    if (
        not isinstance(limitations, list)
        or len(limitations) > 32
        or any(not isinstance(item, str) or len(item) > 2048 for item in limitations)
    ):
        return HostProbeResponse("malformed_response", reason_code="limitations_shape_invalid", returncode=returncode)

    epochs: list[int | None] = []
    for key in ("broker_epoch", "connection_epoch"):
        epoch = value.get(key)
        if epoch is not None and (type(epoch) is not int or epoch <= 0):
            return HostProbeResponse("malformed_response", reason_code=f"{key}_invalid", returncode=returncode)
        epochs.append(epoch)
    if value["success"] and any(status != "passed" for status in raw_statuses.values()):
        return HostProbeResponse("malformed_response", reason_code="success_checkpoint_mismatch", returncode=returncode)
    if returncode is None:
        return HostProbeResponse("malformed_response", reason_code="exit_code_missing", returncode=returncode)
    if (value["success"] and returncode != 0) or (not value["success"] and returncode == 0):
        return HostProbeResponse("malformed_response", reason_code="success_exit_code_mismatch", returncode=returncode)
    return HostProbeResponse(
        "complete",
        success=value["success"],
        broker_epoch=epochs[0],
        connection_epoch=epochs[1],
        checkpoints=normalized,
        limitations_count=len(limitations),
        returncode=returncode,
    )


class DirectCli:
    """Small public-contract client; it never enables offline or CDP modes."""

    def __init__(self, executable: str, *, state_dir: str | None, timeout: float) -> None:
        if not math.isfinite(timeout) or timeout <= 0 or timeout > MAX_CLI_TIMEOUT_SECONDS:
            raise ProbeError("direct CLI timeout is outside the bounded range")
        self.executable = executable
        self.state_dir = state_dir.strip() if isinstance(state_dir, str) and state_dir.strip() else None
        self.timeout = timeout
        self.environment = os.environ.copy()

    def call(self, command: list[str], *, principal: str) -> DirectCliResponse:
        if principal not in LIVE_PRINCIPALS:
            return DirectCliResponse("invalid_request", reason_code="principal_not_allowed")
        if not command or any(not isinstance(item, str) or not item for item in command):
            return DirectCliResponse("invalid_request", reason_code="command_invalid")
        unsafe_options = {"--offline", "--cdp-url", "--websocket-url", "--target-id", "--session-id", "--tab-id"}
        if any(item.split("=", 1)[0] in unsafe_options for item in command):
            return DirectCliResponse("invalid_request", reason_code="unsafe_cli_option")

        argv = [self.executable]
        if self.state_dir is not None:
            argv.extend(("--state-dir", self.state_dir))
        argv.extend(("--principal", principal, "--json"))
        argv.extend(command)
        status, returncode, stdout, _stderr = _run_bounded_process(
            argv,
            env=self.environment,
            timeout=self.timeout,
        )
        if status != "completed":
            return DirectCliResponse(status, reason_code=status)
        return _parse_direct_response(stdout, returncode)


def resolve_direct_cli(value: str | None) -> tuple[str | None, str]:
    """Resolve an executable path or command name without invoking a shell."""
    configured = value if value is not None else os.environ.get(DIRECT_CLI_ENV)
    if configured is not None:
        candidate = configured.strip()
        if not candidate or any(character in candidate for character in "\x00\r\n"):
            return None, "cli_not_configured"
        path_candidate = Path(candidate).expanduser()
        if path_candidate.is_absolute() or "/" in candidate or "\\" in candidate:
            if not path_candidate.is_absolute():
                path_candidate = ROOT / path_candidate
            if path_candidate.is_file() and os.access(path_candidate, os.X_OK):
                return str(path_candidate), "configured"
            return None, "cli_not_executable"
        resolved = shutil.which(candidate)
        if resolved:
            return resolved, "configured"
        return None, "cli_not_found"

    if DIRECT_CLI_DEFAULT.is_file() and os.access(DIRECT_CLI_DEFAULT, os.X_OK):
        return str(DIRECT_CLI_DEFAULT), "repository_debug_binary"
    return None, "cli_not_found"


def resolve_host_probe(value: str | None) -> tuple[str | None, str]:
    """Resolve the real host probe without accepting a descriptor as a probe."""
    configured = value if value is not None else os.environ.get(HOST_PROBE_ENV)
    if configured is not None:
        candidate = configured.strip()
        if not candidate or any(character in candidate for character in "\x00\r\n"):
            return None, "host_probe_not_configured"
        path_candidate = Path(candidate).expanduser()
        if path_candidate.is_absolute() or "/" in candidate or "\\" in candidate:
            if not path_candidate.is_absolute():
                path_candidate = ROOT / path_candidate
            if path_candidate.is_file() and os.access(path_candidate, os.X_OK):
                return str(path_candidate), "configured"
            return None, "host_probe_not_executable"
        resolved = shutil.which(candidate)
        if resolved:
            return resolved, "configured"
        return None, "host_probe_not_found"

    if HOST_PROBE_DEFAULT.is_file() and os.access(HOST_PROBE_DEFAULT, os.X_OK):
        return str(HOST_PROBE_DEFAULT), "repository_debug_binary"
    return None, "host_probe_not_found"


def _fake_host_requested() -> bool:
    return os.environ.get("AGENTYC_FAKE_HOST", "").strip().lower() in {"1", "true", "yes"}


def _logical_id(value: Any, prefix: str) -> bool:
    return (
        isinstance(value, str)
        and 1 < len(value) <= 128
        and value.startswith(prefix)
        and re.fullmatch(r"[A-Za-z0-9_-]+", value) is not None
    )


def _positive_integer(value: Any) -> bool:
    return type(value) is int and value > 0


def _host_status_observation(response: DirectCliResponse) -> tuple[dict[str, Any] | None, str | None]:
    if response.transport != "complete":
        return None, response.reason_code or "host_status_transport_unavailable"
    if response.ok is not True or not isinstance(response.result, dict):
        return None, response.error_code or "host_status_rejected"
    result = response.result
    bridge = result.get("bridge")
    direct_path = result.get("direct_path")
    capabilities = bridge.get("capabilities") if isinstance(bridge, dict) else None
    if (
        result.get("lifecycle") != "ready"
        or not isinstance(bridge, dict)
        or bridge.get("mode") != "extension"
        or bridge.get("connected") is not True
        or bridge.get("test_seam") is not False
        or not isinstance(capabilities, list)
        or not capabilities
        or any(not isinstance(item, str) or len(item) > 64 for item in capabilities)
        or not isinstance(direct_path, dict)
        or direct_path.get("browser_auto_launch") is not False
        or direct_path.get("copied_debug_endpoint") is not False
        or direct_path.get("logical_ids_only") is not True
    ):
        return None, "host_status_does_not_prove_safe_extension_bridge"
    broker_epoch = result.get("broker_epoch")
    if not _positive_integer(broker_epoch):
        return None, "host_status_epoch_invalid"
    return {
        "lifecycle": "ready",
        "bridge_connected": True,
        "test_seam": False,
        "capabilities": tuple(capabilities),
        "broker_epoch": broker_epoch,
    }, None


def _space_create_result(response: DirectCliResponse) -> str | None:
    if response.transport != "complete" or response.ok is not True or not isinstance(response.result, dict):
        return None
    result = response.result
    value = result.get("space_id")
    return value if result.get("lifecycle") == "created" and _logical_id(value, "space_") else None


def _claim_result(response: DirectCliResponse, expected_space: str) -> int | None:
    if response.transport != "complete" or response.ok is not True or not isinstance(response.result, dict):
        return None
    result = response.result
    lease = result.get("lease")
    epoch = lease.get("lease_epoch") if isinstance(lease, dict) else None
    if (
        result.get("space_id") != expected_space
        or result.get("lifecycle") != "agent_owned"
        or not isinstance(lease, dict)
        or not _positive_integer(epoch)
    ):
        return None
    return epoch


def _page_create_result(response: DirectCliResponse, expected_space: str) -> str | None:
    if response.transport != "complete" or response.ok is not True or not isinstance(response.result, dict):
        return None
    result = response.result
    page_id = result.get("page_id")
    if result.get("space_id") != expected_space or not _logical_id(page_id, "page_"):
        return None
    return page_id


def _page_list_count(response: DirectCliResponse, expected_space: str) -> int | None:
    if response.transport != "complete" or response.ok is not True or not isinstance(response.result, dict):
        return None
    result = response.result
    pages = result.get("pages")
    if result.get("space_id") != expected_space or not isinstance(pages, list) or len(pages) > 256:
        return None
    return len(pages)


def _event_count(response: DirectCliResponse) -> int | None:
    if response.transport != "complete" or response.ok is not True or not isinstance(response.result, dict):
        return None
    events = response.result.get("events")
    resume = response.result.get("resume")
    if not isinstance(events, list) or len(events) > 1024 or resume not in {"accepted", "resync_required"}:
        return None
    return len(events)


def _takeover_result(response: DirectCliResponse, expected_space: str, old_epoch: int) -> int | None:
    if response.transport != "complete" or response.ok is not True or not isinstance(response.result, dict):
        return None
    result = response.result
    epoch = result.get("lease_epoch")
    if (
        result.get("space_id") != expected_space
        or result.get("lifecycle") != "agent_owned"
        or result.get("fence_acknowledged") is not True
        or type(epoch) is not int
        or epoch <= old_epoch
    ):
        return None
    return epoch


def _lifecycle_result(response: DirectCliResponse, expected: str) -> bool:
    return bool(
        response.transport == "complete"
        and response.ok is True
        and isinstance(response.result, dict)
        and response.result.get("lifecycle") == expected
    )


def _expected_rejection(response: DirectCliResponse, code: str) -> bool:
    return response.transport == "complete" and response.ok is False and response.error_code == code


def _receipt(
    operation: str,
    response: DirectCliResponse,
    *,
    expected_rejection: bool = False,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "operation": operation,
        "source": "direct_cli",
        "current_run": True,
        "observed": response.transport == "complete",
        "ok": response.ok is True,
    }
    if response.transport != "complete":
        record["reason_code"] = response.reason_code or "cli_unavailable"
    elif response.ok is True:
        record["result_receipt"] = "accepted"
    else:
        record["failure_code"] = response.error_code or "direct_cli_rejected"
        record["expected_rejection"] = expected_rejection
    return record


def _append_receipt(
    receipts: list[dict[str, Any]],
    operation: str,
    response: DirectCliResponse,
    *,
    expected_rejection: bool = False,
) -> None:
    if len(receipts) < MAX_LIVE_RECEIPTS:
        receipts.append(_receipt(operation, response, expected_rejection=expected_rejection))


ENROLLMENT_COMPONENTS = ("profile", "host", "extension")
_ENROLLMENT_STATUSES = {"bound", "connected", "installed", "enrolled", "not_observed", "descriptor_only"}
_NON_LIVE_SCENARIO_STATUSES = {"live_passed", "passed", "success", "completed"}


def _empty_enrollment(*, source: str = "none", current_run: bool = False) -> dict[str, dict[str, Any]]:
    return {
        component: {
            "enrolled": False,
            "observed": False,
            "current_run": current_run,
            "source": source,
            "status": "not_observed",
        }
        for component in ENROLLMENT_COMPONENTS
    }


def _observed_host_enrollment(*, source: str, current_run: bool) -> dict[str, dict[str, Any]]:
    evidence = _empty_enrollment(source=source, current_run=False)
    if current_run:
        for component in ("host", "extension"):
            evidence[component].update({"observed": True, "current_run": True, "status": "connected"})
    return evidence


def _normalize_enrollment(value: Any, *, default_source: str = "none") -> dict[str, dict[str, Any]]:
    """Keep enrollment claims explicit and require observation for enrollment."""
    normalized = _empty_enrollment(source=default_source, current_run=False)
    if not isinstance(value, dict):
        return normalized
    for component in ENROLLMENT_COMPONENTS:
        raw = value.get(component)
        if not isinstance(raw, dict):
            continue
        source = raw.get("source") if isinstance(raw.get("source"), str) else default_source
        current_run = raw.get("current_run") is True
        observed = raw.get("observed") is True
        status = raw.get("status") if raw.get("status") in _ENROLLMENT_STATUSES else "not_observed"
        if raw.get("observed") is not True:
            status = "descriptor_only" if source == "descriptor" else "not_observed"
        enrolled = (
            raw.get("enrolled") is True
            and observed
            and current_run
            and source not in {"descriptor", "offline", "operator_acknowledgement", "operator"}
        )
        normalized[component] = {
            "enrolled": enrolled,
            "observed": observed,
            "current_run": current_run,
            "source": source,
            "status": status,
        }
    return normalized


def _initial_scenarios() -> list[dict[str, Any]]:
    return [
        {
            "name": name,
            "status": "not_observed",
            "observation": {
                "source": "none",
                "observed": False,
                "current_run": False,
                "browser_observed": False,
                "reason_code": "not_attempted",
            },
        }
        for name in REQUIRED_LIVE_SCENARIOS
    ]


def _normalize_scenarios(value: Any, *, live_complete: bool = False) -> list[dict[str, Any]]:
    """Return exactly the ordered ten records without promoting claims to live."""
    records: dict[str, dict[str, Any]] = {}
    if isinstance(value, list):
        for item in value:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            if name in REQUIRED_LIVE_SCENARIOS and name not in records:
                records[name] = item

    normalized = _initial_scenarios()
    for scenario in normalized:
        raw = records.get(scenario["name"])
        if raw is None:
            continue
        raw_status = raw.get("status") if isinstance(raw.get("status"), str) else "not_observed"
        observation = raw.get("observation")
        if not isinstance(observation, dict):
            observation = {}
        source = observation.get("source") if isinstance(observation.get("source"), str) else "none"
        observed = observation.get("observed") is True
        current_run = observation.get("current_run") is True
        browser_observed = observation.get("browser_observed") is True
        reason_code_value = observation.get("reason_code")
        reason_code = reason_code_value if isinstance(reason_code_value, str) else "observation_unavailable"
        receipt_refs = observation.get("receipt_refs")
        safe_receipt_refs = (
            [item for item in receipt_refs if isinstance(item, str) and len(item) <= 160]
            if isinstance(receipt_refs, list)
            else []
        )
        status = raw_status if isinstance(raw_status, str) else "not_observed"
        if not live_complete and status in _NON_LIVE_SCENARIO_STATUSES:
            status = "not_observed"
            observed = False
            current_run = False
            browser_observed = False
            source = "none"
            reason_code = "live_evidence_incomplete"
            safe_receipt_refs = []
        safe_observation: dict[str, Any] = {
            "source": source,
            "observed": observed,
            "current_run": current_run,
            "browser_observed": browser_observed,
            "reason_code": reason_code[:160],
        }
        if safe_receipt_refs:
            safe_observation["receipt_refs"] = safe_receipt_refs
        if observation.get("operator_acknowledged") is True:
            safe_observation["operator_acknowledged"] = True
        scenario["status"] = status[:96]
        scenario["observation"] = safe_observation
    return normalized


def _set_scenario(
    scenarios: list[dict[str, Any]],
    name: str,
    status: str,
    reason_code: str,
    *,
    receipt_refs: tuple[str, ...] = (),
    operator_acknowledged: bool = False,
) -> None:
    for scenario in scenarios:
        if scenario["name"] == name:
            observation: dict[str, Any] = {
                "source": "direct_cli" if receipt_refs else "none",
                "observed": bool(receipt_refs),
                "current_run": bool(receipt_refs),
                "browser_observed": False,
                "reason_code": reason_code,
            }
            if receipt_refs:
                observation["receipt_refs"] = list(receipt_refs)
            if operator_acknowledged:
                observation["operator_acknowledged"] = True
            scenario["status"] = status
            scenario["observation"] = observation
            return


def _operator_checkpoint(name: str, timeout: float) -> tuple[bool, str]:
    """Accept only a fixed acknowledgement; it is never used as evidence."""
    token = f"{CHECKPOINT_TOKEN_PREFIX} {name} ACK"
    print(
        f"Operator checkpoint required for {name}. Enter exactly: {token}",
        file=sys.stderr,
        flush=True,
    )
    try:
        with ExitStack() as stack:
            stream = sys.stdin
            if not stream.isatty():
                stream = stack.enter_context(open("/dev/tty", "r", encoding="utf-8"))
            ready, _, _ = select.select([stream], [], [], timeout)
            if not ready:
                return False, "checkpoint_timeout"
            line = stream.readline(MAX_OPERATOR_LINE_CHARS + 1)
            if len(line) > MAX_OPERATOR_LINE_CHARS:
                return False, "checkpoint_line_oversized"
            acknowledged = line.strip() == token
            return acknowledged, "acknowledged" if acknowledged else "checkpoint_token_invalid"
    except (OSError, ValueError):
        return False, "controlling_tty_unavailable"


def _host_probe_scenarios(response: HostProbeResponse) -> list[dict[str, Any]]:
    scenarios = _initial_scenarios()
    for scenario, checkpoint in zip(scenarios, response.checkpoints):
        if checkpoint["status"] == "passed":
            scenario["status"] = "host_observed_not_browser"
            scenario["observation"] = {
                "source": "host_probe",
                "observed": True,
                "current_run": True,
                "browser_observed": False,
                "reason_code": "host_probe_checkpoint_passed",
            }
        elif checkpoint["status"] == "failed":
            scenario["observation"]["reason_code"] = "host_probe_checkpoint_failed"
        else:
            scenario["observation"]["reason_code"] = "host_probe_checkpoint_skipped"
    return scenarios


def _live_unavailable(reason_code: str, *, cli_reason: str | None = None) -> dict[str, Any]:
    live: dict[str, Any] = {
        "requested": True,
        "required": True,
        "status": "live_required_unavailable",
        "executed": False,
        "current_run": False,
        "evidence_status": "direct_cli_unavailable",
        "provenance": "unavailable",
        "descriptor_policy": "descriptors_not_accepted_as_live_evidence",
        "operator_claims_used": False,
        "host_probe_used": False,
        "reason": (
            "no valid existing-Chrome/extension harness descriptor is accepted; "
            "host-backed direct CLI preflight is unavailable"
        ),
        "reason_code": reason_code,
        "browser": {
            "observed": False,
            "launch": False,
            "download": False,
            "cdp_url_used": False,
            "attached": False,
        },
        "enrollment": _empty_enrollment(),
        "scenarios": _initial_scenarios(),
        "safety": {
            "measurement_status": "not_measured_live_incomplete",
            "current_run": False,
            "user_tab_closes": None,
            "focus_theft": None,
            "cross_space_mutations": None,
            "stale_agent_mutations": None,
        },
        "release_gates": {"eligible": False, "reason_code": "live_observation_incomplete"},
    }
    if cli_reason:
        live["cli_status"] = cli_reason
    return live


def orchestrate_host_probe(
    *,
    executable: str,
    resolution: str,
    state_dir: str | None,
    timeout: float,
) -> dict[str, Any]:
    """Run the real host probe and keep its logical observations non-live."""
    environment = os.environ.copy()
    if state_dir is not None and state_dir.strip():
        environment["AGENTYC_STATE_DIR"] = state_dir.strip()
    process_status, returncode, stdout, _stderr = _run_bounded_process(
        [executable],
        env=environment,
        timeout=timeout,
    )
    if process_status != "completed":
        live = _live_unavailable(f"host_probe_{process_status}", cli_reason=resolution)
        live.update(
            {
                "evidence_status": "host_probe_unavailable",
                "provenance": "host_probe_attempted_current_run",
                "current_run": True,
                "host_probe_used": True,
                "executed": process_status not in {"cli_not_found", "cli_not_executable", "cli_unavailable"},
                "host_probe": {
                    "source": "host_probe",
                    "current_run": True,
                    "executed": True,
                    "transport": process_status,
                },
            }
        )
        return live

    response = _parse_host_probe_response(stdout, returncode)
    if response.transport != "complete":
        live = _live_unavailable(response.reason_code or "host_probe_response_invalid", cli_reason=resolution)
        live.update(
            {
                "evidence_status": "host_probe_response_invalid",
                "provenance": "host_probe_current_run",
                "current_run": True,
                "host_probe_used": True,
                "executed": True,
                "host_probe": {
                    "source": "host_probe",
                    "current_run": True,
                    "executed": True,
                    "transport": "malformed_response",
                },
            }
        )
        return live

    host_connected = response.broker_epoch is not None and response.connection_epoch is not None
    scenarios = _host_probe_scenarios(response)
    return {
        "requested": True,
        "required": True,
        "status": "live_observation_incomplete",
        "executed": True,
        "current_run": True,
        "evidence_status": "live_observation_incomplete",
        "provenance": "host_probe_current_run",
        "descriptor_policy": "descriptors_not_accepted_as_live_evidence",
        "operator_claims_used": False,
        "host_probe_used": True,
        "host_probe": {
            "source": "host_probe",
            "current_run": True,
            "executed": True,
            "success": response.success,
            "return_code": response.returncode,
            "broker_epoch_observed": response.broker_epoch is not None,
            "connection_epoch_observed": response.connection_epoch is not None,
            "checkpoint_count": len(response.checkpoints),
            "limitations_count": response.limitations_count,
        },
        "preflight": {
            "observed": host_connected,
            "source": "host_probe",
            "bridge": "extension" if host_connected else "unknown",
            "browser_observed": False,
        },
        "browser": {
            "observed": False,
            "launch": False,
            "download": False,
            "cdp_url_used": False,
            "attached": False,
        },
        "enrollment": _observed_host_enrollment(
            source="host_probe_current_run",
            current_run=True,
        )
        if host_connected
        else _empty_enrollment(source="host_probe_current_run"),
        "scenarios": scenarios,
        "safety": {
            "measurement_status": "not_measured_live_incomplete",
            "current_run": True,
            "user_tab_closes": None,
            "focus_theft": None,
            "cross_space_mutations": None,
            "stale_agent_mutations": None,
        },
        "release_gates": {"eligible": False, "reason_code": "browser_observations_missing"},
    }


def orchestrate_live(
    *,
    cli_path: str | None,
    state_dir: str | None,
    cli_timeout: float,
    operator_checkpoint: bool,
    checkpoint_timeout: float,
    host_probe_path: str | None = None,
) -> dict[str, Any]:
    """Run the real host probe first, then the bounded direct-CLI fallback."""
    if _fake_host_requested():
        return _live_unavailable("fake_host_environment_forbidden")
    if cli_path is None:
        host_probe, probe_resolution = resolve_host_probe(host_probe_path)
        if host_probe is not None:
            return orchestrate_host_probe(
                executable=host_probe,
                resolution=probe_resolution,
                state_dir=state_dir,
                timeout=cli_timeout,
            )
        if host_probe_path is not None:
            return _live_unavailable(probe_resolution, cli_reason=probe_resolution)
    executable, resolution = resolve_direct_cli(cli_path)
    if executable is None:
        return _live_unavailable(resolution, cli_reason=resolution)

    cli = DirectCli(executable, state_dir=state_dir, timeout=cli_timeout)
    receipts: list[dict[str, Any]] = []
    scenarios = _initial_scenarios()
    spaces: list[dict[str, Any]] = []
    failures: list[str] = []
    preflight = cli.call(["host", "status"], principal=LIVE_PRINCIPALS[0])
    _append_receipt(receipts, "host.status.preflight", preflight)
    host_observation, host_reason = _host_status_observation(preflight)
    if host_observation is None:
        live = _live_unavailable(host_reason or "host_status_invalid", cli_reason=resolution)
        live.update(
            {
                "executed": preflight.transport == "complete",
                "current_run": True,
                "provenance": "direct_cli_current_run",
                "operator_claims_used": bool(operator_checkpoint),
                "preflight": {
                    "observed": False,
                    "source": "direct_cli",
                    "executor_receipt": preflight.transport == "complete",
                    "reason_code": host_reason,
                },
                "enrollment": _empty_enrollment(source="direct_cli_current_run"),
                "receipts": receipts,
            }
        )
        return live
    if "action" not in host_observation["capabilities"]:
        live = _live_unavailable("required_action_capability_missing", cli_reason=resolution)
        live.update(
            {
                "executed": True,
                "current_run": True,
                "provenance": "direct_cli_current_run",
                "operator_claims_used": bool(operator_checkpoint),
                "preflight": {
                    "observed": True,
                    "source": "direct_cli",
                    "bridge": "extension",
                    "action_capability": False,
                },
                "enrollment": _observed_host_enrollment(
                    source="direct_cli_current_run",
                    current_run=True,
                ),
                "receipts": receipts,
            }
        )
        return live

    live: dict[str, Any] = {
        "requested": True,
        "required": True,
        "status": "live_observation_incomplete",
        "executed": True,
        "current_run": True,
        "evidence_status": "live_observation_incomplete",
        "provenance": "direct_cli_current_run",
        "descriptor_policy": "descriptors_not_accepted_as_live_evidence",
        "operator_claims_used": bool(operator_checkpoint),
        "host_probe_used": False,
        "preflight": {
            "observed": True,
            "source": "direct_cli",
            "bridge": "extension",
            "lifecycle": "ready",
            "action_capability": True,
            "direct_path_safe": True,
        },
        "browser": {
            "observed": False,
            "launch": False,
            "download": False,
            "cdp_url_used": False,
            "attached": False,
        },
        "enrollment": _observed_host_enrollment(
            source="direct_cli_current_run",
            current_run=True,
        ),
        "scenarios": scenarios,
        "release_gates": {"eligible": False, "reason_code": "required_browser_observations_missing"},
        "_execution_token": _LIVE_EXECUTION_TOKEN,
    }

    try:
        for label, principal, page_label in (
            ("research", LIVE_PRINCIPALS[0], "results"),
            ("testing", LIVE_PRINCIPALS[1], "app"),
        ):
            create_operation = f"space.create.{label}"
            create = cli.call(["space", "create", "--label", label], principal=principal)
            _append_receipt(receipts, create_operation, create)
            space_id = _space_create_result(create)
            if space_id is None:
                failures.append(f"{label}_space_create_invalid")
                continue
            space = {
                "label": label,
                "principal": principal,
                "space_id": space_id,
                "lease_epoch": None,
                "page_id": None,
                "cleanup_ok": False,
            }
            spaces.append(space)

            claim_operation = f"space.claim.{label}"
            claim = cli.call(["space", "claim", "--space-id", space_id], principal=principal)
            _append_receipt(receipts, claim_operation, claim)
            lease_epoch = _claim_result(claim, space_id)
            if lease_epoch is None:
                failures.append(f"{label}_space_claim_invalid")
                continue
            space["lease_epoch"] = lease_epoch

            page_operation = f"page.create.{label}"
            page = cli.call(
                [
                    "page",
                    "create",
                    "--space-id",
                    space_id,
                    "--lease-epoch",
                    str(lease_epoch),
                    "--label",
                    page_label,
                ],
                principal=principal,
            )
            _append_receipt(receipts, page_operation, page)
            page_id = _page_create_result(page, space_id)
            if page_id is None:
                failures.append(f"{label}_planned_page_create_invalid")
            else:
                space["page_id"] = page_id

            listed = cli.call(["page", "list", "--space-id", space_id], principal=principal)
            _append_receipt(receipts, f"page.list.{label}", listed)
            if _page_list_count(listed, space_id) is None:
                failures.append(f"{label}_page_list_invalid")

            events = cli.call(
                ["events", "--space-id", space_id, "--after-sequence", "0", "--limit", "64"],
                principal=principal,
            )
            _append_receipt(receipts, f"events.resume.{label}", events)
            if _event_count(events) is None:
                failures.append(f"{label}_events_invalid")

        if len(spaces) == 2 and all(space["lease_epoch"] is not None for space in spaces):
            first, second = spaces
            cross_space = cli.call(
                [
                    "page",
                    "create",
                    "--space-id",
                    first["space_id"],
                    "--lease-epoch",
                    str(first["lease_epoch"]),
                    "--label",
                    "cross-space-probe",
                ],
                principal=second["principal"],
            )
            _append_receipt(receipts, "page.create.cross_space_rejection", cross_space, expected_rejection=True)
            if _expected_rejection(cross_space, "space_forbidden"):
                _set_scenario(
                    scenarios,
                    "two-space-isolation",
                    "host_observed",
                    "cross_space_mutation_rejected_by_host",
                    receipt_refs=("page.create.cross_space_rejection",),
                )
            else:
                failures.append("cross_space_mutation_rejection_invalid")

            old_epoch = first["lease_epoch"]
            takeover = cli.call(
                ["space", "takeover", "--space-id", first["space_id"]],
                principal=first["principal"],
            )
            _append_receipt(receipts, "space.takeover.research", takeover)
            new_epoch = _takeover_result(takeover, first["space_id"], old_epoch)
            if new_epoch is None:
                failures.append("takeover_fence_invalid")
            else:
                first["lease_epoch"] = new_epoch
                stale = cli.call(
                    [
                        "page",
                        "create",
                        "--space-id",
                        first["space_id"],
                        "--lease-epoch",
                        str(old_epoch),
                        "--label",
                        "stale-lease-probe",
                    ],
                    principal=first["principal"],
                )
                _append_receipt(receipts, "page.create.stale_lease_rejection", stale, expected_rejection=True)
                if _expected_rejection(stale, "stale_lease"):
                    _set_scenario(
                        scenarios,
                        "takeover-fence",
                        "host_observed",
                        "fence_acknowledged_and_stale_epoch_rejected",
                        receipt_refs=("space.takeover.research", "page.create.stale_lease_rejection"),
                    )
                else:
                    failures.append("stale_epoch_rejection_invalid")

        for space in spaces:
            if space["lease_epoch"] is None:
                continue
            listed = cli.call(["page", "list", "--space-id", space["space_id"]], principal=space["principal"])
            _append_receipt(receipts, f"page.list.cleanup.{space['label']}", listed)
            if _page_list_count(listed, space["space_id"]) is None:
                failures.append(f"{space['label']}_cleanup_page_list_invalid")

    except (KeyError, OSError, TypeError, ValueError):
        failures.append("live_orchestration_exception")
    finally:
        for space in reversed(spaces):
            lease_epoch = space.get("lease_epoch")
            space["cleanup_ok"] = False
            if not isinstance(lease_epoch, int):
                continue
            finish = cli.call(
                [
                    "space",
                    "finish",
                    "--space-id",
                    space["space_id"],
                    "--lease-epoch",
                    str(lease_epoch),
                ],
                principal=space["principal"],
            )
            _append_receipt(receipts, f"space.finish.{space['label']}", finish)
            if not _lifecycle_result(finish, "finished"):
                failures.append(f"{space['label']}_finish_invalid")
                continue
            release = cli.call(
                [
                    "space",
                    "release",
                    "--space-id",
                    space["space_id"],
                    "--lease-epoch",
                    str(lease_epoch),
                ],
                principal=space["principal"],
            )
            _append_receipt(receipts, f"space.release.{space['label']}", release)
            if not _lifecycle_result(release, "released"):
                failures.append(f"{space['label']}_release_invalid")
            else:
                space["cleanup_ok"] = True

    if all(
        scenario["status"] == "not_observed"
        for scenario in scenarios
        if scenario["name"] in {"two-space-isolation", "takeover-fence"}
    ):
        failures.append("host_scenario_receipts_incomplete")

    cleanup_refs = tuple(
        receipt["operation"]
        for receipt in receipts
        if receipt["operation"].startswith(("space.finish.", "space.release.")) and receipt.get("ok") is True
    )
    page_spaces = [space for space in spaces if space.get("page_id") is not None]
    if page_spaces and all(space.get("cleanup_ok") is True for space in page_spaces):
        _set_scenario(
            scenarios,
            "agent-page-cleanup",
            "host_observed_not_browser",
            "planned_logical_pages_released_without_managed_browser_page",
            receipt_refs=cleanup_refs,
        )

    if spaces and not all(space.get("cleanup_ok") is True for space in spaces):
        failures.append("cleanup_incomplete")

    checkpoint_names = (
        "user-tab-preservation",
        "focus-stability",
        "return-control-fresh-lease",
        "worker-restart-recovery",
        "host-restart-recovery",
        "chrome-restart-recovery",
        "extension-update-recovery",
    )
    if operator_checkpoint and not failures:
        for name in checkpoint_names:
            acknowledged, checkpoint_status = _operator_checkpoint(name, checkpoint_timeout)
            checkpoint_record: dict[str, Any] = {
                "name": name,
                "acknowledged": acknowledged,
                "status": checkpoint_status,
                "evidence": "acknowledgement_only",
            }
            live.setdefault("operator_checkpoints", []).append(checkpoint_record)
            if not acknowledged:
                _set_scenario(scenarios, name, "operator_checkpoint_required", checkpoint_status)
                continue
            after_status = cli.call(["host", "status"], principal=LIVE_PRINCIPALS[0])
            _append_receipt(receipts, f"checkpoint.host.status.{name}", after_status)
            after_observation, after_reason = _host_status_observation(after_status)
            after_events = cli.call(
                ["events", "--after-sequence", "0", "--limit", "32"],
                principal=LIVE_PRINCIPALS[0],
            )
            _append_receipt(receipts, f"checkpoint.events.resume.{name}", after_events)
            events_observed = _event_count(after_events) is not None
            if after_observation is None or not events_observed:
                _set_scenario(scenarios, name, "operator_checkpoint_required", after_reason or "post_checkpoint_observation_invalid", operator_acknowledged=True)
                continue
            if name == "host-restart-recovery" and after_observation["broker_epoch"] != host_observation["broker_epoch"]:
                _set_scenario(
                    scenarios,
                    name,
                    "operator_checkpoint_required",
                    "operator_acknowledgement_not_browser_observation",
                    receipt_refs=(f"checkpoint.host.status.{name}", f"checkpoint.events.resume.{name}"),
                    operator_acknowledged=True,
                )
            else:
                _set_scenario(
                    scenarios,
                    name,
                    "operator_checkpoint_required",
                    "operator_acknowledgement_not_browser_observation",
                    receipt_refs=(f"checkpoint.host.status.{name}", f"checkpoint.events.resume.{name}"),
                    operator_acknowledged=True,
                )
    else:
        for name in checkpoint_names:
            _set_scenario(scenarios, name, "operator_checkpoint_required", "operator_checkpoint_not_requested")

    live["scenarios"] = scenarios
    live["receipts"] = receipts
    live["operator_claims_used"] = bool(operator_checkpoint)
    live["safety"] = {
        "measurement_status": "not_measured_live_incomplete",
        "current_run": True,
        "user_tab_closes": None,
        "focus_theft": None,
        "cross_space_mutations": None,
        "stale_agent_mutations": None,
    }
    live["cleanup"] = {
        "spaces_attempted": len(spaces),
        "spaces_released": sum(1 for receipt in receipts if receipt["operation"].startswith("space.release.") and receipt.get("ok") is True),
        "status": (
            "not_attempted"
            if not spaces
            else "passed" if all(space.get("cleanup_ok") is True for space in spaces) else "incomplete"
        ),
    }
    live["failures"] = sorted(set(failures))
    live["status"] = "operator_checkpoint_required" if operator_checkpoint and not failures else "live_observation_incomplete"
    live["evidence_status"] = "operator_checkpoint_required" if operator_checkpoint and not failures else "live_observation_incomplete"
    return live


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ProbeError("required local fixture is missing") from exc
    except json.JSONDecodeError as exc:
        raise ProbeError(f"invalid local fixture JSON: {exc.msg}") from exc


def bounded_string(value: Any, field: str, limit: int = 128) -> str:
    if not isinstance(value, str) or not value or len(value) > limit:
        raise ProbeError(f"{field} must be a bounded non-empty string")
    return value


def validate_manifest() -> dict[str, Any]:
    manifest = read_json(MANIFEST_PATH)
    if not isinstance(manifest, dict):
        raise ProbeError("fixture manifest must be an object")
    if manifest.get("deterministic") is not True or manifest.get("network") != "local-only":
        raise ProbeError("fixture manifest is not deterministic and local-only")
    if manifest.get("external_resources") is not False:
        raise ProbeError("fixture manifest permits external resources")

    entries = manifest.get("fixtures")
    if not isinstance(entries, list) or {entry.get("name") for entry in entries} != set(EXPECTED_FIXTURES):
        raise ProbeError("fixture manifest does not contain the expected fixture set")

    for entry in entries:
        if not isinstance(entry, dict):
            raise ProbeError("fixture manifest entry must be an object")
        name = bounded_string(entry.get("name"), "fixture name")
        expected = EXPECTED_FIXTURES.get(name)
        if expected is None or entry.get("file") != expected["file"]:
            raise ProbeError(f"fixture contract mismatch for {name}")
        path = FIXTURE_ROOT / expected["file"]
        if path.parent != FIXTURE_ROOT or not path.is_file():
            raise ProbeError(f"fixture file is missing for {name}")
        source = path.read_text(encoding="utf-8")
        if _NETWORK.search(source):
            raise ProbeError(f"fixture {name} contains a network or external-resource primitive")
        markers = {
            "row-actions": ("<button", "data-account"),
        }
        for control in expected["controls"]:
            required_markers = markers.get(control, (control,))
            if any(marker not in source for marker in required_markers):
                raise ProbeError(f"fixture {name} is missing control contract {control}")

    return manifest


def validate_scenario(spaces: int, agents: int) -> dict[str, Any]:
    if spaces != 2 or agents != 2:
        raise ProbeError("Phase 0 fixture contract requires exactly two spaces and two agents")
    contract = json.loads(json.dumps(SCENARIO_CONTRACT))
    if len(contract["spaces"]) != spaces or len({item["space"] for item in contract["spaces"]}) != spaces:
        raise ProbeError("space contract is not independent")
    if len({item["agent"] for item in contract["spaces"]}) != agents:
        raise ProbeError("agent contract is not independent")
    if contract["user_tab"]["agent_may_close"] or contract["user_tab"]["agent_may_focus"]:
        raise ProbeError("user-tab safety contract permits agent interference")
    if contract["user_tab"]["must_remain_open"] is not True:
        raise ProbeError("user-tab safety contract does not preserve the user tab")
    if contract["isolation"]["cross_space_mutation"] != "rejected":
        raise ProbeError("cross-space mutation is not rejected")
    if contract["isolation"]["user_tab_mutation"] != "rejected":
        raise ProbeError("user-tab mutation is not rejected")
    return contract


def safe_artifact_dir(value: str) -> Path:
    requested = Path(value)
    if not requested.is_absolute():
        requested = ROOT / requested
    current = requested
    while current != current.parent:
        if current.is_symlink():
            raise ProbeError("artifact path components must not be symlinks")
        current = current.parent
    requested = requested.resolve()
    if requested != DEFAULT_ARTIFACT_ROOT and DEFAULT_ARTIFACT_ROOT not in requested.parents:
        raise ProbeError("artifact directory must be inside artifacts/p0-coexistence/")
    return requested


def validate_artifact_budget(path: Path) -> None:
    if not path.exists():
        return
    files = []
    for item in path.rglob("*"):
        if item.is_symlink():
            raise ProbeError("artifact directory must not contain symlinks")
        if item.is_file():
            files.append(item)
    if len(files) >= MAX_ARTIFACT_FILES:
        raise ProbeError("artifact directory file budget exceeded")
    size = sum(item.stat().st_size for item in files)
    if size > MAX_EXISTING_ARTIFACT_BYTES:
        raise ProbeError("artifact directory byte budget exceeded")


def redact(value: Any, depth: int = 0) -> Any:
    if depth > 12:
        return "<redacted>"
    if isinstance(value, dict):
        output: dict[str, Any] = {}
        for key in sorted(value, key=str):
            name = str(key)
            sensitive_name = _SECRET_KEY.search(name) or _RAW_ID_KEY.search(name)
            output[name] = "<redacted>" if sensitive_name and not isinstance(value[key], bool) else redact(value[key], depth + 1)
        return output
    if isinstance(value, list):
        return [redact(item, depth + 1) for item in value]
    if isinstance(value, str):
        return _SECRET_TEXT.sub("<redacted>", value)
    return value


def _live_evidence_is_complete(live: dict[str, Any]) -> bool:
    """Allow live green only for current-run browser observations."""
    if (
        live.get("_execution_token") is not _LIVE_EXECUTION_TOKEN
        or live.get("executed") is not True
        or live.get("current_run") is not True
        or live.get("status") != "live_passed"
        or live.get("evidence_status") != "live_passed"
        or live.get("evidence_mode") != "live"
        or live.get("provenance") != "direct_cli_current_run"
        or live.get("descriptor_policy") != "descriptors_not_accepted_as_live_evidence"
        or live.get("operator_claims_used") is not False
        or live.get("host_probe_used") is not False
        or live.get("browser_observed") is not True
    ):
        return False
    browser = live.get("browser")
    preflight = live.get("preflight")
    receipts = live.get("receipts")
    scenarios = live.get("scenarios")
    enrollment = live.get("enrollment")
    safety = live.get("safety")
    if (
        not isinstance(browser, dict)
        or browser.get("observed") is not True
        or browser.get("launch") is not False
        or browser.get("download") is not False
        or browser.get("cdp_url_used") is not False
        or not isinstance(preflight, dict)
        or preflight.get("observed") is not True
        or preflight.get("source") != "direct_cli"
        or not isinstance(receipts, list)
        or not receipts
        or not isinstance(scenarios, list)
        or tuple(item.get("name") for item in scenarios if isinstance(item, dict)) != REQUIRED_LIVE_SCENARIOS
        or not isinstance(enrollment, dict)
        or not isinstance(safety, dict)
        or safety.get("measurement_status") != "measured_live"
        or safety.get("current_run") is not True
    ):
        return False
    for component in ENROLLMENT_COMPONENTS:
        item = enrollment.get(component)
        if (
            not isinstance(item, dict)
            or item.get("enrolled") is not True
            or item.get("observed") is not True
            or item.get("current_run") is not True
            or item.get("status") not in {"bound", "connected", "installed", "enrolled"}
            or not isinstance(item.get("source"), str)
            or not item["source"].endswith("_current_run")
        ):
            return False
    receipt_operations: set[str] = set()
    for receipt in receipts:
        if (
            not isinstance(receipt, dict)
            or receipt.get("source") != "direct_cli"
            or receipt.get("current_run") is not True
            or receipt.get("observed") is not True
            or not isinstance(receipt.get("operation"), str)
        ):
            return False
        receipt_operations.add(receipt["operation"])
    for key in ("user_tab_closes", "focus_theft", "cross_space_mutations", "stale_agent_mutations"):
        if type(safety.get(key)) is not int or safety[key] != 0:
            return False
    for expected_name, scenario in zip(REQUIRED_LIVE_SCENARIOS, scenarios):
        if not isinstance(scenario, dict) or scenario.get("name") != expected_name or scenario.get("status") != "live_passed":
            return False
        observation = scenario.get("observation")
        if (
            not isinstance(observation, dict)
            or observation.get("source") != "browser_current_run"
            or observation.get("observed") is not True
            or observation.get("current_run") is not True
            or observation.get("browser_observed") is not True
            or observation.get("operator_acknowledged") is True
            or not isinstance(observation.get("receipt_refs"), list)
            or not observation["receipt_refs"]
            or any(reference not in receipt_operations for reference in observation["receipt_refs"])
        ):
            return False
    return True


def safe_report(*, mode: str, manifest: dict[str, Any], contract: dict[str, Any], live: dict[str, Any]) -> dict[str, Any]:
    executed = _live_evidence_is_complete(live)
    requested_live = mode == "headed"
    reported_status = live.get("status", "offline_passed") if requested_live else "offline_passed"
    if requested_live and reported_status == "live_passed" and not executed:
        reported_status = "live_observation_incomplete"
    report_live = dict(live)
    report_live.pop("_execution_token", None)
    preflight_observed = isinstance(live.get("preflight"), dict) and live["preflight"].get("observed") is True
    evidence_mode = (
        "live"
        if executed
        else "live_observed_incomplete"
        if requested_live and preflight_observed
        else "live_attempted_incomplete"
        if requested_live and live.get("executed")
        else "offline"
    )
    scenarios = _normalize_scenarios(live.get("scenarios"), live_complete=executed)
    enrollment = _normalize_enrollment(
        live.get("enrollment"),
        default_source="direct_cli_current_run" if requested_live else "offline",
    )
    report_live["scenarios"] = scenarios
    report_live["enrollment"] = enrollment
    report_live["current_run"] = live.get("current_run") is True
    report_live["operator_claims_used"] = live.get("operator_claims_used") is True
    report_live["host_probe_used"] = live.get("host_probe_used") is True
    if executed:
        report_live["evidence_mode"] = "live"
    elif report_live.get("status") == "live_passed":
        report_live["status"] = "live_observation_incomplete"
        report_live["evidence_status"] = "live_observation_incomplete"
    measured = executed
    safety_counters = {
        key: 0 if measured else None
        for key in ("user_tab_closes", "focus_theft", "cross_space_mutations", "stale_agent_mutations")
    }
    return redact_for_persistence(
        {
            "schema_version": 1,
            "phase": 0,
            "rollout_phase": 7,
            "probe": "existing-chrome-coexistence",
            "kind": "existing-chrome-coexistence",
            "mode": mode,
            "evidence_mode": evidence_mode,
            "status": reported_status,
            "release_eligible": False,
            "spaces": len(contract["spaces"]),
            "agents": len({item["agent"] for item in contract["spaces"]}),
            "result": {
                "fixture_set": manifest.get("fixture_set"),
                "fixture_count": len(manifest.get("fixtures", [])),
                "scenario": contract,
            },
            "live": report_live,
            "enrollment": enrollment,
            "scenarios": scenarios,
            "execution_policy": {
                "attached": executed,
                "current_run": executed,
                "host_executor_used": bool(live.get("executed")),
                "browser_launch": False,
                "browser_download": False,
                "cdp_url_used": False,
            },
            "safety": {
                "browser_launch": "never",
                "browser_download": "never",
                "user_tab_close": 0 if measured else None,
                **safety_counters,
                "measurement_status": "measured_live" if measured else ("not_measured_live_incomplete" if requested_live else "not_measured_offline"),
                "current_run": measured,
                "raw_browser_ids_logged": False,
                "secrets_logged": False,
            },
            "release_gates": live.get("release_gates"),
            "redaction_status": {
                "status": "applied",
                "raw_browser_ids": False,
                "secrets": False,
                "absolute_paths": False,
                "page_bodies": False,
            },
            "limitations": [
                "Offline mode validates fixture contracts only; it is not evidence from a live Chrome profile.",
                "The headed lane invokes only the public host-backed probe or direct CLI and never launches, downloads, or attaches to Chrome or CDP.",
                "Enrollment descriptors and operator acknowledgements are not accepted as live evidence; host logical observations do not establish browser coexistence.",
            ],
        }
    )


def _descriptor_contains_forbidden_key(value: Any) -> bool:
    forbidden = {
        "cdpurl",
        "websocketurl",
        "targetid",
        "sessionid",
        "tabid",
        "profilepath",
        "extensionid",
        "hostpath",
        "backendnodeid",
        "debuggerendpoint",
    }
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).lower().replace("-", "_")
            if normalized.replace("_", "") in forbidden:
                return True
            if _descriptor_contains_forbidden_key(child):
                return True
    elif isinstance(value, list):
        return any(_descriptor_contains_forbidden_key(item) for item in value)
    elif isinstance(value, str) and (
        _NETWORK.search(value) or _ABSOLUTE_PATH.search(value) or _BROWSER_ID.fullmatch(value)
    ):
        return True
    return False


def _descriptor_errors(descriptor: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if descriptor.get("schema_version") != DESCRIPTOR_SCHEMA_VERSION:
        errors.append("descriptor schema_version must be 2")
    if descriptor.get("kind") != "existing-chrome-enrollment":
        errors.append("descriptor kind must be existing-chrome-enrollment")
    if descriptor.get("mode") not in {"existing-chrome", "existing_chrome"}:
        errors.append("descriptor mode must be existing-chrome")
    if descriptor.get("profile_scope") != "existing_user_profile":
        errors.append("descriptor profile_scope must be existing_user_profile")
    if _descriptor_contains_forbidden_key(descriptor):
        errors.append("descriptor must not contain raw IDs, paths, or debugger endpoints")

    enrollment = descriptor.get("enrollment")
    if not isinstance(enrollment, dict):
        errors.append("explicit enrollment object is required")
    else:
        for name, accepted_statuses in {
            "profile": {"bound", "enrolled"},
            "host": {"enrolled", "connected"},
            "extension": {"installed", "enrolled"},
        }.items():
            component = enrollment.get(name)
            if not isinstance(component, dict) or component.get("enrolled") is not True or component.get("status") not in accepted_statuses:
                errors.append(f"enrollment.{name} must be explicitly enrolled")
        profile = enrollment.get("profile")
        if isinstance(profile, dict) and profile.get("binding_verified") is not True:
            errors.append("enrollment.profile.binding_verified must be true")
        host = enrollment.get("host")
        if isinstance(host, dict) and host.get("origin_match_verified") is not True:
            errors.append("enrollment.host.origin_match_verified must be true")
        extension = enrollment.get("extension")
        if isinstance(extension, dict):
            if extension.get("identity_verified") is not True:
                errors.append("enrollment.extension.identity_verified must be true")
            if extension.get("host_origin_matches") is not True:
                errors.append("enrollment.extension.host_origin_matches must be true")
            if extension.get("distribution") not in {"stable_unpacked", "web_store", "managed"}:
                errors.append("enrollment.extension.distribution is unsupported")

    browser = descriptor.get("browser")
    if not isinstance(browser, dict) or browser.get("status") not in {"already-running", "already_running"}:
        errors.append("descriptor must identify an already-running browser")
    elif browser.get("launch") is not False or browser.get("download") is not False or browser.get("cdp_url_used") is not False:
        errors.append("browser launch, download, and CDP use must all be false")

    safety = descriptor.get("safety")
    if not isinstance(safety, dict) or safety.get("user_tab_preserved") is not True or safety.get("focus_theft") is not False:
        errors.append("descriptor must prove user-tab and focus safety")

    evidence = descriptor.get("evidence")
    if evidence is not None:
        if not isinstance(evidence, dict):
            errors.append("descriptor evidence must be an object")
        elif evidence.get("executed") is False and evidence.get("status") == "descriptor_only":
            pass
        elif evidence.get("executed") is not True or evidence.get("status") != "live_passed":
            errors.append("live evidence must be executed and live_passed")
        else:
            scenarios = evidence.get("scenarios")
            if not isinstance(scenarios, list) or len(scenarios) != len(REQUIRED_LIVE_SCENARIOS) or any(not isinstance(item, dict) for item in scenarios):
                errors.append("live evidence must include exactly all ten scenarios")
            else:
                names = tuple(item.get("name") for item in scenarios)
                if names != REQUIRED_LIVE_SCENARIOS or any(item.get("status") != "live_passed" for item in scenarios):
                    errors.append("live scenario evidence is incomplete, duplicated, or skipped")
    return sorted(set(errors))


def load_enrolled_descriptor(path_value: str | None) -> dict[str, Any] | None:
    if not path_value:
        return None
    path = Path(path_value).expanduser()
    if path.is_dir():
        path = path / "enrollment.json"
    if path.is_symlink():
        return None
    try:
        if path.stat().st_size > MAX_REPORT_BYTES:
            return None
        path = path.resolve(strict=True)
    except OSError:
        return None
    if path.parent == path or not path.is_file():
        return None
    try:
        descriptor = read_json(path)
    except ProbeError:
        return None
    if not isinstance(descriptor, dict) or _descriptor_errors(descriptor):
        return None
    descriptor_browser = descriptor["browser"]
    descriptor_safety = descriptor["safety"]
    descriptor_enrollment = {
        component: {
            "enrolled": False,
            "observed": False,
            "current_run": False,
            "source": "descriptor",
            "status": "descriptor_only",
        }
        for component in ENROLLMENT_COMPONENTS
    }
    # Descriptors describe setup only. Embedded live claims are discarded.
    return redact_for_persistence(
        {
            "status": "harness_supplied",
            "descriptor_version": DESCRIPTOR_SCHEMA_VERSION,
            "enrollment": descriptor_enrollment,
            "browser": {
                "status": descriptor_browser["status"],
                "observed": False,
                "current_run": False,
                "launch": False,
                "download": False,
                "cdp_url_used": False,
            },
            "profile_scope": "existing_user_profile",
            "safety": {
                "user_tab_preserved": descriptor_safety["user_tab_preserved"],
                "focus_theft": descriptor_safety["focus_theft"],
            },
            "executed": False,
            "current_run": False,
            "live_passed": False,
            "evidence_status": "descriptor_only",
            "provenance": "descriptor",
            "operator_claims_used": False,
            "scenarios": _initial_scenarios(),
            "release_gates": None,
        }
    )


# Legacy test helper only. The live execution path deliberately never calls
# this loader; descriptors cannot establish live evidence.
def load_harness(path_value: str | None) -> dict[str, Any] | None:
    return load_enrolled_descriptor(path_value)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--headed", action="store_true", help="run the existing-Chrome host-backed lane without launching Chrome")
    result.add_argument("--require-live", action="store_true", help="same as --headed; fail closed unless the live host lane passes")
    result.add_argument("--dry-run", action="store_true", help="validate local fixtures without any live lane")
    result.add_argument("--harness", help="legacy input ignored; descriptors never establish live evidence")
    result.add_argument("--cli", dest="cli_path", help="existing direct CLI executable; never interpreted through a shell")
    result.add_argument("--host-probe", dest="host_probe_path", help="real existing-Chrome host probe executable; never interpreted through a shell")
    result.add_argument("--state-dir", help="optional direct-CLI host state directory")
    result.add_argument("--cli-timeout", type=float, default=MAX_CLI_TIMEOUT_SECONDS)
    result.add_argument("--operator-checkpoint", action="store_true", help="use bounded fixed-token operator checkpoints for unautomated lifecycle steps")
    result.add_argument("--checkpoint-timeout", type=float, default=30.0)
    result.add_argument("--spaces", type=int, default=2)
    result.add_argument("--agents", type=int, default=2)
    result.add_argument("--artifact-dir", default="artifacts/p0-coexistence")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.require_live:
        args.headed = True
    if args.dry_run:
        if args.headed:
            print("existing-Chrome probe error: --dry-run cannot be combined with --headed or --require-live", file=sys.stderr)
            return 2
        args.headed = False
    if args.spaces < 0 or args.agents < 0:
        print("existing-Chrome probe error: counts must be non-negative", file=sys.stderr)
        return 2

    try:
        artifact_dir = safe_artifact_dir(args.artifact_dir)
        validate_artifact_budget(artifact_dir)
        manifest = validate_manifest()
        contract = validate_scenario(args.spaces, args.agents)
        if not math.isfinite(args.cli_timeout) or args.cli_timeout <= 0 or args.cli_timeout > MAX_CLI_TIMEOUT_SECONDS:
            raise ProbeError("--cli-timeout must be positive, finite, and bounded")
        if not math.isfinite(args.checkpoint_timeout) or args.checkpoint_timeout <= 0 or args.checkpoint_timeout > MAX_OPERATOR_CHECKPOINT_SECONDS:
            raise ProbeError("--checkpoint-timeout must be positive, finite, and bounded")
    except ProbeError as exc:
        print(f"existing-Chrome probe error: {exc}", file=sys.stderr)
        return 2

    live: dict[str, Any] = {
        "requested": bool(args.headed),
        "required": bool(args.headed),
        "status": "not_requested",
        "executed": False,
        "evidence_status": "not_requested",
    }
    status = "offline_passed"
    if args.headed:
        live = orchestrate_live(
            cli_path=args.cli_path,
            state_dir=args.state_dir,
            cli_timeout=args.cli_timeout,
            host_probe_path=args.host_probe_path,
            operator_checkpoint=bool(
                args.operator_checkpoint
                or os.environ.get(CHECKPOINT_ENV, "").strip().lower() in {"1", "true", "yes"}
            ),
            checkpoint_timeout=args.checkpoint_timeout,
        )
        status = str(live.get("status", "live_required_unavailable"))

    report = safe_report(mode="headed" if args.headed else "offline", manifest=manifest, contract=contract, live=live)
    status = str(report.get("status", status))
    add_envelope(report, kind="existing-chrome-coexistence")
    rendered = json.dumps(report, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    encoded = rendered.encode("utf-8")
    if len(encoded) > MAX_REPORT_BYTES:
        print("existing-Chrome probe error: redacted report exceeds the artifact budget", file=sys.stderr)
        return 2
    try:
        artifact_dir.mkdir(parents=True, exist_ok=True)
        write_json_atomic(artifact_dir / "report.json", report, max_bytes=MAX_REPORT_BYTES)
    except (OSError, ValueError) as exc:
        print(f"existing-Chrome probe error: cannot write bounded report: {exc.__class__.__name__}", file=sys.stderr)
        return 2
    print(rendered, end="")
    return 0 if (not args.headed or status == "live_passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
