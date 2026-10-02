#!/usr/bin/env python3
"""Validate the dependency-free Phase 0 test manifest and safety policy."""

from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any

SECRET_ASSIGNMENT = re.compile(
    r"(?i)(?:token|secret|password|passwd|cookie|authorization|credential)\s*[:=]\s*\S+"
)
SHELL_OPERATORS = {";", "|", "||", "&&", ">", ">>", "<", "`", "$()"}
ALLOWED_STATUSES = {"required", "optional", "planned"}


class ManifestError(ValueError):
    """The test manifest is missing a required safety or execution invariant."""


def _tokens(path: Path) -> list[tuple[int, str]]:
    lines: list[tuple[int, str]] = []
    try:
        raw_lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ManifestError("manifest cannot be read") from exc
    number = 0
    index = 0
    while index < len(raw_lines):
        number += 1
        raw = raw_lines[index]
        if "\t" in raw:
            raise ManifestError(f"manifest line {number} uses tabs")
        content = raw.lstrip(" ")
        if not content or content.startswith("#"):
            index += 1
            continue
        indent = len(raw) - len(content)
        if content.startswith(("[", "{")):
            parts = [content]
            depth = content.count("[") - content.count("]") + content.count("{") - content.count("}")
            while depth > 0:
                index += 1
                if index >= len(raw_lines):
                    raise ManifestError("unterminated flow collection")
                continuation = raw_lines[index]
                if "\t" in continuation:
                    raise ManifestError(f"manifest line {index + 1} uses tabs")
                part = continuation.strip()
                if part and not part.startswith("#"):
                    parts.append(part)
                depth += part.count("[") - part.count("]") + part.count("{") - part.count("}")
            joined = " ".join(parts)
            if lines and lines[-1][1].endswith(":"):
                previous_indent, previous_content = lines[-1]
                lines[-1] = (previous_indent, f"{previous_content} {joined}")
            else:
                lines.append((indent, joined))
        else:
            lines.append((indent, content))
        index += 1
    return lines


def _scalar(text: str) -> Any:
    value = text.strip()
    if not value:
        return None
    if value.startswith(("[", "{")) or value.startswith('"'):
        try:
            normalized = re.sub(r",\s*([}\]])", r"\1", value)
            return json.loads(normalized)
        except json.JSONDecodeError as exc:
            raise ManifestError("invalid JSON-style YAML scalar") from exc
    if value.startswith("'") and value.endswith("'"):
        return value[1:-1].replace("''", "'")
    if value in {"true", "True"}:
        return True
    if value in {"false", "False"}:
        return False
    if value in {"null", "Null", "NULL", "~"}:
        return None
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    if re.fullmatch(r"-?(?:\d+\.\d*|\d*\.\d+)(?:[eE][+-]?\d+)?", value):
        return float(value)
    return value


def _split_pair(content: str) -> tuple[str, str]:
    if ":" not in content:
        raise ManifestError("mapping entry has no colon")
    key, value = content.split(":", 1)
    key = key.strip()
    if not key or any(char in key for char in "{}[]"):
        raise ManifestError("invalid mapping key")
    return key, value.strip()


def _node(lines: list[tuple[int, str]], index: int, indent: int) -> tuple[Any, int]:
    if index >= len(lines) or lines[index][0] != indent:
        raise ManifestError("invalid YAML indentation")
    if lines[index][1] == "-" or lines[index][1].startswith("- "):
        return _list(lines, index, indent)
    return _mapping(lines, index, indent)


def _nested(lines: list[tuple[int, str]], index: int, parent_indent: int) -> tuple[Any, int]:
    if index >= len(lines) or lines[index][0] <= parent_indent:
        return {}, index
    return _node(lines, index, lines[index][0])


def _mapping(lines: list[tuple[int, str]], index: int, indent: int) -> tuple[dict[str, Any], int]:
    result: dict[str, Any] = {}
    while index < len(lines) and lines[index][0] == indent and not lines[index][1].startswith("-"):
        key, value = _split_pair(lines[index][1])
        if key in result:
            raise ManifestError(f"duplicate key {key}")
        index += 1
        if value:
            result[key] = _scalar(value)
        else:
            result[key], index = _nested(lines, index, indent)
    return result, index


def _list(lines: list[tuple[int, str]], index: int, indent: int) -> tuple[list[Any], int]:
    result: list[Any] = []
    while index < len(lines) and lines[index][0] == indent and lines[index][1].startswith("-"):
        content = lines[index][1][1:].strip()
        index += 1
        if not content:
            item, index = _nested(lines, index, indent)
            result.append(item)
            continue
        if ":" not in content:
            result.append(_scalar(content))
            continue
        key, value = _split_pair(content)
        item: dict[str, Any] = {}
        if value:
            item[key] = _scalar(value)
        else:
            item[key], index = _nested(lines, index, indent)
        if index < len(lines) and lines[index][0] > indent:
            continuation_indent = lines[index][0]
            continuation, index = _mapping(lines, index, continuation_indent)
            for continuation_key, continuation_value in continuation.items():
                if continuation_key in item:
                    raise ManifestError(f"duplicate list-item key {continuation_key}")
                item[continuation_key] = continuation_value
        result.append(item)
    return result, index


def load_yaml_subset(path: Path) -> dict[str, Any]:
    lines = _tokens(path)
    if not lines:
        raise ManifestError("manifest is empty")
    value, index = _node(lines, 0, lines[0][0])
    if index != len(lines) or not isinstance(value, dict):
        raise ManifestError("manifest root must be a mapping")
    return value


def _mapping_value(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ManifestError(f"{name} must be a mapping")
    return value


def _list_value(value: Any, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise ManifestError(f"{name} must be a list")
    return value


def _safe_text(value: Any, name: str) -> None:
    if not isinstance(value, str):
        return
    if SECRET_ASSIGNMENT.search(value):
        raise ManifestError(f"{name} contains a secret assignment")
    if "\x00" in value:
        raise ManifestError(f"{name} contains NUL")


def _safe_relative_path(value: Any, name: str, root: Path) -> None:
    if not isinstance(value, str) or not value:
        raise ManifestError(f"{name} must be a non-empty path")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise ManifestError(f"{name} must be repository-relative")
    if not str(path).startswith("artifacts/") and name.startswith("artifact"):
        raise ManifestError(f"{name} must be under artifacts/")
    _safe_text(value, name)
    if name == "pinned toolchain" and not (root / path).is_file():
        raise ManifestError("pinned toolchain file is missing")


def validate(manifest_path: Path) -> None:
    manifest = load_yaml_subset(manifest_path)
    root = manifest_path.parent.parent
    if manifest.get("schema_version") != 1 or manifest.get("phase") != 0:
        raise ManifestError("schema_version 1 and phase 0 are required")
    for key in ("name", "owner", "toolchain", "artifact_policy", "commands"):
        if key not in manifest:
            raise ManifestError(f"missing top-level key {key}")
        _safe_text(manifest[key], key)

    toolchain = _mapping_value(manifest["toolchain"], "toolchain")
    for runtime, fields in {
        "rust": ("floor", "pinned"),
        "node": ("floor",),
        "npm": ("floor",),
        "chrome": ("floor", "ceiling"),
    }.items():
        entry = _mapping_value(toolchain.get(runtime), f"toolchain.{runtime}")
        for field in fields:
            if not isinstance(entry.get(field), str) or not entry[field]:
                raise ManifestError(f"toolchain.{runtime}.{field} is required")
            _safe_text(entry[field], f"toolchain.{runtime}.{field}")
        if runtime == "rust":
            _safe_relative_path(entry["pinned"], "pinned toolchain", root)

    policy = _mapping_value(manifest["artifact_policy"], "artifact_policy")
    required_policy = ("root", "write_mode", "required_fields", "redaction_keys", "network")
    for key in required_policy:
        if key not in policy:
            raise ManifestError(f"artifact_policy.{key} is required")
    if policy["root"] != "artifacts/" or policy["write_mode"] != "redacted-only":
        raise ManifestError("artifacts must be local and redacted-only")
    if policy["network"] != "forbidden":
        raise ManifestError("manifest commands must declare network as forbidden")
    required_fields = _list_value(policy["required_fields"], "artifact_policy.required_fields")
    required_field_names = {
        "schema_version",
        "build_tuple",
        "environment",
        "timestamp",
        "command",
        "result",
        "redaction_status",
    }
    if set(required_fields) != required_field_names:
        raise ManifestError("artifact_policy.required_fields is incomplete or has extras")
    redaction_keys = _list_value(policy["redaction_keys"], "artifact_policy.redaction_keys")
    if not redaction_keys or any(not isinstance(item, str) for item in redaction_keys):
        raise ManifestError("artifact_policy.redaction_keys must be non-empty strings")

    commands = _list_value(manifest["commands"], "commands")
    if not commands:
        raise ManifestError("at least one command is required")
    ids: set[str] = set()
    for index, raw_command in enumerate(commands):
        command = _mapping_value(raw_command, f"commands[{index}]")
        for key in (
            "id",
            "status",
            "layer",
            "environment",
            "fixture",
            "owner",
            "quarantine",
            "command",
            "cwd",
            "timeout_seconds",
            "result",
            "artifacts",
            "prerequisites",
        ):
            if key not in command:
                raise ManifestError(f"commands[{index}] missing {key}")
        command_id = command["id"]
        if not isinstance(command_id, str) or not command_id or command_id in ids:
            raise ManifestError("command ids must be unique non-empty strings")
        ids.add(command_id)
        status = command["status"]
        if status not in ALLOWED_STATUSES or status in {"skip", "ignored"}:
            raise ManifestError(f"commands[{index}] has an invalid status")
        if not isinstance(command["layer"], str) or not command["layer"]:
            raise ManifestError(f"commands[{index}].layer is required")
        environment = _list_value(command["environment"], f"commands[{index}].environment")
        if not environment or any(not isinstance(item, str) or not item for item in environment):
            raise ManifestError(f"commands[{index}].environment must be non-empty strings")
        if not isinstance(command["fixture"], str) or not command["fixture"]:
            raise ManifestError(f"commands[{index}].fixture is required")
        if not isinstance(command["owner"], str) or not command["owner"]:
            raise ManifestError(f"commands[{index}].owner is required")
        if not isinstance(command["quarantine"], bool):
            raise ManifestError(f"commands[{index}].quarantine must be boolean")
        if status == "required" and command["quarantine"]:
            raise ManifestError(f"required command {command_id} cannot be quarantined")
        argv = _list_value(command["command"], f"commands[{index}].command")
        if not argv or any(not isinstance(arg, str) or not arg for arg in argv):
            raise ManifestError(f"commands[{index}].command must be a non-empty argv list")
        for arg_index, arg in enumerate(argv):
            _safe_text(arg, f"commands[{index}].command[{arg_index}]")
            if arg in SHELL_OPERATORS or any(operator in arg for operator in (";", "&&", "||", "$(", "`")):
                raise ManifestError("commands must not use shell operators")
        cwd = command["cwd"]
        _safe_relative_path(cwd, f"commands[{index}].cwd", root) if cwd != "." else None
        if cwd != "." and not (root / cwd).is_dir():
            raise ManifestError(f"commands[{index}].cwd does not exist")
        timeout = command["timeout_seconds"]
        if not isinstance(timeout, int) or isinstance(timeout, bool) or not 0 < timeout <= 3600:
            raise ManifestError(f"commands[{index}].timeout_seconds must be 1..3600")
        prerequisites = _list_value(command["prerequisites"], f"commands[{index}].prerequisites")
        if status == "planned" and not prerequisites:
            raise ManifestError(f"planned command {command_id} needs a named prerequisite")
        if any(not isinstance(item, str) or not item for item in prerequisites):
            raise ManifestError(f"commands[{index}].prerequisites must be named strings")
        result = _mapping_value(command["result"], f"commands[{index}].result")
        if result.get("success_exit_codes") != [0] or result.get("failure_is_error") is not True:
            raise ManifestError(f"commands[{index}] must fail on non-zero results")
        if result.get("stdout") != "console" or result.get("stderr") != "console":
            raise ManifestError(f"commands[{index}] must not swallow stdout/stderr")
        artifacts = _list_value(command["artifacts"], f"commands[{index}].artifacts")
        for artifact_index, artifact in enumerate(artifacts):
            _safe_relative_path(artifact, f"artifact {index}:{artifact_index}", root)
        if status in {"required", "optional"}:
            executable = argv[0]
            if "/" not in executable and shutil.which(executable) is None:
                raise ManifestError(f"required executable is unavailable: {executable}")
        if "env" in command:
            env = _mapping_value(command["env"], f"commands[{index}].env")
            for key, value in env.items():
                if re.search(r"(?i)(secret|token|password|cookie|credential|private)", str(key)):
                    raise ManifestError("secret-bearing environment overrides are forbidden")
                _safe_text(value, f"commands[{index}].env.{key}")

    test_targets = _list_value(manifest.get("test_targets", []), "test_targets")
    target_ids: set[str] = set()
    required_target_commands: set[str] = set()
    command_statuses = {
        command["id"]: command["status"]
        for command in commands
    }
    for index, raw_target in enumerate(test_targets):
        target = _mapping_value(raw_target, f"test_targets[{index}]")
        target_id = target.get("id")
        if not isinstance(target_id, str) or not target_id or target_id in target_ids:
            raise ManifestError("test target ids must be unique non-empty strings")
        target_ids.add(target_id)
        status = target.get("status")
        if status not in ALLOWED_STATUSES:
            raise ManifestError(f"test_targets[{index}] has an invalid status")
        command_id = target.get("command_id")
        if command_id not in ids:
            raise ManifestError(f"test_targets[{index}] references an unknown command")
        if command_statuses[command_id] != status:
            raise ManifestError(
                f"test_targets[{index}] status does not match command {command_id}"
            )
        if status == "required":
            required_target_commands.add(command_id)
        if status == "planned" and not target.get("prerequisite"):
            raise ManifestError(f"planned test_targets[{index}] needs a prerequisite")

    for command in commands:
        if command["status"] == "required" and command["id"] not in required_target_commands:
            raise ManifestError(
                f"required command {command['id']} is not referenced by a required target"
            )


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if len(argv) != 1:
        print("usage: check_test_manifest.py MANIFEST", file=sys.stderr)
        return 2
    try:
        validate(Path(argv[0]))
    except (OSError, ManifestError) as exc:
        print(f"check_test_manifest: FAIL: {exc}", file=sys.stderr)
        return 1
    print("check_test_manifest: PASS (bounded commands; explicit results; redacted artifacts)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
