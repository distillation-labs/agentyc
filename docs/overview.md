# Overview

## What agentyc Ships

`agentyc` is a host-backed browser task-space runtime for coding agents, shipped as a native Rust binary whose primary interface is the direct CLI/SDK. MCP is a compatibility adapter, not the primary product interface.

The public workspace includes:

- `crates/agentyc` — direct host-backed CLI, stdio MCP entry point, and separate standalone browser/CDP CLI utilities (`browser`, `run`, and `repl`).
- `crates/agentyc-mcp` — host-backed logical MCP adapter.
- `crates/agentyc-host` — broker, durable ledger, local IPC, and Native Messaging bridge.
- `crates/agentyc-core` — logical IDs, schemas, records, state, and action contracts.
- `extension/` — Chrome MV3 extension and task-space UI.
- `packages/agentyc-browser/` — Node SDK for the host protocol.

See [Architecture](./architecture.md) for how these fit together.

## Public Product Story

agentyc is designed to do a small set of things well:

- Expose direct logical task-space operations through the CLI and Node SDK.
- Provide a host-backed logical MCP adapter over stdio.
- Return browser state scoped to logical pages with stable refs.
- Support automation through the enrolled Chrome extension and Native Messaging host.
- Preserve explicit shared-profile disclosure and host authorization boundaries.

The public server is not an autonomous agent framework. It does not ship a
planner, prompt loop, cloud sync workflow, or LLM-backed extraction fallback —
there is no model in the loop at all.

## Default Behavior

- The `agentyc` command starts the host-backed logical MCP service; direct CLI commands are selected with subcommands such as `space`, `page`, `snapshot`, `action`, and `host`.
- Select `agentyc mcp` explicitly when an MCP client needs the stdio adapter.
- MCP is host-backed and logical only. It does not expose the removed `browser_*` tools or an HTTP transport.
- The offline MCP server exposes 29 logical routes. The connected remote catalog declares 30 routes, 11 of which currently fail with `capability_unavailable`.
- A limited existing-profile MCP run passed stdio, host/Native Messaging connection, and extension fence/rebind; snapshot/action reconciliation and the full Phase 8 gate remain open, so MCP is not distribution-ready. See [MCP compatibility](mcp-compatibility.md).
- The standalone direct-CDP `browser`, `run --cdp-url`, and `repl --cdp-url` CLI commands have been removed. CDP-based installation/test harnesses are not user-facing interfaces; the Node SDK at `packages/agentyc-browser` and extension `chrome.debugger` backend remain.
- No API key is required.

## Primary Use Cases

- Direct CLI/SDK browser tooling for coding-agent workflows.
- Host-backed MCP stdio compatibility for MCP-capable agents, subject to its current route and release limitations.
- Deterministic task-space and logical-page operations through the local host.
- Browser automation through an enrolled extension and Native Messaging host.
- Concurrent automation where leased logical pages are scoped to task spaces.

## MCP Boundary

MCP is an adapter over logical host operations, not a separate browser-state owner. It runs over stdio through `agentyc mcp` (or `agentyc` with no subcommand). The deterministic offline server exposes 29 routes; the connected remote catalog declares 30, with 11 returning typed `capability_unavailable` results before forwarding. A declaration is not proof of connected support. A limited existing-profile run passed connection and fence/rebind, but snapshot/action reconciliation and the full live workflows remain incomplete; distribution readiness remains blocked on route decisions and Phase 8 release gates.

The direct-CDP `browser`, `run`, and `repl` commands are standalone CLI utilities. They are not MCP modes and do not restore the deleted direct-CDP MCP server, `browser_*` MCP tools, or an MCP HTTP route.

## Docs Index

- [README](../README.md) — primary entry point and product boundaries
- [Features](./features.md)
- [Architecture](./architecture.md)
- [API Reference](./api.md)
- [Configuration](./configuration.md)
- [MCP compatibility](./mcp-compatibility.md)
- [Release Gate](./release-gate.md)
- [Tech Stack](./tech-stack.md)
