# Features

## Public MCP Surface

The public MCP server in `agentyc.mcp.server` exposes tools only. It does not publish MCP resources or prompts.

## Operator HUD Surfaces

Agentyc now ships two optional user-visible HUD surfaces on top of the same sanitized activity stream:

- **Browser HUD** via `BrowserProfile(demo_mode=True)` — a compact square-border panel injected into the controlled page
- **Desktop HUD** via `agentyc mcp --hud-overlay` or `AGENTYC_HUD_OVERLAY=1` — a small transparent desktop window for local operator visibility

Both surfaces show short intent/action labels only. They do not expose raw chain-of-thought, typed secrets, cookies, storage values, or raw network payloads. The browser HUD also includes a `REPORT` menu that copies sanitized context and opens bug, feature, or private security destinations.

### Navigation And Session Control

| Tool | Description |
|------|-------------|
| `browser_navigate` | Navigate to a URL, optionally in a new tab |
| `browser_go_back` | Go back in history |
| `browser_go_forward` | Go forward in history |
| `browser_refresh` | Reload the current page |
| `browser_wait` | Wait for a bounded number of seconds |
| `browser_wait_for_network_idle` | Wait until network activity settles |
| `browser_wait_for_request` | Wait until a matching request is observed |
| `browser_wait_for_response` | Wait until a matching response or failure is observed |
| `browser_wait_for_stable_dom` | Wait until DOM mutations settle via `MutationObserver` |
| `browser_save_as_pdf` | Save the current page as a PDF via CDP `Page.printToPDF` |
| `browser_set_viewport` | Set browser viewport width, height, and device scale factor |
| `browser_list_sessions` | List sessions tracked by the current MCP server |
| `browser_close_session` | Close one tracked session |
| `browser_close_all` | Close all tracked sessions |

### Browser State And Inspection

| Tool | Description |
|------|-------------|
| `browser_get_state` | Return structured page state with stable refs and optional screenshot |
| `browser_get_html` | Return full page HTML or HTML for a CSS-selected element |
| `browser_list_frames` | List known frames, including frame ids, URLs, and cross-origin markers |
| `browser_get_frame_html` | Return raw HTML for one frame identified by `frame_id` |
| `browser_screenshot` | Capture a viewport or full-page screenshot (WebP/JPEG/PNG, configurable format, resize, and quality) |
| `browser_find_elements` | Query the current page with a CSS selector |
| `browser_search_page` | Search for text or a regex pattern on the current page |
| `browser_wait_for_element` | Poll until text or a ref appears or disappears |
| `browser_get_focused_element` | Return the element that currently has keyboard focus |
| `browser_evaluate` | Execute JavaScript in the current page context and return the result as text |

### Storage And Browser State Persistence

| Tool | Description |
|------|-------------|
| `browser_get_storage` | Inspect `localStorage` and `sessionStorage` by origin, storage type, or key |
| `browser_set_storage` | Set one storage key for the current origin-scoped page context |
| `browser_clear_storage` | Clear one key, one storage area, or all storage for the current origin-scoped page context |
| `browser_save_state` | Persist cookies and storage to disk |
| `browser_load_state` | Restore cookies and storage from disk |

### Interaction

| Tool | Description |
|------|-------------|
| `browser_click` | Click by stable ref, legacy index, or viewport coordinates |
| `browser_set_intent` | Publish a short operator-facing intent label into the live HUD |
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
| `browser_handle_dialog` | Accept or dismiss JavaScript dialogs (alert, confirm, prompt) |

### Tabs And Cookies

| Tool | Description |
|------|-------------|
| `browser_new_tab` | Create a new browser tab and switch focus to it, optionally navigating to a URL |
| `browser_list_tabs` | List open tabs |
| `browser_switch_tab` | Switch to a tab by `tab_id` |
| `browser_close_tab` | Close a tab by `tab_id` |
| `browser_get_cookies` | Read cookies for the current page URL |
| `browser_set_cookies` | Set one or more cookies |
| `browser_clear_cookies` | Delete one cookie or clear browser cookies |

### Deterministic Extraction, Network Control, And Observability

| Tool | Description |
|------|-------------|
| `browser_extract_content` | Deterministically extract compatible content from the current page |
| `browser_get_console_logs` | Return recent browser console messages captured through CDP |
| `browser_get_network_log` | Return recent network requests captured through CDP; accepts `include_headers` to expose request and response headers |
| `browser_inspect_network_entry` | Inspect one captured network entry with optional request and response bodies |
| `browser_add_network_mock` | Add a narrow fulfill/abort network mock rule for the active tab |
| `browser_remove_network_mock` | Remove one network mock rule or all rules |
| `browser_list_network_mocks` | List active network mock rules and match counts |
| `browser_set_network_conditions` | Apply offline mode or throttling to the active tab |
| `browser_get_network_conditions` | List active per-tab network conditions |
| `browser_replay_request` | Replay a captured request with optional header or body overrides |
| `browser_export_debug_bundle` | Return one compact debug artifact with state, logs, trace summary, optional HTML, and optional screenshot |
| `browser_get_downloads` | List files downloaded during the current browser session |
| `browser_get_attribute` | Get a specific attribute value from an element by ref or index |
| `browser_clear_logs` | Clear console and/or network log buffers |
| `browser_start_trace` | Start a CDP performance trace |
| `browser_stop_trace` | Stop the active trace and return collected events as JSON |

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
- Shared-browser payloads can include `current_tab` with nested ownership/runtime metadata, `display_title`, `parent_tab_id`, and `window_bounds`.
- Screenshots are delivered as MCP image content, not embedded base64 inside the JSON state payload.
- Screenshots are automatically resized, reformatted, and compressed via the LLM screenshot pipeline (configurable through `BrowserSession` constructor params).

Recommended usage:

- Start with `mode="min"` for routine inspection.
- Use `mode="focus"` when you already have a ref and only need one element.
- Use `since_hash` for follow-up polling instead of repeating full-state reads.
- Escalate to `mode="full"` only on ambiguity or failure.

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

## LLM Screenshot Optimization

Screenshots are automatically optimized for LLM consumption through a configurable pipeline in `BrowserSession`:

| Config field | Default | Effect |
|-------------|---------|--------|
| `llm_screenshot_size` | `(480, 270)` | Resize to target before encoding. `None` keeps full resolution. |
| `llm_screenshot_format` | `"webp"` | `"png"`, `"jpeg"`, or `"webp"`. WebP gives the best size/quality ratio. |
| `llm_screenshot_quality` | `85` | Compression quality 1–100 (JPEG/WebP only). |
| `llm_screenshot_grayscale` | `False` | Grayscale conversion saves ~20-30% at marginal information loss for UI understanding. |

The pipeline runs after CDP capture and before base64 encoding — all tools that return image content (`browser_get_state`, `browser_screenshot`, `browser_export_debug_bundle`) automatically apply the active config. The MCP `mimeType` is set dynamically to match the configured format.

**Benchmark (1280×720 viewport):** Default WebP q=85 at 480×270 delivers about **8.3× smaller** than the raw PNG on the benchmark fixture (7,116B vs 58,816B base64).

## CDP-Native Observability

The MCP server records browser diagnostics directly from CDP event streams.

- `browser_get_console_logs` uses the Runtime domain rather than page-side JavaScript injection.
- `browser_get_network_log` uses the Network domain and keeps a bounded in-memory buffer.
- `browser_wait_for_request` and `browser_wait_for_response` use the same capture buffer with wall-clock observation timestamps, so agents can wait for specific API activity instead of relying on generic idle heuristics.
- `browser_inspect_network_entry` rehydrates captured request/response bodies from that same buffer without inflating the default log output.
- `browser_add_network_mock`, `browser_remove_network_mock`, `browser_list_network_mocks`, `browser_set_network_conditions`, and `browser_get_network_conditions` expose deterministic per-tab network control on top of CDP Fetch and Network domains.
- `browser_replay_request` turns a captured request into a page-context `fetch()` replay with sanitized header overrides.
- `browser_export_debug_bundle` packages the current state, recent diagnostics, pending requests, and optional screenshot into one agent-readable response.
- Network and console capture follow the active browser session and its tabs.

In addition, the server emits MCP log messages for tool start/completion/error phases, and tool results include `_meta` timing fields such as `agentyc/browser_duration_ms` so UIs can separate browser time from model reasoning time.

## Shared Browser Behavior

The CLI supports a shared-browser mode through `agentyc browser` plus `agentyc mcp --cdp-url ...`, and now also supports automatic reuse of the latest locally launched Agentyc browser via `agentyc mcp --reuse-local-browser` or `AGENTYC_REUSE_LOCAL_BROWSER=1`.

- Attaching through `--cdp-url` creates a shared-browser tab by default, or a separate runtime window when `--shared-browser-mode window` is used.
- The attached server keeps that browser alive with `keep_alive=True` for the session.
- Attach and `new_tab=true` flows automatically track the runtime's current target.
- Visible activation is controlled by `--shared-browser-focus-policy`.
- Chrome tab ownership cues are not a reliable public contract.
- Separate windows and explicit focus changes are still the most dependable operator model.
- Attached subagents stay in the shared browser profile and automatically receive a dedicated owned tab, so auth/cookies/local storage remain available across runtimes.
- Shared-browser reuse is about reusing the same browser process/profile; each runtime still claims its own collaboration tab or window rather than co-owning one tab.
- `browser_new_tab` is the recommended way for a subagent to open an additional tab after startup without disturbing other runtimes.
- Shared-browser state now groups tabs by owning runtime in a `tab_groups` payload so operators can see which agent owns how many tabs at a glance. The flat `tabs` list is still present for compatibility.

## Python Surface

The package also exports Python entry points such as:

- `AgentycServer`
- `BrowserSession`
- `BrowserProfile`
- `Tools`

Those imports are part of the package surface, but the primary public runtime remains the MCP server described above.
