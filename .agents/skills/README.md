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
`agentyc-browser-automation` is the canonical source for the browser-automation guide shipped by `agentyc init`,
which is flattened into `agentyc/skills/SKILL.md` for end-user distribution.
The other skills in this directory are internal development skills and are not shipped to end users.

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

## Evaluation Standard

Every active skill must ship two things:

- `references/eval-rubric.md` for qualitative pass/fail review
- `evals/cases.yaml` for concrete test prompts and success criteria

Production commands:

```bash
uv run python scripts/validate_skills.py
uv run python scripts/run_skill_evals.py
uv run pytest tests/ci/infrastructure/test_skill_quality.py
```

`validate_skills.py` enforces structural compliance with `skills-guide.md`.
`run_skill_evals.py` evaluates battle-readiness coverage from the manifests and reports pass/fail per skill.

Each `evals/cases.yaml` must cover the three evaluation tracks from `skills-guide.md`:

- triggering: obvious triggers, paraphrases, and unrelated prompts
- functional execution: real-world prompts with expected workflow or output behavior
- performance and robustness: expected improvement over a no-skill baseline, plus stressors

Use real prompts, repo-specific tool expectations, and measurable pass thresholds. Prefer battle-test
scenarios that reflect the actual repo surface over generic toy prompts.

## Consolidation Decisions

- `breakthrough-researcher` and `autoresearch` were combined into `breakthrough-autoresearch`
- `applied-ai-engineer` now focuses on productionization, harnessing, guardrails, and rollout
- `mcp-protocol-architect` was merged into `fastmcp-server-engineer`

## Skills

| Skill | Purpose |
|---|---|
| `applied-ai-engineer` | Turn a chosen AI direction into a benchmarked, observable, production-ready system |
| `async-python-engineer` | Async Python patterns for agentyc: asyncio tasks, bubus EventBus, concurrency |
| `agentyc-browser-automation` | End-to-end guidance for coding agents using Agentyc MCP browser tools effectively |
| `breakthrough-autoresearch` | Deep research plus ruthless metric-driven experiment loops until a target is met or disproven |
| `cdp-browser-engineer` | CDP browser automation: cdp-use typed client, BrowserSession, watchdogs, DOM |
| `dev-contextro-mcp` | Use Contextro MCP for codebase discovery, search, call graphs, git history, memory |
| `docs-maintainer` | Changelogs, README updates, release notes, publication manifests, doc sync |
| `fastmcp-server-engineer` | Design and implement FastMCP server surfaces, including protocol primitives, lifecycle, and transport |
| `llm-provider-engineer` | LLM provider integrations: BaseChatModel Protocol, token tracking, structured output |
| `pydantic-v2-engineer` | Pydantic v2 model design for agentyc: ConfigDict, validators, views/services split |
| `pytest-async-engineer` | Testing patterns for agentyc: pytest-asyncio, pytest-httpserver, BrowserSession lifecycle |
