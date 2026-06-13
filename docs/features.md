# Features

## Public MCP Surface

The MCP server (`agentyc_mcp::BrowserServer`) exposes 61 tools and nothing else
— no MCP resources, no prompts, no LLM in the loop. It runs over stdio by
default, or Streamable HTTP via `agentyc serve`.

### Navigation And Session Control

| Tool | Description |
|------|-------------|
| `browser_navigate` | Navigate to a URL, optionally in a new tab |
| `browser_go_back` | Go back in history |
| `browser_go_forward` | Go forward in history |
| `browser_refresh` | Reload the current page |
| `browser_wait` | Wait for a bounded number of seconds |
| `browser_wait_for_url` | Wait until the URL matches a substring or regex |
| `browser_wait_for_network_idle` | Wait until network activity settles |
| `browser_wait_for_request` | Wait until a matching request is observed |
| `browser_wait_for_response` | Wait until a matching response or failure is observed |
| `browser_wait_for_stable_dom` | Wait until DOM mutations settle |
| `browser_list_sessions` | List sessions tracked by the current MCP server |
| `browser_close_session` | Close one tracked session |
| `browser_close_all` | Close all tracked sessions |

### Browser State And Inspection

| Tool | Description |
|------|-------------|
| `browser_get_state` | Return structured page state with stable refs and optional screenshot |
| `browser_get_html` | Return full page HTML or HTML for a CSS-selected element |
| `browser_screenshot` | Capture a viewport or full-page screenshot |
| `browser_save_as_pdf` | Save the current page as a PDF via CDP `Page.printToPDF` |
| `browser_set_viewport` | Set viewport width, height, and device scale factor |
| `browser_list_frames` | List known frames, including ids, URLs, and cross-origin markers |
| `browser_get_frame_html` | Return raw HTML for one frame by `frame_id` |
| `browser_find_elements` | Query the current page with a CSS selector |
| `browser_search_page` | Search for text or a regex pattern on the current page |
| `browser_wait_for_element` | Poll until text or a ref appears or disappears |
| `browser_get_focused_element` | Return the element with keyboard focus |
| `browser_get_attribute` | Get an attribute value from an element by ref or index |
| `browser_evaluate` | Execute JavaScript and return the result as text |

### Storage And Persistence

| Tool | Description |
|------|-------------|
| `browser_get_storage` | Inspect `localStorage` / `sessionStorage` by origin, type, or key |
| `browser_set_storage` | Set one storage key for the current origin-scoped context |
| `browser_clear_storage` | Clear one key, one area, or all storage for the origin |
| `browser_save_state` | Persist cookies and storage to disk |
| `browser_load_state` | Restore cookies and storage from disk |

### Interaction

| Tool | Description |
|------|-------------|
| `browser_click` | Click by stable ref, index, label, or coordinates, optionally waiting for a URL change |
| `browser_right_click` | Open a context menu by ref, index, or coordinates |
| `browser_double_click` | Double-click an element or coordinates |
| `browser_hover` | Trigger hover states and hover-driven UI |
| `browser_drag_to` | Drag from one element or coordinate to another |
| `browser_type` | Clear and type into an input-like target |
| `browser_fill_form` | Batch text, selects, uploads, and checkbox/radio toggles in one round trip |
| `browser_press_key` | Send a key or shortcut |
| `browser_scroll` | Scroll the page or a scrollable element |
| `browser_scroll_to_text` | Scroll until text is visible |
| `browser_select_option` | Select an option by visible text |
| `browser_get_dropdown_options` | Inspect available options for a dropdown |
| `browser_upload_file` | Upload a local file to an upload control |
| `browser_handle_dialog` | Accept or dismiss JavaScript dialogs |

### Tabs, Cookies, And Emulation

| Tool | Description |
|------|-------------|
| `browser_new_tab` | Create a new tab and switch focus to it |
| `browser_list_tabs` | List open tabs |
| `browser_switch_tab` | Switch to a tab by `tab_id` |
| `browser_close_tab` | Close a tab by `tab_id` |
| `browser_wait_for_tab` | Wait for a new tab to appear and optionally switch to it |
| `browser_get_cookies` | Read cookies for the current page |
| `browser_set_cookies` | Set one or more cookies |
| `browser_clear_cookies` | Delete one cookie or clear all |
| `browser_grant_permissions` | Grant permissions such as geolocation |
| `browser_set_geolocation` | Override geolocation for the session |
| `browser_set_extra_headers` | Set or clear extra HTTP headers |
| `browser_set_user_agent` | Override user agent, platform, and Accept-Language |
| `browser_set_timezone` | Override the timezone |
| `browser_set_locale` | Override the locale |
| `browser_emulate_media` | Emulate CSS media type and user-preference media features |

### Deterministic Extraction

| Tool | Description |
|------|-------------|
| `browser_extract_content` | Deterministically extract compatible content from the current page |

## Browser State

`browser_get_state` is the main inspection primitive used by agents.

| Mode | Behavior |
|------|----------|
| `auto` | Full state on smaller pages, compact ranked state on larger pages |
| `full` | Full interactive-element payload |
| `min` | Compact ranked subset of interactive elements |
| `focus` | Payload for a single referenced element |

Important behavior:

- Stable refs look like `e123` and map to CDP backend node ids.
- `since_hash` returns `changed=false` when the page signature is unchanged, and
  omits the interactive-element payload while keeping `url`, `title`,
  `state_hash`, and `current_tab_id`.
- Compact ranked payloads can report `interactive_elements_truncated`,
  `interactive_elements_remaining`, and `compaction_strategy`.
- Shadow DOM is pierced during element discovery.
- Screenshots are delivered as MCP image content, not embedded base64.

Recommended usage:

- Start with `mode="min"` for routine inspection.
- Use `mode="focus"` when you already have a ref and need one element.
- Use `since_hash` for follow-up polling instead of repeating full-state reads.
- Escalate to `mode="full"` only on ambiguity or failure.

## Deterministic Extraction

`browser_extract_content` is deterministic-only. Supported route families:

- Links
- Link collections (navigation menus, pagination, result lists)
- Images
- Tables
- Lists
- Form fields
- Key-value panels

Guarantees:

- No LLM fallback.
- `output_schema` works only for compatible deterministic routes.
- Unsupported free-form requests return explicit errors.
- Responses include route metadata.

This is the default, no-API-key extraction path.

## Shared Browser Behavior

`agentyc browser` plus `agentyc mcp --cdp-url ...` lets multiple MCP server
processes share one Chrome instance:

- Attaching through `--cdp-url` reuses the running browser instead of launching one.
- The attached browser is kept alive for the session.
- Attach and `new_tab=true` flows track the runtime's current target.
- Attached subagents stay in the shared browser profile, so cookies and local
  storage remain available across runtimes while state snapshots, refs, and logs
  stay scoped to the owned tab.
- `browser_new_tab` is the recommended way for a subagent to open an additional
  tab after startup without disturbing other runtimes.
