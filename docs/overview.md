# Overview

## What agentyc Ships

`agentyc` is a pure MCP-first browser automation runtime for coding agents.

The public surface in this repository is defined by these modules:

- `agentyc/mcp/server.py`
- `agentyc/mcp/cli.py`
- `agentyc/mcp/state.py`
- `agentyc/tools/extraction/router.py`
- `agentyc/tools/service.py`
- `agentyc/config.py`
- `agentyc/browser/session.py`
- `agentyc/browser/session_manager.py`

Those modules describe the current public contract more accurately than legacy directories that still exist in the repo for in-progress scope reduction work.

## Public Product Story

agentyc is designed to do a small set of things well:

- Expose browser automation over stdio MCP.
- Launch or attach to a Chrome or Chromium browser over CDP.
- Return deterministic browser state with stable element refs.
- Provide deterministic extraction for common page structures.
- Surface browser-native console and network diagnostics.

The public server is not an autonomous agent framework. It does not ship a planner, prompt loop, cloud-first sync workflow, or LLM-backed extraction fallback in its default MCP path.

## Default Behavior

- The `agentyc` command starts the MCP server.
- Browser sessions are created lazily on first browser tool use.
- The default server launches a local browser unless `--cdp-url` is provided.
- Deterministic extraction is the default and only public MCP extraction mode.
- No API key is required for the default MCP browser runtime.

## Primary Use Cases

- MCP browser tooling for Claude Desktop or other MCP-capable coding agents.
- Deterministic web navigation and interaction from an external agent loop.
- Browser state capture with stable refs and compact payloads.
- Structured extraction of tables, lists, links, forms, images, and key-value panels.
- CDP-native debugging via console and network logs.

## Shared Browser Positioning

agentyc supports attaching multiple MCP server processes to the same Chrome instance through `--cdp-url`, but the implementation should be described narrowly.

- Each attached server creates its own fresh tab.
- Focus remains an explicit browser action.
- Stock Chrome and CDP do not offer reliable per-tab color ownership.
- Shared-browser collaboration is best-effort operational behavior, not a strict isolation guarantee.

## Docs Index

- [Features](./features.md)
- [Architecture](./architecture.md)
- [API Reference](./api.md)
- [Configuration](./configuration.md)
- [Tech Stack](./tech-stack.md)
