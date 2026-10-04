# Overview

## What agentyc Ships

`agentyc` is a host-backed browser task-space runtime for coding agents,
shipped as a single native Rust binary whose primary interface is the direct CLI/SDK. MCP is compatibility-only.

The public surface in this repository is defined by these workspace crates:

- `crates/agentyc` — the binary and CLI (`mcp`, `serve`, `init`, `browser`)
- `crates/agentyc-mcp` — the MCP compatibility adapter, tool definitions, and state serialization
- `crates/agentyc-cdp` — the Chrome DevTools Protocol client
- `crates/agentyc-browser` — Chrome discovery, launch, profile, and session lifecycle
- `crates/agentyc-dom` — DOM serialization, clickable detection, and HTML→markdown
- `crates/agentyc-tools` — deterministic extraction routing

See [Architecture](./architecture.md) for how these fit together.

## Public Product Story

agentyc is designed to do a small set of things well:

- Expose direct logical task-space operations through the CLI and Node SDK.
- Provide MCP compatibility over the host-backed local adapter, with legacy CDP modes explicit.
- Return deterministic browser state with logical pages and stable element refs.
- Provide deterministic extraction for common page structures.
- Support parallel automation through leased logical pages in an enrolled Chrome profile.

The public server is not an autonomous agent framework. It does not ship a
planner, prompt loop, cloud sync workflow, or LLM-backed extraction fallback —
there is no model in the loop at all.

## Default Behavior

- The `agentyc` command starts the direct host-backed CLI; use the Node SDK for typed application workflows.
- Select `agentyc mcp` explicitly only when an MCP client needs the compatibility adapter.
- The default CLI path does not launch Chrome or attach to a copied CDP URL.
- The existing-Chrome product path uses the enrolled extension/Native Messaging bridge; live enrollment remains separately gated in Phase 0.
- The legacy direct-CDP server is explicit via `agentyc mcp --legacy-cdp`; attached legacy HTTP requires `--cdp-url`.
- Deterministic extraction remains the compatibility server's extraction mode.
- No API key is required.

## Primary Use Cases

- Direct CLI/SDK browser tooling for coding-agent workflows.
- MCP compatibility for Claude Desktop, Cursor, or other MCP-capable agents.
- Deterministic web navigation and interaction from an external agent loop.
- Browser state capture with stable refs and compact, `since_hash`-aware payloads.
- Structured extraction of tables, lists, links, forms, images, and key-value panels.
- Parallel automation where multiple principals each own leased logical pages.

## Shared Browser Positioning

The legacy compatibility surface can attach multiple MCP server processes to the same
Chrome instance through an explicit `--cdp-url`, described narrowly:

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
