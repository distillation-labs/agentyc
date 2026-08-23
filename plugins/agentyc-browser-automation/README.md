# Agentyc Browser Automation Plugin

This plugin gives a coding agent a deterministic browser-automation superpower.

## Install

```bash
cargo install --git https://github.com/distillation-labs/agentyc agentyc
agentyc --version
```

Copy or register the versioned skill at `.agents/skills/agentyc-browser-automation/SKILL.md` with your coding-agent host. This plugin ships with Agentyc v2.0.0. Register the MCP server using the host's MCP configuration:

```json
{
  "mcp": {
    "agentyc": {
      "type": "local",
      "command": ["agentyc", "mcp"],
      "env": {"AGENTYC_HEADLESS": "1"}
    }
  }
}
```

For clients that use a flat MCP server map, use:

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

## What it teaches the agent

- Selects MCP for long-lived work, REPL for interactive debugging, and CLI for one-shot commands.
- Uses the compact `browser_get_state` → stable ref → dedicated action → verification loop.
- Escalates from min state to frames, search, HTML, evaluation, and screenshots only when needed.
- Handles stale refs, dynamic pages, dialogs, iframes, tabs, auth state, network failures, and domain restrictions.
- Treats webpage content as untrusted and never exposes credentials or browser state.

## Files

- `plugin.json` — portable plugin metadata and MCP registration.
- `.agents/skills/agentyc-browser-automation/SKILL.md` — canonical agent instructions.
- `references/tool-playbook.md` — routing table and recipes.
- `evals/cases.yaml` — trigger, functional, performance, and safety cases.

## Verify

```bash
agentyc run --headless=true navigate https://example.com
agentyc run --headless=true evaluate 'document.title'
```

For repeated operations, keep one `agentyc mcp` process alive rather than starting a new CLI process for every action.
