#!/usr/bin/env python3
"""Run the test-only MV3 P0-T2 probe.

Offline mode is the default and performs only deterministic fixture checks.
`--headed` requests live inspection of an already running debug endpoint;
`--require-live` makes that live inspection required and fails when Chrome is
unavailable. `--launch-chrome` is a separate explicit opt-in, is always
required when used, and always uses the supplied isolated profile directory.
The script never touches the default Chrome profile.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
EXTENSION_DIR = ROOT / "extension" / "probes"
DEFAULT_ARTIFACT_DIR = ROOT / "artifacts" / "p0-extension"
REQUIRED_PERMISSIONS = {"debugger", "nativeMessaging", "storage", "tabGroups", "tabs"}


def load_manifest() -> dict[str, Any]:
    manifest = json.loads((EXTENSION_DIR / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("manifest_version") != 3:
        raise ValueError("probe manifest is not MV3")
    permissions = set(manifest.get("permissions", []))
    missing = REQUIRED_PERMISSIONS - permissions
    if missing:
        raise ValueError(f"probe manifest is missing permissions: {sorted(missing)}")
    for filename in ("service_worker.js", "probe.html", "probe.js", "fixture.html"):
        if not (EXTENSION_DIR / filename).is_file():
            raise ValueError(f"probe fixture is missing {filename}")
    return manifest


def safe_artifact_dir(value: str) -> Path:
    requested = Path(value)
    if not requested.is_absolute():
        requested = ROOT / requested
    requested = requested.resolve()
    allowed = DEFAULT_ARTIFACT_DIR.resolve()
    if requested != allowed and allowed not in requested.parents:
        raise SystemExit("artifact directory must be inside artifacts/p0-extension/")
    return requested


def safe_profile_dir(value: str) -> Path:
    requested = Path(value)
    if not requested.is_absolute():
        requested = ROOT / requested
    requested = requested.resolve()
    allowed = DEFAULT_ARTIFACT_DIR.resolve()
    if allowed not in requested.parents and requested != allowed:
        raise SystemExit("profile directory must be inside artifacts/p0-extension/")
    return requested


def chrome_endpoint(port: int, path: str) -> Any:
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}")
    with urllib.request.urlopen(request, timeout=0.75) as response:
        return json.loads(response.read().decode("utf-8"))


def wait_for_chrome(port: int, timeout: float = 8.0) -> dict[str, Any] | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            return chrome_endpoint(port, "/json/version")
        except (OSError, urllib.error.URLError, ValueError):
            time.sleep(0.1)
    return None


def chrome_binary(value: str | None) -> str | None:
    if value:
        return value
    candidates = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary",
        "google-chrome",
        "google-chrome-stable",
        "chromium",
        "chromium-browser",
    ]
    for candidate in candidates:
        if os.path.isabs(candidate) and Path(candidate).is_file():
            return candidate
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    return None


def inspect_live(port: int, profile_dir: Path | None, launch: bool, binary: str | None, artifact_dir: Path) -> dict[str, Any]:
    if launch and profile_dir is None:
        return {"status": "live_unavailable", "limitation": "--launch-chrome requires --profile-dir inside the artifact directory."}
    if launch:
        try:
            chrome_endpoint(port, "/json/version")
        except (OSError, urllib.error.URLError, ValueError):
            pass
        else:
            return {"status": "live_unavailable", "limitation": f"debug port {port} is already in use; refusing to attach or launch ambiguously."}
    process: subprocess.Popen[bytes] | None = None
    launched = False
    try:
        if launch:
            executable = chrome_binary(binary)
            if executable is None:
                return {"status": "live_unavailable", "limitation": "Chrome binary was not found; install Chrome or pass --chrome-binary."}
            assert profile_dir is not None
            profile_dir.mkdir(parents=True, exist_ok=True)
            fixture = (EXTENSION_DIR / "fixture.html").resolve().as_uri()
            command = [
                executable,
                f"--user-data-dir={profile_dir}",
                f"--load-extension={EXTENSION_DIR}",
                f"--remote-debugging-port={port}",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-background-networking",
                "--new-window",
                fixture,
            ]
            process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            launched = True
        version = wait_for_chrome(port)
        if version is None:
            return {"status": "live_unavailable", "limitation": "Chrome did not expose the requested debug endpoint."}
        try:
            targets = chrome_endpoint(port, "/json/list")
            target_count = len(targets) if isinstance(targets, list) else None
        except (OSError, urllib.error.URLError, ValueError):
            target_count = None
        return {
            "status": "live_observed",
            "chrome_version": version.get("Browser", "unknown"),
            "debugger_endpoint": f"127.0.0.1:{port}",
            "target_count": target_count,
            "launched_by_probe": launched,
            "permission_prompts": "not recorded; Chrome UI prompts require explicit user handling",
            "handshake": "not attempted; Native Messaging registration is an explicit installation step",
            "screenshots": [],
            "limitation": "The safe runner observes Chrome only; the extension popup and native host require explicit user installation/click-through.",
        }
    finally:
        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--headed", action="store_true", help="request optional live Chrome inspection")
    parser.add_argument(
        "--require-live",
        "--required-live",
        dest="require_live",
        action="store_true",
        help="require live Chrome inspection and exit nonzero when unavailable",
    )
    parser.add_argument("--launch-chrome", action="store_true", help="explicitly launch isolated Chrome; never implied by default")
    parser.add_argument("--profile-dir", help="isolated profile path, required for --launch-chrome")
    parser.add_argument("--chrome-binary")
    parser.add_argument("--debug-port", type=int, default=9222)
    parser.add_argument("--artifact-dir", default="artifacts/p0-extension")
    args = parser.parse_args()
    if args.launch_chrome and not args.headed:
        parser.error("--launch-chrome requires --headed")
    if args.require_live and not args.headed:
        parser.error("--require-live requires --headed")
    artifact_dir = safe_artifact_dir(args.artifact_dir)
    profile_dir = safe_profile_dir(args.profile_dir) if args.profile_dir else None
    manifest = load_manifest()
    live_required = bool(args.require_live or args.launch_chrome)
    report: dict[str, Any] = {
        "probe": "P0-T2",
        "status": "offline_passed",
        "manifest_version": manifest["manifest_version"],
        "extension_version": manifest["version"],
        "permissions": manifest["permissions"],
        "mode": "headed" if args.headed else "offline",
        "live": {
            "requested": bool(args.headed),
            "required": live_required,
            "status": "not_requested",
        },
        "safety": {
            "default_chrome_launch": False,
            "default_profile_mutation": False,
            "raw_ids_logged": False,
            "secrets_logged": False,
            "fixture_only_mutation": True,
        },
        "handshake_transcript": [],
        "screenshots": [],
        "limitations": [],
    }
    if args.headed:
        live = inspect_live(args.debug_port, profile_dir, args.launch_chrome, args.chrome_binary, artifact_dir)
        report["live"] = {"requested": True, "required": live_required, **live}
        if live["status"] == "live_unavailable":
            report["status"] = "live_required_unavailable" if live_required else "live_optional_unavailable"
            report["limitations"].append(live["limitation"])
        else:
            report["status"] = "live_passed"
            report["limitations"].append(live["limitation"])
    else:
        report["limitations"].append("Real Chrome, extension installation, and Native Messaging registration were not requested.")
    artifact_dir.mkdir(parents=True, exist_ok=True)
    output = artifact_dir / "report.json"
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 1 if report["status"] == "live_required_unavailable" else 0


if __name__ == "__main__":
    raise SystemExit(main())
