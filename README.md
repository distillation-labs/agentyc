# agentyc

Deterministic, MCP-first browser automation for coding agents.

`agentyc` ships a public stdio MCP server for Chrome or Chromium automation plus the Python session primitives that back that server. The public contract is centered on deterministic browser control, token-aware state snapshots, and deterministic extraction without LLM fallback.

## What It Is

- Public stdio MCP server exposed by the `agentyc` console script.
- Browser control over CDP through `agentyc.mcp.server`, `agentyc.tools.service`, `agentyc.mcp.state`, and `agentyc.browser.session`.
- Deterministic extraction routes in `agentyc.tools.extraction.router` for common page structures.
- Python imports such as `AgentycServer`, `BrowserSession`, `BrowserProfile`, and `Tools` for direct embedding.

The default MCP path does not require an API key for navigation, interaction, state inspection, screenshots, HTML access, cookies, or deterministic extraction.

## Install

```bash
uv tool install agentyc
```

From source:

```bash
uv venv --python 3.11
source .venv/bin/activate
uv sync --dev
```

## Bootstrap Your Agent

Run this once in your project directory to write a usage guide your coding agent can read:

```bash
agentyc init
```

This copies the packaged skills guide to `agentyc-skill.md` by default, or to a custom destination with `--output`. The guide covers the read->ref->act loop, `since_hash` polling, dynamic-text waits, error recovery, long-page search patterns, multi-tab handoff, extraction routes, auth persistence, and common pitfalls. Point Claude Code, Cursor, or Copilot at the generated file.

```bash
agentyc init --print          # print to stdout instead of writing a file
agentyc init --force          # overwrite an existing file
agentyc init --output my.md   # custom destination
```

## Run The MCP Server

Start the stdio MCP server:

```bash
agentyc
```

Equivalent explicit form:

```bash
agentyc mcp
```

Override the idle timeout for the browser session (default: never auto-close):

```bash
agentyc --session-timeout-minutes 20
```

Shared-browser collaboration flags are also available on `agentyc mcp` and the no-subcommand form:

- `--runtime-label`
- `--runtime-role`
- `--parent-runtime-id`
- `--shared-browser-mode`
- `--shared-browser-window-bounds`
- `--shared-browser-focus-policy`

## Shared Browser Mode

Launch a Chrome or Chromium instance with remote debugging enabled and print its CDP WebSocket URL:

```bash
agentyc browser --detach
```

Attach the MCP server to that browser instead of launching a separate browser process:

```bash
agentyc mcp --cdp-url ws://127.0.0.1:9222/devtools/browser/...
```

Current shared-browser behavior is intentionally narrow:

- Each attached MCP server creates a collaboration target in the shared browser: a tab by default, or a separate window when `--shared-browser-mode window` is used.
- Attach and `new_tab=true` flows update the runtime's focused target automatically.
- Visible browser activation is controlled by `--shared-browser-focus-policy`: `preserve` avoids stealing the human-focused surface, while `activate` explicitly foregrounds the runtime target.
- `browser_get_state` and `browser_list_tabs` surface ownership metadata, display titles, and optional window bounds for shared-browser targets.
- Stock Chrome and CDP do not provide reliable per-tab color ownership cues.
- Shared-browser workflows should be treated as best-effort collaboration, not hard isolation.

## MCP Tool Surface

Navigation and state:

- `browser_navigate`
- `browser_go_back`
- `browser_go_forward`
- `browser_refresh`
- `browser_wait`
- `browser_wait_for_network_idle`
- `browser_get_state`
- `browser_get_html`
- `browser_screenshot`

Interaction:

- `browser_click`
- `browser_right_click`
- `browser_double_click`
- `browser_hover`
- `browser_drag_to`
- `browser_type`
- `browser_press_key`
- `browser_scroll`
- `browser_scroll_to_text`
- `browser_select_option`
- `browser_get_dropdown_options`
- `browser_upload_file`

Inspection and extraction:

- `browser_extract_content`
- `browser_find_elements`
- `browser_search_page`
- `browser_wait_for_element`
- `browser_get_focused_element`
- `browser_evaluate`

Tabs, cookies, and session state:

- `browser_list_tabs`
- `browser_switch_tab`
- `browser_close_tab`
- `browser_get_cookies`
- `browser_set_cookies`
- `browser_clear_cookies`
- `browser_save_state`
- `browser_load_state`

Observability and lifecycle:

- `browser_get_console_logs`
- `browser_get_network_log`
- `browser_list_sessions`
- `browser_close_session`
- `browser_close_all`

The public server exposes tools only. It does not publish MCP resources or prompts.

## Perceived Speed

agentyc can help clients separate browser work from agent thinking time:

- MCP callers may provide a `progressToken` on tool calls; agentyc emits `notifications/progress` for long-running browser steps when present.
- Tool results include `_meta` timing fields such as `agentyc/browser_duration_ms` and a short `agentyc/tool_phase` label on the first text content block.
- Agents should prefer `browser_get_state(mode="min")`, `browser_get_state(mode="focus")`, and `since_hash` polling over repeated full-state reads.
- Agents should narrate intent briefly before a likely pause, for example `Waiting for validation to finish.`

This makes a live session feel responsive even when the browser is fast and the model is still deciding what to do next.

## State And Element Targeting

`browser_get_state` is the primary inspection primitive.

- Stable element refs such as `e123` are derived from backend node ids.
- `mode` supports `auto`, `full`, `min`, and `focus`.
- `since_hash` allows unchanged-state checks without resending interactive element payloads.
- Compact state payloads can surface `compaction_strategy`, truncation counts, ownership, and runtime metadata when relevant.
- Screenshots are returned as MCP image content, with JSON metadata in a separate text payload.

For best interactive performance, start with `mode="min"` and escalate to `full` only when the compact payload omitted an element you actually need.

Prefer refs from `browser_get_state` over legacy numeric `index` arguments.

## Deterministic Extraction

`browser_extract_content` in the public MCP server is deterministic-only.

- No LLM fallback is used in the public server.
- Compatible routes include links, link collections, images, tables, lists, form fields, and key-value blocks.
- `output_schema` is supported when the query matches a deterministic route.
- Unsupported free-form requests return an explicit error instead of silently degrading.
- Responses include `<extraction_metadata>` describing the route and truncation state.

This means the default extraction path works without any API key.

## Python Surface

The package also exposes Python imports for direct integration:

```python
from agentyc import AgentycServer, BrowserProfile, BrowserSession, Tools
```

The public product story is still MCP-first. Direct Python usage is available for embedding or lower-level control.

## Development

```bash
./scripts/lint.sh
./scripts/test.sh
uv build
```

## Docs

- `docs/overview.md`
- `docs/features.md`
- `docs/architecture.md`
- `docs/api.md`
- `docs/configuration.md`
- `docs/release-gate.md`
- `docs/tech-stack.md`
