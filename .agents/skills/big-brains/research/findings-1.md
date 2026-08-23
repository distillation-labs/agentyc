# Research findings: AGENT.md / AGENTS.md for coding agents

## Source: https://agentdotmd.github.io/website/
- **Date accessed:** 2026-07-06
- **Source type:** Community site / proposed standard
- **Freshness:** No publication date found; current at access
- **Authority:** Medium
- **Key claim:** `AGENT.md` is a vendor-neutral Markdown file at the repository root that tells coding agents how the project works. It should include the project overview, development commands, code style, testing, architecture, and dependency guidance.
- **Confidence:** Medium
- **What it proves or disproves:** Singular `AGENT.md` is a real convention and is intended to be the agent-facing repo instruction file.

## Source: https://agents.md/
- **Date accessed:** 2026-07-06
- **Source type:** Community/open-format site
- **Freshness:** No publication date found; current at access
- **Authority:** Medium
- **Key claim:** `AGENTS.md` is a plain Markdown file for coding agents. It belongs at the repository root, has no required fields, and the nearest file in the directory tree takes precedence for subpaths.
- **Confidence:** High
- **What it proves or disproves:** The plural `AGENTS.md` convention is also active, and nested instruction files can override parent instructions.

## Source: https://github.blog/ai-and-ml/github-copilot/how-to-write-a-great-agents-md-lessons-from-over-2500-repositories/
- **Date accessed:** 2026-07-06
- **Source type:** Official GitHub blog
- **Freshness:** Published 2025-11-19, updated 2025-11-25
- **Authority:** High
- **Key claim:** Effective agent instruction files define a specific persona, list executable commands early, provide project structure and code-style examples, and set explicit boundaries about what the agent must not do. GitHub also recommends nested agent files for subprojects.
- **Confidence:** High
- **What it proves or disproves:** The content of an agent instruction file should be concrete, command-oriented, and boundary-heavy rather than a vague general prompt.

---

**Confirmed facts** (corroborated by 2+ independent sources):
- Agent instruction files are repo-local Markdown docs for coding agents.
- They should include project overview, commands, testing guidance, code style, and boundaries.
- Specific commands and explicit "do not" rules materially improve usefulness.
- Nested or subdirectory-scoped instruction files are a valid pattern.

**Single-source claims** (only 1 source found — needs verification):
- `AGENT.md` singular is the best canonical filename for all tools.

**Unresolved questions** (no source found / sources conflict):
- Which filename is universal across all coding agents: `AGENT.md`, `AGENTS.md`, or both?
- Whether a repo should publish only one file or keep an alias for compatibility across tools.
