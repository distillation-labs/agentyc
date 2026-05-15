# Features

## Public MCP Surface

The public MCP server in `agentyc.mcp.server` exposes tools only. It does not publish MCP resources or prompts.

### Navigation And Session Control

| Tool | Description |
|------|-------------|
| `browser_navigate` | Navigate to a URL, optionally in a new tab |
| `browser_go_back` | Go back in history |
| `browser_go_forward` | Go forward in history |
| `browser_refresh` | Reload the current page |
| `browser_wait` | Wait for a bounded number of seconds |
| `browser_wait_for_network_idle` | Wait until network activity settles |
| `browser_list_sessions` | List sessions tracked by the current MCP server |
| `browser_close_session` | Close one tracked session |
| `browser_close_all` | Close all tracked sessions |

### Browser State And Inspection

| Tool | Description |
|------|-------------|
| `browser_get_state` | Return structured page state with stable refs and optional screenshot |
| `browser_get_html` | Return full page HTML or HTML for a CSS-selected element |
| `browser_screenshot` | Capture a viewport or full-page screenshot |
| `browser_find_elements` | Query the current page with a CSS selector |
| `browser_search_page` | Search for text or a regex pattern on the current page |
| `browser_wait_for_element` | Poll until text or a ref appears or disappears |
| `browser_get_focused_element` | Return the element that currently has keyboard focus |
| `browser_evaluate` | Execute JavaScript in the page context |

### Interaction

| Tool | Description |
|------|-------------|
| `browser_click` | Click by stable ref, legacy index, or viewport coordinates |
| `browser_right_click` | Open a context menu by ref, index, or coordinates |
| `browser_double_click` | Double-click an element or coordinates |
| `browser_hover` | Trigger hover states and hover-driven UI |
| `browser_drag_to` | Drag from one element or coordinate to another |
| `browser_type` | Clear and type into an input-like target |
| `browser_press_key` | Send a key or shortcut |
| `browser_scroll` | Scroll the page or a scrollable element |
| `browser_scroll_to_text` | Scroll until text is visible |
| `browser_select_option` | Select an option by visible text |
| `browser_get_dropdown_options` | Inspect available options for a dropdown |
| `browser_upload_file` | Upload a local file to an upload control |

### Tabs, Cookies, And Persisted Browser State

| Tool | Description |
|------|-------------|
| `browser_list_tabs` | List open tabs |
| `browser_switch_tab` | Switch to a tab by `tab_id` |
| `browser_close_tab` | Close a tab by `tab_id` |
| `browser_get_cookies` | Read cookies for the current page URL |
| `browser_set_cookies` | Set one or more cookies |
| `browser_clear_cookies` | Delete one cookie or clear browser cookies |
| `browser_save_state` | Persist cookies and storage to disk |
| `browser_load_state` | Restore cookies and storage from disk |

### Deterministic Extraction And Observability

| Tool | Description |
|------|-------------|
| `browser_extract_content` | Deterministically extract compatible content from the current page |
| `browser_get_console_logs` | Return recent browser console messages captured through CDP |
| `browser_get_network_log` | Return recent network requests captured through CDP |

## Browser State

`browser_get_state` is the main inspection primitive used by agents.

Supported modes:

| Mode | Behavior |
|------|----------|
| `auto` | Full state on smaller pages, compact ranked state on larger pages |
| `full` | Full interactive-element payload |
| `min` | Compact ranked subset of interactive elements |
| `focus` | Payload for a single referenced element |

Important behavior:

- Stable refs look like `e123` and map to backend node ids.
- `since_hash` returns `changed=false` when the page signature is unchanged.
- Unchanged `since_hash` responses keep `url`, `title`, `state_hash`, `current_tab_id`, and optional `focus_ref`, but omit the interactive element payload.
- Compact modes can omit the legacy numeric `index` field.
- Compact ranked payloads can report `interactive_elements_truncated`, `interactive_elements_remaining`, and `compaction_strategy`.
- Shared-browser payloads can include `current_tab`, `ownership`, `runtime`, `display_title`, `parent_tab_id`, and `window_bounds`.
- Screenshots are delivered as MCP image content, not embedded base64 inside the JSON state payload.

## Deterministic Extraction

`browser_extract_content` is deterministic-only in the public MCP server.

Supported routes in `agentyc.tools.extraction.router`:

- Links
- Link collections such as navigation menus, pagination, and result lists
- Images
- Tables
- Lists
- Form fields
- Key-value panels

Behavioral guarantees:

- No public MCP LLM fallback is used.
- `output_schema` works only for compatible deterministic routes.
- Unsupported free-form extraction requests return explicit errors.
- Responses include route metadata through `<extraction_metadata>`.

This makes deterministic extraction the default no-API-key path for the public server.

## CDP-Native Observability

The MCP server records browser diagnostics directly from CDP event streams.

- `browser_get_console_logs` uses the Runtime domain rather than page-side JavaScript injection.
- `browser_get_network_log` uses the Network domain and keeps a bounded in-memory buffer.
- Network and console capture follow the active browser session and its tabs.

## Shared Browser Behavior

The CLI supports a shared-browser mode through `agentyc browser` plus `agentyc mcp --cdp-url ...`.

- Attaching through `--cdp-url` creates a shared-browser tab by default, or a separate runtime window when `--shared-browser-mode window` is used.
- The attached server keeps that browser alive with `keep_alive=True` for the session.
- Attach and `new_tab=true` flows automatically track the runtime's current target.
- Visible activation is controlled by `--shared-browser-focus-policy`.
- Chrome tab ownership cues are not a reliable public contract.
- Separate windows and explicit focus changes are still the most dependable operator model.

## Python Surface

The package also exports Python entry points such as:

- `AgentycServer`
- `BrowserSession`
- `BrowserProfile`
- `Tools`

Those imports are part of the package surface, but the primary public runtime remains the MCP server described above.
