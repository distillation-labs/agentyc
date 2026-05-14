# Public API Reference

This document reflects the public `0.1.0` release surface shipped by this repository.

## MCP Server

The primary public surface is the stdio MCP server implemented in `agentyc.mcp.server`.

### CLI

```bash
agentyc
```

Optional timeout override:

```bash
agentyc --session-timeout-minutes 20
```

### Python

```python
from agentyc import AgentycServer

server = AgentycServer(session_timeout_minutes=20)
await server.run()
```

`AgentycServer` exposes tools only. The public server does not expose MCP resources or prompts.

### Tool List

| Tool | Arguments |
|------|-----------|
| `browser_navigate` | `url`, optional `new_tab` |
| `browser_click` | `ref` or `index`, or `coordinate_x` plus `coordinate_y`, optional `new_tab` |
| `browser_type` | `text`, plus `ref` or `index` |
| `browser_upload_file` | `path`, plus `ref` or `index` |
| `browser_get_state` | optional `include_screenshot`, `mode`, `focus_ref`, `since_hash` |
| `browser_extract_content` | `query`, optional `extract_links`, `output_schema` |
| `browser_get_html` | optional `selector` |
| `browser_screenshot` | optional `full_page` |
| `browser_scroll` | optional `direction` |
| `browser_go_back` | none |
| `browser_list_tabs` | none |
| `browser_switch_tab` | `tab_id` |
| `browser_close_tab` | `tab_id` |
| `browser_list_sessions` | none |
| `browser_close_session` | `session_id` |
| `browser_close_all` | none |

## Deterministic Extraction API

`browser_extract_content` is deterministic-only in the public MCP server.

- Compatible queries can return plain text or structured JSON via `output_schema`.
- Unsupported extraction requests return a deterministic-route error.
- Extraction metadata is appended in an `<extraction_metadata>` block.

Example:

```json
{
  "query": "Extract the pricing table",
  "output_schema": {
    "type": "object",
    "properties": {
      "rows": {
        "type": "array"
      }
    }
  }
}
```

## Python Imports

These public imports are available from `agentyc`:

- `AgentycServer`
- `BrowserSession`
- `BrowserProfile`
- `Tools`
- `Controller`
- `ActionModel`
- `ActionResult`

The package also lazily exposes multiple LLM provider classes, but those are not part of the public MCP extraction path described above.

## BrowserSession Notes

`BrowserSession` remains available for direct Python use. Relevant methods used by the MCP server include:

- `start()`
- `close()`
- `get_browser_state_summary()`
- `get_current_page_url()`
- `get_tabs()`
- `take_screenshot()`

## Data Shapes

The MCP server returns JSON text payloads for state and metadata-oriented responses.

Important state fields include:

- `url`
- `title`
- `interactive_elements`
- `state_hash`
- `changed`
- `screenshot_dimensions` when a screenshot is included

Tab-oriented responses are JSON arrays containing:

- `tab_id`
- `url`
- `title`

Session-oriented responses are JSON arrays containing:

- `session_id`
- `created_at`
- `last_activity`
- `active`
- `current_url`
- `age_minutes`
