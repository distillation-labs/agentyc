#!/usr/bin/env python3
"""Run the Phase 0 existing-Chrome coexistence probe.

The default lane is an offline, deterministic fixture-contract check.  Headed
lanes are intentionally fail-closed: they only accept an explicit, existing
harness descriptor and never launch, download, attach to, or control Chrome.
The report contains logical scenario names only; browser IDs, secrets, paths,
and page bodies are never persisted.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from artifact_envelope import envelope as add_envelope
from artifact_envelope import write_json_atomic

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "browser-task-spaces"
MANIFEST_PATH = FIXTURE_ROOT / "manifest.json"
DEFAULT_ARTIFACT_ROOT = (ROOT / "artifacts" / "p0-coexistence").resolve()
MAX_REPORT_BYTES = 64 * 1024
MAX_ARTIFACT_FILES = 8
MAX_EXISTING_ARTIFACT_BYTES = 512 * 1024

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


class ProbeError(ValueError):
    """A deterministic input or fixture contract failure."""


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


def safe_report(*, mode: str, manifest: dict[str, Any], contract: dict[str, Any], live: dict[str, Any]) -> dict[str, Any]:
    return redact(
        {
            "schema_version": 1,
            "phase": 0,
            "probe": "existing-chrome-coexistence",
            "mode": mode,
            "status": live.get("status", "offline_passed"),
            "spaces": len(contract["spaces"]),
            "agents": len({item["agent"] for item in contract["spaces"]}),
            "result": {
                "fixture_set": manifest.get("fixture_set"),
                "fixture_count": len(manifest.get("fixtures", [])),
                "scenario": contract,
            },
            "live": live,
            "safety": {
                "browser_launch": "never",
                "browser_download": "never",
                "user_tab_close": None,
                "user_tab_closes": None,
                "focus_theft_outside_user_action": None,
                "focus_theft": None,
                "cross_space_mutations": None,
                "measurement_status": "not_measured_offline",
                "raw_browser_ids_logged": False,
                "secrets_logged": False,
            },
            "redaction_status": "applied; no page bodies, paths, browser IDs, or secrets retained",
            "limitations": [
                "Offline mode validates fixture contracts only; it is not evidence from a live Chrome profile.",
            ],
        }
    )


def load_harness(path_value: str | None) -> dict[str, Any] | None:
    if not path_value:
        return None
    path = Path(path_value).expanduser().resolve()
    if path.is_dir():
        path = path / "harness.json"
    if path.parent == path or not path.is_file():
        return None
    try:
        descriptor = read_json(path)
    except ProbeError:
        return None
    if not isinstance(descriptor, dict):
        return None
    # A descriptor is evidence supplied by the caller, not a command to run.
    if descriptor.get("schema_version") != 1:
        return None
    if descriptor.get("mode") not in {"existing-chrome", "existing_chrome"}:
        return None
    if descriptor.get("chrome") != "already-running" or descriptor.get("extension") != "installed":
        return None
    if descriptor.get("user_tab_safety") is not True:
        return None
    return {"status": "harness_supplied", "evidence": "caller-supplied existing Chrome and installed extension descriptor"}


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--headed", action="store_true", help="require an explicit existing-Chrome harness descriptor")
    result.add_argument("--require-live", action="store_true", help="same as --headed; fail closed without a supplied harness")
    result.add_argument("--dry-run", action="store_true", help="validate local fixtures without any live-harness lane")
    result.add_argument("--harness", help="read-only JSON descriptor for an already-running Chrome and installed extension")
    result.add_argument("--spaces", type=int, default=2)
    result.add_argument("--agents", type=int, default=2)
    result.add_argument("--artifact-dir", default="artifacts/p0-coexistence")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.require_live:
        args.headed = True
    if args.spaces < 0 or args.agents < 0:
        print("existing-Chrome probe error: counts must be non-negative", file=sys.stderr)
        return 2

    try:
        artifact_dir = safe_artifact_dir(args.artifact_dir)
        validate_artifact_budget(artifact_dir)
        manifest = validate_manifest()
        contract = validate_scenario(args.spaces, args.agents)
    except ProbeError as exc:
        print(f"existing-Chrome probe error: {exc}", file=sys.stderr)
        return 2

    live: dict[str, Any] = {"requested": bool(args.headed), "required": bool(args.headed), "status": "not_requested"}
    status = "offline_passed"
    if args.headed:
        harness = load_harness(args.harness or os.environ.get("AGENTYC_EXISTING_CHROME_HARNESS"))
        if harness is None:
            status = "live_required_unavailable"
            live = {
                "requested": True,
                "required": True,
                "status": status,
                "reason": "no valid existing-Chrome/extension harness descriptor was supplied",
            }
        else:
            # This remains a contract check. The script deliberately does not
            # claim a live result because no browser process is started here.
            status = "live_harness_supplied_not_executed"
            live = {"requested": True, "required": True, **harness}

    report = safe_report(mode="headed" if args.headed else "offline", manifest=manifest, contract=contract, live=live)
    report["status"] = status
    if status != "offline_passed":
        report["limitations"].append("No browser action was executed by this probe; live evidence must come from the supplied harness.")
    rendered = json.dumps(report, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    encoded = rendered.encode("utf-8")
    if len(encoded) > MAX_REPORT_BYTES:
        print("existing-Chrome probe error: redacted report exceeds the artifact budget", file=sys.stderr)
        return 2
    try:
        artifact_dir.mkdir(parents=True, exist_ok=True)
        add_envelope(report, kind="existing-chrome-coexistence")
        write_json_atomic(artifact_dir / "report.json", report, max_bytes=MAX_REPORT_BYTES)
    except OSError as exc:
        print(f"existing-Chrome probe error: cannot write bounded report: {exc.__class__.__name__}", file=sys.stderr)
        return 2
    print(rendered, end="")
    # A descriptor only proves that a caller claims to have a harness; this
    # offline script never executes browser actions, so every headed lane must
    # remain non-zero until real evidence is supplied by a separate runner.
    return 1 if args.headed else 0


if __name__ == "__main__":
    raise SystemExit(main())
