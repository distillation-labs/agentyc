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
from artifact_envelope import redact_for_persistence, write_json_atomic

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "browser-task-spaces"
MANIFEST_PATH = FIXTURE_ROOT / "manifest.json"
DEFAULT_ARTIFACT_ROOT = (ROOT / "artifacts" / "p0-coexistence").resolve()
MAX_REPORT_BYTES = 64 * 1024
MAX_ARTIFACT_FILES = 8
MAX_EXISTING_ARTIFACT_BYTES = 512 * 1024

DESCRIPTOR_SCHEMA_VERSION = 2
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
    executed = live.get("executed") is True and live.get("evidence_status") == "live_passed"
    return redact_for_persistence(
        {
            "schema_version": 1,
            "phase": 0,
            "rollout_phase": 7,
            "probe": "existing-chrome-coexistence",
            "kind": "existing-chrome-coexistence",
            "mode": mode,
            "evidence_mode": "live" if executed else "offline",
            "status": live.get("status", "offline_passed"),
            "release_eligible": False,
            "spaces": len(contract["spaces"]),
            "agents": len({item["agent"] for item in contract["spaces"]}),
            "result": {
                "fixture_set": manifest.get("fixture_set"),
                "fixture_count": len(manifest.get("fixtures", [])),
                "scenario": contract,
            },
            "live": live,
            "enrollment": live.get("enrollment"),
            "scenarios": live.get("scenarios", []),
            "execution_policy": {
                "attached": executed,
                "browser_launch": False,
                "browser_download": False,
                "cdp_url_used": False,
            },
            "safety": {
                "browser_launch": "never",
                "browser_download": "never",
                "user_tab_close": 0 if executed else None,
                "user_tab_closes": 0 if executed else None,
                "focus_theft_outside_user_action": 0 if executed else None,
                "focus_theft": 0 if executed else None,
                "cross_space_mutations": 0 if executed else None,
                "stale_agent_mutations": 0 if executed else None,
                "measurement_status": "measured_live" if executed else "not_measured_offline",
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
                "This runner never launches, downloads, or attaches to the product browser; live evidence is caller-supplied from an enrolled host/extension descriptor.",
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
        if not isinstance(evidence, dict) or evidence.get("executed") is not True or evidence.get("status") != "live_passed":
            errors.append("live evidence must be executed and live_passed")
        else:
            scenarios = evidence.get("scenarios")
            if not isinstance(scenarios, list) or len(scenarios) != len(REQUIRED_LIVE_SCENARIOS) or any(not isinstance(item, dict) for item in scenarios):
                errors.append("live evidence must include exactly all ten scenarios")
            else:
                names = {item.get("name") for item in scenarios}
                if names != set(REQUIRED_LIVE_SCENARIOS) or any(item.get("status") != "live_passed" for item in scenarios):
                    errors.append("live scenario evidence is incomplete or skipped")
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
    enrollment = descriptor["enrollment"]
    browser = descriptor["browser"]
    safety = descriptor["safety"]
    evidence = descriptor.get("evidence") if isinstance(descriptor.get("evidence"), dict) else None
    return redact_for_persistence(
        {
            "status": "harness_supplied",
            "descriptor_version": DESCRIPTOR_SCHEMA_VERSION,
            "enrollment": enrollment,
            "browser": {
                "status": browser["status"],
                "launch": False,
                "download": False,
                "cdp_url_used": False,
            },
            "profile_scope": "existing_user_profile",
            "safety": {
                "user_tab_preserved": safety["user_tab_preserved"],
                "focus_theft": safety["focus_theft"],
            },
            "executed": bool(evidence and evidence.get("executed") is True),
            "evidence_status": evidence.get("status") if evidence else "descriptor_only",
            "scenarios": evidence.get("scenarios", []) if evidence else [],
            "release_gates": evidence.get("release_gates") if evidence else None,
        }
    )


# Backward-compatible function name; the accepted input is now the strict
# enrollment descriptor above, never an arbitrary CDP/browser handle.
def load_harness(path_value: str | None) -> dict[str, Any] | None:
    return load_enrolled_descriptor(path_value)


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

    live: dict[str, Any] = {"requested": bool(args.headed), "required": bool(args.headed), "status": "not_requested", "executed": False, "evidence_status": "not_requested"}
    status = "offline_passed"
    if args.headed:
        harness = load_harness(args.harness or os.environ.get("AGENTYC_EXISTING_CHROME_HARNESS"))
        if harness is None:
            status = "live_required_unavailable"
            live = {
                "requested": True,
                "required": True,
                "status": status,
                "executed": False,
                "evidence_status": "descriptor_missing_or_invalid",
                "reason": "no valid existing-Chrome/extension harness descriptor or enrolled host/extension descriptor was supplied",
            }
        else:
            executed = harness.get("executed") is True and harness.get("evidence_status") == "live_passed"
            status = "live_passed" if executed else "live_descriptor_validated_not_executed"
            live = {
                "requested": True,
                "required": True,
                "status": status,
                "executed": executed,
                "evidence_status": harness.get("evidence_status"),
                "enrollment": harness.get("enrollment"),
                "browser": harness.get("browser"),
                "profile_scope": harness.get("profile_scope"),
                "safety": harness.get("safety"),
                "scenarios": harness.get("scenarios", []),
                "release_gates": harness.get("release_gates"),
            }

    report = safe_report(mode="headed" if args.headed else "offline", manifest=manifest, contract=contract, live=live)
    report["status"] = status
    if status != "offline_passed":
        report["limitations"].append("No browser action was executed by this probe; live evidence must come from the supplied harness.")
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
    # The runner itself never attaches. A headed lane is green only when the
    # descriptor includes independently captured, executed live evidence.
    return 0 if (not args.headed or status == "live_passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
