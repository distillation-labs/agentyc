#!/usr/bin/env python3
"""Run the dependency-free P0-T3 Native Messaging probe.

Default mode is deterministic and offline. This script never starts Chrome and
never starts the host process; use the standalone host fixture only when an
explicit external harness has installed the platform manifest.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROBE_MODULE = ROOT / "tests" / "probes" / "native_messaging.py"
_spec = importlib.util.spec_from_file_location("agentyc_p0_native_messaging", PROBE_MODULE)
if _spec is None or _spec.loader is None:
    raise RuntimeError("cannot load the local native messaging probe")
_module = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _module
_spec.loader.exec_module(_module)
run_deterministic_suite = _module.run_deterministic_suite


def safe_artifact_path(value: str) -> Path:
    requested = Path(value)
    if not requested.is_absolute():
        requested = ROOT / requested
    requested = requested.resolve()
    allowed = (ROOT / "artifacts" / "p0-native-protocol").resolve()
    if requested != allowed and allowed not in requested.parents:
        raise SystemExit("artifact must be inside artifacts/p0-native-protocol/")
    return requested


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--artifact",
        default="artifacts/p0-native-protocol/report.json",
        help="JSON report path under artifacts/p0-native-protocol/",
    )
    parser.add_argument(
        "--live-host",
        action="store_true",
        help="Only records that live-host execution is explicitly requested; does not install or launch it.",
    )
    args = parser.parse_args()
    artifact = safe_artifact_path(args.artifact)
    report = run_deterministic_suite()
    report["probe"] = "P0-T3"
    report["live"] = {
        "requested": bool(args.live_host),
        "status": "not_run",
        "limitation": "Chrome host installation and platform registration are not performed by this safe harness.",
    }
    report["environment"] = {
        "python": sys.version.split()[0],
        "cwd": str(Path.cwd()),
        "chrome_launch": "never",
        "secrets_logged": False,
    }
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
