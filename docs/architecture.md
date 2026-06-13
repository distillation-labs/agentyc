# Architecture

## High-Level View

```text
MCP client
  |
  | stdio (default)  ── or ──  Streamable HTTP (agentyc serve, via axum)
  v
agentyc (binary)  ── crates/agentyc/src/main.rs
  |
  v
agentyc_mcp::run_stdio / BrowserServer  ── crates/agentyc-mcp
  |
  +--> rmcp ToolRouter (61 #[rmcp::tool] handlers, six routers)
  |
  +--> tools::{navigation, state_tools, interaction,
  |            inspection, frames_storage, tabs_session}
  |
  +--> ServerState  (Arc<Mutex<ServerState>>: CdpClient, session_id, tab, browser)
  |
  +--> agentyc_tools  ── deterministic extraction routing
  |
  +--> agentyc_dom    ── DOM serialization, clickable detection, markdown
  |
  +--> agentyc_browser ── Chrome discovery, launch, profile, session lifecycle
  |
  +--> agentyc_cdp     ── CdpClient
         |
         +--> Chrome / Chromium over the Chrome DevTools Protocol
```

## Workspace Crates (source of truth)

The workspace is defined in the root `Cargo.toml` (`edition = "2024"`). Each
crate owns one concern:

| Crate | Path | Responsibility |
|-------|------|----------------|
| `agentyc` | `crates/agentyc` | Binary + CLI (`mcp`, `serve`, `init`, `browser`). |
| `agentyc-mcp` | `crates/agentyc-mcp` | The MCP server: tool definitions, schemas, dispatch, state serialization. |
| `agentyc-cdp` | `crates/agentyc-cdp` | Chrome DevTools Protocol client over WebSocket / HTTP attach. |
| `agentyc-browser` | `crates/agentyc-browser` | Chrome discovery, launch, profile/env config, session lifecycle. |
| `agentyc-dom` | `crates/agentyc-dom` | DOM tree serialization, clickable-element heuristics, HTML→markdown. |
| `agentyc-tools` | `crates/agentyc-tools` | Deterministic extraction route selection. |
| `agentyc-tests` | `crates/agentyc-tests` | Integration-test harness and ported suites. |

## CLI Layer

`crates/agentyc/src/main.rs` parses the CLI with `clap` and dispatches:

1. `agentyc` / `agentyc mcp` → `agentyc_mcp::run_stdio` (stdio JSON-RPC, the default).
2. `agentyc serve` → a Streamable HTTP server built with `axum` and
   `rmcp`'s `StreamableHttpService`.
3. `agentyc init` → writes the bundled `SKILL.md` (embedded via `include_str!`).
4. `agentyc browser` → launches Chrome with remote debugging and prints the CDP
   WebSocket URL, for shared/attached sessions.

Tracing is configured to write to stderr only — stdout is reserved for the
JSON-RPC channel — with the level taken from `AGENTYC_LOGGING_LEVEL`.

## MCP Server Layer

`agentyc_mcp::BrowserServer` is the public server. It implements `rmcp`'s
`ServerHandler` via `#[tool_handler]`, and its tools are declared with
`#[rmcp::tool(...)]` grouped into six `#[tool_router]` blocks that are summed
together in `BrowserServer::new()`:

- `tool_router_nav` — navigation and wait tools
- `tool_router_state` — state, HTML, screenshot, PDF, viewport
- `tool_router_interaction` — click, type, scroll, select, dialogs, etc.
- `tool_router_inspection` — extraction, find, search, attributes, evaluate
- `tool_router_frames` — frames and storage
- `tool_router_tabs` — tabs, cookies, emulation, session control

After composition, `slim_tool_schemas()` strips verbose `schemars`-generated
fields (`$schema`, `title`, `$defs`, per-property `format`) from every tool's
input schema to keep the `tools/list` payload compact.

Responsibilities:

- Register the MCP tool list (61 tools) and serve them over stdio or HTTP.
- Lazily launch / connect a browser on first browser tool use.
- Translate MCP arguments (typed `Deserialize` + `JsonSchema` param structs)
  into `tools::*` calls.
- Return text and image content in MCP response format.
- Surface errors as `isError` tool content with structured codes so agents can
  branch programmatically (see below).

The server advertises tools only — no MCP resources or prompts.

### Shared State

`tools::ServerState` (held as `Arc<Mutex<ServerState>>`) is the single runtime
object passed to every tool function. It owns:

- `cdp: Option<CdpClient>` — the live CDP connection.
- `session_id: Option<String>` — the attached page target's CDP session id.
- `current_tab_id: Option<String>` — the focused tab (last 4 chars of target id).
- `launched_browser: Option<LaunchedBrowser>` — kept alive for the session.

### Structured Error Codes

`tools::res` converts an `anyhow::Result<CallToolResult>` into the rmcp result
type. Instead of propagating raw failures, it maps known CDP error strings into
agent-readable, prefixed messages with recovery hints:

- `[stale_ref]` — element id no longer valid → call `browser_get_state`.
- `[element_not_interactable]` — off-screen / Shadow DOM → use coordinates or `browser_evaluate`.
- `[no_browser]` — nothing connected → call `browser_navigate` to auto-launch.
- `[domain_blocked]` — navigation outside `AGENTYC_ALLOWED_DOMAINS`.
- `[timeout]` — increase `timeout_seconds` / verify the page loaded.
- `[session_error]` — reconnect via `browser_navigate`.

## CDP Layer

`agentyc_cdp::CdpClient` speaks the Chrome DevTools Protocol directly:

- `connect` over a `ws://` / `wss://` debugger URL (`tokio-tungstenite`).
- `connect_via_http` to resolve a debugger URL from an HTTP endpoint (`reqwest`).
- `send::<T>(method, params, session_id)` issues a CDP command, optionally
  scoped to a page session, with a response timeout taken from
  `AGENTYC_CDP_TIMEOUT_S` (default 60s).

On connect, the server enables `Network`, `Runtime`, and `Page` domains
browser-wide and per page session, then attaches to the first page target.

## Browser Session Layer

`agentyc_browser` owns the local browser lifecycle:

- `find_chrome_binary()` locates Chrome/Chromium across platform-specific
  install locations and the `PLAYWRIGHT_BROWSERS_PATH` cache, honoring an
  explicit override.
- `BrowserProfile` reads runtime configuration from the environment
  (`AGENTYC_HEADLESS`, `AGENTYC_ALLOWED_DOMAINS`, `AGENTYC_PROXY_*`).
- `LaunchedBrowser` represents the spawned process; it is stored on
  `ServerState` so it lives for the duration of the MCP session.

Defaults relevant to clients:

- `headless=false` (a visible browser) unless `AGENTYC_HEADLESS=1`.
- Per-session isolated temporary profile.
- Downloads path under `~/Downloads/agentyc-mcp`.

## Browser State Serialization

The state module shapes a page snapshot into the MCP-facing payload returned by
`browser_get_state`:

- Stable refs are generated as `e<backend_node_id>` and survive re-renders.
- `state_hash` summarizes the page and interactive elements.
- Modes: `auto` (full on small pages, ranked compaction on dense pages),
  `full`, `min` (proximity-scored budget), and `focus` (single element).
- An unchanged `since_hash` returns `changed=false` with no element payload.
- Shadow DOM is pierced during element discovery.

## Deterministic Extraction Pipeline

`agentyc_tools` chooses a deterministic extraction route from the query string
for `browser_extract_content`. Supported route families:

- Links
- Link collections
- Images
- Tables
- Lists
- Form fields
- Key-value / definition blocks

If no deterministic route matches, the server returns an explicit error. It
never falls back to an LLM — there is no model in the loop.

## Shared / Attached Browser Flow

When `--cdp-url` is provided to `agentyc mcp` (or `agentyc serve`):

- The server attaches to an already-running browser instead of launching one.
- It enables the required CDP domains browser-wide and on the first page target.
- The attached browser is not torn down when the MCP session ends.

### Parallel Automation

Multiple subagent processes can share one browser:

1. A primary agent starts a shared browser with
   `agentyc browser --port 9222 --detach` and captures the printed CDP URL.
2. Each subagent runs its own `agentyc mcp --cdp-url <url>` process.
3. `browser_new_tab` gives each subagent an isolated working surface; state
   snapshots, refs, and network logs are scoped per tab while cookies and
   storage remain shared across the profile.

## Request Flows

All MCP tool calls follow the same path:

1. The MCP client sends a stdio (or HTTP) `tools/call`.
2. `rmcp` routes it to the matching `#[rmcp::tool]` method on `BrowserServer`.
3. The method deserializes typed params and calls the relevant `tools::*` fn.
4. Browser tools lazily launch / attach a browser if needed.
5. The function drives Chrome through `agentyc_cdp` and returns text — or text
   plus image content for `browser_get_state` and `browser_screenshot`.

### `browser_get_state`

1. The server reads the DOM and interactive elements over CDP.
2. The state module compacts and serializes the snapshot.
3. The server returns JSON text plus optional image content.
4. Clients should prefer `mode="min"` + `since_hash` for follow-up polling.

### `browser_extract_content`

1. The client sends `query`, optional `extract_links`, and optional `output_schema`.
2. `agentyc_dom` produces clean markdown / structured nodes from the page.
3. `agentyc_tools` picks a deterministic route.
4. The server returns deterministic content, or a deterministic-route error.
