# Browser-Use: Overview

## What It Is

**agentyc** (v0.12.6) is an async Python library that exposes Chrome browser control as a typed, deterministic API for AI agents. It bridges the gap between LLM decision-making and browser automation by:

- Wrapping CDP (Chrome DevTools Protocol) operations into structured, schema-validated actions
- Serializing DOM state (element tree, screenshots, tab info) into token-efficient representations
- Exposing those actions over MCP (Model Context Protocol) so any MCP-capable client (Claude Desktop, other agents) can drive a browser

The library is **MCP-first**: no autonomous agent loops, no built-in task planner. It gives the AI deterministic primitives and gets out of the way.

## Design Philosophy

- **Deterministic over magical**: actions have explicit schemas, return typed results, and fail loudly. No best-effort retries or hidden fallbacks.
- **Token efficiency**: DOM state can be emitted in four modes (`full`, `min`, `auto`, `focus`) to control how many tokens a state snapshot costs.
- **Deterministic extraction**: common data patterns (tables, forms, lists, links, key-value pairs) are extracted without an LLM round-trip.
- **Hard failure boundaries**: security watchdog, domain allowlists, and action timeouts enforce the execution envelope. Agents can't accidentally browse malicious domains.
- **Modular watchdog architecture**: each concern (downloads, popups, security, CAPTCHA, storage, recording) is an isolated async service that subscribes to the event bus.

## Primary Use Cases

- AI-driven web research and content extraction
- Form filling and UI automation in agentic pipelines
- Browser state capture for LLM context injection
- MCP-based tool integration with Claude Desktop or other MCP clients
- Automated screenshot and PDF generation

## Key Numbers

| Metric | Value |
|--------|-------|
| Python version | ≥ 3.11 |
| LLM providers | 15+ |
| Watchdog services | 14 |
| MCP tools exposed | 15 |
| Deterministic extractors | 6 |
| Action timeout (default) | 180s |

## Docs Index

- [Architecture](./architecture.md) — components, event bus, data flow
- [Features](./features.md) — full capability inventory
- [Tech Stack](./tech-stack.md) — dependencies and external services
- [Configuration](./configuration.md) — env vars, BrowserProfile, config file
- [API Reference](./api.md) — public Python API
