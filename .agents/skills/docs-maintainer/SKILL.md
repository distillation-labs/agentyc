---
name: docs-maintainer
description: >
  Use for writing and maintaining product documentation and release-facing docs:
  changelogs, README updates, release notes, version/tag docs, publication manifests,
  installation and usage docs, and cross-doc sync. Trigger when the user asks to
  add or revise documentation that reflects shipped behavior, to update release notes,
  to keep docs aligned with code changes, to remove stale claims, or to fix broken
  documentation links and references. Do not use for implementing product features,
  code architecture decisions, or general prose writing unrelated to repo documentation.
when_to_use: >
  Especially useful after code changes, before releases/tags, when doc claims drift
  from implementation, when changelog or README updates are required, or when multiple
  docs must be kept consistent across launch, release, and publication artifacts.
metadata:
  version: "0.1.0"
  category: docs-ops
  tags: [docs, changelog, readme, release-notes, publication, release, sync, links]
license: Proprietary
---

# Docs Maintainer

Maintain the documentation surface that users, release managers, and future agents
rely on.

## Agentyc Documentation Structure

The primary doc surface follows CLAUDE.md guidelines:

- **README.md**: Primary public surface. Contains comparison table (vs browser-use, Playwright MCP), tool inventory, and benchmark table.
- **`docs/` directory**: Overview, Features, and API Reference. Can defer detail to README.
- **CLAUDE.md & AGENTS.md**: Repository-specific guidance for coding agents. Keep these aligned to the public modules listed in CLAUDE.md.

When adding a new MCP tool, update all three: README tool tables, `docs/features.md`, and `docs/api.md`.
When release-gate thresholds change: update README benchmarks table.

## Core Rules

- Keep docs true to the codebase as it exists now.
- Do not publish unsupported benchmark or performance claims.
- Avoid version-specific marketing language unless backed by current package metadata.
- If there is a mismatch between code and docs, call it out explicitly rather than papering over it.
- Prefer small, targeted updates over broad rewrites.

## Benchmark Claim Provenance

- Cite the source of every latency, reliability, token, or benchmark claim: benchmark script,
  release-gate doc, test artifact, or measured output.
- If benchmark thresholds, tool counts, or public surfaces change, update all affected docs in the
  same pass, especially `README.md`, `docs/features.md`, `docs/api.md`, and `docs/release-gate.md`.
- When internal skill guidance changes, keep `.agents/skills/README.md` and the shipped platform
  copies of each edited `SKILL.md` aligned.
- Prefer removing an unverified performance claim over softening it into vague hype.

## Use It For

- Changelog entries for user-visible changes.
- README updates for installation, usage, and release instructions.
- Tag/release notes, version bumps, and artifact manifests.
- Documentation path updates after file moves or deletions.
- Cross-document consistency checks after code or packaging changes.
- Publication-facing docs that summarize benchmarks, launch notes, or evidence.

## Do Not Use It For

- Implementing product features or code refactors.
- Drafting marketing copy without a repo-docs maintenance goal.
- Rewriting large docs when a surgical patch is sufficient.
- Fabricating measurements, claims, or release outcomes.

## Preferred Workflow

```text
1. Identify the authoritative source of truth (code, tests, release artifacts).
2. Update the most specific doc first (changelog, README, release note, manifest).
3. Sync dependent docs only where necessary.
4. Remove stale links, paths, screenshots, and claims.
5. Verify all references and filenames still resolve.
```

## Common Tasks

| Task | Use | Notes |
|---|---|---|
| Add a release entry | changelog | Summarize shipped user-visible changes |
| Update install steps | README / installation docs | Match current commands and package names |
| Revise release notes | publication / tag docs | Keep claims bounded and evidence-based |
| Fix broken links | all docs | Update paths after moves or deletions |
| Sync docs after code change | README + changelog | Keep scope narrow and consistent |
| Remove outdated doc sections | docs | Prefer deletion over contradictory duplication |

## Examples

Example 1: New tool shipped
User says: "Update the docs for this new MCP tool."
Actions:
- confirm the shipped behavior from code and tests
- update README and dependent docs together
- keep claims bounded to what actually shipped
Result: the public docs match the repo state

Example 2: Path drift cleanup
User says: "Fix the broken docs links after this refactor."
Actions:
- find stale paths and commands
- update all dependent references in one pass
Result: no dead links or contradictory instructions remain

## Troubleshooting

- If docs disagree with code, prefer the code and make the mismatch explicit.
- If a claim cannot be verified from the repo, remove or soften it.
- If a change touches multiple docs, update them in one pass to avoid temporary drift.

## Output Rules

- Favor concrete file-level edits over abstract recommendations.
- Preserve the repo's documentation tone and structure.
- Call out any doc claims that cannot be verified from the current repository state.
- If a change affects multiple docs, update them in a single consistent pass.
- Attach benchmark-claim provenance when touching performance or release-gate language.

## Anti-Patterns

- leaving stale paths or obsolete commands in examples
- adding release claims without evidence
- duplicating the same content in multiple docs without a source of truth
- making docs broader than the actual shipped scope
- hiding a breaking doc change behind unrelated prose edits
- turning a measured benchmark delta into a broader claim than the data supports

## References

- `references/docs-maintenance-patterns.md`
- `references/eval-rubric.md`
- `evals/cases.yaml`
