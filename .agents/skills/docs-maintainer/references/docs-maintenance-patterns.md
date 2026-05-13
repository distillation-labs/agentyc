# Docs Maintenance Patterns

## Strong Practice

- Treat docs as a contract with the current repo state.
- Keep release notes factual and bounded to shipped changes.
- Update the most authoritative doc first, then sync derived docs.
- Remove stale paths, screenshots, and file names after moves or deletions.
- Use changelog entries for user-visible changes, not implementation trivia.
- Prefer concise README examples that match the current CLI or MCP surface.

## Weak Practice

- duplicating the same instructions in multiple places without a source of truth
- leaving obsolete install commands, package names, or file paths
- claiming features or metrics that are not verifiable in the repo
- mixing roadmap items into release documentation
- overexplaining simple maintenance edits with long prose
