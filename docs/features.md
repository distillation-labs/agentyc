# Features

## Public MCP Surface

The public `0.1.0` release is centered on the stdio MCP server in `traverse.mcp.server`.

Exposed MCP tools:

| Tool | Description |
|------|-------------|
| `browser_navigate` | Navigate to a URL, optionally in a new tab |
| `browser_click` | Click by stable ref, legacy index, or viewport coordinates |
| `browser_type` | Type text into an input-like element |
| `browser_upload_file` | Upload a local file to a file input or upload control |
| `browser_get_state` | Return structured browser state with optional screenshot |
| `browser_extract_content` | Deterministically extract compatible page content |
| `browser_get_html` | Return page HTML or HTML for a CSS-selected element |
| `browser_screenshot` | Capture a screenshot and return viewport metadata |
| `browser_scroll` | Scroll the page up or down |
| `browser_go_back` | Navigate back in history |
| `browser_list_tabs` | List open tabs |
| `browser_switch_tab` | Switch to a tab by tab ID |
| `browser_close_tab` | Close a tab by tab ID |
| `browser_list_sessions` | List tracked browser sessions |
| `browser_close_session` | Close one tracked browser session |
| `browser_close_all` | Close all tracked browser sessions |

Resources and prompts are not exposed in the public MCP server.

## Browser State

`browser_get_state` is the primary inspection primitive for MCP clients.

Supported state modes:

| Mode | Description |
|------|-------------|
| `auto` | Default mode; uses full state on smaller pages and a compact ranked view on larger pages |
| `full` | Returns the full interactive-element payload |
| `min` | Returns a compact ranked subset of interactive elements |
| `focus` | Returns state for a single referenced element |

Notable behavior:

- Stable element refs such as `e123` are returned for targeting.
- `since_hash` supports cheap unchanged-state checks.
- Optional screenshots are returned as MCP image content, with viewport dimensions included in the text payload.

## Deterministic Extraction

`browser_extract_content` is deterministic-only in the public MCP server.

Supported deterministic routes include:

- Links
- Link collections such as nav menus, pagination, and search results
- Tables
- Lists and checklists
- Form fields
- Key-value panels
- Images

Structured extraction is available through `output_schema` when the query matches one of those deterministic routes.

If no deterministic route matches, the MCP tool returns an explicit error instead of falling back to an LLM. This is the shipped behavior for `0.1.0`.

## Interaction Semantics

- `browser_click` and `browser_type` prefer stable refs from `browser_get_state`.
- `browser_click` also supports viewport coordinates.
- Ref-based actions attempt limited live recovery after small DOM drift.
- Action failures use machine-readable prefixes such as `Error [stale_ref]` and `Error [target_disabled]`.
- `browser_upload_file` accepts either an absolute local path or a file name from the traverse file system.

## Session Model

- Browser sessions are created lazily on first browser tool use.
- Sessions are tracked by the server for management and timeout cleanup.
- The default idle timeout is 10 minutes and can be overridden with `--session-timeout-minutes`.

## Python Surface

The package also exposes Python imports including:

- `BrowserSession`
- `BrowserProfile`
- `Tools`
- `TraverseServer`

These are importable from `traverse`, but the primary public release surface is the MCP server and the documented tool set above.
