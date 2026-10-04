#!/usr/bin/env python3
"""Validate the Phase 1 extension permission/capability policy and matrix."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MAX_DOC_BYTES = 4 * 1024 * 1024
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_MATRIX_BYTES = 8 * 1024 * 1024
DEFAULT_DOC = Path("docs/security/extension-permissions.md")
DEFAULT_MANIFEST = Path("extension/manifest.json")
DEFAULT_MATRIX = Path("artifacts/p0-capabilities.json")
ERROR_REGISTRY = Path("extension/src/protocol.mjs")
VALID_STATUSES = {"supported", "partial", "unsupported", "legacy-only"}
REQUIRED_DEBUGGER_DOMAINS = {
    "Accessibility",
    "DOM",
    "DOMSnapshot",
    "Input",
    "IO",
    "Log",
    "Network",
    "Page",
    "Runtime",
}
REQUIRED_MARKERS = (
    "required baseline permissions",
    "debugger",
    "required; never optional",
    "nativeMessaging",
    "tabGroups",
    "optional permissions and host access",
    "optional_host_permissions",
    "debugger domain allowlist",
    "Accessibility",
    "DOMSnapshot",
    "content-script and page worlds",
    "isolated world",
    "`MAIN` world is denied by default",
    "capability matrix",
    "cookies",
    "storage",
    "downloads",
    "upload",
    "evaluate",
    "enterprise policy",
    "live revocation",
    "incognito",
    "restricted URLs",
    "user gestures",
    "screenshot and DLP denial",
    "typed",
    "automatic browser download",
    "automatic browser launch",
    "artifact approval",
    "artifact_denied",
    "origin/frame/navigation/document scope",
    "Chrome error classifier",
    "separate debugger event allowlist",
    "0.1",
    "`Target` events are not allowed",
    "policy_denied",
    "incognito_not_supported",
    "unknown",
)


class PermissionError(ValueError):
    """The extension policy or capability matrix is incomplete."""


def resolve_root(value: str | None) -> Path:
    root = Path(value).expanduser() if value else ROOT
    if not root.is_dir():
        raise PermissionError("repository root is not a directory")
    return root.resolve()


def safe_file(root: Path, requested: str | None, default: Path, max_bytes: int) -> tuple[Path, bytes]:
    relative = Path(requested) if requested else default
    path = relative if relative.is_absolute() else root / relative
    try:
        resolved = path.resolve()
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise PermissionError("input must remain inside the repository root") from exc
    try:
        parts = resolved.relative_to(root).parts
    except ValueError as exc:
        raise PermissionError("input path is invalid") from exc
    current = root
    for component in parts:
        current = current / component
        if current.is_symlink():
            raise PermissionError("input path contains a symlink component")
    if not resolved.is_file() or resolved.is_symlink():
        raise PermissionError(f"missing input: {default.as_posix() if not requested else requested}")
    try:
        raw = resolved.read_bytes()
    except OSError as exc:
        raise PermissionError("input cannot be read") from exc
    if len(raw) > max_bytes:
        raise PermissionError("input exceeds the bounded read limit")
    return resolved, raw


def load_json(root: Path, requested: str | None) -> dict[str, Any]:
    _path, raw = safe_file(root, requested, DEFAULT_MATRIX, MAX_MATRIX_BYTES)
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PermissionError("capability matrix is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise PermissionError("capability matrix root must be an object")
    return value


def load_doc(root: Path, requested: str | None) -> str:
    _path, raw = safe_file(root, requested, DEFAULT_DOC, MAX_DOC_BYTES)
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PermissionError("extension permission document is not UTF-8") from exc


def load_manifest(root: Path, requested: str | None) -> dict[str, Any]:
    _path, raw = safe_file(root, requested, DEFAULT_MANIFEST, MAX_MANIFEST_BYTES)
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PermissionError("extension manifest is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise PermissionError("extension manifest root must be an object")
    return value


def _reject_main_world(value: Any, path: str = "manifest", depth: int = 0) -> None:
    if depth > 32:
        raise PermissionError("extension manifest nesting exceeds the bound")
    if isinstance(value, dict):
        if isinstance(value.get("world"), str) and value["world"].upper() == "MAIN":
            raise PermissionError(f"extension manifest MAIN world is not allowed at {path}.world")
        for key, child in value.items():
            _reject_main_world(child, f"{path}.{key}", depth + 1)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_main_world(child, f"{path}[{index}]", depth + 1)


def validate_error_registry(root: Path) -> None:
    _path, raw = safe_file(root, str(ERROR_REGISTRY), ERROR_REGISTRY, MAX_MANIFEST_BYTES)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PermissionError("extension protocol registry is not UTF-8") from exc
    required = (
        "export const PUBLIC_ERROR_CODES",
        '"artifact_denied"',
        '"download_denied"',
        '"permission_denied"',
        '"policy_denied"',
        '"restricted_url"',
        '"upload_denied"',
        '"unknown"',
    )
    missing = [marker for marker in required if marker not in text]
    if missing:
        raise PermissionError("closed extension error registry is incomplete: " + ", ".join(missing))


def validate_manifest(manifest: dict[str, Any]) -> None:
    if not isinstance(manifest, dict):
        raise PermissionError("extension manifest root must be an object")
    if manifest.get("manifest_version") != 3:
        raise PermissionError("extension manifest must use Manifest V3")
    permissions = manifest.get("permissions")
    if not isinstance(permissions, list) or any(
        not isinstance(permission, str) for permission in permissions
    ):
        raise PermissionError("extension manifest permissions must be a string list")
    if permissions.count("debugger") != 1:
        raise PermissionError("debugger must appear exactly once in required permissions")
    for key in ("optional_permissions", "host_permissions", "optional_host_permissions"):
        if key in manifest:
            raise PermissionError(f"extension manifest must not declare {key}")
    if "scripting" in permissions:
        raise PermissionError("extension manifest must not declare scripting")
    if manifest.get("incognito") != "not_allowed":
        raise PermissionError("extension manifest must set incognito to not_allowed")
    _reject_main_world(manifest)


def validate_document(text: str) -> None:
    lowered = text.lower()
    missing = [marker for marker in REQUIRED_MARKERS if marker.lower() not in lowered]
    if missing:
        raise PermissionError("missing permission-policy markers: " + ", ".join(missing))

    if not re.search(
        r"^\s*\|\s*`debugger`\s*\|\s*\*{2}required;\s*never\s+optional\*{2}",
        text,
        re.IGNORECASE | re.MULTILINE,
    ):
        raise PermissionError("debugger is not frozen as a required, never-optional permission")
    if "debugger" not in lowered or "optional permission" not in lowered:
        raise PermissionError("required/optional permission distinction is missing")
    if "wildcard host grant" not in lowered or "no broad hidden host grant" not in lowered:
        raise PermissionError("host-access minimization rule is missing")
    if "content scripts cannot call native messaging directly" not in lowered:
        raise PermissionError("content-script Native Messaging boundary is missing")
    if "permission_denied" not in lowered or "capability_unavailable" not in lowered:
        raise PermissionError("typed permission/capability denial is missing")
    if "no retry" not in lowered or "no fallback" not in lowered:
        raise PermissionError("revocation/policy fail-closed behavior is missing")
    if "tab-group deletion/rename/regrouping" not in lowered:
        raise PermissionError("visual-group negative test is missing")

    for domain in REQUIRED_DEBUGGER_DOMAINS:
        if domain.lower() not in lowered:
            raise PermissionError(f"debugger domain {domain} is not documented")
    if "raw passthrough" not in lowered:
        raise PermissionError("raw debugger/evaluate passthrough prohibition is missing")


def validate_matrix(matrix: dict[str, Any]) -> None:
    if matrix.get("schema_version") != 1 or matrix.get("phase") != 0:
        raise PermissionError("matrix must be the Phase 0 schema-version 1 capability matrix")
    if matrix.get("kind") != "capability-matrix":
        raise PermissionError("matrix kind is not capability-matrix")
    if matrix.get("mode") not in {"offline", "target", "headed", "managed"}:
        raise PermissionError("matrix mode is invalid")

    policy = matrix.get("browser_policy")
    if not isinstance(policy, dict):
        raise PermissionError("browser_policy is missing")
    if policy.get("automatic_launch") is not False or policy.get("automatic_download") is not False:
        raise PermissionError("capability matrix permits automatic browser launch/download")
    if policy.get("cdp_url_required_for_target") is not False:
        raise PermissionError("target mode incorrectly requires a copied debugger URL")
    if not isinstance(policy.get("target_description"), str) or "existing" not in policy["target_description"].lower():
        raise PermissionError("matrix does not identify existing user-approved Chrome")

    operations = matrix.get("operations")
    coverage = matrix.get("coverage")
    if not isinstance(operations, list) or not operations:
        raise PermissionError("matrix has no operations")
    if not isinstance(coverage, dict) or coverage.get("operation_count") != len(operations):
        raise PermissionError("operation count does not match matrix entries")
    if coverage.get("basis") != "catalog_claims":
        raise PermissionError("matrix does not distinguish catalog claims from observations")

    names: set[str] = set()
    statuses: dict[str, int] = {status: 0 for status in VALID_STATUSES}
    categories: set[str] = set()
    for index, item in enumerate(operations):
        if not isinstance(item, dict):
            raise PermissionError(f"operation {index} is not an object")
        name = item.get("name")
        claim = item.get("catalog_claim")
        observed = item.get("observed_probe")
        if not isinstance(name, str) or not name or name in names:
            raise PermissionError("operation names must be unique non-empty strings")
        if not isinstance(claim, dict) or claim.get("status") not in VALID_STATUSES:
            raise PermissionError(f"operation {name} has no valid catalog status")
        if not isinstance(claim.get("category"), str) or not claim["category"]:
            raise PermissionError(f"operation {name} has no category")
        if not isinstance(claim.get("source"), str) or not claim["source"]:
            raise PermissionError(f"operation {name} has no catalog source")
        if not isinstance(observed, dict) or not isinstance(observed.get("status"), str):
            raise PermissionError(f"operation {name} has no observation status")
        names.add(name)
        statuses[claim["status"]] += 1
        categories.add(claim["category"])

    if coverage.get("statuses") != statuses:
        raise PermissionError("coverage status counts do not match operation entries")
    declared_categories = coverage.get("categories")
    if not isinstance(declared_categories, list) or set(declared_categories) != categories:
        raise PermissionError("coverage categories do not match operation entries")
    if "permissions" not in categories:
        raise PermissionError("capability matrix lacks permission coverage")

    evidence = matrix.get("evidence")
    if not isinstance(evidence, dict) or not isinstance(evidence.get("catalog"), dict):
        raise PermissionError("matrix catalog evidence is missing")
    if evidence["catalog"].get("status") != "loaded" or evidence["catalog"].get("claim_count") != len(operations):
        raise PermissionError("matrix catalog evidence is incomplete")
    if matrix.get("mode") == "offline":
        probe = evidence.get("probe")
        if not isinstance(probe, dict) or probe.get("status") != "not-run" or probe.get("observed_operation_count") != 0:
            raise PermissionError("offline matrix must explicitly say that live probes did not run")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matrix", help="repository-relative Phase 0 capability matrix")
    parser.add_argument("--document", help="repository-relative extension permission document")
    parser.add_argument("--manifest", help="repository-relative MV3 extension manifest")
    parser.add_argument("--root", help="repository root; defaults to the checkout containing this script")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        root = resolve_root(args.root)
        validate_manifest(load_manifest(root, args.manifest))
        validate_error_registry(root)
        validate_document(load_doc(root, args.document))
        validate_matrix(load_json(root, args.matrix))
    except (PermissionError, OSError) as exc:
        print(f"check_extension_permissions: FAIL: {exc}", file=sys.stderr)
        return 1
    print("check_extension_permissions: PASS (MV3 manifest, required permissions, capability policy, and Phase 0 matrix)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
