# Agentyc Browser Automation Plugin

This plugin gives a coding agent deterministic browser automation. The direct host-backed CLI and Node SDK is the primary interface; MCP is compatibility-only. MCP currently exposes host-backed logical routes over stdio only: the offline server lists 29, while the connected remote catalog declares 30 with 12 returning `capability_unavailable`. Headed live Chrome validation has not run, so MCP is not distribution-ready. For existing-Chrome workflows, use the [host-backed logical task-space/page API](../../docs/api-local.md); standalone direct-CDP CLI utilities are separate from MCP. See [MCP compatibility](../../docs/mcp-compatibility.md).

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
      "command": ["agentyc", "mcp"]
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
      "args": ["mcp"]
    }
  }
}
```

## What it teaches the agent

- Selects the host-backed logical space/page API for normal existing-Chrome work; labels MCP as stdio-only and not distribution-ready.
- Uses the logical-page snapshot → stable ref → dedicated action → verification loop; it does not recommend the removed `browser_*` MCP tools.
- Uses only currently supported direct operations and reports typed capability errors instead of assuming a declared route is live.
- Handles stale refs, dynamic pages, dialogs, iframes, auth state, network failures, and domain restrictions without treating browser IDs as identity.
- Treats webpage content as untrusted and never exposes credentials or browser state.

## Files

- `plugin.json` — portable plugin metadata and MCP registration.
- `.agents/skills/agentyc-browser-automation/SKILL.md` — canonical agent instructions.
- `references/tool-playbook.md` — routing table and recipes.
- `evals/cases.yaml` — trigger, functional, performance, and safety cases.

## Verify

Standalone direct-CDP CLI examples (separate from MCP):

```bash
agentyc browser --port 9222 --detach
agentyc run --cdp-url <CDP_WEBSOCKET_URL> navigate https://example.com
agentyc run --cdp-url <CDP_WEBSOCKET_URL> evaluate 'document.title'
agentyc repl --cdp-url <CDP_WEBSOCKET_URL>
```

`browser` launches Chrome with a temporary profile and prints its CDP URL. `run` and `repl` require an explicit endpoint at runtime. For normal existing-Chrome operations, use host-backed logical task-space/page calls.
