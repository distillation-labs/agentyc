#!/usr/bin/env python3
"""Run a disposable Chrome production-extension/native-host smoke probe.

This is an opt-in macOS/Linux probe. It never attaches to the user's existing
Chrome profile and never claims Phase 4 completion by itself; it only verifies
that the production MV3 extension can be loaded into an owned profile and that
its Native Messaging host reaches the broker endpoint.
"""

from __future__ import annotations

import argparse
import json
import os
import select
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Any

from artifact_envelope import envelope as add_envelope
from artifact_envelope import write_json_atomic
from run_chrome_probe import (
    _endpoint_belongs_to_process,
    _manifest_extension_id,
    build_chrome_command,
    chrome_binary,
    chrome_endpoint,
    load_extension_via_browser_cdp,
    wait_for_chrome,
)

ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "extension"
HOST_BINARY = ROOT / "target" / "debug" / "agentyc-native-host"
MAX_WAIT_SECONDS = 20.0
CLI_BINARY = ROOT / "target" / "debug" / "agentyc"


class _Phase4FixtureHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = (
            b"<!doctype html><html><head><title>Agentyc Phase 4</title></head>"
            b"<body><main><h1>Extension E2E fixture</h1>"
            b"<button type='button'>Verify snapshot</button></main></body></html>"
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def terminate(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (OSError, ProcessLookupError):
        process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            process.kill()
        process.wait(timeout=3)


def run_cli(state_dir: Path, arguments: list[str]) -> tuple[int, dict[str, Any] | None, str]:
    if not CLI_BINARY.is_file() or not os.access(CLI_BINARY, os.X_OK):
        raise RuntimeError("build target/debug/agentyc before running this probe")
    environment = os.environ.copy()
    environment["AGENTYC_STATE_DIR"] = str(state_dir)
    try:
        endpoint = json.loads((state_dir / "broker.endpoint.json").read_text(encoding="utf-8"))
        local_socket = endpoint.get("local_socket")
        if isinstance(local_socket, str) and local_socket:
            environment["AGENTYC_HOST_SOCKET"] = local_socket
    except (OSError, json.JSONDecodeError):
        pass
    completed = subprocess.run(
        [str(CLI_BINARY), "--state-dir", str(state_dir), "--principal", "phase4-live", "--json", *arguments],
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError:
        value = None
    diagnostic = completed.stderr[-512:]
    if completed.stdout:
        diagnostic = f"{diagnostic} stdout={completed.stdout[-512:]}"
    return completed.returncode, value if isinstance(value, dict) else None, diagnostic


def _mcp_snapshot_read(
    state_dir: Path,
    profile_binding: str,
    space_id: str | None = None,
    page_id: str | None = None,
    lease_epoch: int | None = None,
    *,
    setup: bool = False,
    initial_url: str | None = None,
) -> dict[str, Any]:
    environment = os.environ.copy()
    environment["AGENTYC_STATE_DIR"] = str(state_dir)
    try:
        endpoint = json.loads((state_dir / "broker.endpoint.json").read_text(encoding="utf-8"))
        local_socket = endpoint.get("local_socket")
        if isinstance(local_socket, str) and local_socket:
            environment["AGENTYC_HOST_SOCKET"] = local_socket
    except (OSError, json.JSONDecodeError):
        pass
    process = subprocess.Popen(
        [
            str(CLI_BINARY),
            "--state-dir",
            str(state_dir),
            "--principal",
            "phase4-live",
            "--profile-binding-id",
            profile_binding,
            "mcp",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=environment,
        text=True,
    )
    request_id = 0

    def send(message: dict[str, Any]) -> None:
        if process.stdin is None:
            raise RuntimeError("MCP stdin is unavailable")
        process.stdin.write(json.dumps(message) + "\n")
        process.stdin.flush()

    def request(method: str, params: dict[str, Any]) -> dict[str, Any]:
        nonlocal request_id
        request_id += 1
        current_id = request_id
        send({"jsonrpc": "2.0", "id": current_id, "method": method, "params": params})
        if process.stdout is None:
            raise RuntimeError("MCP stdout is unavailable")
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            ready, _, _ = select.select([process.stdout], [], [], max(0, deadline - time.monotonic()))
            if not ready:
                break
            line = process.stdout.readline()
            if not line:
                break
            response = json.loads(line)
            if response.get("id") == current_id:
                return response
        raise RuntimeError(f"MCP {method} did not return a response")

    try:
        initialized = request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "phase4-live-probe", "version": "1"},
            },
        )
        if not isinstance(initialized.get("result"), dict):
            return {"status": "failed", "outcome_code": "mcp_initialize_failed"}
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})

        def find_field(value: Any, field: str) -> Any:
            if isinstance(value, dict):
                if field in value:
                    return value[field]
                for child in value.values():
                    found = find_field(child, field)
                    if found is not None:
                        return found
            elif isinstance(value, list):
                for child in value:
                    found = find_field(child, field)
                    if found is not None:
                        return found
            return None

        if setup:
            created = request(
                "tools/call",
                {
                    "name": "host_space_create",
                    "arguments": {
                        "label": "phase4-live",
                        "profile_scope": "shared_existing_profile",
                        "shared_state_notice": "shared_profile_state",
                        "isolation_claim": False,
                        "profile_disclosure_acknowledged": True,
                    },
                },
            )
            space_id = find_field(created, "space_id")
            if not isinstance(space_id, str):
                return {"status": "failed", "outcome_code": "mcp_space_create_failed"}
            claimed = request(
                "tools/call",
                {
                    "name": "host_lease_acquire",
                    "arguments": {"space_id": space_id, "now": 1, "ttl": 60000},
                },
            )
            lease_epoch = find_field(claimed, "lease_epoch")
            if not isinstance(lease_epoch, int):
                return {"status": "failed", "outcome_code": "mcp_space_claim_failed"}
            page_created = request(
                "tools/call",
                {
                    "name": "host_page_create_managed",
                    "arguments": {
                        "space_id": space_id,
                        "lease_epoch": lease_epoch,
                        "label": "phase4-page",
                        "url": initial_url or "https://example.test/",
                        "title": "phase4-page",
                        "now": 1,
                    },
                },
            )
            page_id = find_field(page_created, "page_id")
            if not isinstance(page_id, str):
                return {"status": "failed", "outcome_code": "mcp_page_create_failed"}
        if not isinstance(space_id, str) or not isinstance(page_id, str) or not isinstance(lease_epoch, int):
            return {"status": "failed", "outcome_code": "mcp_setup_missing_scope"}
        page_list = request(
            "tools/call",
            {"name": "host_page_list", "arguments": {"space_id": space_id}},
        ).get("result", {})
        page_content = page_list.get("structuredContent", {}).get("result", {})
        pages = page_content.get("pages", []) if isinstance(page_content, dict) else []
        page = next(
            (item for item in pages if isinstance(item, dict) and item.get("page_id") == page_id),
            None,
        )
        page_binding = page.get("binding") if isinstance(page, dict) else None
        if page_binding != "bound":
            return {
                "status": "partial",
                "page_binding": page_binding or "not_found",
                "snapshot_hash": None,
                "logical_ref_count": 0,
            }
        response = request(
            "tools/call",
            {
                "name": "host_snapshot_read",
                "arguments": {
                    "space_id": space_id,
                    "page_id": page_id,
                    "lease_epoch": lease_epoch,
                },
            },
        ).get("result", {})
        content = response.get("structuredContent", {})
        error = content.get("error", {}) if isinstance(content, dict) else {}
        result = content.get("result", {}) if isinstance(content, dict) else {}

        snapshot_hash = find_field(result, "snapshot_hash")
        refs = find_field(result, "refs")
        ref_count = len(refs) if isinstance(refs, (dict, list)) else 0
        successful = (
            response.get("isError") is not True
            and content.get("ok") is True
            and isinstance(snapshot_hash, str)
            and snapshot_hash
        )
        if not successful:
            return {
                "status": "partial",
                "page_binding": "bound",
                "snapshot_hash": snapshot_hash if isinstance(snapshot_hash, str) else None,
                "logical_ref_count": ref_count,
                "outcome_code": error.get("code") if isinstance(error, dict) else None,
                "action_status": None,
                "page_unbound_after_action": False,
            }

        action_request = {
            "name": "host_action_execute",
            "arguments": {
                "space_id": space_id,
                "page_id": page_id,
                "lease_epoch": lease_epoch,
                "now": 1,
            },
        }
        action_request["name"] = "host_page_close"
        action_response = request("tools/call", action_request).get("result", {})
        action_content = action_response.get("structuredContent", {})
        action_status = "succeeded" if action_content.get("ok") is True else None
        action_error = action_content.get("error", {}) if isinstance(action_content, dict) else {}

        page_unbound_after_action = False
        page_binding_after = None
        page_lifecycle_after = None
        for attempt in range(10):
            after_response = request(
                "tools/call",
                {"name": "host_page_list", "arguments": {"space_id": space_id}},
            ).get("result", {})
            after_content = after_response.get("structuredContent", {}).get("result", {})
            after_pages = after_content.get("pages", []) if isinstance(after_content, dict) else []
            after_page = next(
                (
                    item
                    for item in after_pages
                    if isinstance(item, dict) and item.get("page_id") == page_id
                ),
                None,
            )
            page_binding_after = (
                after_page.get("binding") if isinstance(after_page, dict) else None
            )
            page_lifecycle_after = (
                after_page.get("lifecycle") if isinstance(after_page, dict) else None
            )
            page_unbound_after_action = (
                after_page is None
                or page_binding_after in ("closed", "lost")
                or page_lifecycle_after in ("closed", "lost")
            )
            if page_unbound_after_action or attempt == 9:
                break
            time.sleep(0.2)
        successful = (
            action_response.get("isError") is not True
            and action_status == "succeeded"
            and page_unbound_after_action
        )
        return {
            "status": "passed" if successful else "partial",
            "page_binding": "bound",
            "snapshot_hash": snapshot_hash if isinstance(snapshot_hash, str) else None,
            "logical_ref_count": ref_count,
            "outcome_code": error.get("code") if isinstance(error, dict) else None,
            "action_status": action_status,
            "page_binding_after": page_binding_after,
            "page_lifecycle_after": page_lifecycle_after,
            "page_unbound_after_action": page_unbound_after_action,
            "space_id": space_id,
            "page_id": page_id,
            "lease_epoch": lease_epoch,
        }
    finally:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)


def run_probe(
    chrome_path: str | None,
    exercise_cli: bool = False,
    exercise_mcp_e2e: bool = False,
) -> dict[str, Any]:
    if not HOST_BINARY.is_file() or not os.access(HOST_BINARY, os.X_OK):
        raise RuntimeError("build target/debug/agentyc-native-host before running this probe")
    manifest = json.loads((EXTENSION / "manifest.json").read_text(encoding="utf-8"))
    extension_id = _manifest_extension_id(manifest)
    if not isinstance(extension_id, str):
        raise RuntimeError("production extension key did not derive a stable ID")
    executable = chrome_binary(chrome_path)
    if executable is None:
        raise RuntimeError("Google Chrome was not found")

    profile = Path(tempfile.mkdtemp(prefix="agentyc-phase4-profile-"))
    fixture_server = ThreadingHTTPServer(("127.0.0.1", 0), _Phase4FixtureHandler) if exercise_mcp_e2e else None
    fixture_thread = None
    if fixture_server is not None:
        fixture_thread = Thread(target=fixture_server.serve_forever, daemon=True)
        fixture_thread.start()
    fixture_url = (
        f"http://127.0.0.1:{fixture_server.server_port}/"
        if fixture_server is not None
        else "data:text/html,<title>agentyc-phase4</title>"
    )
    exercise_cli = exercise_cli or exercise_mcp_e2e
    extension_copy = profile / "extension"
    state_dir = profile / "state"
    native_dir = profile / "NativeMessagingHosts"
    shutil.copytree(EXTENSION, extension_copy)
    native_dir.mkdir(mode=0o700)
    native_manifest = json.loads(
        (EXTENSION / "native_host_manifest.macos.json").read_text(encoding="utf-8")
    )
    native_manifest["path"] = str(HOST_BINARY.resolve())
    native_manifest["allowed_origins"] = [f"chrome-extension://{extension_id}/"]
    (native_dir / "com.agentyc.host.json").write_text(
        json.dumps(native_manifest, indent=2) + "\n", encoding="utf-8"
    )
    port = free_port()
    env = os.environ.copy()
    env["AGENTYC_CDP_PORT"] = str(port)
    env["AGENTYC_STATE_DIR"] = str(state_dir)
    env["AGENTYC_DEBUG_LOG"] = str(profile / "host-debug.log")
    command = build_chrome_command(
        executable,
        profile,
        port,
        extension_dir=None,
        fixture_url=fixture_url,
        operator_assisted=False,
    )
    stderr_path = profile / "chrome.stderr.log"
    stderr_handle = stderr_path.open("wb")
    process = subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=stderr_handle,
        env=env,
        start_new_session=True,
    )
    try:
        if wait_for_chrome(port, timeout=8.0) is None or not _endpoint_belongs_to_process(port, process):
            raise RuntimeError("owned Chrome debug endpoint did not become ready")
        client, session_id, loaded_id, load_evidence = load_extension_via_browser_cdp(
            port, process, extension_copy, manifest, extension_id
        )
        try:
            deadline = time.monotonic() + MAX_WAIT_SECONDS
            worker_seen = False
            endpoint_seen = False
            while time.monotonic() < deadline:
                targets = chrome_endpoint(port, "/json/list")
                worker_seen = any(
                    isinstance(target, dict)
                    and target.get("type") == "service_worker"
                    and isinstance(target.get("url"), str)
                    and extension_id in target["url"]
                    for target in targets if isinstance(targets, list)
                )
                endpoint_seen = (state_dir / "broker.endpoint.json").is_file()
                if worker_seen and endpoint_seen:
                    break
                time.sleep(0.1)
            cli_status = None
            cli_space = None
            cli_lease = None
            cli_page = None
            status = None
            profile_binding = None
            space_id = None
            page_id = None
            lease_epoch = None
            cli_errors = []
            mcp_e2e = None
            if exercise_cli and worker_seen and endpoint_seen:
                code, status, error = run_cli(state_dir, ["host", "status"])
                cli_status = code == 0 and status is not None
                if code != 0 or status is None:
                    cli_errors.append(f"host.status:{error[:256]}")
                status_result = status.get("result", status) if status else None
                profile_binding = status_result.get("profile_instance_id") if isinstance(status_result, dict) else None
                if cli_status and isinstance(profile_binding, str):
                    if exercise_mcp_e2e:
                        mcp_e2e = _mcp_snapshot_read(
                            state_dir,
                            profile_binding,
                            setup=True,
                            initial_url=fixture_url,
                        )
                        space_id = mcp_e2e.get("space_id")
                        page_id = mcp_e2e.get("page_id")
                        lease_epoch = mcp_e2e.get("lease_epoch")
                        cli_space = isinstance(space_id, str)
                        cli_lease = isinstance(lease_epoch, int)
                        cli_page = isinstance(page_id, str)
                        if not (cli_space and cli_lease and cli_page):
                            cli_errors.append(
                                f"mcp.setup:{mcp_e2e.get('outcome_code', 'failed')}"
                            )
                    else:
                        create_args = [
                            "--profile-binding-id",
                            profile_binding,
                            "space",
                            "create",
                            "--label",
                            "phase4-live",
                            "--accept-shared-profile-disclosure",
                        ]
                        code, created, error = run_cli(state_dir, create_args)
                        created_result = created.get("result", created) if created else None
                        cli_space = code == 0 and isinstance(created_result, dict)
                        if code != 0 or created is None:
                            cli_errors.append(f"space.create:{error[:256]}")
                        space_id = created_result.get("space_id") if isinstance(created_result, dict) else None
                        if cli_space and isinstance(space_id, str):
                            code, claimed, error = run_cli(
                                state_dir,
                                ["--profile-binding-id", profile_binding, "space", "claim", "--space-id", space_id],
                            )
                            claimed_result = claimed.get("result", claimed) if claimed else None
                            lease = claimed_result.get("lease") if isinstance(claimed_result, dict) else None
                            cli_lease = code == 0 and isinstance(lease, dict)
                            if code != 0 or claimed is None:
                                cli_errors.append(f"space.claim:{error[:256]}")
                            if cli_lease:
                                lease_epoch = lease.get("lease_epoch")
                                code, page, error = run_cli(
                                    state_dir,
                                    [
                                        "--profile-binding-id",
                                        profile_binding,
                                        "page",
                                        "create-managed",
                                        "--space-id",
                                        space_id,
                                        "--lease-epoch",
                                        str(lease_epoch),
                                        "--label",
                                        "phase4-page",
                                        "--url",
                                        "https://example.test/",
                                    ],
                                )
                                page_result = page.get("result", page) if page else None
                                cli_page = code == 0 and isinstance(page_result, dict)
                                if code != 0 or page is None:
                                    cli_errors.append(f"page.create-managed:{error[:256]}")
                                page_id = page_result.get("page_id") if isinstance(page_result, dict) else None
            host_debug_tail = ""
            try:
                host_debug_tail = (profile / "host-debug.log").read_text(errors="replace")[-4096:]
            except OSError:
                pass
            stderr_tail = ""
            try:
                stderr_tail = stderr_path.read_text(errors="replace")[-4096:]
            except OSError:
                pass
            return {
                "schema_version": 1,
                "phase": 4,
                "evidence_mode": "live_disposable_profile",
                "release_eligible": False,
                "status": "passed"
                if worker_seen
                and endpoint_seen
                and loaded_id == extension_id
                and (
                    not exercise_cli
                    or (cli_status and cli_space and cli_lease and cli_page)
                )
                and (
                    not exercise_mcp_e2e
                    or (isinstance(mcp_e2e, dict) and mcp_e2e.get("status") == "passed")
                )
                else "failed",
                "extension_load": load_evidence,
                "service_worker_seen": worker_seen,
                "host_endpoint_seen": endpoint_seen,
                "browser_target_session_used": bool(session_id),
                "cli_exercised": exercise_cli,
                "cli_host_status": cli_status,
                "cli_profile_binding_found": isinstance(profile_binding, str),
                "cli_status_fields": sorted(status.keys()) if isinstance(status, dict) else [],
                "cli_space_create": cli_space,
                "cli_space_claim": cli_lease,
                "cli_page_create_managed": cli_page,
                "cli_errors": cli_errors,
                "mcp_e2e": mcp_e2e,
                "chrome_stderr_tail": stderr_tail,
                "host_debug_tail": host_debug_tail,
                "nonclaims": [
                    "existing user profile",
                    "production distribution",
                    "enterprise policy",
                    "full side-panel accessibility workflow",
                    "release eligibility",
                ],
            }
        finally:
            client.close()
    finally:
        terminate(process)
        try:
            stderr_handle.close()
        except Exception:
            pass
        shutil.rmtree(profile, ignore_errors=True)
        if fixture_server is not None:
            fixture_server.shutdown()
            fixture_server.server_close()
        if fixture_thread is not None:
            fixture_thread.join(timeout=2)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--chrome-binary")
    parser.add_argument("--exercise-cli", action="store_true")
    parser.add_argument("--exercise-mcp-e2e", action="store_true")
    parser.add_argument(
        "--artifact-dir",
        type=Path,
        default=ROOT / "artifacts" / "p4-live-disposable",
    )
    args = parser.parse_args()
    try:
        report = run_probe(args.chrome_binary, args.exercise_cli, args.exercise_mcp_e2e)
    except Exception as error:  # bounded CLI diagnostic; no secret or page data is printed
        report = {
            "schema_version": 1,
            "phase": 4,
            "evidence_mode": "live_disposable_profile",
            "status": "failed",
            "release_eligible": False,
            "error": str(error),
        }
    artifact_root = args.artifact_dir.resolve()
    repository_artifacts = (ROOT / "artifacts").resolve()
    artifact_root.relative_to(repository_artifacts)
    artifact_root.mkdir(parents=True, exist_ok=True)
    persisted = add_envelope(
        report,
        kind="phase4-live-disposable",
        command=sys.argv,
        result={"status": report.get("status"), "phase": 4},
    )
    write_json_atomic(artifact_root / "report.json", persisted)
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
