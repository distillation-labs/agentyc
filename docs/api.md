# Public API Reference

This document describes the public contract implemented by the `agentyc` binary:
its CLI and its 61 MCP tools.

## CLI

The binary is `agentyc`. It is built with `clap` and exposes four subcommands.

### `agentyc` / `agentyc mcp` — run the MCP server (stdio)

```bash
agentyc           # no-subcommand form, alias for `agentyc mcp`
agentyc mcp
```

| Argument | Description |
|----------|-------------|
| `--cdp-url` | Attach to an existing Chrome/Chromium instance over CDP instead of launching a local browser. Accepts a `ws://`/`wss://` debugger URL or an HTTP endpoint. |

### `agentyc serve` — run the MCP server (Streamable HTTP)

```bash
agentyc serve --host 127.0.0.1 --port 8765
```

| Argument | Default | Description |
|----------|---------|-------------|
| `--host` | `127.0.0.1` | Bind address. |
| `--port` | `8765` | Bind port. The server is mounted at `/mcp`. |
| `--cdp-url` | — | Attach to an existing browser over CDP. |

### `agentyc init` — write the skills guide

```bash
agentyc init
agentyc init --output .cursor/rules/agentyc.md
agentyc init --print
```

Writes the bundled agent skills guide to a file your coding agent can read. It covers the
`read -> ref -> act -> verify` loop, tool selection, frontend choice (MCP, REPL, CLI),
`since_hash` polling, error recovery, multi-tab handoff, extraction routes, auth persistence,
safety, and a quick-reference tool list. The canonical installable skill and portable plugin
are documented in [`docs/skills-and-plugins.md`](skills-and-plugins.md).

| Argument | Default | Description |
|----------|---------|-------------|
| `--output` | `agentyc-skill.md` | Destination path. |
| `--print` | — | Print to stdout instead of writing a file. |
| `--force` | — | Overwrite the destination if it already exists. |

### `agentyc browser` — launch a shared browser

```bash
agentyc browser --port 9222 --detach
```

Launches Chrome with remote debugging and prints its CDP WebSocket URL, for use
with `--cdp-url`.

| Argument | Default | Description |
|----------|---------|-------------|
| `--port` | `9222` | Remote debugging port. |
| `--headless` | — | Launch headless (`--headless=new`). |
| `--detach` | — | Print the CDP URL and exit without waiting on the process. |

## MCP Server

The server is `agentyc_mcp::BrowserServer`, run via `agentyc_mcp::run_stdio`
(stdio) or an `axum`-hosted `StreamableHttpService` (HTTP). It implements
`rmcp`'s `ServerHandler` and advertises tools only — no resources or prompts.

Startup path:

1. `crates/agentyc/src/main.rs` parses the CLI.
2. MCP mode calls `agentyc_mcp::run_stdio(cdp_url)`.
3. `BrowserServer::new()` composes six tool routers (61 tools) and slims their
   input schemas.
4. `rmcp` routes each `tools/call` to the matching `#[rmcp::tool]` handler.

## MCP Tools

### Navigation And Waits

| Tool | Arguments |
|------|-----------|
| `browser_navigate` | `url`, optional `new_tab` |
| `browser_go_back` | none |
| `browser_go_forward` | none |
| `browser_refresh` | none |
| `browser_wait` | optional `seconds` |
| `browser_wait_for_url` | optional `url_substring`, `url_regex`, `timeout_seconds` |
| `browser_wait_for_network_idle` | optional `timeout_seconds`, `idle_duration_ms` |
| `browser_wait_for_request` | optional `url_substring`, `url_regex`, `method`, `resource_type`, `timeout_seconds`, `include_headers` |
| `browser_wait_for_response` | optional `url_substring`, `url_regex`, `method`, `resource_type`, `status`, `timeout_seconds`, `include_headers` |
| `browser_wait_for_stable_dom` | optional `timeout_seconds`, `quiet_ms` |

### Page State And Reading

| Tool | Arguments |
|------|-----------|
| `browser_get_state` | optional `mode`, `focus_ref`, `since_hash`, `include_screenshot` |
| `browser_get_html` | optional `selector` |
| `browser_screenshot` | optional `full_page` |
| `browser_save_as_pdf` | optional `file_name`, `print_background`, `landscape`, `scale`, `paper_format` |
| `browser_set_viewport` | `width`, `height`, optional `device_scale_factor` |

### Interaction

| Tool | Arguments |
|------|-----------|
| `browser_click` | one of `ref` / `index` / `label` / (`coordinate_x` + `coordinate_y`); optional `wait_for_url_substring`, `wait_for_url_regex`, `url_timeout_seconds` |
| `browser_right_click` | `ref` or `index`, or `coordinate_x` + `coordinate_y` |
| `browser_double_click` | `ref` or `index`, or `coordinate_x` + `coordinate_y` |
| `browser_hover` | `ref` or `index`, or `coordinate_x` + `coordinate_y` |
| `browser_drag_to` | source and target refs or coordinates, optional `steps` |
| `browser_type` | `text`, plus `ref`, `index`, or `label` |
| `browser_fill_form` | `fields[]`, each with `ref`/`index`/`label` and one of `text`/`option_text`/`path`/`checked` |
| `browser_press_key` | `key` |
| `browser_scroll` | optional `direction`, `pages`, `ref`, `index` |
| `browser_scroll_to_text` | `text` |
| `browser_select_option` | `text`, plus `ref`, `index`, or `label` |
| `browser_get_dropdown_options` | optional `ref`, `index`, `label` |
| `browser_upload_file` | `path`, plus `ref`, `index`, or `label` |
| `browser_handle_dialog` | optional `accept`, `prompt_text` |

### Inspection And Extraction

| Tool | Arguments |
|------|-----------|
| `browser_extract_content` | `query`, optional `extract_links`, `output_schema` |
| `browser_find_elements` | `selector`, optional `attributes`, `max_results` |
| `browser_search_page` | `pattern`, optional `regex`, `max_results` |
| `browser_wait_for_element` | optional `text`, `ref`, `appear`, `timeout_seconds` |
| `browser_get_focused_element` | none |
| `browser_get_attribute` | `name`, optional `ref`, `index` |
| `browser_evaluate` | `code` |

### Frames And Storage

| Tool | Arguments |
|------|-----------|
| `browser_list_frames` | none |
| `browser_get_frame_html` | `frame_id` |
| `browser_get_storage` | optional `origin`, `storage_type`, `key` |
| `browser_set_storage` | `origin`, `storage_type`, `key`, `value` |
| `browser_clear_storage` | `origin`, optional `storage_type`, `key` |

### Tabs And Session Control

| Tool | Arguments |
|------|-----------|
| `browser_new_tab` | optional `url` |
| `browser_list_tabs` | none |
| `browser_switch_tab` | `tab_id` |
| `browser_close_tab` | `tab_id` |
| `browser_wait_for_tab` | optional `url_substring`, `url_regex`, `timeout_seconds`, `switch_focus` |
| `browser_get_cookies` | none |
| `browser_set_cookies` | `cookies` |
| `browser_clear_cookies` | optional `name` |
| `browser_grant_permissions` | `permissions[]`, optional `origin` |
| `browser_set_geolocation` | `latitude`, `longitude`, optional `accuracy` |
| `browser_set_extra_headers` | `headers` object; pass `{}` to clear |
| `browser_set_user_agent` | `user_agent`, optional `accept_language`, `platform` |
| `browser_set_timezone` | optional `timezone_id`; pass `""` to clear |
| `browser_set_locale` | optional `locale`; omit or pass `""` to clear |
| `browser_emulate_media` | optional `media`, `color_scheme`, `reduced_motion`, `forced_colors` |
| `browser_save_state` | optional `path` |
| `browser_load_state` | `path` |
| `browser_list_sessions` | none |
| `browser_close_session` | `session_id` |
| `browser_close_all` | none |

## Result Semantics

### Errors

Tool failures are returned as `isError` tool content (not JSON-RPC errors), so
agents can read and recover from them. Known CDP failures are prefixed with a
structured code and a recovery hint: `[stale_ref]`, `[element_not_interactable]`,
`[no_browser]`, `[domain_blocked]`, `[timeout]`, `[session_error]`.

### `browser_get_state`

Returns a JSON text payload. When `include_screenshot=true`, an MCP image
content item is also returned. Key fields: `url`, `title`, `tabs`,
`current_tab_id`, `mode`, `state_hash`, `changed`, `interactive_element_count`,
`interactive_elements`, and `viewport`. Compact ranked payloads may add
`interactive_elements_truncated`, `interactive_elements_remaining`, and
`compaction_strategy`.

Interactive elements use stable refs such as `e123` (derived from CDP backend
node ids). Prefer `mode="min"` first and pass `since_hash` on follow-up reads;
an unchanged `since_hash` returns `changed=false` with no element payload.

### `browser_screenshot`

Returns JSON metadata as text plus the screenshot as a separate MCP image
content item.

### `browser_extract_content`

Deterministic-only. Compatible queries return extracted content as text;
compatible `output_schema` requests return structured JSON; unsupported requests
return an explicit deterministic-route error. There is no LLM fallback.

### `browser_evaluate`

Executes JavaScript in the current page and returns the result as text. For
multi-statement logic, wrap in an IIFE: `(function(){ ... })()`.

## Deterministic Extraction Contract

Supported deterministic route families: Links, Link collections, Images, Tables,
Lists, Form fields, and Key-value panels. `output_schema` is only valid when one
of those routes can satisfy the query.

## Related Docs

- [Architecture](./architecture.md)
- [Tech Stack](./tech-stack.md)
- [Release Gate](./release-gate.md)
