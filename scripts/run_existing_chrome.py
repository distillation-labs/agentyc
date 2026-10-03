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
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
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
MAX_INVENTORY_POLL_ATTEMPTS = 5
INVENTORY_POLL_DELAY_SECONDS = 0.05
MAX_FIXTURE_SERVER_SHUTDOWN_SECONDS = 2.0
RESTART_SCENARIOS = (
    "worker-restart-recovery",
    "host-restart-recovery",
    "chrome-restart-recovery",
    "extension-update-recovery",
)

DESCRIPTOR_SCHEMA_VERSION = 2
DIRECT_CLI_ENV = "AGENTYC_CLI"
DIRECT_CLI_DEFAULT = ROOT / "target" / "debug" / "agentyc"
HOST_PROBE_ENV = "AGENTYC_EXISTING_CHROME_PROBE"
HOST_PROBE_DEFAULT = ROOT / "target" / "debug" / "agentyc-existing-chrome-probe"
CHECKPOINT_ENV = "AGENTYC_EXISTING_CHROME_OPERATOR_CHECKPOINT"
PROFILE_BINDING_ENV = "AGENTYC_PROFILE_BINDING"
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
    r"(?:token|secret|password|passwd|cookie|authorization|credential|private[_-]?key|websocket[_-]?url|control[_-]?ticket)",
    re.IGNORECASE,
)
_RAW_ID_KEY = re.compile(
    r"(?:raw[_-]?id|cdp[_-]?id|backend[_-]?node[_-]?id|target[_-]?id|session[_-]?id|tab[_-]?id|group[_-]?id|profile(?:[_-]?(?:binding|instance))?[_-]?id)",
    re.IGNORECASE,
)
_SECRET_TEXT = re.compile(r"(?i)(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]+")
_RAW_URL = re.compile(r"(?i)(?:https?|wss?)://[^\s\"'`,;)}\]]+")
_NETWORK = re.compile(r"(?:https?|wss?)://|\b(?:fetch|XMLHttpRequest|WebSocket)\b", re.IGNORECASE)
_ABSOLUTE_PATH = re.compile(r"(?i)(?:/(?:Users|home|private|tmp|var|etc|opt|Applications|workspace)(?:/[^\s\"'`,;)}\]]*)?|[A-Za-z]:[\\\\/][^\s\"'`,;)}\]]*)")
_BROWSER_ID = re.compile(r"^[a-p]{32}$")
_FILE_URL = re.compile(r"(?i)file://[^\s\"'`,;)}\]]+")


class ProbeError(ValueError):
    """A deterministic input or fixture contract failure."""


class _FixtureRequestHandler(SimpleHTTPRequestHandler):
    """Serve the checked-in fixture directory without emitting request paths."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(FIXTURE_ROOT), **kwargs)

    def log_message(self, format: str, *_args: Any) -> None:
        del format


@contextmanager
def fixture_server() -> Iterator[str]:
    """Serve only the local fixture root on a bounded loopback listener."""
    if FIXTURE_ROOT.is_symlink() or not FIXTURE_ROOT.is_dir():
        raise ProbeError("fixture root is not a local directory")
    try:
        server = ThreadingHTTPServer(("127.0.0.1", 0), _FixtureRequestHandler)
    except OSError as exc:
        raise ProbeError("loopback fixture server is unavailable") from exc
    server.daemon_threads = True
    thread = threading.Thread(
        target=server.serve_forever,
        name="agentyc-p0-fixtures",
        daemon=True,
    )
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        yield base_url
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=MAX_FIXTURE_SERVER_SHUTDOWN_SECONDS)
        if thread.is_alive():
            raise ProbeError("loopback fixture server did not stop within the bound")


def _fixture_url(base_url: str, fixture_name: str) -> str:
    expected = EXPECTED_FIXTURES.get(fixture_name)
    if expected is None:
        raise ProbeError("unknown local fixture")
    return f"{base_url}/{expected['file']}"


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

    def __init__(
        self,
        executable: str,
        *,
        state_dir: str | None,
        timeout: float,
        profile_binding_id: str | None = None,
    ) -> None:
        if not math.isfinite(timeout) or timeout <= 0 or timeout > MAX_CLI_TIMEOUT_SECONDS:
            raise ProbeError("direct CLI timeout is outside the bounded range")
        self.executable = executable
        self.state_dir = state_dir.strip() if isinstance(state_dir, str) and state_dir.strip() else None
        self.timeout = timeout
        self.environment = os.environ.copy()
        configured_profile = (
            profile_binding_id
            if profile_binding_id is not None
            else self.environment.get(PROFILE_BINDING_ENV)
        )
        if isinstance(configured_profile, str):
            configured_profile = configured_profile.strip() or None
        if configured_profile is not None and not _logical_id(configured_profile, "profile_"):
            raise ProbeError("profile binding ID is invalid")
        self.profile_binding_id = configured_profile

    def call(self, command: list[str], *, principal: str) -> DirectCliResponse:
        if principal not in LIVE_PRINCIPALS:
            return DirectCliResponse("invalid_request", reason_code="principal_not_allowed")
        if not command or any(not isinstance(item, str) or not item for item in command):
            return DirectCliResponse("invalid_request", reason_code="command_invalid")
        unsafe_options = {
            "--offline",
            "--cdp-url",
            "--websocket-url",
            "--target-id",
            "--session-id",
            "--tab-id",
            "--profile-binding-id",
        }
        if any(item.split("=", 1)[0] in unsafe_options for item in command):
            return DirectCliResponse("invalid_request", reason_code="unsafe_cli_option")

        argv = [self.executable]
        if self.state_dir is not None:
            argv.extend(("--state-dir", self.state_dir))
        if self.profile_binding_id is not None:
            argv.extend(("--profile-binding-id", self.profile_binding_id))
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

    profile_ids: list[str] = []
    containers: list[Any] = [result, bridge]
    for key in ("profile", "bridge_status", "extension"):
        value = result.get(key)
        if isinstance(value, dict):
            containers.append(value)
    for container in containers:
        if not isinstance(container, dict) or "profile_instance_id" not in container:
            continue
        value = container.get("profile_instance_id")
        if not isinstance(value, str) or not _logical_id(value, "profile_"):
            return None, "host_status_profile_instance_id_invalid"
        profile_ids.append(value)
    if not profile_ids:
        return None, "host_status_profile_instance_id_missing"
    if len(set(profile_ids)) != 1:
        return None, "host_status_profile_instance_id_mismatch"

    observation: dict[str, Any] = {
        "lifecycle": "ready",
        "bridge_connected": True,
        "test_seam": False,
        "capabilities": tuple(capabilities),
        "broker_epoch": broker_epoch,
        "profile_instance_id": profile_ids[0],
        "profile_binding_observed": True,
    }
    for key in (
        "connection_epoch",
        "worker_instance_epoch",
        "browser_session_epoch",
    ):
        value = result.get(key)
        if value is not None:
            if not _positive_integer(value):
                return None, f"host_status_{key}_invalid"
            observation[key] = value
    epochs = result.get("epochs")
    if isinstance(epochs, dict):
        for key in ("connection_epoch", "worker_instance_epoch", "browser_session_epoch"):
            value = epochs.get(key)
            if value is not None:
                if not _positive_integer(value):
                    return None, f"host_status_{key}_invalid"
                observation[key] = value
    extension_version = result.get("extension_version")
    if extension_version is not None:
        if not isinstance(extension_version, str) or not extension_version or len(extension_version) > 128:
            return None, "host_status_extension_version_invalid"
        observation["extension_version"] = extension_version
    profile_scope = result.get("profile_scope")
    profile = result.get("profile")
    if isinstance(profile, dict) and profile.get("scope") is not None:
        profile_scope = profile.get("scope")
    if profile_scope is not None and profile_scope != "existing_user_profile":
        return None, "host_status_profile_scope_invalid"
    explicit_bound = [result.get("profile_bound")]
    if isinstance(profile, dict):
        explicit_bound.extend((profile.get("bound"), profile.get("enrolled")))
    if any(value is False for value in explicit_bound):
        return None, "host_status_profile_not_bound"
    # A valid profile_instance_id is the host's current binding observation. The
    # scope is fixed by this runner's existing-user-profile lane when omitted by
    # the direct status projection; it is not inferred from a descriptor.
    observation["profile_scope"] = "existing_user_profile"
    observation["profile_bound"] = True
    return observation, None


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


def _managed_page_create_result(
    response: DirectCliResponse,
    expected_space: str,
) -> str | None:
    """Accept only a host result that describes a managed logical page."""
    if response.transport != "complete" or response.ok is not True or not isinstance(response.result, dict):
        return None
    result = response.result
    page_id = result.get("page_id")
    if result.get("space_id") != expected_space or not _logical_id(page_id, "page_"):
        return None
    page = result.get("page")
    if not isinstance(page, dict):
        return None
    if (
        page.get("page_id") != page_id
        or page.get("space_id") != expected_space
        or page.get("lifecycle") not in {"managed", "Managed"}
        or page.get("ownership") not in {"agent", "Agent"}
        or page.get("binding") not in {"bound", "Bound"}
    ):
        return None
    return page_id


def _inventory_epochs(result: dict[str, Any], records: list[dict[str, Any]]) -> dict[str, Any]:
    """Extract only bounded epoch observations; never retain opaque identities."""
    epochs: dict[str, Any] = {}
    candidates: list[Any] = [result]
    for key in ("epochs", "host_epochs", "extension_epochs", "observation"):
        value = result.get(key)
        if isinstance(value, dict):
            candidates.append(value)
    for candidate in candidates:
        for key in (
            "broker_epoch",
            "connection_epoch",
            "worker_instance_epoch",
            "browser_session_epoch",
        ):
            value = candidate.get(key)
            if _positive_integer(value):
                epochs[key] = value
        extension_version = candidate.get("extension_version")
        if isinstance(extension_version, str) and extension_version and len(extension_version) <= 128:
            epochs["extension_version"] = extension_version
    page_epochs = {
        record.get("browser_session_epoch")
        for record in records
        if _positive_integer(record.get("browser_session_epoch"))
    }
    if len(page_epochs) == 1:
        epochs.setdefault("browser_session_epoch", next(iter(page_epochs)))
    return epochs


def _user_focus_observation(result: dict[str, Any]) -> dict[str, Any] | None:
    """Require a live unmanaged active record, not a claimed boolean."""
    candidates: list[dict[str, Any]] = []
    raw_collections = [
        result.get("unmanaged_pages"),
        result.get("user_tabs"),
        result.get("user_tab_records"),
    ]
    pages = result.get("pages")
    if isinstance(pages, list):
        raw_collections.append(pages)
    for collection in raw_collections:
        if not isinstance(collection, list):
            continue
        for record in collection:
            if not isinstance(record, dict):
                continue
            if record.get("ownership") not in {"unmanaged", "user"}:
                continue
            if record.get("space_id") not in (None, ""):
                continue
            if record.get("active") is not True or record.get("incognito") is True:
                continue
            tab_hint = record.get("tab_hint")
            if isinstance(tab_hint, str) and 1 < len(tab_hint) <= 128:
                candidates.append(record)
    if not candidates:
        return None
    focus = result.get("active_focus")
    if focus is None:
        focus = result.get("focus")
    focus_hint: str | None = None
    if isinstance(focus, dict):
        focus_hint = focus.get("tab_hint")
        if focus.get("ownership") not in (None, "unmanaged", "user"):
            return None
        if focus.get("active") is False:
            return None
    elif isinstance(focus, str):
        focus_hint = focus
    if focus_hint is not None and not isinstance(focus_hint, str):
        return None
    active_hints = {record["tab_hint"] for record in candidates}
    if focus_hint is not None and focus_hint not in active_hints:
        return None
    selected_hint = focus_hint or min(active_hints)
    selected = next(record for record in candidates if record["tab_hint"] == selected_hint)
    return {
        "observed": True,
        "unmanaged": True,
        "active": True,
        "tab_hint": selected["tab_hint"],
        "active_focus_hint": focus_hint or selected["tab_hint"],
    }


def _visual_group_observation(
    result: dict[str, Any],
    expected_spaces: set[str],
) -> dict[str, Any] | None:
    """Require explicit extension group observations for every managed space."""
    groups: list[dict[str, Any]] = []
    for key in ("groups", "visual_groups", "group_evidence"):
        value = result.get(key)
        if isinstance(value, list):
            groups.extend(item for item in value if isinstance(item, dict))
    pages = result.get("pages")
    if isinstance(pages, list):
        for page in pages:
            if not isinstance(page, dict):
                continue
            group = page.get("visual_group")
            if isinstance(group, dict):
                groups.append(group)
            elif page.get("visual_group_present") is True:
                groups.append(
                    {
                        "space_id": page.get("space_id"),
                        "present": True,
                        "drift": page.get("visual_group_drift", False),
                        "member_count": page.get("visual_group_member_count", 1),
                    }
                )
    observed_spaces: set[str] = set()
    member_count = 0
    for group in groups:
        space_id = group.get("space_id")
        if space_id not in expected_spaces:
            continue
        present = group.get("present") is True or group.get("status") in {"present", "passed"}
        drift = group.get("drift") is True
        members = group.get("member_count")
        if present and not drift and type(members) is int and members > 0:
            observed_spaces.add(space_id)
            member_count += members
    if observed_spaces != expected_spaces:
        return None
    return {
        "observed": True,
        "spaces": len(observed_spaces),
        "member_count": member_count,
    }


def _managed_inventory_observation(
    response: DirectCliResponse,
    expected_space: str,
    expected_pages: dict[str, int],
    *,
    require_user_focus: bool = True,
) -> dict[str, Any] | None:
    """Validate the fresh extension-backed inventory for one logical space."""
    if response.transport != "complete" or response.ok is not True or not isinstance(response.result, dict):
        return None
    result = response.result
    pages = result.get("pages")
    if result.get("space_id") != expected_space or not isinstance(pages, list) or len(pages) > 256:
        return None
    records: list[dict[str, Any]] = []
    by_page: dict[str, dict[str, Any]] = {}
    for page in pages:
        if not isinstance(page, dict):
            continue
        page_id = page.get("page_id")
        if not isinstance(page_id, str) or not _logical_id(page_id, "page_") or page.get("space_id") != expected_space:
            continue
        if page_id in by_page:
            return None
        by_page[page_id] = page
    for page_id, lease_epoch in expected_pages.items():
        record = by_page.get(page_id)
        if record is None:
            return None
        if (
            record.get("ownership") not in {"agent", "Agent"}
            or record.get("lifecycle") not in {"managed", "Managed"}
            or record.get("binding_state") not in {"bound", "Bound"}
            or record.get("lease_epoch") != lease_epoch
            or record.get("active") is not False
            or not _positive_integer(record.get("target_generation"))
            or not _positive_integer(record.get("browser_session_epoch"))
        ):
            return None
        if record.get("extension_backed") is False or record.get("browser_backed") is False:
            return None
        records.append(record)
    focus = _user_focus_observation(result)
    if require_user_focus and focus is None:
        return None
    groups = _visual_group_observation(result, {expected_space})
    if groups is None:
        return None
    safety = result.get("safety")
    safety_measured = (
        isinstance(safety, dict)
        and safety.get("measurement_status") == "measured_live"
        and safety.get("current_run") is True
        and all(type(safety.get(key)) is int for key in ("user_tab_closes", "focus_theft"))
    )
    return {
        "managed_count": len(records),
        "extension_backed": True,
        "visual_groups": groups,
        "user_focus": focus,
        "epochs": _inventory_epochs(result, records),
        "safety_measured": safety_measured,
        "recovery_observed": result.get("recovery_observed") is True,
    }


def _cleanup_inventory_observation(
    response: DirectCliResponse,
    expected_space: str,
    expected_page_ids: set[str],
) -> dict[str, Any] | None:
    if response.transport != "complete" or response.ok is not True or not isinstance(response.result, dict):
        return None
    result = response.result
    pages = result.get("pages")
    if result.get("space_id") != expected_space or not isinstance(pages, list) or len(pages) > 256:
        return None
    if any(isinstance(page, dict) and page.get("page_id") in expected_page_ids for page in pages):
        return None
    groups = []
    for key in ("groups", "visual_groups", "group_evidence"):
        value = result.get(key)
        if isinstance(value, list):
            groups.extend(item for item in value if isinstance(item, dict))
    if any(
        group.get("space_id") == expected_space
        and (group.get("present") is True or group.get("status") in {"present", "passed"})
        for group in groups
    ):
        return None
    focus = _user_focus_observation(result)
    if focus is None:
        return None
    return {
        "managed_count": 0,
        "extension_backed": True,
        "visual_groups": {"observed": True, "spaces": 1, "member_count": 0},
        "user_focus": focus,
        "epochs": _inventory_epochs(result, []),
    }


def _action_execute_result(
    response: DirectCliResponse,
    expected_space: str,
    expected_page: str,
    expected_action: str,
    expected_lease: int,
) -> bool:
    if response.transport != "complete" or response.ok is not True or not isinstance(response.result, dict):
        return False
    result = response.result
    receipt = result.get("receipt")
    if not isinstance(receipt, dict):
        return False
    return not (
        result.get("action_id") != expected_action
        or receipt.get("action_id") != expected_action
        or receipt.get("space_id") != expected_space
        or receipt.get("page_id") != expected_page
        or receipt.get("lease_epoch") != expected_lease
        or receipt.get("status") not in {"succeeded", "Succeeded"}
        or receipt.get("unknown") is True
        or receipt.get("completion_source") not in (None, "extension", "Extension")
        or receipt.get("dispatch_state") in {"not_dispatched", "NotDispatched"}
    )


def _snapshot_result(
    response: DirectCliResponse,
    expected_space: str,
    expected_page: str,
) -> bool:
    if response.transport != "complete" or response.ok is not True or not isinstance(response.result, dict):
        return False
    result = response.result
    snapshot = result.get("snapshot")
    if not isinstance(snapshot, dict):
        snapshot = result
    return (
        snapshot.get("space_id") == expected_space
        and snapshot.get("page_id") == expected_page
        and isinstance(snapshot.get("snapshot_hash"), str)
        and _positive_integer(snapshot.get("document_generation"))
    )


def _action_ids(label: str) -> tuple[str, str, str]:
    suffix = f"{label}-{uuid.uuid4().hex[:12]}"
    return f"req_{suffix}", f"action_{suffix}", f"idem_{suffix}"


def _browser_receipt(
    operation: str,
    response: DirectCliResponse,
    *,
    expected_rejection: bool = False,
) -> dict[str, Any]:
    record = _receipt(operation, response, expected_rejection=expected_rejection)
    record.update({
        "source": "browser_current_run",
        "observed": True,
        "current_run": True,
        "browser_observed": True,
    })
    return record


def _append_browser_receipt(
    receipts: list[dict[str, Any]],
    operation: str,
    response: DirectCliResponse,
    *,
    expected_rejection: bool = False,
) -> None:
    if len(receipts) < MAX_LIVE_RECEIPTS and not any(item.get("operation") == operation for item in receipts):
        receipts.append(_browser_receipt(operation, response, expected_rejection=expected_rejection))


def _focus_is_unchanged(before: dict[str, Any] | None, after: dict[str, Any] | None) -> bool:
    return bool(
        isinstance(before, dict)
        and isinstance(after, dict)
        and before.get("observed") is True
        and after.get("observed") is True
        and before.get("unmanaged") is True
        and after.get("unmanaged") is True
        and before.get("active") is True
        and after.get("active") is True
        and before.get("tab_hint") == after.get("tab_hint")
        and before.get("active_focus_hint") == after.get("active_focus_hint")
    )


def _set_browser_scenario(
    scenarios: list[dict[str, Any]],
    name: str,
    reason_code: str,
    receipt_refs: tuple[str, ...],
) -> None:
    for scenario in scenarios:
        if scenario.get("name") == name:
            scenario["status"] = "live_passed"
            scenario["observation"] = {
                "source": "browser_current_run",
                "observed": True,
                "current_run": True,
                "browser_observed": True,
                "reason_code": reason_code,
                "receipt_refs": list(receipt_refs),
            }
            return


def _checkpoint_epoch_transition(
    name: str,
    before_host: dict[str, Any],
    after_host: dict[str, Any],
    before_inventory: dict[str, Any],
    after_inventory: dict[str, Any],
) -> bool:
    before = dict(before_host)
    before.update(before_inventory.get("epochs", {}))
    after = dict(after_host)
    after.update(after_inventory.get("epochs", {}))
    if name == "worker-restart-recovery":
        return (
            _positive_integer(before.get("worker_instance_epoch"))
            and _positive_integer(after.get("worker_instance_epoch"))
            and after["worker_instance_epoch"] > before["worker_instance_epoch"]
            and _positive_integer(before.get("browser_session_epoch"))
            and after.get("browser_session_epoch") == before["browser_session_epoch"]
        )
    if name == "host-restart-recovery":
        return (
            _positive_integer(before.get("broker_epoch"))
            and _positive_integer(after.get("broker_epoch"))
            and after["broker_epoch"] > before["broker_epoch"]
        )
    if name == "chrome-restart-recovery":
        return (
            _positive_integer(before.get("browser_session_epoch"))
            and _positive_integer(after.get("browser_session_epoch"))
            and after["browser_session_epoch"] > before["browser_session_epoch"]
        )
    if name == "extension-update-recovery":
        version_changed = (
            isinstance(before.get("extension_version"), str)
            and isinstance(after.get("extension_version"), str)
            and before["extension_version"] != after["extension_version"]
        )
        worker_changed = (
            _positive_integer(before.get("worker_instance_epoch"))
            and _positive_integer(after.get("worker_instance_epoch"))
            and after["worker_instance_epoch"] > before["worker_instance_epoch"]
        )
        return bool(version_changed or worker_changed) and _positive_integer(after.get("browser_session_epoch"))
    return False


def _poll_managed_inventory(
    cli: DirectCli,
    spaces: list[dict[str, Any]],
    *,
    receipts: list[dict[str, Any]],
    transport_receipts: list[dict[str, Any]],
    phase: str,
    require_user_focus: bool = True,
) -> dict[str, Any]:
    last_reason = "inventory_not_observed"
    observations: dict[str, dict[str, Any]] = {}
    receipt_refs: list[str] = []
    focus: dict[str, Any] | None = None
    for attempt in range(1, MAX_INVENTORY_POLL_ATTEMPTS + 1):
        observations = {}
        receipt_refs = []
        focus = None
        all_valid = True
        for space in spaces:
            lease_epoch = space.get("lease_epoch")
            page_id = space.get("page_id")
            if not isinstance(lease_epoch, int) or not isinstance(page_id, str):
                all_valid = False
                last_reason = "managed_page_identity_missing"
                continue
            response = cli.call(
                ["page", "inventory", "--space-id", space["space_id"]],
                principal=space["principal"],
            )
            operation = f"page.inventory.{phase}.{space['label']}.{attempt}"
            _append_receipt(transport_receipts, operation, response)
            observation = _managed_inventory_observation(
                response,
                space["space_id"],
                {page_id: lease_epoch},
                require_user_focus=require_user_focus,
            )
            if observation is None:
                all_valid = False
                last_reason = "managed_inventory_or_visual_group_invalid"
                continue
            observations[space["label"]] = observation
            if focus is None:
                focus = observation.get("user_focus")
            elif observation.get("user_focus") is not None and not _focus_is_unchanged(focus, observation["user_focus"]):
                all_valid = False
                last_reason = "active_focus_observation_disagrees"
            receipt_operation = f"browser.inventory.{phase}.{space['label']}.{attempt}"
            _append_browser_receipt(receipts, receipt_operation, response)
            receipt_refs.append(receipt_operation)
        if all_valid and len(observations) == len(spaces) and (not require_user_focus or focus is not None):
            epochs: dict[str, Any] = {}
            for observation in observations.values():
                epochs.update(observation.get("epochs", {}))
            return {
                "observed": True,
                "observations": observations,
                "receipt_refs": tuple(receipt_refs),
                "user_focus": focus,
                "epochs": epochs,
                "safety_measured": bool(observations) and all(
                    observation.get("safety_measured") is True
                    for observation in observations.values()
                ),
                "recovery_observed": bool(observations) and all(
                    observation.get("recovery_observed") is True
                    for observation in observations.values()
                ),
                "reason_code": "managed_inventory_observed",
            }
        if attempt < MAX_INVENTORY_POLL_ATTEMPTS:
            time.sleep(INVENTORY_POLL_DELAY_SECONDS)
    return {
        "observed": False,
        "observations": observations,
        "receipt_refs": tuple(receipt_refs),
        "user_focus": focus,
        "epochs": {},
        "reason_code": last_reason,
    }


def _poll_cleanup_inventory(
    cli: DirectCli,
    spaces: list[dict[str, Any]],
    *,
    receipts: list[dict[str, Any]],
    transport_receipts: list[dict[str, Any]],
) -> dict[str, Any]:
    receipt_refs: list[str] = []
    observations: dict[str, dict[str, Any]] = {}
    focus: dict[str, Any] | None = None
    for attempt in range(1, MAX_INVENTORY_POLL_ATTEMPTS + 1):
        receipt_refs = []
        observations = {}
        valid = True
        focus = None
        for space in spaces:
            page_id = space.get("page_id")
            if not isinstance(page_id, str):
                valid = False
                continue
            response = cli.call(
                ["page", "inventory", "--space-id", space["space_id"]],
                principal=space["principal"],
            )
            operation = f"page.inventory.cleanup.{space['label']}.{attempt}"
            _append_receipt(transport_receipts, operation, response)
            observation = _cleanup_inventory_observation(response, space["space_id"], {page_id})
            if observation is None:
                valid = False
                continue
            observations[space["label"]] = observation
            current_focus = observation.get("user_focus")
            if focus is None:
                focus = current_focus
            elif not _focus_is_unchanged(focus, current_focus):
                valid = False
            receipt_operation = f"browser.inventory.cleanup.{space['label']}.{attempt}"
            _append_browser_receipt(receipts, receipt_operation, response)
            receipt_refs.append(receipt_operation)
        if valid and len(observations) == len(spaces) and focus is not None:
            return {
                "observed": True,
                "receipt_refs": tuple(receipt_refs),
                "user_focus": focus,
                "observations": observations,
            }
        if attempt < MAX_INVENTORY_POLL_ATTEMPTS:
            time.sleep(INVENTORY_POLL_DELAY_SECONDS)
    return {"observed": False, "receipt_refs": (), "user_focus": focus, "observations": observations}


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


def _return_control_result(
    response: DirectCliResponse,
    expected_space: str,
    expected_epoch: int,
) -> tuple[int, dict[str, Any]] | None:
    """Validate return control and retain only a safe, in-memory ticket envelope."""
    if response.transport != "complete" or response.ok is not True or not isinstance(response.result, dict):
        return None
    result = response.result
    released_epoch = result.get("released_epoch")
    fence_epoch = result.get("fence_epoch")
    ticket = result.get("control_ticket")
    if (
        result.get("space_id") != expected_space
        or result.get("lifecycle") != "user_owned"
        or released_epoch != expected_epoch
        or type(released_epoch) is not int
        or released_epoch <= 0
        or type(fence_epoch) is not int
        or fence_epoch <= 0
        or not isinstance(ticket, dict)
    ):
        return None
    assert isinstance(released_epoch, int)
    assert isinstance(fence_epoch, int)
    allowed = {"space_id", "broker_epoch", "fence_epoch", "token", "opaque", "in_memory"}
    if set(ticket) - allowed:
        return None
    ticket_broker_epoch = ticket.get("broker_epoch")
    token = ticket.get("token")
    if (
        ticket.get("space_id") != expected_space
        or type(ticket_broker_epoch) is not int
        or ticket_broker_epoch <= 0
        or ticket.get("fence_epoch") != fence_epoch
        or not isinstance(token, str)
        or not 8 <= len(token) <= 128
        or re.fullmatch(r"[A-Za-z0-9._:-]+", token) is None
    ):
        return None
    assert isinstance(ticket_broker_epoch, int)
    # The ticket is retained only in memory until the paired reclaim call; it
    # is never copied into receipts or persisted artifacts.
    return released_epoch, {
        "space_id": expected_space,
        "broker_epoch": ticket_broker_epoch,
        "fence_epoch": fence_epoch,
        "token": token,
    }


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
    source: str = "direct_cli",
    browser_observed: bool = False,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "operation": operation,
        "source": source,
        "current_run": True,
        "observed": response.transport == "complete",
        "browser_observed": browser_observed,
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
    source: str = "direct_cli",
    browser_observed: bool = False,
) -> None:
    if len(receipts) < MAX_LIVE_RECEIPTS:
        receipts.append(
            _receipt(
                operation,
                response,
                expected_rejection=expected_rejection,
                source=source,
                browser_observed=browser_observed,
            )
        )


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


def _observed_host_enrollment(
    *,
    source: str,
    current_run: bool,
    profile_bound: bool = False,
) -> dict[str, dict[str, Any]]:
    evidence = _empty_enrollment(source=source, current_run=False)
    if current_run:
        for component in ("host", "extension"):
            evidence[component].update({"observed": True, "current_run": True, "status": "connected"})
        if profile_bound:
            evidence["profile"].update({
                "enrolled": True,
                "observed": True,
                "current_run": True,
                "status": "bound",
            })
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
        "profile_binding_observed": False,
        "reason": "no enrolled existing host/extension connection was observed; direct CLI preflight is unavailable",
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
    profile_binding_id: str | None = None,
    host_probe_path: str | None = None,
) -> dict[str, Any]:
    """Run only the enrolled existing-host/extension lane through the direct CLI."""
    del host_probe_path  # Host probes do not contain browser observations and are never used here.
    if _fake_host_requested():
        return _live_unavailable("fake_host_environment_forbidden")
    executable, resolution = resolve_direct_cli(cli_path)
    if executable is None:
        return _live_unavailable(resolution, cli_reason=resolution)

    try:
        cli = DirectCli(
            executable,
            state_dir=state_dir,
            timeout=cli_timeout,
            profile_binding_id=profile_binding_id,
        )
    except ProbeError:
        return _live_unavailable("profile_binding_id_invalid", cli_reason=resolution)
    transport_receipts: list[dict[str, Any]] = []
    browser_receipts: list[dict[str, Any]] = []
    scenarios = _initial_scenarios()
    spaces: list[dict[str, Any]] = []
    failures: list[str] = []
    preflight = cli.call(["host", "status"], principal=LIVE_PRINCIPALS[0])
    _append_receipt(transport_receipts, "host.status.preflight", preflight)
    host_observation, host_reason = _host_status_observation(preflight)
    if host_observation is None:
        live = _live_unavailable(host_reason or "host_status_invalid", cli_reason=resolution)
        live.update(
            {
                "executed": preflight.transport == "complete",
                "current_run": True,
                "provenance": "direct_cli_current_run",
                "preflight": {
                    "observed": False,
                    "source": "direct_cli",
                    "executor_receipt": preflight.transport == "complete",
                    "reason_code": host_reason,
                    "profile_binding_observed": False,
                },
                "profile_binding_observed": False,
                "enrollment": _empty_enrollment(source="direct_cli_current_run"),
                "transport_receipts": transport_receipts,
            }
        )
        return live

    observed_profile_binding = host_observation.get("profile_instance_id")
    configured_profile_binding = cli.profile_binding_id
    if (
        not _logical_id(observed_profile_binding, "profile_")
        or configured_profile_binding is not None
        and configured_profile_binding != observed_profile_binding
    ):
        live = _live_unavailable("profile_binding_id_mismatch", cli_reason=resolution)
        live.update(
            {
                "executed": True,
                "current_run": True,
                "provenance": "direct_cli_current_run",
                "preflight": {
                    "observed": True,
                    "source": "direct_cli",
                    "profile_binding_observed": False,
                    "reason_code": "profile_binding_id_mismatch",
                },
                "profile_binding_observed": False,
                "enrollment": _empty_enrollment(source="direct_cli_current_run"),
                "transport_receipts": transport_receipts,
            }
        )
        return live
    try:
        # Every mutation, inventory, snapshot, and recovery call carries the
        # binding observed in this run's initial host.status response.
        cli = DirectCli(
            executable,
            state_dir=state_dir,
            timeout=cli_timeout,
            profile_binding_id=observed_profile_binding,
        )
    except ProbeError:
        return _live_unavailable("profile_binding_id_invalid", cli_reason=resolution)
    if "action" not in host_observation["capabilities"]:
        live = _live_unavailable("required_action_capability_missing", cli_reason=resolution)
        live.update(
            {
                "executed": True,
                "current_run": True,
                "provenance": "direct_cli_current_run",
                "preflight": {
                    "observed": True,
                    "source": "direct_cli",
                    "bridge": "extension",
                    "action_capability": False,
                },
                "enrollment": _observed_host_enrollment(
                    source="direct_cli_current_run",
                    current_run=True,
                    profile_bound=host_observation.get("profile_bound") is True,
                ),
                "transport_receipts": transport_receipts,
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
        "provenance": "existing_chrome_current_run",
        "descriptor_policy": "descriptors_not_accepted_as_live_evidence",
        "operator_claims_used": False,
        "host_probe_used": False,
        "profile_scope": "existing_user_profile" if host_observation.get("profile_scope") == "existing_user_profile" else "unobserved",
        "profile_binding_observed": host_observation.get("profile_binding_observed") is True,
        "preflight": {
            "observed": True,
            "source": "direct_cli",
            "bridge": "extension",
            "lifecycle": "ready",
            "action_capability": True,
            "direct_path_safe": True,
            "profile_observed": host_observation.get("profile_bound") is True,
            "profile_binding_observed": host_observation.get("profile_binding_observed") is True,
            "epochs": {
                key: host_observation[key]
                for key in ("broker_epoch", "connection_epoch", "worker_instance_epoch", "browser_session_epoch")
                if key in host_observation
            },
        },
        "browser": {
            "observed": False,
            "current_run": True,
            "launch": False,
            "download": False,
            "cdp_url_used": False,
            "attached": False,
        },
        "enrollment": _observed_host_enrollment(
            source="direct_cli_current_run",
            current_run=True,
            profile_bound=host_observation.get("profile_bound") is True,
        ),
        "scenarios": scenarios,
        "release_gates": {"eligible": False, "reason_code": "required_browser_observations_missing"},
        "_execution_token": _LIVE_EXECUTION_TOKEN,
    }
    initial_inventory: dict[str, Any] | None = None
    latest_inventory: dict[str, Any] | None = None
    baseline_focus: dict[str, Any] | None = None
    focus_checks: list[bool] = []
    cross_space_rejected = False
    stale_rejected = False
    return_ticket_json: str | None = None
    fixture_stack = ExitStack()

    try:
        fixture_base_url = fixture_stack.enter_context(fixture_server())
        fixture_urls = {
            "research": _fixture_url(fixture_base_url, "dynamic-feed"),
            "testing": _fixture_url(fixture_base_url, "small-form"),
        }
        for label, principal, page_label, title in (
            ("research", LIVE_PRINCIPALS[0], "results", "Agentyc research"),
            ("testing", LIVE_PRINCIPALS[1], "app", "Agentyc testing"),
        ):
            create = cli.call(["space", "create", "--label", label], principal=principal)
            _append_receipt(transport_receipts, f"space.create.{label}", create)
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
                "cleanup_inventory_ok": False,
            }
            spaces.append(space)
            claim = cli.call(["space", "claim", "--space-id", space_id], principal=principal)
            _append_receipt(transport_receipts, f"space.claim.{label}", claim)
            lease_epoch = _claim_result(claim, space_id)
            if lease_epoch is None:
                failures.append(f"{label}_space_claim_invalid")
                continue
            space["lease_epoch"] = lease_epoch
            page = cli.call(
                [
                    "page",
                    "create-managed",
                    "--space-id",
                    space_id,
                    "--lease-epoch",
                    str(lease_epoch),
                    "--label",
                    page_label,
                    "--title",
                    title,
                    "--url",
                    fixture_urls[label],
                ],
                principal=principal,
            )
            _append_receipt(transport_receipts, f"page.create-managed.{label}", page)
            page_id = _managed_page_create_result(page, space_id)
            if page_id is None:
                failures.append(f"{label}_managed_page_create_invalid")
            else:
                space["page_id"] = page_id

        if len(spaces) != 2 or not all(isinstance(space.get("lease_epoch"), int) and isinstance(space.get("page_id"), str) for space in spaces):
            failures.append("two_managed_pages_not_created")
        else:
            initial_inventory = _poll_managed_inventory(
                cli,
                spaces,
                receipts=browser_receipts,
                transport_receipts=transport_receipts,
                phase="initial",
            )
            latest_inventory = initial_inventory
            baseline_focus = initial_inventory.get("user_focus")
            if initial_inventory.get("observed"):
                focus_checks.append(baseline_focus is not None)
            if not initial_inventory.get("observed"):
                failures.append(initial_inventory.get("reason_code", "initial_inventory_invalid"))
            else:
                refs = initial_inventory.get("receipt_refs", ())
                if refs:
                    _set_browser_scenario(scenarios, "user-tab-preservation", "unmanaged_active_tab_observed", (refs[0],))
                    if len(refs) > 1:
                        _set_browser_scenario(scenarios, "two-space-isolation", "two_scoped_managed_inventories_observed", (refs[1],))

            if initial_inventory.get("observed"):
                for space in spaces:
                    snapshot = cli.call(
                        [
                            "snapshot",
                            "--space-id",
                            space["space_id"],
                            "--page-id",
                            space["page_id"],
                            "--lease-epoch",
                            str(space["lease_epoch"]),
                        ],
                        principal=space["principal"],
                    )
                    _append_receipt(transport_receipts, f"snapshot.{space['label']}", snapshot)
                    if _snapshot_result(snapshot, space["space_id"], space["page_id"]):
                        _append_browser_receipt(browser_receipts, f"browser.snapshot.{space['label']}", snapshot)
                    else:
                        failures.append(f"{space['label']}_snapshot_invalid")
            else:
                failures.append("snapshot_requires_managed_inventory")

            for space in spaces:
                request_id, action_id, idempotency_key = _action_ids(space["label"])
                action = cli.call(
                    [
                        "action",
                        "execute",
                        "--request-id",
                        request_id,
                        "--action-id",
                        action_id,
                        "--idempotency-key",
                        idempotency_key,
                        "--space-id",
                        space["space_id"],
                        "--page-id",
                        space["page_id"],
                        "--lease-epoch",
                        str(space["lease_epoch"]),
                        "--operation",
                        "screenshot",
                    ],
                    principal=space["principal"],
                )
                operation = f"browser.action.execute.{space['label']}"
                _append_receipt(transport_receipts, f"action.execute.{space['label']}", action)
                if _action_execute_result(action, space["space_id"], space["page_id"], action_id, space["lease_epoch"]):
                    _append_browser_receipt(browser_receipts, operation, action)
                else:
                    failures.append(f"{space['label']}_allowlisted_action_invalid")

            post_action_inventory = _poll_managed_inventory(
                cli,
                spaces,
                receipts=browser_receipts,
                transport_receipts=transport_receipts,
                phase="post-action",
            )
            latest_inventory = post_action_inventory
            if not post_action_inventory.get("observed"):
                failures.append("post_action_inventory_invalid")
            else:
                focus_checks.append(_focus_is_unchanged(baseline_focus, post_action_inventory.get("user_focus")))
                refs = post_action_inventory.get("receipt_refs", ())
                if refs:
                    _set_browser_scenario(scenarios, "focus-stability", "focus_preserved_after_allowlisted_actions", (refs[0],))

            first, second = spaces
            cross_request, cross_action, cross_idem = _action_ids("cross-space")
            cross_space = cli.call(
                [
                    "action",
                    "execute",
                    "--request-id",
                    cross_request,
                    "--action-id",
                    cross_action,
                    "--idempotency-key",
                    cross_idem,
                    "--space-id",
                    first["space_id"],
                    "--page-id",
                    first["page_id"],
                    "--lease-epoch",
                    str(first["lease_epoch"]),
                    "--operation",
                    "screenshot",
                ],
                principal=second["principal"],
            )
            _append_receipt(transport_receipts, "action.execute.cross_space_rejection", cross_space, expected_rejection=True)
            cross_space_rejected = _expected_rejection(cross_space, "space_forbidden")
            if not cross_space_rejected:
                failures.append("cross_space_mutation_rejection_invalid")
            else:
                isolation_inventory = _poll_managed_inventory(
                    cli,
                    spaces,
                    receipts=browser_receipts,
                    transport_receipts=transport_receipts,
                    phase="isolation",
                )
                latest_inventory = isolation_inventory
                if isolation_inventory.get("observed"):
                    focus_checks.append(_focus_is_unchanged(baseline_focus, isolation_inventory.get("user_focus")))
                if isolation_inventory.get("observed") and isolation_inventory.get("receipt_refs"):
                    _set_browser_scenario(
                        scenarios,
                        "two-space-isolation",
                        "cross_space_action_rejected_with_both_inventories_observed",
                        (isolation_inventory["receipt_refs"][0],),
                    )
                else:
                    failures.append("isolation_inventory_invalid")

            old_epoch = first["lease_epoch"]
            takeover = cli.call(
                ["space", "takeover", "--space-id", first["space_id"]],
                principal=first["principal"],
            )
            _append_receipt(transport_receipts, "space.takeover.research", takeover)
            new_epoch = _takeover_result(takeover, first["space_id"], old_epoch)
            if new_epoch is None:
                failures.append("takeover_fence_invalid")
            else:
                first["lease_epoch"] = new_epoch
                stale_request, stale_action, stale_idem = _action_ids("stale-fence")
                stale = cli.call(
                    [
                        "action",
                        "execute",
                        "--request-id",
                        stale_request,
                        "--action-id",
                        stale_action,
                        "--idempotency-key",
                        stale_idem,
                        "--space-id",
                        first["space_id"],
                        "--page-id",
                        first["page_id"],
                        "--lease-epoch",
                        str(old_epoch),
                        "--operation",
                        "screenshot",
                    ],
                    principal=first["principal"],
                )
                _append_receipt(transport_receipts, "action.execute.stale_lease_rejection", stale, expected_rejection=True)
                stale_rejected = _expected_rejection(stale, "stale_lease")
                if not stale_rejected:
                    failures.append("stale_epoch_rejection_invalid")
                fence_inventory = _poll_managed_inventory(
                    cli,
                    spaces,
                    receipts=browser_receipts,
                    transport_receipts=transport_receipts,
                    phase="takeover",
                    require_user_focus=True,
                )
                latest_inventory = fence_inventory
                if fence_inventory.get("observed"):
                    focus_checks.append(_focus_is_unchanged(baseline_focus, fence_inventory.get("user_focus")))
                if fence_inventory.get("observed") and fence_inventory.get("receipt_refs") and stale_rejected:
                    _set_browser_scenario(scenarios, "takeover-fence", "managed_page_stale_action_fenced", (fence_inventory["receipt_refs"][0],))
                else:
                    failures.append("takeover_inventory_invalid")

            return_epoch_candidate = second.get("lease_epoch")
            if not isinstance(return_epoch_candidate, int):
                failures.append("return_control_lease_epoch_invalid")
                _set_scenario(
                    scenarios,
                    "return-control-fresh-lease",
                    "not_observed",
                    "return_control_lease_epoch_invalid",
                )
            else:
                returned = cli.call(
                    [
                        "space",
                        "return",
                        "--space-id",
                        second["space_id"],
                        "--lease-epoch",
                        str(return_epoch_candidate),
                    ],
                    principal=second["principal"],
                )
                _append_receipt(transport_receipts, "space.return.testing", returned)
                return_result = _return_control_result(
                    returned,
                    second["space_id"],
                    return_epoch_candidate,
                )
                if return_result is None:
                    failures.append("return_control_ticket_invalid")
                    _set_scenario(
                        scenarios,
                        "return-control-fresh-lease",
                        "not_observed",
                        "return_control_or_ticket_invalid",
                    )
                else:
                    returned_epoch, control_ticket = return_result
                    return_ticket_json = json.dumps(control_ticket, separators=(",", ":"))
                    reclaimed = cli.call(
                        [
                            "space",
                            "reclaim",
                            "--space-id",
                            second["space_id"],
                            "--control-ticket",
                            return_ticket_json,
                        ],
                        principal=second["principal"],
                    )
                    _append_receipt(transport_receipts, "space.reclaim.testing", reclaimed)
                    reclaimed_epoch = _takeover_result(
                        reclaimed,
                        second["space_id"],
                        returned_epoch,
                    )
                    # Do not retain the ticket after the reclaim attempt.
                    del control_ticket
                    del return_ticket_json
                    return_ticket_json = None
                    if reclaimed_epoch is None:
                        failures.append("return_control_reclaim_invalid")
                        _set_scenario(
                            scenarios,
                            "return-control-fresh-lease",
                            "not_observed",
                            "return_control_reclaim_invalid",
                        )
                    else:
                        second["lease_epoch"] = reclaimed_epoch
                        return_inventory = _poll_managed_inventory(
                            cli,
                            spaces,
                            receipts=browser_receipts,
                            transport_receipts=transport_receipts,
                            phase="return-control",
                        )
                        latest_inventory = return_inventory
                        if return_inventory.get("observed"):
                            focus_checks.append(_focus_is_unchanged(baseline_focus, return_inventory.get("user_focus")))
                        return_refs = return_inventory.get("receipt_refs", ())
                        if (
                            return_inventory.get("observed")
                            and return_refs
                            and reclaimed_epoch > returned_epoch
                        ):
                            _set_browser_scenario(
                                scenarios,
                                "return-control-fresh-lease",
                                "return_control_reclaimed_with_fresh_lease",
                                (return_refs[-1],),
                            )
                        else:
                            failures.append("return_control_inventory_invalid")
                            _set_scenario(
                                scenarios,
                                "return-control-fresh-lease",
                                "not_observed",
                                "return_control_inventory_invalid",
                            )

            if initial_inventory and latest_inventory:
                focus_checks.append(_focus_is_unchanged(baseline_focus, latest_inventory.get("user_focus")))

            if operator_checkpoint:
                checkpoint_before_host = host_observation
                checkpoint_before_inventory = latest_inventory
                if not isinstance(checkpoint_before_inventory, dict) or not checkpoint_before_inventory.get("observed"):
                    failures.append("checkpoint_baseline_inventory_missing")
                for name in RESTART_SCENARIOS:
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
                        failures.append(f"{name}_checkpoint_required")
                        continue
                    after_status = cli.call(["host", "status"], principal=LIVE_PRINCIPALS[0])
                    _append_receipt(transport_receipts, f"checkpoint.host.status.{name}", after_status)
                    after_host, after_reason = _host_status_observation(after_status)
                    if after_host is None:
                        checkpoint_record["post_observation"] = {"host": False, "inventory": False, "epoch": False, "profile_binding": False}
                        _set_scenario(scenarios, name, "operator_checkpoint_required", after_reason or "post_checkpoint_host_invalid")
                        failures.append(f"{name}_post_host_invalid")
                        continue
                    binding_current = after_host.get("profile_instance_id") == observed_profile_binding
                    after_inventory = _poll_managed_inventory(
                        cli,
                        spaces,
                        receipts=browser_receipts,
                        transport_receipts=transport_receipts,
                        phase=f"checkpoint-{name}",
                    )
                    epoch_changed = bool(
                        isinstance(checkpoint_before_inventory, dict)
                        and checkpoint_before_inventory.get("observed")
                        and after_inventory.get("observed")
                        and _checkpoint_epoch_transition(
                            name,
                            checkpoint_before_host,
                            after_host,
                            checkpoint_before_inventory,
                            after_inventory,
                        )
                    )
                    if after_inventory.get("observed"):
                        focus_checks.append(_focus_is_unchanged(baseline_focus, after_inventory.get("user_focus")))
                    recovery_observed = after_inventory.get("recovery_observed") is True
                    checkpoint_record["post_observation"] = {
                        "host": True,
                        "inventory": after_inventory.get("observed") is True,
                        "epoch": epoch_changed,
                        "profile_binding": binding_current,
                        "recovery": recovery_observed,
                    }
                    if not after_inventory.get("observed") or not epoch_changed or not binding_current or not recovery_observed:
                        _set_scenario(scenarios, name, "operator_checkpoint_required", "post_checkpoint_observation_incomplete")
                        failures.append(f"{name}_post_observation_invalid")
                    else:
                        refs = after_inventory.get("receipt_refs", ())
                        if refs:
                            _set_browser_scenario(scenarios, name, "post_checkpoint_host_inventory_epoch_observed", (refs[0],))
                        checkpoint_before_host = after_host
                        checkpoint_before_inventory = after_inventory
                        latest_inventory = after_inventory
            else:
                for name in RESTART_SCENARIOS:
                    live.setdefault("operator_checkpoints", []).append({
                        "name": name,
                        "acknowledged": False,
                        "status": "operator_checkpoint_not_requested",
                        "evidence": "acknowledgement_only",
                    })
                    _set_scenario(scenarios, name, "operator_checkpoint_required", "operator_checkpoint_not_requested")
                    failures.append(f"{name}_checkpoint_required")

    except (KeyError, OSError, TypeError, ValueError):
        failures.append("live_orchestration_exception")
    finally:
        for space in reversed(spaces):
            lease_epoch = space.get("lease_epoch")
            space["cleanup_ok"] = False
            if not isinstance(lease_epoch, int):
                continue
            finish = cli.call(
                ["space", "finish", "--space-id", space["space_id"], "--lease-epoch", str(lease_epoch)],
                principal=space["principal"],
            )
            _append_receipt(transport_receipts, f"space.finish.{space['label']}", finish)
            if not _lifecycle_result(finish, "finished"):
                failures.append(f"{space['label']}_finish_invalid")
                continue
            cleanup_inventory = _poll_cleanup_inventory(
                cli,
                [space],
                receipts=browser_receipts,
                transport_receipts=transport_receipts,
            )
            space["cleanup_inventory_ok"] = cleanup_inventory.get("observed") is True
            if cleanup_inventory.get("observed"):
                focus_checks.append(_focus_is_unchanged(baseline_focus, cleanup_inventory.get("user_focus")))
            if not space["cleanup_inventory_ok"]:
                failures.append(f"{space['label']}_managed_page_cleanup_observation_invalid")
            release = cli.call(
                ["space", "release", "--space-id", space["space_id"], "--lease-epoch", str(lease_epoch)],
                principal=space["principal"],
            )
            _append_receipt(transport_receipts, f"space.release.{space['label']}", release)
            if not _lifecycle_result(release, "released"):
                failures.append(f"{space['label']}_release_invalid")
            else:
                space["cleanup_ok"] = space["cleanup_inventory_ok"] is True
        try:
            fixture_stack.close()
        except ProbeError:
            failures.append("fixture_server_shutdown_invalid")

    cleanup_spaces = [space for space in spaces if isinstance(space.get("page_id"), str)]
    cleanup_refs = tuple(
        receipt["operation"]
        for receipt in browser_receipts
        if receipt["operation"].startswith("browser.inventory.cleanup.")
    )
    if cleanup_spaces and all(space.get("cleanup_ok") is True for space in cleanup_spaces) and cleanup_refs:
        _set_browser_scenario(scenarios, "agent-page-cleanup", "managed_pages_absent_after_host_cleanup", (cleanup_refs[0],))
    else:
        failures.append("cleanup_incomplete")

    if not focus_checks:
        failures.append("focus_observation_missing")
    if all(focus_checks) and browser_receipts:
        focus_ref = next(
            (receipt["operation"] for receipt in browser_receipts if receipt["operation"].startswith("browser.inventory.post-action.")),
            None,
        )
        if focus_ref:
            _set_browser_scenario(scenarios, "focus-stability", "unmanaged_active_tab_and_focus_unchanged", (focus_ref,))

    live["scenarios"] = scenarios
    live["receipts"] = browser_receipts
    live["transport_receipts"] = transport_receipts
    live["operator_claims_used"] = False
    browser_observed = bool(initial_inventory and initial_inventory.get("observed"))
    all_focus_stable = bool(focus_checks) and all(focus_checks)
    live["browser_observed"] = browser_observed
    live["browser"] = {
        "observed": browser_observed,
        "current_run": True,
        "launch": False,
        "download": False,
        "cdp_url_used": False,
        "attached": browser_observed,
        "managed_pages_observed": sum(1 for space in spaces if isinstance(space.get("page_id"), str)),
        "visual_groups_observed": sum(
            observation.get("visual_groups", {}).get("spaces", 0)
            for observation in (latest_inventory or {}).get("observations", {}).values()
            if isinstance(observation, dict) and isinstance(observation.get("visual_groups"), dict)
        ),
        "user_tab_observed": baseline_focus is not None,
        "focus_unchanged": all_focus_stable,
    }
    live["browser_observations"] = {
        "managed_page_count": live["browser"]["managed_pages_observed"],
        "visual_group_count": live["browser"]["visual_groups_observed"],
        "user_tab_preserved": baseline_focus is not None and all_focus_stable,
        "focus_unchanged": all_focus_stable,
        "current_run": True,
    }
    latest_safety_measured = bool(
        isinstance(latest_inventory, dict) and latest_inventory.get("safety_measured") is True
    )
    safety_measured = (
        browser_observed
        and baseline_focus is not None
        and bool(focus_checks)
        and all_focus_stable
        and latest_safety_measured
    )
    live["safety"] = {
        "measurement_status": "measured_live" if safety_measured and cross_space_rejected and stale_rejected else "not_measured_live_incomplete",
        "current_run": True,
        "user_tab_closes": 0 if safety_measured else None,
        "focus_theft": 0 if safety_measured else None,
        "cross_space_mutations": 0 if cross_space_rejected else None,
        "stale_agent_mutations": 0 if stale_rejected else None,
    }
    live["cleanup"] = {
        "spaces_attempted": len(spaces),
        "spaces_released": sum(
            1 for receipt in transport_receipts
            if receipt["operation"].startswith("space.release.") and receipt.get("ok") is True
        ),
        "status": "passed" if cleanup_spaces and all(space.get("cleanup_ok") is True for space in cleanup_spaces) else "incomplete",
    }
    live["failures"] = sorted(set(failures))
    all_scenarios_live = tuple(item.get("name") for item in scenarios) == REQUIRED_LIVE_SCENARIOS and all(
        item.get("status") == "live_passed" for item in scenarios
    )
    candidate = all_scenarios_live and not failures
    live["status"] = "live_passed" if candidate else "live_observation_incomplete"
    live["evidence_status"] = "live_passed" if candidate else "live_observation_incomplete"
    live["evidence_mode"] = "live"
    live["release_gates"] = {"eligible": candidate, "reason_code": "live_passed" if candidate else "live_observation_incomplete"}
    if candidate and not _live_evidence_is_complete(live):
        live["status"] = "live_observation_incomplete"
        live["evidence_status"] = "live_observation_incomplete"
        live["release_gates"] = {"eligible": False, "reason_code": "live_evidence_validator_rejected"}
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
        value = _SECRET_TEXT.sub("<redacted>", value)
        value = _RAW_URL.sub("<redacted>", value)
        value = _ABSOLUTE_PATH.sub("<redacted>", value)
        value = _FILE_URL.sub("<redacted>", value)
        return value
    return value


def _live_evidence_is_complete(live: dict[str, Any]) -> bool:
    """Allow live green only for current-run host and browser observations."""
    if (
        live.get("_execution_token") is not _LIVE_EXECUTION_TOKEN
        or live.get("executed") is not True
        or live.get("current_run") is not True
        or live.get("status") != "live_passed"
        or live.get("evidence_status") != "live_passed"
        or live.get("evidence_mode") != "live"
        or live.get("provenance") != "existing_chrome_current_run"
        or live.get("descriptor_policy") != "descriptors_not_accepted_as_live_evidence"
        or live.get("operator_claims_used") is not False
        or live.get("host_probe_used") is not False
        or live.get("browser_observed") is not True
        or live.get("profile_scope") != "existing_user_profile"
        or live.get("profile_binding_observed") is not True
    ):
        return False
    browser = live.get("browser")
    browser_observations = live.get("browser_observations")
    preflight = live.get("preflight")
    receipts = live.get("receipts")
    scenarios = live.get("scenarios")
    enrollment = live.get("enrollment")
    safety = live.get("safety")
    cleanup = live.get("cleanup")
    checkpoints = live.get("operator_checkpoints")
    if (
        not isinstance(browser, dict)
        or browser.get("observed") is not True
        or browser.get("current_run") is not True
        or browser.get("attached") is not True
        or browser.get("launch") is not False
        or browser.get("download") is not False
        or browser.get("cdp_url_used") is not False
        or browser.get("managed_pages_observed") != 2
        or browser.get("visual_groups_observed") != 2
        or browser.get("user_tab_observed") is not True
        or browser.get("focus_unchanged") is not True
        or not isinstance(browser_observations, dict)
        or browser_observations.get("managed_page_count") != 2
        or browser_observations.get("visual_group_count") != 2
        or browser_observations.get("user_tab_preserved") is not True
        or browser_observations.get("focus_unchanged") is not True
        or not isinstance(preflight, dict)
        or preflight.get("observed") is not True
        or preflight.get("source") != "direct_cli"
        or preflight.get("profile_binding_observed") is not True
        or not isinstance(receipts, list)
        or not receipts
        or not isinstance(scenarios, list)
        or tuple(item.get("name") for item in scenarios if isinstance(item, dict)) != REQUIRED_LIVE_SCENARIOS
        or not isinstance(enrollment, dict)
        or not isinstance(safety, dict)
        or safety.get("measurement_status") != "measured_live"
        or safety.get("current_run") is not True
        or not isinstance(cleanup, dict)
        or cleanup.get("status") != "passed"
        or not isinstance(checkpoints, list)
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
            or receipt.get("source") != "browser_current_run"
            or receipt.get("current_run") is not True
            or receipt.get("observed") is not True
            or receipt.get("browser_observed") is not True
            or not isinstance(receipt.get("operation"), str)
            or receipt["operation"] in receipt_operations
        ):
            return False
        receipt_operations.add(receipt["operation"])
    if not all(
        any(operation == f"browser.action.execute.{label}" for operation in receipt_operations)
        for label in ("research", "testing")
    ):
        return False
    if not all(
        any(operation == f"browser.snapshot.{label}" for operation in receipt_operations)
        for label in ("research", "testing")
    ):
        return False
    if not any(operation.startswith("browser.inventory.cleanup.") for operation in receipt_operations):
        return False
    for checkpoint_name in RESTART_SCENARIOS:
        matching = [item for item in checkpoints if isinstance(item, dict) and item.get("name") == checkpoint_name]
        if len(matching) != 1:
            return False
        post = matching[0].get("post_observation")
        if matching[0].get("acknowledged") is not True or not isinstance(post, dict):
            return False
        if (
            post.get("host") is not True
            or post.get("inventory") is not True
            or post.get("epoch") is not True
            or post.get("profile_binding") is not True
        ):
            return False
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
            or len(observation["receipt_refs"]) != len(set(observation["receipt_refs"]))
            or any(reference not in receipt_operations for reference in observation["receipt_refs"])
        ):
            return False
        if expected_name == "return-control-fresh-lease" and not any(
            reference.startswith("browser.inventory.return-control.")
            for reference in observation["receipt_refs"]
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
        redact(
            {
                "schema_version": 1,
                "phase": 0,
            "rollout_phase": 7,
            "probe": "existing-chrome-coexistence",
            "kind": "existing-chrome-coexistence",
            "mode": mode,
            "evidence_mode": evidence_mode,
            "status": reported_status,
            "release_eligible": executed,
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
                "The headed lane invokes only the public host-backed direct CLI and never launches, downloads, or attaches to Chrome or CDP.",
                "Enrollment descriptors and operator acknowledgements are not accepted as live evidence; host logical observations do not establish browser coexistence.",
                ],
            }
        )
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
    result.add_argument(
        "--profile-binding-id",
        dest="profile_binding_id",
        help=f"enrolled profile binding ID; defaults to {PROFILE_BINDING_ENV}",
    )
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
            profile_binding_id=args.profile_binding_id,
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
    # Persist only a fixed logical invocation marker; never pass raw argv,
    # profile bindings, URLs, or paths to the artifact envelope.
    add_envelope(
        report,
        kind="existing-chrome-coexistence",
        command=["scripts/run_existing_chrome.py", "--headed" if args.headed else "--offline"],
    )
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
