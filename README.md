# traverse

Lightweight, MCP-first browser automation for coding agents.

This repository currently ships a public stdio MCP server for deterministic browser control plus the underlying Python browser/session primitives used by that server.

## What ships in `0.1.0`

- A stdio MCP server exposed by the `traverse` console script.
- Deterministic browser tools for navigation, interaction, state inspection, screenshots, HTML access, file upload, tab management, and session cleanup.
- Deterministic-only MCP extraction. `browser_extract_content` does not use an LLM in the public MCP server.
- Python imports such as `BrowserSession`, `BrowserProfile`, `Tools`, and `TraverseServer` for direct integration.

## Install

```bash
uv tool install traverse
```

Or from source:

```bash
uv venv --python 3.11
source .venv/bin/activate
uv sync --dev
```

## Run The MCP Server

```bash
traverse
```

Optional timeout override:

```bash
traverse --session-timeout-minutes 20
```

The server uses stdio transport and advertises `server_name="traverse"` with `server_version="0.1.0"`.

## MCP Tool Surface

- `browser_navigate`
- `browser_click`
- `browser_type`
- `browser_upload_file`
- `browser_get_state`
- `browser_extract_content`
- `browser_get_html`
- `browser_screenshot`
- `browser_scroll`
- `browser_go_back`
- `browser_list_tabs`
- `browser_switch_tab`
- `browser_close_tab`
- `browser_list_sessions`
- `browser_close_session`
- `browser_close_all`

`browser_click` and `browser_type` accept stable element refs from `browser_get_state`, such as `e123`.

## Deterministic Extraction

`browser_extract_content` is deterministic-only in the public MCP server.

- Compatible links, link collections, tables, lists, form fields, key-value blocks, and image queries can be extracted without an LLM.
- Optional `output_schema` supports deterministic structured extraction for compatible queries.
- Unsupported free-form extraction requests return an error instead of silently falling back to an LLM.
- Responses include `<extraction_metadata>` so callers can inspect the deterministic route and partial/truncation markers.

## Development

```bash
./scripts/lint.sh
./scripts/test.sh
uv build
```

`./scripts/test.sh` runs the targeted deterministic MCP/browser-core CI suite.

## Release Notes

- Package version: `0.1.0`
- Build artifacts: `uv build`
- Publish workflow: `.github/workflows/workflow.yml`
- Trusted publisher repository: `distillation-labs/traverse`
