#!/usr/bin/env python3
"""Validate the deterministic Phase 1 quality contract.

The gate reads one bounded Markdown artifact, validates its strict JSON schema,
checks the referenced repository evidence, and runs only the fixed set of
read-only Python checkers declared below.  It never starts a browser, host,
extension, network client, cargo command, or arbitrary command from the
artifact.  A pass is deterministic contract evidence only; it is not live
behavior or production evidence.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ARTIFACT = Path("artifacts/p1-quality-review.md")
MAX_ARTIFACT_BYTES = 4 * 1024 * 1024
MAX_EVIDENCE_BYTES = 4 * 1024 * 1024
MAX_COMMAND_OUTPUT = 16 * 1024
COMMAND_TIMEOUT_SECONDS = 30
JSON_FENCE_RE = re.compile(
    r"(?ms)^```json[ \t]+phase-1-quality-v1[ \t]*\n(.*?)^```[ \t]*$"
)


class QualityError(ValueError):
    """The Phase 1 quality artifact or deterministic evidence is incomplete."""


COMMAND_SPECS: tuple[dict[str, Any], ...] = (
    {
        "id": "core-contracts",
        "argv": ["python3", "scripts/check_core_contracts.py", "--root", ".", "--negative-identity-fixtures"],
        "cwd": ".",
        "expected_exit_code": 0,
        "timeout_seconds": COMMAND_TIMEOUT_SECONDS,
        "network": "forbidden",
        "browser_launch": False,
        "browser_attach": False,
    },
    {
        "id": "state-machines",
        "argv": [
            "python3",
            "scripts/check_state_machines.py",
            "--root",
            ".",
            "--artifact",
            "artifacts/p1-state-machines.md",
        ],
        "cwd": ".",
        "expected_exit_code": 0,
        "timeout_seconds": COMMAND_TIMEOUT_SECONDS,
        "network": "forbidden",
        "browser_launch": False,
        "browser_attach": False,
    },
    {
        "id": "host-protocol",
        "argv": [
            "python3",
            "scripts/check_host_protocol.py",
            "--root",
            ".",
            "--artifact",
            "artifacts/p1-host-trust.md",
        ],
        "cwd": ".",
        "expected_exit_code": 0,
        "timeout_seconds": COMMAND_TIMEOUT_SECONDS,
        "network": "forbidden",
        "browser_launch": False,
        "browser_attach": False,
    },
    {
        "id": "extension-permissions",
        "argv": [
            "python3",
            "scripts/check_extension_permissions.py",
            "--root",
            ".",
            "--document",
            "docs/security/extension-permissions.md",
            "--matrix",
            "artifacts/p0-capabilities.json",
        ],
        "cwd": ".",
        "expected_exit_code": 0,
        "timeout_seconds": COMMAND_TIMEOUT_SECONDS,
        "network": "forbidden",
        "browser_launch": False,
        "browser_attach": False,
    },
    {
        "id": "recovery-matrix",
        "argv": [
            "python3",
            "scripts/check_recovery_matrix.py",
            "--root",
            ".",
            "--artifact",
            "artifacts/p1-recovery.md",
        ],
        "cwd": ".",
        "expected_exit_code": 0,
        "timeout_seconds": COMMAND_TIMEOUT_SECONDS,
        "network": "forbidden",
        "browser_launch": False,
        "browser_attach": False,
    },
    {
        "id": "mcp-docs",
        "argv": ["python3", "scripts/check_mcp_docs.py", "--root", "."],
        "cwd": ".",
        "expected_exit_code": 0,
        "timeout_seconds": COMMAND_TIMEOUT_SECONDS,
        "network": "forbidden",
        "browser_launch": False,
        "browser_attach": False,
    },
    {
        "id": "mcp-deps",
        "argv": ["python3", "scripts/check_mcp_deps.py"],
        "cwd": ".",
        "expected_exit_code": 0,
        "timeout_seconds": COMMAND_TIMEOUT_SECONDS,
        "network": "forbidden",
        "browser_launch": False,
        "browser_attach": False,
    },
)
COMMANDS_BY_ID = {command["id"]: command for command in COMMAND_SPECS}

LEGACY_ALLOWLIST: tuple[tuple[str, str], ...] = (
    ("crates/agentyc-mcp/src/legacy.rs", "legacy_mcp_compatibility"),

    ("crates/agentyc-mcp/src/tools", "legacy_mcp_compatibility"),
    ("crates/agentyc-mcp/src/host_adapter_audit.rs", "test_audit_only"),
    ("crates/agentyc/src/main.rs", "legacy_cli_compatibility"),
    ("crates/agentyc/src/frontend.rs", "legacy_cli_compatibility"),
)

REQUIRED_MUTATIONS: tuple[str, ...] = (
    "space.create",
    "space.claim",
    "space.renew",
    "space.takeover",
    "space.return_control",
    "space.finish",
    "space.release",
    "page.create",
    "page.close",
    "action.navigate",
    "action.click",
    "action.input",
    "action.evaluate",
    "action.scroll",
    "action.screenshot",
    "action.storage_write",
    "action.cookie_write",
    "action.upload",
)

REQUIRED_EPOCHS: tuple[str, ...] = (
    "broker_epoch",
    "connection_epoch",
    "browser_session_epoch",
    "worker_instance_epoch",
)

REQUIRED_RESIDUALS = {
    "production_extension_chrome_path",
    "ordinary_user_distribution",
    "live_permission_restart_rollback",
}

# These checks are intentionally repository-owned.  The artifact may explain
# them, but cannot weaken the paths or markers that this gate verifies.
EVIDENCE_RULES: dict[str, dict[str, Any]] = {
    "ownership": {
        "paths": [
            "docs/architecture-existing-chrome.md",
            "artifacts/p1-architecture-review.md",
        ],
        "commands": ["mcp-docs", "mcp-deps", "state-machines"],
        "markers": {
            "docs/architecture-existing-chrome.md": (
                "### Ownership matrix",
                "host ledger and core contracts",
                "Chrome tabs, targets, frames, groups",
                "CLI/SDK role",
                "MCP role",
            ),
            "artifacts/p1-architecture-review.md": (
                "host broker is the single authority",
                "MV3 extension is the Chrome API adapter",
                "CLI/SDK clients use local IPC",
                "MCP is a compatibility adapter",
            ),
        },
    },
    "profile_sharing": {
        "paths": [
            "docs/architecture-existing-chrome.md",
            "artifacts/p1-architecture-review.md",
        ],
        "commands": ["extension-permissions", "recovery-matrix", "state-machines"],
        "markers": {
            "docs/architecture-existing-chrome.md": (
                "not storage isolation",
                "shared profile state",
                "MUST disclose shared profile state before a space is created",
                "before a space is created",
                "future isolated-profile mode is a separate product decision",
            ),
            "artifacts/p1-architecture-review.md": (
                "Shared profile with explicit disclosure",
                "Claiming per-space cookie/storage isolation",
            ),
        },
    },
    "public_identity": {
        "paths": [
            "docs/architecture-existing-chrome.md",
            "docs/api.md",
            "artifacts/p1-legacy-path-audit.md",
            "artifacts/p1-schema-review.md",
        ],
        "commands": ["core-contracts", "mcp-docs", "mcp-deps"],
        "markers": {
            "docs/architecture-existing-chrome.md": (
                "MUST NOT appear in primary output",
                "They MUST NOT appear in primary output, authorize an action, or replace a logical handle.",
            ),
            "docs/api.md": (
                "Raw `tab_id` values are adapter compatibility fields only",
            ),
            "artifacts/p1-legacy-path-audit.md": (
                "Legacy CDP/runtime matches are allowlisted only",
                "explicit compatibility/test surfaces",
            ),
            "artifacts/p1-schema-review.md": (
                "Logical IDs and generation/epoch fields are the only public routing",
                "Chrome tab, target, debugger-session, and group handles remain adapter-private",
            ),
        },
    },
    "mutation_inventory": {
        "paths": [
            "docs/architecture-existing-chrome.md",
            "artifacts/p1-state-machines.md",
            "artifacts/p1-permissions.md",
        ],
        "commands": ["host-protocol", "state-machines", "extension-permissions"],
        "markers": {
            "docs/architecture-existing-chrome.md": (
                "Every mutation checks principal",
                "at enqueue, dequeue, and immediately before extension dispatch",
                "no raw browser command is replayed",
            ),
            "artifacts/p1-state-machines.md": (
                "Mutations are checked at admission",
                "A lost result after dispatch is `unknown`; no replay",
            ),
            "artifacts/p1-permissions.md": (
                "Cookies, storage writes, downloads/uploads",
                "additional host policy and/or user-intent requirements",
            ),
        },
    },
    "runtime_epochs": {
        "paths": [
            "docs/architecture-existing-chrome.md",
            "artifacts/p1-state-machines.md",
            "artifacts/p1-host-trust.md",
        ],
        "commands": ["host-protocol", "recovery-matrix", "state-machines"],
        "markers": {
            "docs/architecture-existing-chrome.md": (
                "The four runtime epochs are distinct",
                "broker_epoch",
                "connection_epoch",
                "browser_session_epoch",
                "worker_instance_epoch",
            ),
            "artifacts/p1-state-machines.md": (
                "These epochs are not interchangeable",
            ),
            "artifacts/p1-host-trust.md": (
                "broker_epoch",
                "connection_epoch",
                "browser_session_epoch",
                "worker_instance_epoch",
            ),
        },
    },
    "fence": {
        "paths": [
            "docs/architecture-existing-chrome.md",
            "artifacts/p1-state-machines.md",
            "artifacts/p1-host-trust.md",
        ],
        "commands": ["host-protocol", "state-machines"],
        "markers": {
            "docs/architecture-existing-chrome.md": (
                "durable fence acknowledgement",
                "missing acknowledgement fails closed",
                "fence_pending",
            ),
            "artifacts/p1-state-machines.md": (
                "missing or uncertain acknowledgement leaves the space paused in `fence_pending`",
            ),
            "artifacts/p1-host-trust.md": (
                "Lost fence acknowledgement",
                "Keep paused",
            ),
        },
    },
    "constraints": {
        "paths": [
            "docs/security/host-protocol.md",
            "docs/security/extension-permissions.md",
            "docs/architecture-existing-chrome.md",
            "artifacts/p1-recovery.md",
        ],
        "commands": ["extension-permissions", "host-protocol", "mcp-docs", "recovery-matrix"],
        "markers": {
            "docs/security/host-protocol.md": (
                "same-user threat",
                "Remote TCP is disabled by default",
                "fail closed",
                "no automatic browser launch",
            ),
            "docs/security/extension-permissions.md": (
                "enterprise policy",
                "live revocation",
                "screenshot and DLP denial",
                "automatic browser download",
            ),
            "docs/architecture-existing-chrome.md": (
                "Chrome Web Store-signed extension",
                "not production distribution evidence",
            ),
            "artifacts/p1-recovery.md": (
                "Rollback or kill switch",
                "Never close user tabs or kill user Chrome",
                "no implicit cleanup",
            ),
        },
    },
    "evidence_boundary": {
        "paths": [
            "artifacts/p1-architecture-review.md",
            "artifacts/p1-permissions.md",
            "artifacts/p1-performance.md",
        ],
        "commands": ["host-protocol", "extension-permissions", "recovery-matrix"],
        "markers": {
            "artifacts/p1-architecture-review.md": (
                "not a live integration test",
                "no Phase 1 production Chrome/extension evidence is claimed",
            ),
            "artifacts/p1-permissions.md": (
                "Unrun live evidence",
                "No production claim",
            ),
            "artifacts/p1-performance.md": (
                "not measured",
                "no live evidence or release eligibility claimed",
            ),
        },
    },
}


def resolve_root(value: str | None) -> Path:
    root = Path(value).expanduser() if value else ROOT
    try:
        resolved = root.resolve(strict=True)
    except OSError as exc:
        raise QualityError("repository root is missing or unreadable") from exc
    if not resolved.is_dir():
        raise QualityError("repository root is not a directory")
    return resolved


def _safe_relative(relative: str | Path, *, name: str) -> Path:
    value = str(relative)
    if not value or "\x00" in value:
        raise QualityError(f"{name} must be a non-empty repository-relative path")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise QualityError(f"{name} must be repository-relative")
    return path


def _safe_path(root: Path, relative: str | Path, *, name: str, directory: bool = False) -> Path:
    path = _safe_relative(relative, name=name)
    current = root
    for component in path.parts:
        current = current / component
        if current.is_symlink():
            raise QualityError(f"{name} contains a symlink component")
    try:
        resolved = (root / path).resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise QualityError(f"{name} is missing or outside the repository root") from exc
    if resolved.is_symlink() or (not resolved.is_dir() if directory else not resolved.is_file()):
        expected = "directory" if directory else "regular file"
        raise QualityError(f"{name} is not a {expected}")
    return resolved


def read_bounded(root: Path, relative: str | Path, *, name: str = "evidence") -> str:
    path = _safe_path(root, relative, name=name)
    try:
        if path.stat().st_size > MAX_EVIDENCE_BYTES:
            raise QualityError(f"{name} exceeds the bounded read limit")
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise QualityError(f"{name} is unreadable") from exc
    if "\x00" in text:
        raise QualityError(f"{name} contains NUL data")
    return text


def read_artifact(root: Path, requested: str | None = None) -> tuple[Path, str]:
    relative = Path(requested) if requested else DEFAULT_ARTIFACT
    path = _safe_path(root, relative, name="quality artifact")
    try:
        if path.stat().st_size > MAX_ARTIFACT_BYTES:
            raise QualityError("quality artifact exceeds the bounded read limit")
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise QualityError("quality artifact is unreadable") from exc
    if "\x00" in text:
        raise QualityError("quality artifact contains NUL data")
    return path, text


def _unique_object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise QualityError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def parse_artifact_text(text: str) -> dict[str, Any]:
    matches = JSON_FENCE_RE.findall(text)
    if len(matches) != 1:
        raise QualityError("quality artifact must contain exactly one phase-1-quality-v1 JSON fence")
    try:
        value = json.loads(matches[0], object_pairs_hook=_unique_object_pairs)
    except (json.JSONDecodeError, QualityError) as exc:
        raise QualityError("quality artifact JSON is invalid") from exc
    if not isinstance(value, dict):
        raise QualityError("quality artifact JSON root must be an object")
    return value


def _expect_keys(value: Any, expected: set[str], name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise QualityError(f"{name} must be an object")
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        details: list[str] = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if extra:
            details.append("unexpected " + ", ".join(extra))
        raise QualityError(f"{name} schema mismatch ({'; '.join(details)})")
    return value


def _string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise QualityError(f"{name} must be a non-empty string")
    return value


def _bool(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise QualityError(f"{name} must be boolean")
    return value


def _list(value: Any, name: str) -> list[Any]:
    if not isinstance(value, list) or not value:
        raise QualityError(f"{name} must be a non-empty list")
    return value


def _strings(value: Any, name: str, *, exact: set[str] | None = None) -> list[str]:
    items = _list(value, name)
    if any(not isinstance(item, str) or not item or "\x00" in item for item in items):
        raise QualityError(f"{name} must contain non-empty strings")
    if len(set(items)) != len(items):
        raise QualityError(f"{name} must not contain duplicates")
    if exact is not None and set(items) != exact:
        raise QualityError(f"{name} does not match the required set")
    return items


def _normalized(text: str) -> str:
    return " ".join(text.lower().split())


def _require_markers(text: str, markers: tuple[str, ...], label: str) -> None:
    normalized = _normalized(text)
    missing = [marker for marker in markers if _normalized(marker) not in normalized]
    if missing:
        raise QualityError(f"{label} is missing: " + ", ".join(missing))


def _validate_evidence_paths(root: Path, requirement_id: str, paths: Any) -> None:
    expected = EVIDENCE_RULES[requirement_id]["paths"]
    values = _strings(paths, f"{requirement_id}.evidence_paths")
    if values != expected:
        raise QualityError(f"{requirement_id}.evidence_paths must match the gate-owned evidence list")
    for relative in expected:
        text = read_bounded(root, relative, name=f"{requirement_id} evidence {relative}")
        _require_markers(text, EVIDENCE_RULES[requirement_id]["markers"][relative], relative)


def _validate_command_refs(command_refs: Any, requirement_id: str) -> None:
    values = _strings(command_refs, f"{requirement_id}.command_refs")
    expected = set(EVIDENCE_RULES[requirement_id]["commands"])
    if set(values) != expected:
        raise QualityError(f"{requirement_id}.command_refs must match the gate-owned command list")
    unknown = [value for value in values if value not in COMMANDS_BY_ID]
    if unknown:
        raise QualityError(f"{requirement_id}.command_refs contains unknown commands: " + ", ".join(unknown))


def _validate_test_ref(root: Path, value: Any, name: str) -> None:
    reference = _string(value, name)
    if "::" not in reference:
        raise QualityError(f"{name} must use path::test-symbol syntax")
    relative_text, symbol = reference.split("::", 1)
    relative = _safe_relative(relative_text, name=name)
    if not symbol.strip():
        raise QualityError(f"{name} has an empty test symbol")
    text = read_bounded(root, relative, name=f"{name} source")
    if symbol.lower() not in text.lower():
        raise QualityError(f"{name} references a missing test symbol")


def _validate_test_refs(root: Path, values: Any, name: str, *, minimum: int = 1) -> list[str]:
    refs = _strings(values, name)
    if len(refs) < minimum:
        raise QualityError(f"{name} needs at least {minimum} test references")
    for index, reference in enumerate(refs):
        _validate_test_ref(root, reference, f"{name}[{index}]")
    return refs


def _validate_commands(root: Path, value: Any) -> list[dict[str, Any]]:
    commands = _list(value, "commands")
    if len(commands) != len(COMMAND_SPECS):
        raise QualityError("commands must contain every fixed deterministic checker exactly once")
    seen: set[str] = set()
    for index, raw in enumerate(commands):
        command = _expect_keys(
            raw,
            {
                "id",
                "argv",
                "cwd",
                "expected_exit_code",
                "timeout_seconds",
                "network",
                "browser_launch",
                "browser_attach",
            },
            f"commands[{index}]",
        )
        command_id = _string(command["id"], f"commands[{index}].id")
        if command_id in seen or command_id not in COMMANDS_BY_ID:
            raise QualityError(f"commands[{index}] has an unknown or duplicate id")
        seen.add(command_id)
        expected = COMMANDS_BY_ID[command_id]
        argv = _strings(command["argv"], f"commands[{index}].argv")
        if argv != expected["argv"]:
            raise QualityError(f"commands[{index}] does not match the fixed argv for {command_id}")
        if command["cwd"] != expected["cwd"]:
            raise QualityError(f"commands[{index}].cwd is not repository-relative dot")
        if command["expected_exit_code"] != 0 or command["timeout_seconds"] != COMMAND_TIMEOUT_SECONDS:
            raise QualityError(f"commands[{index}] has an invalid bounded result policy")
        if command["network"] != "forbidden":
            raise QualityError(f"commands[{index}] must forbid network access")
        if command["browser_launch"] is not False or command["browser_attach"] is not False:
            raise QualityError(f"commands[{index}] must not launch or attach to a browser")
        if any(
            token.lower() in " ".join(argv).lower()
            for token in ("curl", "wget", "cargo", "npm", "playwright", "--live", "--headed", "http://", "https://")
        ):
            raise QualityError(f"commands[{index}] contains a non-deterministic or network operation")
    if seen != set(COMMANDS_BY_ID):
        raise QualityError("commands omit a fixed deterministic checker")
    # The dependency checker has no --root option and therefore cannot safely
    # be used as evidence for a synthetic root.
    if root != ROOT and "mcp-deps" in seen:
        raise QualityError("mcp-deps evidence requires the checkout root")
    return commands


def _validate_ownership(root: Path, value: Any) -> None:
    section = _expect_keys(value, {"roles", "evidence_paths", "command_refs"}, "ownership")
    roles = _list(section["roles"], "ownership.roles")
    if len(roles) != 4:
        raise QualityError("ownership.roles must contain host, extension, CLI/SDK, and MCP")
    expected_roles = {
        "host": ("host_broker", {"identity", "leases", "epochs", "policy", "persistence", "reconciliation", "cleanup_authorization"}),
        "extension": ("extension_adapter", {"Chrome API calls", "browser observations"}),
        "cli_sdk": ("direct_local_protocol_client", {"local host protocol requests", "host receipts"}),
        "mcp": ("compatibility_adapter", {"compatibility translation"}),
    }
    seen: set[str] = set()
    for index, raw in enumerate(roles):
        role = _expect_keys(raw, {"id", "canonical_owner", "owns", "must_not_own"}, f"ownership.roles[{index}]")
        role_id = _string(role["id"], f"ownership.roles[{index}].id")
        if role_id in seen or role_id not in expected_roles:
            raise QualityError("ownership role ids must be unique and canonical")
        seen.add(role_id)
        owner, required_owns = expected_roles[role_id]
        if role["canonical_owner"] != owner:
            raise QualityError(f"ownership role {role_id} has the wrong canonical owner")
        owns = set(_strings(role["owns"], f"ownership.roles[{index}].owns"))
        if not required_owns.issubset(owns):
            raise QualityError(f"ownership role {role_id} omits an owned concern")
        must_not = set(_strings(role["must_not_own"], f"ownership.roles[{index}].must_not_own"))
        if role_id == "extension" and not {"authoritative leases", "action state", "profile authentication", "cleanup policy"}.issubset(must_not):
            raise QualityError("extension ownership must exclude authoritative state and policy")
        if role_id == "cli_sdk" and not {"Chrome API calls", "authoritative state"}.issubset(must_not):
            raise QualityError("CLI/SDK ownership must exclude Chrome APIs and authoritative state")
        if role_id == "mcp" and not {"second state owner", "direct Chrome API authority", "policy bypass"}.issubset(must_not):
            raise QualityError("MCP ownership must exclude authority and policy bypass")
    if seen != set(expected_roles):
        raise QualityError("ownership roles are incomplete")
    _validate_evidence_paths(root, "ownership", section["evidence_paths"])
    _validate_command_refs(section["command_refs"], "ownership")


def _validate_profile_sharing(root: Path, value: Any) -> None:
    section = _expect_keys(
        value,
        {"mode", "isolation_claim", "shared_state", "pre_create_disclosure", "evidence_paths", "command_refs"},
        "profile_sharing",
    )
    if section["mode"] != "shared_existing_profile" or section["isolation_claim"] is not False:
        raise QualityError("profile sharing must be explicit and must not claim isolation")
    _strings(
        section["shared_state"],
        "profile_sharing.shared_state",
        exact={"cookies", "local_storage", "session_storage", "history", "bookmarks", "permissions", "installed_extensions"},
    )
    disclosure = _expect_keys(
        section["pre_create_disclosure"],
        {
            "contract_id",
            "owner",
            "operation",
            "phase",
            "required",
            "required_fields",
            "acknowledgement",
            "acknowledgement_field",
            "isolation_claim",
            "missing_disclosure",
            "test_refs",
        },
        "profile_sharing.pre_create_disclosure",
    )
    if disclosure["contract_id"] != "profile-sharing-pre-create-v1":
        raise QualityError("profile disclosure contract id is not frozen")
    if disclosure["owner"] != "host_broker" or disclosure["operation"] != "space.create":
        raise QualityError("profile disclosure must be host-owned and run for space.create")
    if disclosure["phase"] != "before_ledger_commit" or disclosure["required"] is not True:
        raise QualityError("profile disclosure must precede the ledger commit and be required")
    _strings(
        disclosure["required_fields"],
        "profile_sharing.pre_create_disclosure.required_fields",
        exact={"profile_scope", "shared_state_notice", "isolation_claim"},
    )
    if disclosure["acknowledgement"] != "explicit_user_acceptance" or disclosure["acknowledgement_field"] != "profile_disclosure_acknowledged":
        raise QualityError("profile disclosure acknowledgement contract is incomplete")
    if disclosure["isolation_claim"] is not False or disclosure["missing_disclosure"] != "reject_before_ledger_commit":
        raise QualityError("missing profile disclosure must reject before creation")
    _validate_test_refs(root, disclosure["test_refs"], "profile_sharing.pre_create_disclosure.test_refs")
    for relative, markers in {
        "crates/agentyc-core/src/records.rs": (
            "pub struct ProfileDisclosure",
            "PROFILE_SCOPE",
            "explicit shared-profile disclosure acknowledgement is required",
        ),
        "crates/agentyc-host/src/protocol.rs": ("fn profile_disclosure", "space.create"),
        "crates/agentyc-host/src/bin/agentyc-native-host.rs": ("fn profile_disclosure_param", "profile_disclosure_acknowledged"),
        "crates/agentyc/src/commands/direct.rs": ("accept_shared_profile_disclosure",),
        "extension/src/sidepanel/index.html": ("profile-disclosure-acknowledged", "shared-profile behavior"),
        "extension/src/sidepanel/app.mjs": ("profile_scope", "profile_disclosure_acknowledged"),
    }.items():
        _require_markers(read_bounded(root, relative, name=f"profile disclosure source {relative}"), markers, relative)
    _validate_evidence_paths(root, "profile_sharing", section["evidence_paths"])
    _validate_command_refs(section["command_refs"], "profile_sharing")


def _validate_public_identity(root: Path, value: Any) -> None:
    section = _expect_keys(
        value,
        {
            "host_backed_public_handles",
            "raw_browser_identifiers",
            "public_raw_ids_forbidden",
            "extension_internal_hints_allowed",
            "visual_group_policy",
            "legacy_allowlist",
            "legacy_allowlist_exhaustive",
            "host_backed_legacy_bypass",
            "evidence_paths",
            "command_refs",
        },
        "public_identity",
    )
    _strings(
        section["host_backed_public_handles"],
        "public_identity.host_backed_public_handles",
        exact={
            "space_id",
            "page_id",
            "frame_id",
            "document_id",
            "navigation_id",
            "snapshot_id",
            "ref_id",
            "action_id",
            "event_id",
            "request_id",
        },
    )
    _strings(
        section["raw_browser_identifiers"],
        "public_identity.raw_browser_identifiers",
        exact={"tab_id", "target_id", "session_id", "debugger_id", "window_id", "group_id", "websocket_url"},
    )
    if section["public_raw_ids_forbidden"] is not True or section["extension_internal_hints_allowed"] is not True:
        raise QualityError("public raw-ID boundary is incomplete")
    if section["visual_group_policy"] != "visual_only_never_identity_authorization_isolation_adoption_or_cleanup":
        raise QualityError("visual group policy is not non-authoritative")
    allowlist = _list(section["legacy_allowlist"], "public_identity.legacy_allowlist")
    expected = {path: scope for path, scope in LEGACY_ALLOWLIST}
    seen: set[str] = set()
    for index, raw in enumerate(allowlist):
        entry = _expect_keys(raw, {"path", "scope", "reason", "audit_ref"}, f"public_identity.legacy_allowlist[{index}]")
        path = _string(entry["path"], f"public_identity.legacy_allowlist[{index}].path")
        if path in seen or path not in expected:
            raise QualityError("legacy allowlist contains an unknown or duplicate path")
        seen.add(path)
        if entry["scope"] != expected[path]:
            raise QualityError(f"legacy allowlist scope is wrong for {path}")
        _string(entry["reason"], f"public_identity.legacy_allowlist[{index}].reason")
        if entry["audit_ref"] != "artifacts/p1-legacy-path-audit.md":
            raise QualityError("legacy allowlist entries must cite the legacy path audit")
        _safe_path(root, path, name=f"legacy allowlist {path}", directory=path.endswith("/tools"))
    if seen != set(expected) or section["legacy_allowlist_exhaustive"] is not True:
        raise QualityError("legacy allowlist is not exhaustive")
    if section["host_backed_legacy_bypass"] is not False:
        raise QualityError("legacy compatibility must not bypass the host-backed path")
    audit = read_bounded(root, "artifacts/p1-legacy-path-audit.md", name="legacy path audit")
    audit_lower = audit.lower()
    for path in expected:
        if path.lower() in audit_lower:
            continue
        basename = Path(path).name.lower()
        if not any(
            f"`{basename}`" in line.lower()
            and ("legacy" in line.lower() or "audit" in line.lower())
            for line in audit.splitlines()
        ):
            raise QualityError(f"legacy path audit omits allowlisted path {path}")
    _validate_evidence_paths(root, "public_identity", section["evidence_paths"])
    _validate_command_refs(section["command_refs"], "public_identity")


def _validate_mutation_inventory(root: Path, value: Any) -> None:
    section = _expect_keys(
        value,
        {"admission_points", "required_fields", "mutations", "evidence_paths", "command_refs"},
        "mutation_inventory",
    )
    _strings(section["admission_points"], "mutation_inventory.admission_points", exact={"enqueue", "dequeue", "pre_dispatch"})
    _strings(
        section["required_fields"],
        "mutation_inventory.required_fields",
        exact={"principal", "space_id", "page_id", "lease_epoch", "broker_epoch", "connection_epoch", "policy", "capability", "generation", "test_refs"},
    )
    mutations = _list(section["mutations"], "mutation_inventory.mutations")
    if len(mutations) != len(REQUIRED_MUTATIONS):
        raise QualityError("mutation inventory must cover the fixed Phase 1 mutation set")
    seen: set[str] = set()
    for index, raw in enumerate(mutations):
        mutation = _expect_keys(
            raw,
            {"id", "owner", "lease", "epoch", "policy", "test_refs"},
            f"mutation_inventory.mutations[{index}]",
        )
        mutation_id = _string(mutation["id"], f"mutation_inventory.mutations[{index}].id")
        if mutation_id in seen or mutation_id not in REQUIRED_MUTATIONS:
            raise QualityError("mutation ids must be unique and Phase 1-owned")
        seen.add(mutation_id)
        if mutation["owner"] != "host_broker":
            raise QualityError(f"{mutation_id} must be host-broker owned")
        lease = _expect_keys(
            mutation["lease"],
            {"required", "field", "checks"},
            f"mutation {mutation_id}.lease",
        )
        if lease["required"] is not True or lease["field"] != "lease_epoch":
            raise QualityError(f"{mutation_id} lacks a required lease epoch")
        _strings(lease["checks"], f"mutation {mutation_id}.lease.checks", exact={"enqueue", "dequeue", "pre_dispatch"})
        epoch = _expect_keys(
            mutation["epoch"],
            {"required", "fields", "stale_rejected"},
            f"mutation {mutation_id}.epoch",
        )
        if epoch["required"] is not True or epoch["stale_rejected"] is not True:
            raise QualityError(f"{mutation_id} lacks stale-epoch rejection")
        _strings(
            epoch["fields"],
            f"mutation {mutation_id}.epoch.fields",
            exact={"broker_epoch", "connection_epoch", "browser_session_epoch", "worker_instance_epoch", "lease_epoch"},
        )
        policy = _expect_keys(
            mutation["policy"],
            {"required", "owner", "capability_required", "deny_on_unknown", "user_intent"},
            f"mutation {mutation_id}.policy",
        )
        if (
            policy["required"] is not True
            or policy["owner"] != "host_policy"
            or policy["capability_required"] is not True
            or policy["deny_on_unknown"] is not True
            or policy["user_intent"] not in {"not_required", "explicit_confirmation", "single_use_ticket_required"}
        ):
            raise QualityError(f"{mutation_id} lacks a fail-closed policy contract")
        _validate_test_refs(root, mutation["test_refs"], f"mutation {mutation_id}.test_refs", minimum=2)
    if seen != set(REQUIRED_MUTATIONS):
        raise QualityError("mutation inventory is incomplete")
    _validate_evidence_paths(root, "mutation_inventory", section["evidence_paths"])
    _validate_command_refs(section["command_refs"], "mutation_inventory")


def _validate_epochs(root: Path, value: Any) -> None:
    section = _expect_keys(value, {"epochs", "same_value_is_not_substitution", "evidence_paths", "command_refs"}, "runtime_epochs")
    if section["same_value_is_not_substitution"] is not True:
        raise QualityError("runtime epochs must be explicitly non-interchangeable")
    epochs = _list(section["epochs"], "runtime_epochs.epochs")
    if len(epochs) != len(REQUIRED_EPOCHS):
        raise QualityError("runtime_epochs must contain exactly four epochs")
    seen: set[str] = set()
    for index, raw in enumerate(epochs):
        epoch = _expect_keys(
            raw,
            {"name", "owner", "changes_on", "authority_consequence", "distinct_from", "test_refs"},
            f"runtime_epochs.epochs[{index}]",
        )
        name = _string(epoch["name"], f"runtime_epochs.epochs[{index}].name")
        if name in seen or name not in REQUIRED_EPOCHS:
            raise QualityError("runtime epoch names must be the four canonical distinct epochs")
        seen.add(name)
        _string(epoch["owner"], f"runtime_epochs.epochs[{index}].owner")
        _string(epoch["changes_on"], f"runtime_epochs.epochs[{index}].changes_on")
        _string(epoch["authority_consequence"], f"runtime_epochs.epochs[{index}].authority_consequence")
        if set(_strings(epoch["distinct_from"], f"runtime_epochs.epochs[{index}].distinct_from")) != set(REQUIRED_EPOCHS) - {name}:
            raise QualityError(f"{name} must name every other epoch as distinct")
        _validate_test_refs(root, epoch["test_refs"], f"runtime_epochs.epochs[{index}].test_refs")
    if seen != set(REQUIRED_EPOCHS):
        raise QualityError("runtime epoch coverage is incomplete")
    _validate_evidence_paths(root, "runtime_epochs", section["evidence_paths"])
    _validate_command_refs(section["command_refs"], "runtime_epochs")


def _validate_fence(root: Path, value: Any) -> None:
    section = _expect_keys(value, {"states", "transition", "missing_ack", "test_refs", "evidence_paths", "command_refs"}, "fence")
    _strings(section["states"], "fence.states", exact={"fence_pending", "fence_dispatched", "fence_acknowledged", "user_owned"})
    transition = _expect_keys(
        section["transition"],
        {"owner", "ack_owner", "durable_record", "user_owned_requires", "lease_epoch_increment", "lower_epoch_rejected"},
        "fence.transition",
    )
    if (
        transition["owner"] != "host_broker"
        or transition["ack_owner"] != "extension_adapter"
        or transition["durable_record"] != "fence_acknowledgement"
        or transition["user_owned_requires"] != "fence_acknowledged"
        or transition["lease_epoch_increment"] is not True
        or transition["lower_epoch_rejected"] is not True
    ):
        raise QualityError("fence transition does not require durable acknowledgement and stale rejection")
    missing = _expect_keys(
        section["missing_ack"],
        {"result_state", "pause_new_mutations", "admit_mutations", "fail_closed", "reconciliation_required"},
        "fence.missing_ack",
    )
    if (
        missing["result_state"] != "fence_pending"
        or missing["pause_new_mutations"] is not True
        or missing["admit_mutations"] is not False
        or missing["fail_closed"] is not True
        or missing["reconciliation_required"] is not True
    ):
        raise QualityError("missing fence acknowledgement does not fail closed")
    _validate_test_refs(root, section["test_refs"], "fence.test_refs", minimum=2)
    _validate_evidence_paths(root, "fence", section["evidence_paths"])
    _validate_command_refs(section["command_refs"], "fence")


def _validate_constraints(root: Path, value: Any) -> None:
    section = _expect_keys(
        value,
        {"security", "privacy", "distribution", "rollback", "evidence_paths", "command_refs"},
        "constraints",
    )
    security = _expect_keys(
        section["security"],
        {"local_peer_auth", "native_origin", "remote_tcp", "same_user_threat", "fail_closed_on_mismatch"},
        "constraints.security",
    )
    if (
        security["local_peer_auth"] != "required"
        or security["native_origin"] != "transport_metadata_only"
        or security["remote_tcp"] != "disabled_by_default"
        or security["same_user_threat"] != "explicitly_not_claimed"
        or security["fail_closed_on_mismatch"] is not True
    ):
        raise QualityError("security constraints are incomplete")
    privacy = _expect_keys(
        section["privacy"],
        {"ledger_secrets", "ledger_page_bodies", "ledger_cookies", "raw_ids_in_public_outputs", "redacted_artifacts"},
        "constraints.privacy",
    )
    if any(privacy[key] is not False for key in ("ledger_secrets", "ledger_page_bodies", "ledger_cookies", "raw_ids_in_public_outputs")) or privacy["redacted_artifacts"] is not True:
        raise QualityError("privacy constraints permit secret, page, cookie, or raw-ID exposure")
    distribution = _expect_keys(
        section["distribution"],
        {"default_mode", "ordinary_user", "unpacked_is_production_proof", "enterprise_self_hosted", "production_evidence"},
        "constraints.distribution",
    )
    if (
        distribution["default_mode"] != "existing_chrome_extension"
        or distribution["ordinary_user"] != "Chrome Web Store-signed extension"
        or distribution["unpacked_is_production_proof"] is not False
        or distribution["enterprise_self_hosted"] != "managed_path_only"
        or distribution["production_evidence"] != "not_claimed"
    ):
        raise QualityError("distribution constraints overclaim production readiness")
    rollback = _expect_keys(
        section["rollback"],
        {"new_mutations", "pages_retained", "user_tabs_preserved", "chrome_process_killed", "implicit_cleanup", "incompatible_ledger", "test_refs"},
        "constraints.rollback",
    )
    if (
        rollback["new_mutations"] != "disabled"
        or rollback["pages_retained"] is not True
        or rollback["user_tabs_preserved"] is not True
        or rollback["chrome_process_killed"] is not False
        or rollback["implicit_cleanup"] is not False
        or rollback["incompatible_ledger"] != "quarantine_and_fail_closed"
    ):
        raise QualityError("rollback constraints do not retain pages and user Chrome fail-closed")
    _validate_test_refs(root, rollback["test_refs"], "constraints.rollback.test_refs", minimum=2)
    _validate_evidence_paths(root, "constraints", section["evidence_paths"])
    _validate_command_refs(section["command_refs"], "constraints")


def _validate_boundary(root: Path, value: Any) -> None:
    section = _expect_keys(
        value,
        {"mode", "live_behavior", "production_path", "release_eligible", "residuals", "forbidden_substitutes", "evidence_paths", "command_refs"},
        "evidence_boundary",
    )
    if (
        section["mode"] != "deterministic_offline"
        or section["live_behavior"] != "not_run"
        or section["production_path"] != "not_proven"
        or section["release_eligible"] is not False
    ):
        raise QualityError("quality gate must not claim live or release evidence")
    residuals = _list(section["residuals"], "evidence_boundary.residuals")
    seen: set[str] = set()
    for index, raw in enumerate(residuals):
        residual = _expect_keys(raw, {"id", "status", "owner", "reason"}, f"evidence_boundary.residuals[{index}]")
        residual_id = _string(residual["id"], f"evidence_boundary.residuals[{index}].id")
        if residual_id in seen or residual_id not in REQUIRED_RESIDUALS:
            raise QualityError("residual live-evidence ids are incomplete or duplicated")
        seen.add(residual_id)
        if residual["status"] != "not_proven":
            raise QualityError(f"residual {residual_id} must remain not_proven")
        _string(residual["owner"], f"evidence_boundary.residuals[{index}].owner")
        reason = _string(residual["reason"], f"evidence_boundary.residuals[{index}].reason")
        if "deterministic" not in reason.lower() and "live" not in reason.lower():
            raise QualityError(f"residual {residual_id} lacks a live-evidence limitation")
    if seen != REQUIRED_RESIDUALS:
        raise QualityError("required residual live-evidence limits are missing")
    _strings(
        section["forbidden_substitutes"],
        "evidence_boundary.forbidden_substitutes",
        exact={"operator_claims", "acknowledgement_only", "source_presence", "disposable_browser"},
    )
    _validate_evidence_paths(root, "evidence_boundary", section["evidence_paths"])
    _validate_command_refs(section["command_refs"], "evidence_boundary")


def validate_spec(root: Path, spec: dict[str, Any], artifact_text: str, *, execute_commands: bool = False) -> None:
    expected_top = {
        "schema_version",
        "phase",
        "kind",
        "evidence_mode",
        "result",
        "release_eligible",
        "live_claims",
        "commands",
        "ownership",
        "profile_sharing",
        "public_identity",
        "mutation_inventory",
        "runtime_epochs",
        "fence",
        "constraints",
        "evidence_boundary",
    }
    _expect_keys(spec, expected_top, "quality artifact")
    if spec["schema_version"] != 1 or spec["phase"] != 1:
        raise QualityError("schema_version 1 and phase 1 are required")
    if spec["kind"] != "phase-1-quality-review" or spec["evidence_mode"] != "deterministic_offline" or spec["result"] != "pass":
        raise QualityError("quality artifact kind, evidence mode, or result is invalid")
    if spec["release_eligible"] is not False or spec["live_claims"] is not False:
        raise QualityError("quality artifact must not claim release eligibility or live behavior")
    if "not live" not in artifact_text.lower() and "live_behavior" not in artifact_text.lower():
        raise QualityError("quality artifact must disclose that live behavior is not claimed")

    commands = _validate_commands(root, spec["commands"])
    _validate_ownership(root, spec["ownership"])
    _validate_profile_sharing(root, spec["profile_sharing"])
    _validate_public_identity(root, spec["public_identity"])
    _validate_mutation_inventory(root, spec["mutation_inventory"])
    _validate_epochs(root, spec["runtime_epochs"])
    _validate_fence(root, spec["fence"])
    _validate_constraints(root, spec["constraints"])
    _validate_boundary(root, spec["evidence_boundary"])

    all_refs: set[str] = set()
    for section in (
        spec["ownership"],
        spec["profile_sharing"],
        spec["public_identity"],
        spec["mutation_inventory"],
        spec["runtime_epochs"],
        spec["fence"],
        spec["constraints"],
        spec["evidence_boundary"],
    ):
        all_refs.update(section["command_refs"])
    if all_refs != set(COMMANDS_BY_ID):
        raise QualityError("every fixed checker command must be referenced by a quality requirement")
    if execute_commands:
        run_commands(root, commands)


def run_commands(root: Path, commands: list[dict[str, Any]] | None = None) -> None:
    """Run only the already-validated, fixed read-only checker commands."""
    selected = commands if commands is not None else [dict(command) for command in COMMAND_SPECS]
    for command in selected:
        command_id = command["id"]
        expected = COMMANDS_BY_ID.get(command_id)
        if expected is None or command.get("argv") != expected["argv"]:
            raise QualityError(f"refusing an unallowlisted command: {command_id}")
        executable = [sys.executable, *command["argv"][1:]]
        environment = {
            "PATH": os.environ.get("PATH", ""),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
            "PYTHONHASHSEED": "0",
        }
        try:
            completed = subprocess.run(
                executable,
                cwd=root,
                env=environment,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                check=False,
                timeout=command["timeout_seconds"],
                shell=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise QualityError(f"deterministic command {command_id} could not complete") from exc
        if completed.returncode != command["expected_exit_code"]:
            output = (completed.stderr or completed.stdout or "").strip().replace("\x00", "")
            output = output[:MAX_COMMAND_OUTPUT]
            detail = f": {output}" if output else ""
            raise QualityError(f"deterministic command {command_id} failed with exit {completed.returncode}{detail}")


def validate(root: Path, artifact: str | None = None, *, execute_commands: bool = True) -> None:
    _path, text = read_artifact(root, artifact)
    validate_spec(root, parse_artifact_text(text), text, execute_commands=execute_commands)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", help="repository root; defaults to the checkout containing this script")
    parser.add_argument("--artifact", help="repository-relative Phase 1 quality artifact")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        root = resolve_root(args.root)
        validate(root, args.artifact, execute_commands=True)
    except (QualityError, OSError) as exc:
        print(f"check_phase_1_quality: FAIL: {exc}", file=sys.stderr)
        return 1
    print("check_phase_1_quality: PASS (deterministic Phase 1 contract; static evidence commands passed; live behavior not claimed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
