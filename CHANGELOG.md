# Changelog

## [Unreleased]

## [0.4.0] - 2026-06-11

The first release of the Rust implementation: `agentyc` is a single native binary
that runs a deterministic, CDP-native MCP server for browser automation.

### Highlights

- **Single native binary** — no interpreter, no virtualenv, no install step. Cold
  start in a few milliseconds; idle RSS around 12 MB; binary under 10 MB. Uses
  `mimalloc` as the global allocator.
- **61 MCP tools** over stdio (and Streamable HTTP via `agentyc serve`), built on
  the `rmcp` SDK. Tool names, descriptions, and input schemas are stable.
- **Auto-launches Chrome** — `agentyc mcp` discovers and launches a Chrome/Chromium
  process on the first `browser_navigate` call. No separate browser setup needed.
- **Deterministic HTML extraction** — `browser_extract_content` uses a native Rust
  HTML parser (`scraper`) for tables, links, images, form fields, lists, and
  key-value pairs. No LLM in the loop.
- **Stable element refs** — `browser_get_state` returns `e<backend_node_id>` refs
  that survive re-renders, with `since_hash` polling and `auto`/`full`/`min`/`focus`
  modes.
- **`SKILL.md` embedded** — `agentyc init` writes the skills guide straight from the
  binary via `include_str!`, with no filesystem lookups.

### CLI

- `agentyc` / `agentyc mcp [--cdp-url]` — run the stdio MCP server.
- `agentyc serve [--host --port --cdp-url]` — run the MCP server over Streamable HTTP.
- `agentyc init [--output --print --force]` — write the bundled skills guide.
- `agentyc browser [--port --headless --detach]` — launch Chrome with remote
  debugging and print the CDP WebSocket URL.

### Configuration

- Honors `AGENTYC_HEADLESS`, `AGENTYC_ALLOWED_DOMAINS`, `AGENTYC_ACTION_TIMEOUT_S`,
  `AGENTYC_CDP_TIMEOUT_S`, `AGENTYC_PROXY_URL`, `AGENTYC_PROXY_BYPASS`,
  `AGENTYC_PROXY_USERNAME`, `AGENTYC_PROXY_PASSWORD`, and `AGENTYC_LOGGING_LEVEL`.
- Per-session isolated Chrome temp profiles — multiple agents can run independent
  browsers simultaneously without conflicts.

### Workspace

- Crates: `agentyc` (binary/CLI), `agentyc-mcp` (server + tools), `agentyc-cdp`
  (CDP client), `agentyc-browser` (launch/profile/session), `agentyc-dom` (DOM
  serialization, clickable detection, markdown), `agentyc-tools` (deterministic
  extraction routing), and `agentyc-tests` (integration harness + suites).

---

Releases prior to `0.4.0` predate the Rust implementation and are available in the
project's git history.
