# Public API Reference

This document describes the current public contract implemented by the repository's MCP server and package exports.

## CLI

The console entry point is `agentyc`.

### Start The MCP Server

```bash
agentyc
agentyc mcp
```

Supported MCP CLI arguments:

| Argument | Description |
|----------|-------------|
| `--session-timeout-minutes` | Idle timeout in minutes; `0` (default) keeps the session alive indefinitely |
| `--cdp-url` | Attach to an existing Chrome or Chromium instance instead of launching a local browser |
| `--runtime-label` | Human-readable ownership label for this runtime in shared-browser mode |
| `--runtime-role` | Collaboration role string for this runtime, such as `primary` or `assistant` |
| `--parent-runtime-id` | Optional parent runtime identifier for nested collaboration flows |
| `--shared-browser-mode` | Create a shared-browser tab or a separate runtime window on attach |
| `--shared-browser-window-bounds` | Optional JSON window bounds applied when shared-browser mode is `window` |
| `--shared-browser-focus-policy` | Keep the human-focused surface active or explicitly activate the runtime target |

The no-subcommand form is a backward-compatible alias for `agentyc mcp`.

### Write The Skills Guide

```bash
agentyc init
agentyc init --output .cursor/rules/agentyc.md
agentyc init --print
```

Copies the packaged skills guide to a destination file your coding agent can read. The default output path is `agentyc-skill.md`; `--output` writes to a custom destination, and `--print` writes the packaged guide to stdout instead of creating a file. The guide covers the `read -> ref -> act` loop, `since_hash` polling, dynamic-text waits, error recovery, long-page search patterns, multi-tab handoff, extraction routes, auth persistence, parallel agents, and a quick-reference tool list.

| Argument | Description |
|----------|-------------|
| `--output` | Destination path (default: `agentyc-skill.md`) |
| `--print` | Print to stdout instead of writing a file |
| `--force` | Overwrite if the destination already exists |

### Start A Shared Browser

```bash
agentyc browser --port 9222 --detach
```

Supported browser subcommand arguments:

| Argument | Description |
|----------|-------------|
| `--port` | Remote debugging port for Chrome or Chromium |
| `--headless` | Launch shared Chrome in headless mode |
| `--detach` | Print the CDP URL and exit without waiting on the browser process |

## MCP Server

The primary public surface is `agentyc.mcp.server.AgentycServer`.

```python
from agentyc import AgentycServer

server = AgentycServer(session_timeout_minutes=20)
await server.run()
```

The server exposes MCP tools only. It does not publish resources or prompts.

The runtime startup path is:

1. `agentyc` runs `agentyc.mcp.cli.main`.
2. The CLI enters MCP mode directly or through `agentyc mcp`.
3. `agentyc.mcp.server.main` constructs `AgentycServer` and serves MCP over stdio.
4. Tool calls are routed through `agentyc.mcp.tool_dispatch._execute_tool`.

## MCP Tools

### Navigation And State

| Tool | Arguments |
|------|-----------|
| `browser_navigate` | `url`, optional `new_tab` |
| `browser_go_back` | none |
| `browser_go_forward` | none |
| `browser_refresh` | none |
| `browser_wait` | optional `seconds` |
| `browser_wait_for_network_idle` | optional `timeout_seconds`, `idle_duration_ms` |
| `browser_wait_for_stable_dom` | optional `timeout_seconds`, `quiet_ms` |
| `browser_save_as_pdf` | optional `file_name`, `print_background`, `landscape`, `scale`, `paper_format` |
| `browser_set_viewport` | `width`, `height`, optional `device_scale_factor` |
| `browser_get_state` | optional `include_screenshot`, `mode`, `focus_ref`, `since_hash` |
| `browser_get_html` | optional `selector` |
| `browser_screenshot` | optional `full_page` |

### Interaction

| Tool | Arguments |
|------|-----------|
| `browser_click` | `ref` or `index`, or `coordinate_x` plus `coordinate_y`, optional `new_tab` |
| `browser_right_click` | `ref` or `index`, or `coordinate_x` plus `coordinate_y` |
| `browser_double_click` | `ref` or `index`, or `coordinate_x` plus `coordinate_y` |
| `browser_hover` | `ref` or `index`, or `coordinate_x` plus `coordinate_y` |
| `browser_drag_to` | source and target refs or coordinates, optional `steps` |
| `browser_type` | `text`, plus `ref` or `index` |
| `browser_press_key` | `key` |
| `browser_scroll` | optional `direction`, `pages`, `ref`, `index` |
| `browser_scroll_to_text` | `text` |
| `browser_select_option` | `text`, plus `ref` or `index` |
| `browser_get_dropdown_options` | optional `ref`, optional `index` |
| `browser_upload_file` | `path`, plus `ref` or `index` |
| `browser_handle_dialog` | optional `accept`, optional `prompt_text` |

### Inspection And Extraction

| Tool | Arguments |
|------|-----------|
| `browser_extract_content` | `query`, optional `extract_links`, optional `output_schema` |
| `browser_find_elements` | `selector`, optional `attributes`, optional `max_results` |
| `browser_search_page` | `pattern`, optional `regex`, optional `max_results` |
| `browser_wait_for_element` | optional `text`, optional `ref`, optional `appear`, optional `timeout_seconds` |
| `browser_get_focused_element` | none |
| `browser_get_attribute` | `name`, optional `ref`, optional `index` |
| `browser_evaluate` | `code` |

### Tabs And Tab Management

| Tool | Arguments |
|------|-----------|
| `browser_new_tab` | optional `url` (string, default: `about:blank`) |
| `browser_list_tabs` | none |
| `browser_switch_tab` | `tab_id` |
| `browser_close_tab` | `tab_id` |
| `browser_get_cookies` | none |
| `browser_set_cookies` | `cookies` |
| `browser_clear_cookies` | optional `name` |
| `browser_save_state` | optional `path` |
| `browser_load_state` | `path` |

### Observability And Session Administration

| Tool | Arguments |
|------|-----------|
| `browser_get_console_logs` | optional `level`, optional `max_entries` |
| `browser_get_network_log` | optional `type_filter`, optional `status_filter`, optional `max_entries`, optional `include_headers` (boolean, default: false) |
| `browser_get_downloads` | none |
| `browser_clear_logs` | optional `console`, optional `network` |
| `browser_start_trace` | optional `categories` |
| `browser_stop_trace` | none |
| `browser_list_sessions` | none |
| `browser_close_session` | `session_id` |
| `browser_close_all` | none |

## Result Semantics

### Progress And Timing

MCP clients may include a request `_meta.progressToken` on `tools/call`. When present, agentyc emits `notifications/progress` for long-running browser phases such as navigation, state reads, screenshot capture, extraction, and session startup.

Tool results also attach timing metadata on the first returned text content block through `_meta`:

- `agentyc/tool_name`
- `agentyc/tool_phase`
- `agentyc/browser_duration_ms`
- `agentyc/is_error`

Clients can use these fields to distinguish browser work from the agent's own reasoning time.

### `browser_get_state`

`browser_get_state` returns a JSON text payload. When `include_screenshot=true`, the server also returns an MCP image content item.

Important fields:

- `url`
- `title`
- `tabs`
- `current_tab_id` and `current_tab` when the active tab can be resolved
- `ownership` when the active tab is owned by a tracked runtime
- `runtime` when ownership metadata includes a tracked runtime
- `mode`
- `effective_mode`
- `state_hash`
- `changed`
- `focus_ref` when focus mode is requested
- `interactive_element_count`
- `interactive_elements`
- `interactive_elements_truncated`, `interactive_elements_remaining`, and `compaction_strategy` when compact ranked state is used
- `viewport`, `page`, and `scroll` when available
- `screenshot_dimensions` when a screenshot is included

For interactive use, prefer `mode="min"` first and pass `since_hash` on follow-up reads. Escalate to `mode="full"` only when the compact payload omitted an element you actually need.

Interactive elements use stable refs such as `e123`. Those refs are the preferred public targeting contract.

`tabs` and `current_tab` can include collaboration metadata when available:

- `tab_id`
- `parent_tab_id`
- `display_title`
- `ownership`
- `window_bounds`

### `browser_screenshot`

Returns JSON metadata as text and the PNG image as a separate MCP image content item.

### `browser_extract_content`

The public MCP server is deterministic-only.

- Compatible queries return extracted content in text.
- Compatible `output_schema` requests return `<structured_result>` JSON.
- Responses include `<extraction_metadata>`.
- Unsupported requests return an explicit deterministic-route error.

### `browser_evaluate`

Executes JavaScript in the current page context and returns the resulting value as text. For multi-statement logic, wrap the expression in an IIFE such as `(function(){ ... })()`.

## Deterministic Extraction Contract

Supported deterministic route families:

- Links
- Link collections
- Images
- Tables
- Lists
- Form fields
- Key-value panels

`output_schema` is only valid when one of those routes can satisfy the query.

## Tab And Session Shapes

`browser_list_tabs` returns a JSON object with:

- `tabs` — flat compatibility list of tab objects
- `tab_groups` — tabs grouped by owner/runtime for the default operator view

Each tab object contains:

- `tab_id`
- optional `parent_tab_id`
- `url`
- `title`
- optional `display_title`
- optional `ownership`
- optional `window_bounds`

Each `tab_groups[]` entry can contain:

- `group_id`
- `owner_kind`
- `display_label`
- optional `runtime_id`
- optional `runtime_label`
- optional `runtime_role`
- optional `parent_runtime_id`
- `tab_count`
- optional `current_tab_id`
- `tab_ids` — ordered list of 4-char tab IDs in this group (cross-reference with flat `tabs` for full tab data)

`browser_list_sessions` returns JSON objects with:

- `session_id`
- `created_at`
- `last_activity`
- `active`
- `current_url`
- `age_minutes`

## Python Imports

Public package imports available from `agentyc` include:

- `AgentycServer`
- `BrowserSession`
- `BrowserProfile`
- `Tools`
- `Controller`
- `ActionModel`
- `ActionResult`

The package also contains LLM-provider integrations, but those are separate from the public deterministic MCP extraction contract described here.

## Related Docs

- [Architecture](./architecture.md)
- [Release Gate](./release-gate.md)
