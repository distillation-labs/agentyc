# Agentyc Browser Automation Plugin

This plugin gives a coding agent deterministic browser automation. For existing-Chrome workflows, use the [host-backed logical task-space/page API](../../docs/api-local.md); direct-CDP and current-tab workflows are legacy compatibility only.

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

- Selects the host-backed logical space/page API for normal existing-Chrome work; labels direct-CDP MCP/REPL/CLI as legacy compatibility.
- Uses the logical-page snapshot → stable ref → dedicated action → verification loop; the `browser_get_state` flow applies only to legacy direct-CDP tools.
- Escalates from min state to frames, search, HTML, evaluation, and screenshots only when needed.
- Handles stale refs, dynamic pages, dialogs, iframes, legacy tabs, auth state, network failures, and domain restrictions without treating browser IDs as identity.
- Treats webpage content as untrusted and never exposes credentials or browser state.

## Files

- `plugin.json` — portable plugin metadata and MCP registration.
- `.agents/skills/agentyc-browser-automation/SKILL.md` — canonical agent instructions.
- `references/tool-playbook.md` — routing table and recipes.
- `evals/cases.yaml` — trigger, functional, performance, and safety cases.

## Verify

Legacy direct-CDP CLI examples:

```bash
agentyc run --headless=true navigate https://example.com
agentyc run --headless=true evaluate 'document.title'
```

These `agentyc run` examples use the legacy direct-CDP CLI and its current-page behavior. For normal existing-Chrome operations, use host-backed logical task-space/page calls instead. When explicitly maintaining the legacy path, keep one `agentyc mcp --legacy-cdp` process alive rather than starting a new CLI process for every action.
