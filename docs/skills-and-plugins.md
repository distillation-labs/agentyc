# Agent Skills and Plugins

Agentyc ships an agent-facing browser automation skill and a portable plugin bundle. They teach coding agents how to use the browser as a deterministic, evidence-producing tool rather than a screenshot-driven side channel.

## Available package

- **`agentyc-browser-automation`** — host-backed logical browser automation for QA, web workflows, extraction, auth-state handling, task-space pages, network debugging, and browser-mediated verification. MCP is compatibility-only.

Package contents:

```text
plugins/agentyc-browser-automation/
├── plugin.json
└── README.md

.agents/skills/agentyc-browser-automation/
├── SKILL.md
├── references/tool-playbook.md
└── evals/cases.yaml
```

## Install with an agent

Install the binary, then use the direct host-backed CLI or Node SDK as the primary interface. See the [Direct CLI](./cli.md) and [Local browser SDK](./api-local.md) guides.

```bash
cargo install --git https://github.com/distillation-labs/agentyc agentyc
```

For MCP clients, register the optional compatibility adapter:

```json
{
  "mcpServers": {
    "agentyc": {
      "command": "agentyc",
      "args": ["mcp"],
      "env": {"AGENTYC_HEADLESS": "1"}
    }
  }
}
```

Then load `.agents/skills/agentyc-browser-automation/SKILL.md` through the coding agent's skill/plugin mechanism. The skill is the source of truth; `plugin.json` provides portable metadata and optional MCP compatibility registration.

## What makes it a superpower

The skill encodes an operational loop:

1. Read compact browser state.
2. Resolve stable element refs.
3. Act with the narrowest dedicated tool.
4. Verify the actual user-visible or network outcome.
5. Recover from stale refs, frames, dialogs, dynamic content, and failed actions using evidence.

It also teaches interface selection: use the direct CLI/SDK as the primary host-backed interface, and MCP only when compatibility with an MCP client is needed. Use REPL for interactive debugging and CLI for isolated one-shot commands. Evaluation cases cover functional behavior, performance, and safety.

## End-user bootstrap

For a single generated guide, run:

```bash
agentyc init --output agentyc-skill.md
```

That generated guide is synchronized with the canonical skill's core workflow and current CLI/REPL commands.
