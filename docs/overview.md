# Overview

## What agentyc Ships

`agentyc` is a pure MCP-first browser automation runtime for coding agents,
shipped as a single native Rust binary.

The public surface in this repository is defined by these workspace crates:

- `crates/agentyc` — the binary and CLI (`mcp`, `serve`, `init`, `browser`)
- `crates/agentyc-mcp` — the MCP server, tool definitions, and state serialization
- `crates/agentyc-cdp` — the Chrome DevTools Protocol client
- `crates/agentyc-browser` — Chrome discovery, launch, profile, and session lifecycle
- `crates/agentyc-dom` — DOM serialization, clickable detection, and HTML→markdown
- `crates/agentyc-tools` — deterministic extraction routing

See [Architecture](./architecture.md) for how these fit together.

## Public Product Story

agentyc is designed to do a small set of things well:

- Expose browser automation over stdio MCP (or Streamable HTTP via `agentyc serve`).
- Launch or attach to a Chrome or Chromium browser over CDP.
- Return deterministic browser state with stable element refs.
- Provide deterministic extraction for common page structures.
- Support parallel automation through per-subagent owned tabs in a shared browser.

The public server is not an autonomous agent framework. It does not ship a
planner, prompt loop, cloud sync workflow, or LLM-backed extraction fallback —
there is no model in the loop at all.

## Default Behavior

- The `agentyc` command (or `agentyc mcp`) starts the stdio MCP server.
- `crates/agentyc/src/main.rs` is the CLI entrypoint; it dispatches MCP mode
  into `agentyc_mcp::run_stdio`.
- Browser sessions are created lazily on first browser tool use.
- The default server launches a local browser unless `--cdp-url` is provided.
- Deterministic extraction is the default and only public MCP extraction mode.
- No API key is required.

## Primary Use Cases

- MCP browser tooling for Claude Desktop, Cursor, or other MCP-capable agents.
- Deterministic web navigation and interaction from an external agent loop.
- Browser state capture with stable refs and compact, `since_hash`-aware payloads.
- Structured extraction of tables, lists, links, forms, images, and key-value panels.
- Parallel automation where multiple subagents each own a dedicated tab in a shared browser.

## Shared Browser Positioning

agentyc supports attaching multiple MCP server processes to the same Chrome
instance through `--cdp-url`, described narrowly:

- Each attached server claims its own collaboration tab by default.
- Attach and `new_tab=true` flows update the runtime's focused target automatically.
- Attached subagents stay in the shared browser profile, so cookies and local
  storage remain available across runtimes, while state snapshots, element refs,
  and logs stay scoped to the owned tab.
- `browser_new_tab` remains available when a runtime needs another tab after startup.

## Docs Index

- [README](../README.md) — primary entry point with comparison table, benchmarks, and tool inventory
- [Features](./features.md)
- [Architecture](./architecture.md)
- [API Reference](./api.md)
- [Configuration](./configuration.md)
- [Release Gate](./release-gate.md)
- [Tech Stack](./tech-stack.md)
