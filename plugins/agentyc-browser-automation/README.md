# Agentyc Browser Automation Plugin

This plugin gives a coding agent deterministic browser automation. The host-backed logical CLI and Node SDK are the primary interfaces; MCP is compatibility-only. MCP exposes host-backed logical routes over stdio only: the offline server lists 29, while the connected remote catalog declares 30 with 12 returning `capability_unavailable`. Headed live Chrome validation has not run, so MCP is not distribution-ready. For existing-Chrome workflows, use the [host-backed logical task-space/page API](../../docs/api-local.md). The Node SDK in `packages/agentyc-browser` is distinct from the removed Rust crate; the extension's `chrome.debugger` backend remains. See [MCP compatibility](../../docs/mcp-compatibility.md).

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

### Host-backed offline CLI check

```bash
agentyc --state-dir /tmp/agentyc-state --offline --json host status
agentyc --state-dir /tmp/agentyc-state --offline --json space list
```

The standalone direct-CDP `browser`, `run`, and `repl` commands are not shipped. Internal installation/test harnesses that use CDP are not user interfaces.
