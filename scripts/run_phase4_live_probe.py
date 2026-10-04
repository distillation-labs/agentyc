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
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
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


def run_probe(chrome_path: str | None, exercise_cli: bool = False) -> dict[str, Any]:
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
    env["AGENTYC_STATE_DIR"] = str(state_dir)
    env["AGENTYC_DEBUG_LOG"] = str(profile / "host-debug.log")
    command = build_chrome_command(
        executable,
        profile,
        port,
        extension_dir=None,
        fixture_url="data:text/html,<title>agentyc-phase4</title>",
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
            cli_errors = []
            if exercise_cli and worker_seen and endpoint_seen:
                code, status, error = run_cli(state_dir, ["host", "status"])
                cli_status = code == 0 and status is not None
                if code != 0 or status is None:
                    cli_errors.append(f"host.status:{error[:256]}")
                status_result = status.get("result", status) if status else None
                profile_binding = status_result.get("profile_instance_id") if isinstance(status_result, dict) else None
                if cli_status and isinstance(profile_binding, str):
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--chrome-binary")
    parser.add_argument("--exercise-cli", action="store_true")
    parser.add_argument(
        "--artifact-dir",
        type=Path,
        default=ROOT / "artifacts" / "p4-live-disposable",
    )
    args = parser.parse_args()
    try:
        report = run_probe(args.chrome_binary, args.exercise_cli)
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
