# Architecture

## High-Level View

```text
MCP client
  |
  | stdio
  v
agentyc.mcp.server.AgentycServer
  |
  +--> agentyc.tools.service.Tools
  |      |
  |      +--> agentyc.tools.extraction.router
  |
  +--> agentyc.mcp.state
  |
  +--> agentyc.browser.session.BrowserSession
         |
         +--> agentyc.browser.session_manager.SessionManager
         |
         +--> Chrome / Chromium over CDP
```

## Public Source Of Truth Modules

These modules define the public contract that docs should follow:

- `agentyc/mcp/server.py`
- `agentyc/mcp/cli.py`
- `agentyc/mcp/state.py`
- `agentyc/tools/extraction/router.py`
- `agentyc/tools/service.py`
- `agentyc/config.py`
- `agentyc/browser/session.py`
- `agentyc/browser/session_manager.py`

## MCP Server Layer

`agentyc.mcp.server.AgentycServer` is the public stdio server.

Responsibilities:

- Register the MCP tool list.
- Lazily create a browser session on first browser tool use.
- Translate MCP arguments into tool-runtime calls.
- Return text and image content in MCP response format.
- Track recent console and network events captured from CDP.
- Close idle sessions after the configured timeout.

The server advertises no MCP resources and no MCP prompts.

## Tool Runtime Layer

`agentyc.tools.service.Tools` is the execution layer for validated browser actions.

Responsibilities:

- Validate action input through Pydantic models.
- Enforce a bounded per-action timeout.
- Dispatch typed browser events onto the session event bus.
- Run deterministic extraction through `agentyc.tools.extraction.router`.
- Return `ActionResult` payloads that the MCP server formats for clients.

For the public MCP server, extraction is invoked with `page_extraction_llm=None`, which keeps `browser_extract_content` on deterministic routes only.

## Browser Session Layer

`agentyc.browser.session.BrowserSession` owns the live browser connection.

Responsibilities:

- Launch a local browser or attach to an existing browser by CDP URL.
- Maintain an event bus for browser operations.
- Track the focused tab.
- Provide browser state summaries, screenshots, cookies, and DOM lookup helpers.
- Register and coordinate watchdog-style services around the CDP session.

`BrowserSession` is the long-lived runtime object underneath the MCP server.

## Target And Session Tracking

`agentyc.browser.session_manager.SessionManager` is the single source of truth for browser targets and CDP sessions.

Responsibilities:

- Observe `Target.attachedToTarget` and `Target.detachedFromTarget` events.
- Maintain mappings between target ids and CDP session ids.
- Initialize monitoring for page targets.
- Recover focus when the active target detaches.

This is why tab and target behavior should be documented in CDP terms rather than with older selector- or playwright-style abstractions.

## Browser State Serialization

`agentyc.mcp.state` shapes `BrowserStateSummary` objects into the MCP-facing payload.

Important behavior:

- Stable refs are generated as `e<backend_node_id>`.
- `state_hash` summarizes the page and interactive elements.
- `auto` mode falls back to ranked compaction when pages are dense.
- `focus` mode narrows the payload to one referenced element.
- Unchanged `since_hash` responses use a metadata-only fast path.
- Shared-browser payloads can expose ownership, runtime metadata, display titles, parent tab ids, and optional window bounds.

This module is the reason the public docs should talk about refs and compaction, not old integer-only element targeting.

## Deterministic Extraction Pipeline

`agentyc.tools.extraction.router` chooses a deterministic route from the extraction query.

Supported route families:

- Links
- Link collections
- Images
- Tables
- Lists
- Form fields
- Key-value blocks

If no deterministic route matches, the public MCP server returns an explicit error. It does not silently fall back to an LLM.

## Configuration Flow

`agentyc.config` merges configuration from:

1. Environment variables
2. Config file
3. Code defaults

The MCP server then combines that config with its own runtime defaults when creating `BrowserProfile` instances.

Publicly relevant defaults from the server include:

- `downloads_path=~/Downloads/agentyc-mcp`
- `keep_alive=False` for local browser sessions launched by the MCP server
- `user_data_dir=~/.config/agentyc/profiles/default`
- `headless=False` unless overridden
- `disable_security=False`

## Shared Browser Flow

When `--cdp-url` is provided:

- The server attaches to an already-running browser.
- `BrowserProfile.keep_alive` is set so the shared browser is not torn down when the MCP session ends.
- The attached runtime creates a collaboration target: a tab by default, or a separate window when `shared_browser_mode='window'`.
- Optional `shared_browser_window_bounds` can be applied when the runtime uses a separate window.
- The runtime updates its focused target automatically on attach and other internal new-target flows.
- Visible browser activation is policy-driven through `shared_browser_focus_policy` rather than assumed.

This is the public contract that exists today. Any richer collaboration UX should be described as directional unless it is implemented in the source-of-truth modules above.

## Request Flows

### `browser_get_state`

1. MCP client calls `browser_get_state`.
2. `AgentycServer` asks `BrowserSession` for a `BrowserStateSummary`.
3. `agentyc.mcp.state` compacts and serializes that summary.
4. The server returns JSON text plus optional MCP image content.

### `browser_extract_content`

1. MCP client sends `query`, optional `extract_links`, and optional `output_schema`.
2. `Tools.extract` obtains clean markdown from the current page.
3. `agentyc.tools.extraction.router` picks a deterministic route.
4. The server returns deterministic content plus extraction metadata, or a deterministic-route error.

### Navigation And Interaction

1. MCP client calls a browser tool.
2. `AgentycServer` resolves refs or indices as needed.
3. `Tools` dispatches typed browser events.
4. `BrowserSession` and its event handlers execute the CDP operations.
5. The server returns a concise text result to the MCP client.
