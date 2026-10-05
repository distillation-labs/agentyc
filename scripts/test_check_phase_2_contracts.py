#!/usr/bin/env python3
"""Mutation tests for the read-only Phase 2 contract checker."""

from __future__ import annotations

import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any

from scripts import check_phase_2_contracts as checker

ROOT = checker.ROOT


def _path_candidates(value: Any) -> list[str]:
    found: list[str] = []
    if isinstance(value, str):
        found.append(value.split("::", 1)[0])
    elif isinstance(value, list):
        for item in value:
            found.extend(_path_candidates(item))
    elif isinstance(value, dict):
        for item in value.values():
            found.extend(_path_candidates(item))
    return found


def _copy_contract_repo(destination: Path) -> None:
    manifest = checker.parse_manifest(ROOT / checker.MANIFEST_PATH)
    candidates = set(_path_candidates(manifest))
    candidates.update(
        {
            checker.MANIFEST_PATH.as_posix(),
            checker.ARTIFACT_PATH.as_posix(),
            checker.TRACEABILITY_PATH.as_posix(),
            checker.PHASE_PLAN_PATH.as_posix(),
            checker.PHASE_1_PLAN_PATH.as_posix(),
            checker.PLAN_INDEX_PATH.as_posix(),
            checker.README_PATH.as_posix(),
            checker.TOOL_CATALOG_PATH.as_posix(),
            checker.OPERATIONS_PATH.as_posix(),
        }
    )
    index = json.loads((ROOT / "tests/fixtures/mcp/index.v1.json").read_text(encoding="utf-8"))
    candidates.update(_path_candidates(index))
    candidates.add("tests/fixtures/mcp/tool_catalog.json")

    for candidate in sorted(candidates):
        path = Path(candidate)
        if path.is_absolute() or ".." in path.parts:
            continue
        source = ROOT / path
        if not source.is_file() or source.is_symlink():
            continue
        target = destination / path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)

    manifest_target = destination / checker.MANIFEST_PATH
    manifest_target.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def _mutate_manifest(root: Path, mutate) -> None:
    path = root / checker.MANIFEST_PATH
    value = json.loads(path.read_text(encoding="utf-8"))
    mutate(value)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _mutate_artifact(root: Path, mutate) -> None:
    path = root / checker.ARTIFACT_PATH
    text = path.read_text(encoding="utf-8")
    match = re.search(
        r"(?ms)(^```json phase-2-contracts-v1[ \t]*\n)(.*?)(^```[ \t]*$)",
        text,
    )
    assert match is not None
    value = json.loads(match.group(2))
    mutate(value)
    replacement = match.group(1) + json.dumps(value, indent=2) + "\n" + match.group(3)
    path.write_text(text[: match.start()] + replacement + text[match.end() :], encoding="utf-8")


class Phase2ContractCheckerTests(unittest.TestCase):
    def _repo(self):
        context = tempfile.TemporaryDirectory()
        root = Path(context.name)
        _copy_contract_repo(root)
        self.addCleanup(context.cleanup)
        return root

    def test_valid_repository_passes(self) -> None:
        manifest = checker.validate(ROOT)
        self.assertIn(manifest["status"], {"active", "complete"})
        self.assertFalse(manifest["release_eligible"])

    def test_missing_module_fails(self) -> None:
        root = self._repo()
        _mutate_manifest(
            root,
            lambda value: value["tasks"][0]["required_modules"].append(
                "crates/agentyc-core/src/missing_phase_2_module.rs"
            ),
        )
        with self.assertRaises(checker.ContractError):
            checker.validate(root)

    def test_unchecked_plan_task_or_quality_item_fails(self) -> None:
        root = self._repo()
        plan = root / checker.PHASE_PLAN_PATH
        text = plan.read_text(encoding="utf-8").replace(
            "- [x] P2-T3", "- [ ] P2-T3", 1
        )
        plan.write_text(text, encoding="utf-8")
        with self.assertRaises(checker.ContractError):
            checker.validate(root)

        root = self._repo()
        plan = root / checker.PHASE_PLAN_PATH
        text = plan.read_text(encoding="utf-8").replace(
            "- [x] Every mutation has request/action/idempotency identity and lease epoch.",
            "- [ ] Every mutation has request/action/idempotency identity and lease epoch.",
            1,
        )
        plan.write_text(text, encoding="utf-8")
        with self.assertRaises(checker.ContractError):
            checker.validate(root)

    def test_missing_required_command_fails(self) -> None:
        root = self._repo()
        _mutate_manifest(
            root,
            lambda value: value["commands"].pop(0),
        )
        with self.assertRaises(checker.ContractError):
            checker.validate(root)

    def test_missing_exact_test_symbol_fails(self) -> None:
        root = self._repo()
        _mutate_manifest(
            root,
            lambda value: value["tasks"][0]["tests"].__setitem__(
                0,
                "crates/agentyc-core/tests/contract.rs::request_envelope_symbol_was_removed",
            ),
        )
        with self.assertRaises(checker.ContractError):
            checker.validate(root)

    def test_swapped_local_native_byte_order_fails(self) -> None:
        root = self._repo()
        _mutate_manifest(
            root,
            lambda value: value["framing"]["local"].__setitem__("byte_order", "little_endian"),
        )
        with self.assertRaises(checker.ContractError):
            checker.validate(root)

    def test_missing_canonical_error_guidance_fails(self) -> None:
        root = self._repo()
        path = root / "tests/fixtures/mcp/errors/v1.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        value["errors"][0]["guidance"] = "none"
        path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
        with self.assertRaises(checker.ContractError):
            checker.validate(root)

    def test_identity_allowlist_rejects_wildcards_and_removed_legacy_paths(self) -> None:
        root = self._repo()
        _mutate_manifest(
            root,
            lambda value: value["identity"].__setitem__(
                "allowlist",
                [{
                    "path": "crates/agentyc-mcp/src/*.rs",
                    "scope": "sanitized_fixture",
                    "reason": "test wildcard rejection",
                }],
            ),
        )
        with self.assertRaises(checker.ContractError):
            checker.validate(root)

        root = self._repo()
        _mutate_manifest(
            root,
            lambda value: value["identity"].__setitem__(
                "allowlist",
                [{
                    "path": "crates/agentyc-mcp/src/legacy.rs",
                    "scope": "compatibility_only",
                    "reason": "test removed legacy path rejection",
                }],
            ),
        )
        with self.assertRaises(checker.ContractError):
            checker.validate(root)

    def test_missing_mapping_or_unexplained_unsupported_mapping_fails(self) -> None:
        root = self._repo()
        _mutate_manifest(
            root,
            lambda value: value["mappings"].pop(0),
        )
        with self.assertRaises(checker.ContractError):
            checker.validate(root)

        root = self._repo()
        def make_unexplained(value: dict[str, Any]) -> None:
            entry = next(item for item in value["mappings"] if item["key"] == "action.cancel")
            entry["reason"] = None

        _mutate_manifest(root, make_unexplained)
        with self.assertRaises(checker.ContractError):
            checker.validate(root)

    def test_missing_fixture_index_transcript_or_schema_fails(self) -> None:
        for relative in (
            "tests/fixtures/mcp/index.v1.json",
            "tests/fixtures/mcp/transcripts/stdio-initialize.v1.jsonl",
            "tests/fixtures/mcp/schemas/tools.v1.json",
        ):
            with self.subTest(relative=relative):
                root = self._repo()
                (root / relative).unlink()
                with self.assertRaises(checker.ContractError):
                    checker.validate(root)

    def test_artifact_claiming_live_or_release_evidence_fails(self) -> None:
        root = self._repo()
        _mutate_artifact(root, lambda value: value.__setitem__("live_claims", True))
        with self.assertRaises(checker.ContractError):
            checker.validate(root)

        root = self._repo()
        _mutate_artifact(root, lambda value: value.__setitem__("release_eligible", True))
        with self.assertRaises(checker.ContractError):
            checker.validate(root)

    def test_path_traversal_and_symlink_inputs_fail(self) -> None:
        root = self._repo()
        with self.assertRaises(checker.ContractError):
            checker.validate_manifest(root, "../phase-2-manifest.yaml")

        root = self._repo()
        manifest = root / checker.MANIFEST_PATH
        manifest.unlink()
        manifest.symlink_to(ROOT / checker.MANIFEST_PATH)
        with self.assertRaises(checker.ContractError):
            checker.validate(root)

    def test_artifact_requires_one_versioned_json_block(self) -> None:
        root = self._repo()
        path = root / checker.ARTIFACT_PATH
        path.write_text("# no machine-readable evidence\n", encoding="utf-8")
        with self.assertRaises(checker.ContractError):
            checker.validate(root)


if __name__ == "__main__":
    unittest.main()
