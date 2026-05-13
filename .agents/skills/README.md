# Agent Skills — Single Source of Truth

All skill definitions live here. The platform-specific directories are derived copies.

## Source of truth

```
.agents/skills/<skill-name>/
├── SKILL.md          # Canonical skill definition
├── references/       # Reference docs used in skill instructions
└── evals/            # Eval cases for skill quality testing
```

`dev-contextro-mcp` is the only skill distributed by the `@contextro/skills` package.
The other skills in this directory are internal development skills and are not shipped to end users.
The distributable bundle lives in `packages/skills/skills/dev-contextro-mcp/` and is kept in sync with this development copy.

## Platform copies (SKILL.md only)

| Platform       | Directory           | Reads skills from                          |
|----------------|---------------------|--------------------------------------------|
| Claude Code    | `.claude/skills/`   | `.claude/skills/<name>/SKILL.md`           |
| GitHub Copilot | `.github/skills/`   | `.github/skills/<name>/SKILL.md`           |
| Kiro CLI       | `.kiro/skills/`     | `.kiro/skills/<name>/SKILL.md`             |
| OpenCode       | `.opencode/skills/` | `.opencode/skills/<name>/SKILL.md`         |

In-repo derived copies may contain only `SKILL.md` for compatibility. The published
`@contextro/skills` package distributes the full `dev-contextro-mcp` bundle, including
`references/` and `evals/`, to each supported skill surface.

## Updating a skill

1. Edit `.agents/skills/<name>/SKILL.md`
2. Copy to the platform directories:

```bash
for platform in .claude/skills .github/skills .opencode/skills .kiro/skills; do
  cp .agents/skills/<name>/SKILL.md $platform/<name>/SKILL.md
done
```

Or to sync all skills at once:

```bash
for platform in .claude/skills .github/skills .opencode/skills .kiro/skills; do
  for skill in .agents/skills/*/; do
    name=$(basename "$skill")
    cp "$skill/SKILL.md" "$platform/$name/SKILL.md"
  done
done
```

## Skills

| Skill | Purpose |
|---|---|
| `applied-ai-engineer` | Turn research ideas into benchmarked, observable, production-ready systems |
| `autoresearch` | Autonomous metric-driven experiment loops until a breakthrough target is met |
| `breakthrough-researcher` | Deep technical research, hypothesis generation, ranked experiment backlog |
| `dev-contextro-mcp` | Use Contextro MCP for codebase discovery, search, call graphs, git history, memory |
| `docs-maintainer` | Changelogs, README updates, release notes, publication manifests, doc sync |
| `fastmcp-server-engineer` | Build or refactor FastMCP servers, tools, resources, middleware, and validation |
| `mcp-protocol-architect` | Design MCP servers around correct protocol primitives and transport choices |
| `rust-extension-engineer` | PyO3/maturin Rust extensions for hot paths, FFI boundaries, parity testing |
