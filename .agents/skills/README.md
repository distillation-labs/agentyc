# Agent Skills — Single Source of Truth

All skill definitions live here. The platform-specific directories are derived copies.

## Source of truth

```
.agents/skills/<skill-name>/
├── SKILL.md          # Canonical skill definition
├── references/       # Reference docs used in skill instructions
└── evals/            # Triggering, functional, and performance eval cases
```

`dev-contextro-mcp` is distributed by the `@contextro/skills` package.
`agentyc-browser-automation` is the canonical source for the browser-automation guide; the
end-user guide shipped by `agentyc init` is the repo-root `SKILL.md`, embedded into the binary
via `include_str!`.
The other skills in this directory are internal development skills and are not shipped to end users.

## Platform copies (SKILL.md only)

| Platform       | Directory           | Reads skills from                          |
|----------------|---------------------|--------------------------------------------|
| Claude Code    | `.claude/skills/`   | `.claude/skills/<name>/SKILL.md`           |
| GitHub Copilot | `.github/skills/`   | `.github/skills/<name>/SKILL.md`           |
| Kiro CLI       | `.kiro/skills/`     | `.kiro/skills/<name>/SKILL.md`             |
| OpenCode       | `.opencode/skills/` | `.opencode/skills/<name>/SKILL.md`         |

In-repo derived copies may contain only `SKILL.md` for compatibility.

## Updating a skill

1. Edit `.agents/skills/<name>/SKILL.md`
2. Copy to the platform directories:

```bash
for platform in .claude/skills .github/skills .opencode/skills .kiro/skills; do
  [ -d "$platform/<name>" ] && cp .agents/skills/<name>/SKILL.md $platform/<name>/SKILL.md
done
```

Or to sync all skills at once:

```bash
for platform in .claude/skills .github/skills .opencode/skills .kiro/skills; do
  [ -d "$platform" ] || continue
  for skill in .agents/skills/*/; do
    name=$(basename "$skill")
    [ -d "$platform/$name" ] && cp "$skill/SKILL.md" "$platform/$name/SKILL.md"
  done
done
```

## Browser Automation Operating Doctrine

Skills that touch browser automation, MCP runtime design, retrieval, routing, or evals should encode
the same defaults:

- Start from agentyc's actual architecture: a Rust workspace with deterministic CDP control, compact
  `browser_get_state(...)` reads, explicit inspection surfaces, and no hidden fallback automation
  loops.
- Ground improvement claims in the canonical cargo gates:
  - `cargo test --workspace` — unit + integration tests (browser tests need Chrome)
  - `AGENTYC_HEADLESS=1 cargo test -p agentyc-tests --test benchmark -- --nocapture` — performance gate
  - `cargo fmt --all -- --check` — formatting
  - `cargo clippy --workspace --all-targets -- -D warnings` — lints
- Every improvement loop must name the primary metric, baseline, breakthrough target, guardrails,
  held-out tasks or stressors, and keep/discard rule.
- Never game the benchmark: do not weaken tests or evals, overfit one site, inflate context or
  tokens without measuring, or count "click succeeded" as task success.
- Prefer minimal-context retrieval, stable refs, `since_hash`, focused state reads, deterministic
  extraction, and explicit browser or network evidence over broad reads, custom JS, or
  screenshot-only proof.
- When a skill update changes a shipped `SKILL.md`, sync the existing platform copies in the same
  change.

## Skills

| Skill | Purpose |
|---|---|
| `applied-ai-engineer` | Turn a chosen AI direction into a benchmarked, observable, production-ready system |
| `agentyc-browser-automation` | End-to-end guidance for coding agents using agentyc MCP browser tools effectively |
| `breakthrough-autoresearch` | Deep research plus ruthless metric-driven experiment loops until a target is met or disproven |
| `dev-contextro-mcp` | Use Contextro MCP for codebase discovery, search, call graphs, git history, memory |
| `docs-maintainer` | Changelogs, README updates, release notes, publication manifests, doc sync |
