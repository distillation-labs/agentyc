#!/usr/bin/env python3
"""Check the deterministic Phase 4 MV3 extension slice and bounded live evidence.

The active gate records a limited existing-profile MCP run without treating it
as complete headed-browser acceptance or production distribution evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PLAN = Path("docs/exec-plans/active/agentyc-browser-task-spaces/plans/phase-4-extension.md")
MANIFEST = Path("tests/phase-4-manifest.yaml")
ARTIFACT = Path("artifacts/p4-extension-review.md")
AUDIT = Path("docs/exec-plans/active/agentyc-browser-task-spaces/research/phase-4-chrome-docs-audit.md")
LIVE_ARTIFACT = Path("artifacts/p4-live-disposable/report.json")
LIVE_EXISTING_PROFILE_MCP_ARTIFACT = Path("artifacts/p4-existing-chrome-mcp-e2e.json")
MAX_BYTES = 4 * 1024 * 1024
MAX_PROVENANCE_AGE_SECONDS = 7 * 24 * 60 * 60
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
TASK_IDS = tuple(f"P4-T{i}" for i in range(1, 8))
REDACTION_FALSE_FIELDS = ("raw_browser_ids", "secrets", "absolute_paths", "page_bodies", "errors")
PHASE4_SOURCE_PATHS = (
    PLAN,
    Path("extension/manifest.json"),
    Path("extension/src/tab-creation-worker.mjs"),
    Path("extension/src/native-messaging.mjs"),
    Path("extension/tests/manifest.test.mjs"),
    Path("extension/tests/tab-creation-worker.test.mjs"),
    Path("crates/agentyc-host/src/cdp.rs"),
)


class Phase4Error(ValueError):
    pass


def read(root: Path, path: Path) -> str:
    if path.is_absolute() or ".." in path.parts:
        raise Phase4Error(f"unsafe path: {path}")
    candidate = root / path
    if candidate.is_symlink() or not candidate.is_file() or candidate.stat().st_size > MAX_BYTES:
        raise Phase4Error(f"missing, symlinked, or oversized path: {path}")
    return candidate.read_text(encoding="utf-8")


def manifest(root: Path) -> dict[str, Any]:
    try:
        value = json.loads(read(root, MANIFEST))
    except json.JSONDecodeError as exc:
        raise Phase4Error("Phase 4 manifest must be strict JSON") from exc
    if not isinstance(value, dict):
        raise Phase4Error("Phase 4 manifest root must be an object")
    return value


def artifact(root: Path) -> dict[str, Any]:
    text = read(root, ARTIFACT)
    blocks = re.findall(r"(?ms)^```json[ \t]+phase-4-extension-v1[ \t]*\n(.*?)^```[ \t]*$", text)
    if len(blocks) != 1:
        raise Phase4Error("Phase 4 artifact must contain one phase-4-extension-v1 block")
    try:
        value = json.loads(blocks[0])
    except json.JSONDecodeError as exc:
        raise Phase4Error("Phase 4 artifact JSON is invalid") from exc
    if not isinstance(value, dict):
        raise Phase4Error("Phase 4 artifact JSON must be an object")
    return value


def require(condition: bool, message: str) -> None:
    if not condition:
        raise Phase4Error(message)


def _safe_relative_path(value: Any, *, field: str) -> Path:
    require(isinstance(value, str) and bool(value), f"{field} must be a non-empty relative path")
    require("\\" not in value, f"{field} must use repository-relative separators")
    candidate = Path(value)
    require(not candidate.is_absolute() and ".." not in candidate.parts, f"{field} must remain inside the repository")
    require(candidate.as_posix() == value and value != ".", f"{field} is not canonical")
    return candidate


def _read_bytes(root: Path, path: Path) -> bytes:
    if path.is_absolute() or ".." in path.parts:
        raise Phase4Error(f"unsafe path: {path}")
    current = root
    for part in path.parts:
        current = current / part
        if current.is_symlink():
            raise Phase4Error(f"symlinked evidence path: {path}")
    candidate = root / path
    if not candidate.is_file() or candidate.stat().st_size > MAX_BYTES:
        raise Phase4Error(f"missing or oversized evidence path: {path}")
    try:
        return candidate.read_bytes()
    except (OSError, UnicodeError) as exc:
        raise Phase4Error(f"unreadable evidence path: {path}") from exc


def _sha256(root: Path, path: Path) -> str:
    return hashlib.sha256(_read_bytes(root, path)).hexdigest()


def phase4_source_hashes(root: Path = ROOT) -> dict[str, str]:
    """Return hashes for every source file used by the Phase 4 static gate."""
    return {path.as_posix(): _sha256(root, path) for path in PHASE4_SOURCE_PATHS}


def _fresh_timestamp(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    if parsed.tzinfo is None:
        return False
    age = (datetime.now(timezone.utc) - parsed).total_seconds()
    return 0 <= age <= MAX_PROVENANCE_AGE_SECONDS


def _validate_redaction(record: dict[str, Any], *, label: str) -> dict[str, Any]:
    redaction = record.get("redaction_status")
    if not isinstance(redaction, dict):
        raise Phase4Error(f"{label} redaction status is missing")
    require(redaction.get("status") == "applied", f"{label} redaction status is invalid")
    for field in REDACTION_FALSE_FIELDS:
        require(redaction.get(field) is False, f"{label} redaction field is unsafe: {field}")
    return redaction


def _validate_source_hashes(root: Path, value: Any, *, label: str) -> dict[str, str]:
    require(isinstance(value, dict) and bool(value), f"{label} source hashes are missing")
    normalized: dict[str, str] = {}
    for raw_path, expected in value.items():
        path = _safe_relative_path(raw_path, field=f"{label} source hash path")
        require(isinstance(expected, str) and SHA256_RE.fullmatch(expected) is not None, f"{label} source hash is invalid")
        actual = _sha256(root, path)
        require(actual == expected, f"{label} source hash is stale")
        normalized[path.as_posix()] = expected
    for path in PHASE4_SOURCE_PATHS:
        require(path.as_posix() in normalized, f"{label} source hash is missing")
    return normalized


def _validate_active_record(record: dict[str, Any], *, label: str) -> None:
    """Validate active evidence as a historical, non-release snapshot.

    Active artifacts are intentionally not rewritten during checker alignment.
    Their declared hashes and provenance must remain well-formed and bound;
    complete evidence still requires fresh hashes for every current source.
    """
    require(record.get("schema_version") == 1 and record.get("phase") == 4, f"{label} identity is invalid")
    require(record.get("status") == "active", f"{label} status is not active")
    require(record.get("release_eligible") is False, f"{label} cannot claim release eligibility")
    require(record.get("evidence_mode") == "deterministic", f"{label} evidence mode is invalid")
    _validate_redaction(record, label=label)

    hashes = record.get("source_hashes")
    require(isinstance(hashes, dict) and bool(hashes), f"{label} source hashes are missing")
    for raw_path, digest in hashes.items():
        _safe_relative_path(raw_path, field=f"{label} source hash path")
        require(isinstance(digest, str) and SHA256_RE.fullmatch(digest) is not None, f"{label} source hash is invalid")

    provenance = record.get("provenance")
    require(isinstance(provenance, dict), f"{label} provenance is missing")
    require(
        provenance.get("schema_version") == 1 and provenance.get("phase") == 4,
        f"{label} provenance identity is invalid",
    )
    require(provenance.get("status") == record.get("status"), f"{label} provenance status mismatch")
    require(provenance.get("evidence_mode") == record.get("evidence_mode"), f"{label} provenance evidence mode mismatch")
    require(provenance.get("redaction_status") == record.get("redaction_status"), f"{label} provenance redaction mismatch")
    build = record.get("build_tuple")
    require(
        isinstance(build, dict)
        and build.get("phase") == 4
        and build.get("artifact_kind") == "phase-4-extension-review"
        and build.get("producer") == "scripts/check_phase_4_extension.py"
        and isinstance(build.get("producer_sha256"), str)
        and SHA256_RE.fullmatch(build["producer_sha256"]) is not None,
        f"{label} build tuple is invalid",
    )
    require(provenance.get("build_tuple") == build, f"{label} provenance build tuple mismatch")
    require(provenance.get("source_hashes") == hashes, f"{label} provenance source hashes mismatch")
    require(provenance.get("timestamp") == record.get("timestamp"), f"{label} provenance timestamp mismatch")
    require(provenance.get("nonce") == record.get("nonce"), f"{label} provenance nonce mismatch")
    require(provenance.get("evidence_artifacts") == record.get("evidence_artifacts"), f"{label} provenance artifacts mismatch")


def _validate_current_acceptance(plan: str) -> None:
    match = re.search(
        r"(?ms)^## Current acceptance criteria[ \t]*\n(.*?)(?=^## |\Z)",
        plan,
    )
    require(match is not None, "current Phase 4 acceptance criteria are missing")
    criteria = {
        line[2:].strip()
        for line in match.group(1).splitlines()
        if line.startswith("- ")
    }
    required = {
        "The host connects only to the user-launched profile's loopback CDP endpoint and owns navigation, snapshots, actions, waits, and lifecycle operations.",
        "The extension authenticates to the host through Native Messaging and performs only the requested tab-creation operation.",
        "The extension manifest exposes no popup, side panel, content scripts, debugger permission, or tab-inventory capability; clicking the extension icon has no effect.",
        "Deterministic tests prove that no extension route can perform browser control beyond creating a tab, and that CDP endpoints outside loopback are rejected.",
        "Dedicated-profile Chrome E2E and store-distribution/update proof remain release gates. Host/browser restart recovery and cross-origin frame behavior are release gates only when those capabilities are claimed; deterministic tests do not substitute for any required live evidence.",
    }
    require(required <= criteria, "Phase 4 plan current acceptance criteria are incomplete")


def _validate_current_manifest(value: dict[str, Any]) -> None:
    require(value.get("manifest_version") == 3, "extension manifest is not MV3")
    require(
        value.get("background")
        == {"service_worker": "src/tab-creation-worker.mjs", "type": "module"},
        "extension manifest worker is not the tab-creation worker",
    )
    require(
        value.get("permissions") == ["nativeMessaging", "storage"],
        "extension manifest permissions exceed the tab-creation boundary",
    )
    forbidden = {
        "action",
        "browser_action",
        "page_action",
        "side_panel",
        "content_scripts",
        "host_permissions",
        "optional_permissions",
        "optional_host_permissions",
    }
    require(not forbidden.intersection(value), "extension manifest exposes a UI, script, or browser-control route")


def _validate_create_only_runtime(worker: str, native_messaging: str) -> None:
    required = (
        'message?.kind !== "request"',
        'message.method !== "tab.create"',
        "only host-requested tab creation is supported",
        "Object.keys(params).length !== 1",
        'Object.hasOwn(params, "bootstrap_url")',
        "validateBootstrapUrl(params.bootstrap_url)",
        "active: false",
        "requestedCapabilities: []",
        "this.native.requestedCapabilities = []",
        "handleTabCreationRequest(message",
    )
    for marker in required:
        require(marker in worker, f"tab-creation runtime invariant missing: {marker}")

    tab_methods = set(re.findall(r"\btabs\s*\??\.\s*([A-Za-z_$][\w$]*)", worker))
    require(tab_methods == {"create"}, "extension runtime exposes a non-creation tabs route")
    require(len(re.findall(r"\bonMessage\s*:", worker)) == 1, "extension has an unexpected Native Messaging route")
    chrome_namespaces = set(
        re.findall(
            r"\b(?:chromeApi|this\.chrome|globalThis\.chrome)\s*(?:\?\.|\.)\s*"
            r"([A-Za-z_$][\w$]*)",
            worker,
        )
    )
    require(
        chrome_namespaces <= {"runtime", "storage", "tabs"},
        "extension runtime exposes an unapproved Chrome API",
    )
    native_namespaces = set(
        re.findall(
            r"\bthis\.chrome\s*(?:\?\.|\.)\s*([A-Za-z_$][\w$]*)",
            native_messaging,
        )
    )
    require(
        native_namespaces <= {"runtime"},
        "Native Messaging client accesses browser APIs",
    )
    for marker in (
        "connectNative",
        "createNonce()",
        'makeEnvelope("hello"',
        "expectedNonce: this.nonce",
        'message.kind !== "hello_ok"',
    ):
        require(marker in native_messaging, f"Native Messaging handshake invariant missing: {marker}")


def _validate_host_cdp_boundary(cdp: str) -> None:
    required = (
        "fn browser_websocket_url(",
        "SocketAddr::from(([127, 0, 0, 1], port))",
        '.strip_prefix("ws://127.0.0.1:")',
        "parsed_port != port",
        "SocketAddr::from(([127, 0, 0, 1], parsed_port))",
        "fn browser_websocket_url_is_restricted_to_the_configured_loopback_port()",
        '"ws://192.0.2.1:9222/devtools/browser/opaque"',
        '"ws://127.0.0.1:9223/devtools/browser/opaque"',
        '"Page.navigate"',
        '"Target.closeTarget"',
    )
    for marker in required:
        require(marker in cdp, f"host CDP loopback invariant missing: {marker}")


def validate_current_architecture(
    plan: str,
    manifest_value: dict[str, Any],
    worker: str,
    native_messaging: str,
    cdp: str,
) -> None:
    """Check the active host-CDP/minimal-extension acceptance contract."""
    _validate_current_acceptance(plan)
    _validate_current_manifest(manifest_value)
    _validate_create_only_runtime(worker, native_messaging)
    _validate_host_cdp_boundary(cdp)


def _validate_json_evidence_envelope(value: Any, *, label: str) -> None:
    require(isinstance(value, dict), f"{label} JSON evidence must be an object")
    require(value.get("schema_version") == 1, f"{label} schema version is invalid")
    build = value.get("build_tuple")
    require(isinstance(build, dict) and build.get("phase") == 4, f"{label} provenance phase is invalid")
    provenance = value.get("provenance")
    require(isinstance(provenance, dict), f"{label} provenance is missing")
    require(provenance.get("build_tuple") == build, f"{label} provenance does not match its build tuple")
    _validate_redaction(value, label=label)


def _validate_named_artifacts(root: Path, value: Any, *, label: str) -> list[dict[str, str]]:
    require(isinstance(value, list) and bool(value), f"{label} named evidence artifacts are missing")
    normalized: list[dict[str, str]] = []
    names: set[str] = set()
    paths: set[str] = set()
    for item in value:
        if isinstance(item, str):
            path = _safe_relative_path(item, field=f"{label} evidence artifact path")
            name = path.stem
        else:
            require(isinstance(item, dict), f"{label} evidence artifact entry is invalid")
            name = item.get("name")
            path = _safe_relative_path(item.get("path"), field=f"{label} evidence artifact path")
            if "sha256" in item:
                digest = item.get("sha256")
                require(isinstance(digest, str) and SHA256_RE.fullmatch(digest) is not None, f"{label} evidence artifact hash is invalid")
                require(_sha256(root, path) == digest, f"{label} evidence artifact hash is stale")
        require(isinstance(name, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{1,127}", name) is not None, f"{label} evidence artifact name is invalid")
        require(name not in names and path.as_posix() not in paths, f"{label} evidence artifacts are duplicated")
        _read_bytes(root, path)
        names.add(name)
        paths.add(path.as_posix())
        normalized.append({"name": name, "path": path.as_posix()})
        if path.suffix == ".json":
            try:
                payload = json.loads(_read_bytes(root, path))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise Phase4Error(f"{label} JSON evidence is invalid") from exc
            _validate_json_evidence_envelope(payload, label=f"{label} {name}")
    require(ARTIFACT.as_posix() in paths, f"{label} extension review artifact is not named")
    return normalized


def _validate_complete_record(root: Path, record: dict[str, Any], *, label: str) -> None:
    required = ("schema_version", "provenance", "redaction_status", "source_hashes", "evidence_artifacts")
    for field in required:
        require(field in record, f"{label} is missing {field}")
    require(record.get("schema_version") == 1 and record.get("phase") == 4, f"{label} identity is invalid")
    require(record.get("status") == "complete", f"{label} status is not complete")
    require(isinstance(record.get("evidence_mode"), str) and record["evidence_mode"] != "deterministic", f"{label} complete evidence mode is invalid")
    _validate_redaction(record, label=label)
    _validate_source_hashes(root, record["source_hashes"], label=label)
    _validate_named_artifacts(root, record["evidence_artifacts"], label=label)

    provenance = record["provenance"]
    require(isinstance(provenance, dict), f"{label} provenance is invalid")
    require(provenance.get("schema_version") == 1 and provenance.get("phase") == 4, f"{label} provenance identity is invalid")
    require(isinstance(record.get("timestamp"), str) and _fresh_timestamp(record["timestamp"]), f"{label} timestamp is stale or invalid")
    require(provenance.get("timestamp") == record["timestamp"], f"{label} provenance timestamp mismatch")
    require(isinstance(record.get("nonce"), str) and record["nonce"], f"{label} nonce is missing")
    require(provenance.get("nonce") == record["nonce"], f"{label} provenance nonce mismatch")
    require(provenance.get("source_hashes") == record["source_hashes"], f"{label} provenance source hashes mismatch")
    require(provenance.get("evidence_artifacts") == record["evidence_artifacts"], f"{label} provenance artifacts mismatch")
    if "status" in provenance:
        require(provenance["status"] == record["status"], f"{label} provenance status mismatch")
    if "evidence_mode" in provenance:
        require(provenance["evidence_mode"] == record["evidence_mode"], f"{label} provenance evidence mode mismatch")
    if "redaction_status" in provenance:
        require(provenance["redaction_status"] == record["redaction_status"], f"{label} provenance redaction mismatch")
    if "build_tuple" in record:
        build = record["build_tuple"]
        require(isinstance(build, dict) and build.get("phase") == 4, f"{label} build phase is invalid")
        if "producer" in build or "producer_sha256" in build:
            producer = _safe_relative_path(build.get("producer"), field=f"{label} producer path")
            producer_hash = build.get("producer_sha256")
            require(isinstance(producer_hash, str) and SHA256_RE.fullmatch(producer_hash) is not None, f"{label} producer hash is invalid")
            require(_sha256(root, producer) == producer_hash, f"{label} producer hash is stale")
        if "build_tuple" in provenance:
            require(provenance["build_tuple"] == build, f"{label} build provenance mismatch")


def validate_complete_evidence(root: Path, values: dict[str, Any], evidence: dict[str, Any]) -> None:
    """Fail closed when a manifest claims complete Phase 4 evidence."""
    _validate_complete_record(root, values, label="Phase 4 manifest")
    _validate_complete_record(root, evidence, label="Phase 4 artifact")
    require(values.get("provenance") == evidence.get("provenance"), "Phase 4 manifest/artifact provenance mismatch")
    require(values.get("redaction_status") == evidence.get("redaction_status"), "Phase 4 manifest/artifact redaction mismatch")
    require(values.get("source_hashes") == evidence.get("source_hashes"), "Phase 4 manifest/artifact source hashes mismatch")
    require(values.get("evidence_artifacts") == evidence.get("evidence_artifacts"), "Phase 4 manifest/artifact artifacts mismatch")
    require(values.get("timestamp") == evidence.get("timestamp"), "Phase 4 manifest/artifact timestamp mismatch")
    require(values.get("nonce") == evidence.get("nonce"), "Phase 4 manifest/artifact nonce mismatch")
    require(values.get("evidence_mode") == evidence.get("evidence_mode"), "Phase 4 manifest/artifact evidence mode mismatch")


def check(root: Path = ROOT) -> dict[str, Any]:
    plan = read(root, PLAN)
    values = manifest(root)
    evidence = artifact(root)
    try:
        extension_manifest = json.loads(read(root, Path("extension/manifest.json")))
    except json.JSONDecodeError as exc:
        raise Phase4Error("extension manifest must be strict JSON") from exc
    require(isinstance(extension_manifest, dict), "extension manifest root must be an object")
    worker = read(root, Path("extension/src/tab-creation-worker.mjs"))
    native_messaging = read(root, Path("extension/src/native-messaging.mjs"))
    cdp = read(root, Path("crates/agentyc-host/src/cdp.rs"))
    validate_current_architecture(plan, extension_manifest, worker, native_messaging, cdp)
    live_text = read(root, LIVE_ARTIFACT) if (root / LIVE_ARTIFACT).is_file() else None
    live_mcp_text = (
        read(root, LIVE_EXISTING_PROFILE_MCP_ARTIFACT)
        if (root / LIVE_EXISTING_PROFILE_MCP_ARTIFACT).is_file()
        else None
    )
    live_mcp = None
    if live_mcp_text is not None:
        try:
            live_mcp = json.loads(live_mcp_text)
        except json.JSONDecodeError as exc:
            raise Phase4Error("existing-profile MCP evidence is invalid JSON") from exc
        _validate_json_evidence_envelope(live_mcp, label="existing-profile MCP evidence")
        require(live_mcp.get("phase") == 4, "existing-profile MCP evidence phase is invalid")
        require(live_mcp.get("status") == "partial", "existing-profile MCP evidence status is invalid")
        require(
            live_mcp.get("evidence_mode") == "headed_existing_profile",
            "existing-profile MCP evidence mode is invalid",
        )
        require(live_mcp.get("release_eligible") is False, "live MCP evidence cannot claim release eligibility")
        observations = live_mcp.get("observations")
        require(isinstance(observations, dict), "existing-profile MCP observations are missing")
        provenance = live_mcp.get("provenance")
        require(isinstance(provenance, dict), "existing-profile MCP provenance is missing")
        require(
            provenance.get("timestamp") == live_mcp.get("timestamp"),
            "existing-profile MCP provenance timestamp mismatch",
        )
        require(
            provenance.get("nonce") == live_mcp.get("nonce"),
            "existing-profile MCP provenance nonce mismatch",
        )
        takeover = observations.get("takeover_rebind")
        require(isinstance(takeover, dict), "existing-profile takeover/rebind evidence is missing")
        require(
            takeover.get("status") == "passed"
            and takeover.get("page_binding") == "bound"
            and takeover.get("agent_page_active") is False,
            "existing-profile MCP takeover/rebind evidence is missing",
        )
        reconciliation = observations.get("unknown_action_reconciliation")
        require(isinstance(reconciliation, dict), "existing-profile reconciliation evidence is missing")
        require(
            reconciliation.get("status") == "unknown"
            and reconciliation.get("action_replayed") is False,
            "existing-profile unknown-action outcome must remain explicit",
        )
        snapshot_read = observations.get("snapshot_read")
        require(isinstance(snapshot_read, dict), "existing-profile snapshot evidence is missing")
        require(
            snapshot_read.get("error_code") == "unknown_outcome"
            and snapshot_read.get("snapshot_hash") is None,
            "existing-profile snapshot outcome must remain explicit",
        )
        expected_live_artifact = {
            "name": "phase4-existing-chrome-mcp-e2e",
            "path": LIVE_EXISTING_PROFILE_MCP_ARTIFACT.as_posix(),
        }
        for record, label in ((values, "manifest"), (evidence, "artifact")):
            require(
                expected_live_artifact in record.get("evidence_artifacts", []),
                f"{label} does not name the existing-profile MCP evidence",
            )
    require(values.get("schema_version") == 1 and values.get("phase") == 4, "manifest identity is invalid")
    require(values.get("status") in {"active", "complete"}, "manifest status is invalid")
    require(values.get("release_eligible") is False, "Phase 4 cannot claim release eligibility")
    require(values.get("plan") == PLAN.as_posix(), "manifest plan path is invalid")
    require(values.get("audit") == AUDIT.as_posix(), "manifest audit path is invalid")
    require("status: active" in plan or "status: complete" in plan, "Phase 4 plan status is missing")
    manifest_mode = values.get("evidence_mode", "deterministic")
    artifact_mode = evidence.get("evidence_mode", manifest_mode)
    require(evidence.get("status") == values.get("status"), "manifest/artifact status mismatch")
    require(artifact_mode == manifest_mode, "manifest/artifact evidence mode mismatch")
    if values["status"] == "active":
        require(manifest_mode == "deterministic", "active Phase 4 evidence mode is invalid")
        for record, label in ((values, "manifest"), (evidence, "artifact")):
            _validate_active_record(record, label=f"Phase 4 {label}")
        require(values.get("provenance") == evidence.get("provenance"), "Phase 4 manifest/artifact provenance mismatch")
    else:
        expected_source_hashes = phase4_source_hashes(root)
        expected_build_tuple = {
            "phase": 4,
            "artifact_kind": "phase-4-extension-review",
            "producer": "scripts/check_phase_4_extension.py",
            "producer_sha256": _sha256(root, Path("scripts/check_phase_4_extension.py")),
        }
        for record, label in ((values, "manifest"), (evidence, "artifact")):
            source_hashes = _validate_source_hashes(
                root, record.get("source_hashes"), label=f"Phase 4 {label}"
            )
            require(
                source_hashes == expected_source_hashes,
                f"Phase 4 {label} source hashes are not current",
            )
            provenance = record.get("provenance")
            require(isinstance(provenance, dict), f"Phase 4 {label} provenance is missing")
            require(
                provenance.get("source_hashes") == source_hashes,
                f"Phase 4 {label} provenance source hashes mismatch",
            )
            require(
                record.get("build_tuple") == expected_build_tuple,
                f"Phase 4 {label} build tuple is stale",
            )
            require(
                provenance.get("build_tuple") == expected_build_tuple,
                f"Phase 4 {label} provenance build tuple is stale",
            )
            require(
                provenance.get("timestamp") == record.get("timestamp"),
                f"Phase 4 {label} provenance timestamp mismatch",
            )
            require(
                provenance.get("nonce") == record.get("nonce"),
                f"Phase 4 {label} provenance nonce mismatch",
            )
            require(
                provenance.get("evidence_artifacts") == record.get("evidence_artifacts"),
                f"Phase 4 {label} provenance artifact list mismatch",
            )
    require(
        values.get("evidence_artifacts") == evidence.get("evidence_artifacts"),
        "Phase 4 manifest/artifact evidence lists mismatch",
    )

    require(evidence.get("schema_version") == 1 and evidence.get("phase") == 4, "artifact identity is invalid")
    require(evidence.get("release_eligible") is False, "artifact cannot claim release eligibility")
    if live_text is not None:
        try:
            live = json.loads(live_text)
        except json.JSONDecodeError as exc:
            raise Phase4Error("live Phase 4 artifact is invalid JSON") from exc
        require(live.get("status") == "passed", "live disposable Phase 4 artifact did not pass")
        require(live.get("evidence_mode") == "live_disposable_profile", "live Phase 4 evidence mode is invalid")
        require(live.get("release_eligible") is False, "live Phase 4 artifact cannot claim release eligibility")
    require(isinstance(evidence.get("nonclaims"), list) and evidence["nonclaims"], "Phase 4 nonclaims missing")
    require("headed existing-profile Chrome" in " ".join(evidence["nonclaims"]), "headed Chrome nonclaim missing")
    if values["status"] == "complete":
        validate_complete_evidence(root, values, evidence)

    return {
        "phase": 4,
        "status": values["status"],
        "deterministic": manifest_mode == "deterministic",
        "release_eligible": False,
        "tasks": len(TASK_IDS),
        "live_disposable": live_text is not None,
        "live_existing_profile_mcp": live_mcp.get("status") if live_mcp else "not_run",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    try:
        result = check(args.root.resolve())
    except (OSError, Phase4Error) as exc:
        print(f"check_phase_4_extension: FAIL: {exc}", file=sys.stderr)
        return 1
    print("check_phase_4_extension: PASS")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
