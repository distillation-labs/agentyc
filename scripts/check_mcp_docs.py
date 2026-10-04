#!/usr/bin/env python3
"""Audit primary documentation for obsolete MCP-first/browser guidance."""

from __future__ import annotations

import argparse
import os
import re
import stat
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REQUIRED_DOCS = (
    Path("README.md"),
    Path("docs/overview.md"),
    Path("docs/cli.md"),
    Path("docs/api-local.md"),
)
MAX_FILE_BYTES = 1024 * 1024
MAX_TOTAL_BYTES = 8 * 1024 * 1024
MAX_FILES = 256

LEGACY_HEADING_RE = re.compile(r"\b(?:legacy|compatibility|adapter)\b", re.IGNORECASE)
EXPLICIT_COMPATIBILITY_PREFIX_RE = re.compile(
    r"^\s*(?:[-*]\s*)?\*\*(?:legacy\s+compatibility\s+only|"
    r"compatibility-only|adapter-only)\b",
    re.IGNORECASE,
)
NEGATION_RE = re.compile(
    r"\b(?:not|never|no|don't|doesn't|do not|does not|must not|should not|"
    r"cannot|can't|without|avoid|instead of|rather than|not the default|"
    r"not primary|compatibility-only)\b",
    re.IGNORECASE,
)
RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "mcp_as_default_or_primary",
        re.compile(
            r"\b(?:mcp[- ]first|mcp\s+(?:is|as)\s+(?:the\s+)?(?:default|primary|canonical)|"
            r"(?:default|primary|canonical)\s+(?:interface|path|product|api|workflow)\s+"
            r"(?:is|uses|starts|runs)?\s*mcp|"
            r"(?:default|primary)\s+(?:command|entry\s+point)\s+.{0,40}\bmcp|"
            r"(?:starts|runs|launches)\s+(?:the\s+)?(?:host-backed\s+)?mcp\s+(?:adapter|server))\b",
            re.IGNORECASE,
        ),
    ),
    (
        "raw_tab_or_target_id_recommendation",
        re.compile(
            r"\b(?:use|pass|provide|select|switch|close|identify|address|"
            r"choose|enter|supply)\b[^\n]{0,100}\b(?:raw\s+)?(?:tab_id|target_id|tab\s+id|target\s+id)\b|"
            r"\b(?:tab_id|target_id|tab\s+id|target\s+id)\b[^\n]{0,80}"
            r"\b(?:is|are|as)\s+(?:the\s+)?(?:primary|canonical|public)\s+(?:identity|handle|identifier)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "id_name_output_format",
        re.compile(r"(?:\[\s*id\s*\]\s*name|\[\s*\d+\s*\]\s*[A-Z][\w.-]+)", re.IGNORECASE),
    ),
    (
        "global_close_recommendation",
        re.compile(
            r"\b(?:browser_close_all|close_all\s*\(|close\s+all\s+(?:tabs?|pages?|sessions?|browsers?))\b",
            re.IGNORECASE,
        ),
    ),
    (
        "browser_launch_or_download_recommendation",
        re.compile(
            r"\b(?:agentyc\s+browser\s+(?:--[\w-]+|\[)|--launch-chrome\b|"
            r"(?:launch|start|open|download|install)\s+(?:google\s+)?chrome\b|"
            r"download\s+(?:the\s+)?(?:chrome|chromium)\s+browser\b)",
            re.IGNORECASE,
        ),
    ),
)


class DocsAuditError(ValueError):
    """Documentation evidence is unsafe, incomplete, or outside configured bounds."""


def _read_text(path: Path, root: Path) -> str:
    try:
        relative = path.relative_to(root)
        current = root
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise DocsAuditError(f"not a regular non-symlink file: {relative.as_posix()}")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as handle:
            metadata = os.fstat(handle.fileno())
            if not stat.S_ISREG(metadata.st_mode):
                raise DocsAuditError(f"not a regular non-symlink file: {relative.as_posix()}")
            if metadata.st_size > MAX_FILE_BYTES:
                raise DocsAuditError(f"file exceeds {MAX_FILE_BYTES} bytes: {relative.as_posix()}")
            data = handle.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            raise DocsAuditError(f"file exceeds {MAX_FILE_BYTES} bytes: {relative.as_posix()}")
        return data.decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise DocsAuditError(f"cannot read documentation file: {path}") from exc


def _primary_doc_paths(root: Path) -> list[Path]:
    paths = set(root.glob("docs/*.md"))
    for relative in (
        Path("README.md"),
        Path(".agents/skills/agentyc-browser-automation/SKILL.md"),
        Path("plugins/agentyc-browser-automation/README.md"),
    ):
        candidate = root / relative
        if candidate.exists() or candidate.is_symlink():
            paths.add(candidate)
    return sorted(paths, key=lambda path: path.relative_to(root).as_posix())


def _is_legacy_section(line_number: int, headings: list[tuple[int, int, bool]]) -> bool:
    stack: list[tuple[int, bool]] = []
    for heading_line, level, is_legacy in headings:
        if heading_line > line_number:
            break
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, is_legacy))
    return any(is_legacy for _, is_legacy in stack)


def _violations_for_text(relative: str, text: str) -> list[str]:
    headings: list[tuple[int, int, bool]] = []
    for number, line in enumerate(text.splitlines(), 1):
        heading = re.match(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if heading:
            headings.append((number, len(heading.group(1)), bool(LEGACY_HEADING_RE.search(heading.group(2)))))

    findings: list[str] = []
    for number, line in enumerate(text.splitlines(), 1):
        if (
            not line.strip()
            or _is_legacy_section(number, headings)
            or EXPLICIT_COMPATIBILITY_PREFIX_RE.match(line)
        ):
            continue
        clauses = re.split(r"(?<=[.!?])\s+|[;|]", line)
        for rule, pattern in RULES:
            for clause in clauses:
                match = pattern.search(clause)
                if match and not NEGATION_RE.search(clause[: match.start()]):
                    evidence = " ".join(line.strip().split())[:180]
                    findings.append(f"{relative}:{number}: {rule}: {evidence}")
                    break
    return findings


def _policy_findings(texts: list[str]) -> list[str]:
    corpus = "\n".join(texts)
    missing: list[str] = []
    direct_primary = re.compile(
        r"\b(?:direct\s+(?:host/)?CLI/SDK|CLI/SDK|direct\s+CLI\s+and\s+SDK)[^\n.]{0,60}"
        r"\b(?:is\s+)?(?:the\s+)?(?:primary|canonical)\b",
        re.IGNORECASE,
    )
    if not direct_primary.search(corpus):
        missing.append("policy: missing explicit direct CLI/SDK primary statement")
    if not re.search(r"\bMCP\s+is\s+compatibility-only\b", corpus, re.IGNORECASE):
        missing.append("policy: missing explicit MCP compatibility-only statement")
    return missing


def check(root: Path) -> list[str]:
    """Return sorted findings; missing, unsafe, or excessive input raises an error."""
    try:
        root = root.resolve(strict=True)
    except OSError as exc:
        raise DocsAuditError("repository root is missing or unreadable") from exc
    if not root.is_dir():
        raise DocsAuditError("repository root is not a directory")

    for relative in REQUIRED_DOCS:
        if not (root / relative).exists():
            raise DocsAuditError(f"required primary document is missing: {relative.as_posix()}")

    paths = _primary_doc_paths(root)
    if len(paths) > MAX_FILES:
        raise DocsAuditError(f"documentation file count exceeds {MAX_FILES}")
    total_bytes = 0
    findings: list[str] = []
    policy_texts: list[str] = []
    for path in paths:
        relative = path.relative_to(root).as_posix()
        text = _read_text(path, root)
        total_bytes += len(text.encode("utf-8"))
        if total_bytes > MAX_TOTAL_BYTES:
            raise DocsAuditError(f"documentation input exceeds {MAX_TOTAL_BYTES} bytes")
        policy_texts.append(text)
        if relative != "docs/mcp-compatibility.md":
            findings.extend(_violations_for_text(relative, text))
    findings.extend(_policy_findings(policy_texts))
    return sorted(findings)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(ROOT), help="repository root")
    parser.add_argument(
        "--negative-output-audit",
        action="store_true",
        help="run the static negative-output documentation audit (default)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        findings = check(Path(args.root).expanduser())
    except DocsAuditError as exc:
        print(f"check_mcp_docs: FAIL: {exc}", file=sys.stderr)
        return 1
    if findings:
        print("check_mcp_docs: FAIL")
        for finding in findings:
            print(f"- {finding}")
        return 1
    print("check_mcp_docs: PASS (primary docs recommend direct CLI/SDK; MCP is compatibility-only)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
