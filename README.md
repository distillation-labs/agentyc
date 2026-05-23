# agentyc

<p align="center">
  <em>Deterministic, MCP-first browser automation for coding agents.</em><br>
  No API key needed. No LLM fallback. Just CDP, stdio MCP, and 53 tools.
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-≥3.11-3776AB?style=flat&logo=python&logoColor=white" alt="Python ≥3.11">
  <img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="MIT">
  <img src="https://img.shields.io/badge/MCP-stdio-000?style=flat&logo=modelcontextprotocol&logoColor=white" alt="MCP stdio">
  <img src="https://img.shields.io/badge/CDP-native-46BC99?style=flat" alt="CDP-native">
  <a href="https://pypi.org/project/agentyc/"><img src="https://img.shields.io/pypi/v/agentyc" alt="PyPI"></a>
  <a href="https://github.com/distillation-labs/agentyc"><img src="https://img.shields.io/github/stars/distillation-labs/agentyc?style=flat" alt="GitHub Stars"></a>
  <img src="https://hits.sh/github.com/distillation-labs/agentyc.svg?style=flat&label=views&color=46BC99" alt="Repo views">
</p>

---

## What Is It

`agentyc` ships a public stdio MCP server for browser automation. It speaks CDP directly — no Playwright, no Puppeteer, no LLM fallback. Every tool is deterministic, every response is compact, and the default path works with **zero API keys**.

**For coding agents that need to read a page, click a button, fill a form, or extract a table — agentyc is the browser backend.**

```bash
uv tool install agentyc
agentyc                          # Starts the MCP server — that's it
```

---

## How It Compares

| | agentyc | browser-use | Playwright MCP |
|---|---|---|---|
| **Protocol** | stdio MCP (native) | Python script + custom loop | MCP wrapper over library |
| **LLM required** | No | Yes (planner) | No |
| **Extraction** | Deterministic routes (7 families) | LLM-based | Raw page access |
| **State snapshots** | Token-aware, compact, `since_hash` polling | Full DOM dump | Full DOM or accessibility tree |
| **Element targeting** | Stable refs (`e123`) survive re-renders | XPath/CSS selectors | Playwright locators |
| **Browser backend** | CDP direct (no Playwright) | Playwright | Playwright |
| **Extraction API key** | Not needed | N/A | Not needed |
| **Auto-close default** | No (session stays alive) | Varies | Varies |
| **Parallel agent model** | Shared browser profile + per-runtime owned tabs | N/A | N/A |
| **Dependencies** | ~20 core (lean) | ~40+ (heavy) | Playwright + SDK |
| **Install size** | Small (Python package) | Very large | Moderate |
| **Tool count** | 53 | ~15-20 actions | ~20 tools |
| **Console/Network capture** | CDP-native built-in | Limited | Limited |
| **Deterministic extraction** | Tables, lists, forms, links, images, key-value | None (LLM only) | None |
| **Headless by default** | No (visible), flag for headless | Configurable | Configurable |

**agentyc is not a testing framework or an autonomous agent loop.** It is a browser MCP: launch it, give your agent 53 tools, let it inspect and interact — deterministically, compactly, without an LLM in the critical path.

---

## Quick Start

```bash
# 1. Install
uv tool install agentyc

# 2. Run the MCP server
agentyc

# 3. Configure your agent
agentyc init     # writes agentyc-skill.md — point Claude Code/Cursor at it
```

Add to `opencode.json`:

```json
{
  "mcp": {
    "agentyc": {
      "type": "local",
      "command": ["uv", "run", "agentyc", "mcp"]
    }
  }
}
```

From source:

```bash
uv venv --python 3.11
source .venv/bin/activate
uv sync --dev
```

---

## Bootstrap Your Agent

```bash
agentyc init                      # writes agentyc-skill.md
agentyc init --output .agent.md   # custom path
agentyc init --print              # stdout
agentyc init --force              # overwrite
```

The skills guide covers: read→ref→act loop, `since_hash` polling, precise network waits, debug bundles, dynamic-text waits, error recovery, long-page search, multi-tab handoff, extraction routes, auth persistence, parallel agents, JS evaluation, and common pitfalls.

---

## MCP Surface: 53 Tools

### Navigation & State (14 tools)

| Tool | What it does |
|------|-------------|
| `browser_navigate` | Navigate to a URL, optionally in a new tab |
| `browser_go_back` | History back |
| `browser_go_forward` | History forward |
| `browser_refresh` | Reload current page |
| `browser_wait` | Wait N seconds (bounded) |
| `browser_wait_for_network_idle` | Wait until AJAX/XHR settles |
| `browser_wait_for_request` | Wait for a matching request by URL, method, or resource type |
| `browser_wait_for_response` | Wait for a matching response or network failure by URL, method, or status |
| `browser_wait_for_stable_dom` | Wait until DOM mutations settle via `MutationObserver` |
| `browser_get_state` | **Primary primitive** — structured DOM with stable refs, screenshots, 4 modes |
| `browser_get_html` | Raw HTML (full or CSS-selected) |
| `browser_screenshot` | Viewport or full-page PNG |
| `browser_save_as_pdf` | Save current page as PDF via CDP `Page.printToPDF` |
| `browser_set_viewport` | Set browser viewport width, height, and scale |

### Interaction (13 tools)

| Tool | What it does |
|------|-------------|
| `browser_click` | Click by ref, index, or viewport coordinates |
| `browser_right_click` | Context menu |
| `browser_double_click` | Double-click (text selection, file open) |
| `browser_hover` | Trigger hover states and menus |
| `browser_drag_to` | Drag source to target (kanban, sliders, drop zones) |
| `browser_type` | Clear and type into a field |
| `browser_press_key` | Send keys / shortcuts (Enter, Tab, Meta+r) |
| `browser_scroll` | Scroll page or element |
| `browser_scroll_to_text` | Bring text into viewport |
| `browser_select_option` | Pick a `<select>` option by label |
| `browser_get_dropdown_options` | Inspect all options in a combobox |
| `browser_upload_file` | Upload a file to a file input |
| `browser_handle_dialog` | Accept/dismiss JS dialogs (alert, confirm, prompt) |

### Inspection & Extraction (7 tools)

| Tool | What it does |
|------|-------------|
| `browser_extract_content` | **Deterministic extraction** — tables, lists, forms, links, images, key-value |
| `browser_find_elements` | CSS selector search |
| `browser_search_page` | Ctrl+F for text or regex |
| `browser_wait_for_element` | Poll until text/appears or disappears |
| `browser_get_focused_element` | Current keyboard focus |
| `browser_get_attribute` | Get attribute from element by ref/index (href, src, value, disabled) |
| `browser_evaluate` | Execute JavaScript in page context |

### Tabs & Session State (9 tools)

| Tool | What it does |
|------|-------------|
| `browser_new_tab` | Create tab + switch focus — **parallel agent primitive** |
| `browser_list_tabs` | List open tabs, grouped by owning agent/runtime by default |
| `browser_switch_tab` | Switch by 4-char `tab_id` |
| `browser_close_tab` | Close by `tab_id` |
| `browser_get_cookies` | Read cookies for current domain |
| `browser_set_cookies` | Inject cookies (auth persistence) |
| `browser_clear_cookies` | Delete one or all cookies |
| `browser_save_state` | Persist cookies + storage to disk |
| `browser_load_state` | Restore from disk |

### Observability & Lifecycle (10 tools)

| Tool | What it does |
|------|-------------|
| `browser_get_console_logs` | CDP-native console capture (log/warn/error) |
| `browser_get_network_log` | CDP-native network log with optional headers |
| `browser_export_debug_bundle` | Bundle state, console, network, trace summary, optional HTML, and screenshot in one round-trip |
| `browser_get_downloads` | List downloaded files from the session |
| `browser_clear_logs` | Clear console and/or network log buffers |
| `browser_start_trace` | Start CDP performance trace |
| `browser_stop_trace` | Stop trace and return collected events as JSON |
| `browser_list_sessions` | List tracked sessions |
| `browser_close_session` | Close one session |
| `browser_close_all` | Close all sessions |

---

## State & Element Targeting

`browser_get_state` is the primary inspection primitive.

| Mode | Behavior |
|------|----------|
| `auto` | Full state on small pages, compact ranked on dense pages |
| `full` | Complete interactive-element payload |
| `min` | Compact ranked subset (30 elements, viewport-proximity scored) |
| `focus` | Single-element payload |

- **Stable refs**: Elements get `e123` refs derived from backend node IDs — survive re-renders.
- **`since_hash`**: Poll unchanged pages with `changed=false` — zero element payload sent.
- **In `min` mode**: elements within 2× viewport height get a proximity score boost.
- **Unchanged responses**: still return `url`, `title`, `state_hash`, `current_tab_id`, scroll position.
- **Shared-browser tabs**: `tabs` stays flat for compatibility, and `tab_groups` groups tabs by owning agent/runtime by default.
- **Screenshots**: delivered as MCP image content, not embedded base64.

**Best practice:** Start with `mode="min"`, use `since_hash` for follow-up reads, escalate to `mode="full"` only when compact payload omitted something you need.

---

## Deterministic Extraction

`browser_extract_content` is **deterministic-only** — no LLM fallback, no API key required.

Supported route families:

| Route | Extracts |
|-------|----------|
| Links | `<a>` elements |
| Link collections | Nav menus, pagination, result lists |
| Images | `<img>` + `alt` text |
| Tables | `<table>` rows and cells |
| Lists | `<ul>` / `<ol>` items |
| Form fields | Inputs, selects, textareas |
| Key-value | Definition lists, property panels |

- `output_schema` works when the query matches a deterministic route.
- Unrecognized queries return an explicit error with examples — no silent degradation.
- Responses include `<extraction_metadata>` with route and truncation info.

---

## Shared Browser & Parallel Agents

```bash
# Start a browser for sharing
agentyc browser --port 9222 --detach
# → ws://127.0.0.1:9222/devtools/browser/...

# Attach MCP servers to it
agentyc mcp --cdp-url ws://127.0.0.1:9222/devtools/browser/...
```

**Parallel automation flow:**

1. Primary agent starts a shared browser with `agentyc browser --detach`
2. Each subagent spawns `agentyc mcp --cdp-url <url>` — Agentyc claims a dedicated collaboration tab in the shared browser profile
3. Subagents can immediately navigate and work in that owned tab; `browser_new_tab` is only needed when one subagent wants an additional tab of its own
4. Subagents operate independently — refs, network logs, and console logs stay scoped to the owned tab, while auth/cookies/local storage remain shared with the browser profile
5. Primary coordinates and collects results

When multiple runtimes share one browser, Agentyc surfaces a grouped tab view by default so developers can quickly see which agent owns how many tabs.

**Collaboration flags:**

- `--runtime-label` — human-readable ownership label
- `--runtime-role` — `primary` / `assistant`
- `--shared-browser-mode` — `tab` (default) or `window`
- `--shared-browser-focus-policy` — `preserve` or `activate`
- `--shared-browser-window-bounds` — JSON bounds for window mode

> Tab mode is the default for parallel subagents. Window mode remains optional when an operator needs a separate visible surface.

---

## Debugging Loops

- **`browser_export_debug_bundle`** returns one compact artifact with current state, recent console logs, recent network activity, pending requests, trace summary, optional scoped HTML, and an optional screenshot.
- **`browser_wait_for_request` / `browser_wait_for_response`** are the precise sync primitives for API-heavy apps when generic `networkidle` is too blunt.
- Network waits use the same CDP capture buffer as `browser_get_network_log`, so agents can wait for a specific call and then immediately inspect the matching traffic.

---

## Perceived Speed

agentyc helps separate browser work from agent thinking time:

- **MCP progress notifications** — emit `notifications/progress` for long browser phases when the caller provides a `progressToken`.
- **Tool timing** — every result includes `_meta.agentyc/browser_duration_ms` and `agentyc/tool_phase`.
- **Since-hash polling** — unchanged pages return in <1 ms without resending element payloads.
- **Compact mode** — `mode="min"` surfaces the 30 most actionable elements with proximity scoring.
- **Agent narration** — agents should narrate intent briefly before a likely pause: "Waiting for validation to finish."

---

## Benchmarks

Measured by the release-gate benchmark suite (`scripts/benchmark_mcp_runtime.py`). The values below are the median of two confirmed headless post-change runs:

| Metric | Threshold | Current |
|--------|-----------|---------|
| Python import time | ≤ 2500 ms | 220.0 ms |
| Cold-start session init | ≤ 35000 ms | 1531.3 ms |
| `auto` payload reduction | ≥ 8.0% | 8.3% |
| `auto` element recall | ≥ 0.99 | 1.0 |
| `min` element recall | ≥ 0.99 | 1.0 |
| Deterministic extraction recall | ≥ 0.99 | 1.0 |
| Structured extraction recall | ≥ 0.99 | 1.0 |
| Action success rate | ≥ 1.0 | 1.0 |
| Collaboration check pass rate | ≥ 1.0 | 1.0 |
| Collaboration latency | informational | 1598.6 ms |

Confirmed headless stdio tool-surface median across two runs (`scripts/benchmark_mcp_stdio_e2e.py --targets source`):

- success / accuracy / precision: `1.0 / 1.0 / 1.0`
- total duration: `45146.9 ms`
- average / p95 tool latency: `41.4 ms / 155.1 ms`

---

## Python Surface

```python
from agentyc import AgentycServer, BrowserSession, BrowserProfile, Tools

server = AgentycServer(session_timeout_minutes=20)
await server.run()
```

The primary public story is MCP-first. Direct Python imports are available for embedding or lower-level control.

---

## Configuration

| CLI flag | Default | Description |
|----------|---------|-------------|
| `--session-timeout-minutes` | 0 (never) | Auto-close idle sessions |
| `--cdp-url` | — | Attach to existing browser |
| `--runtime-label` | — | Ownership label for shared browser |
| `--runtime-role` | — | Collaboration role |
| `--shared-browser-mode` | `tab` | `tab` or `window` |
| `--shared-browser-focus-policy` | `preserve` | `preserve` or `activate` |

Environment variables: `AGENTYC_HEADLESS`, `AGENTYC_ALLOWED_DOMAINS`, `AGENTYC_ACTION_TIMEOUT_S`, `AGENTYC_PROXY_*`, `AGENTYC_LOGGING_LEVEL`.

Browser defaults: `headless=false`, `downloads_path=~/Downloads/agentyc-mcp`, `user_data_dir=~/.config/agentyc/profiles/default`.

---

## Development

```bash
source .venv/bin/activate
./scripts/lint.sh     # ruff
./scripts/test.sh     # pytest + pytest-asyncio
uv run pyright        # static types
uv build              # package
```

---

## Docs

- [Overview](docs/overview.md)
- [Features](docs/features.md)
- [Architecture](docs/architecture.md)
- [API Reference](docs/api.md)
- [Configuration](docs/configuration.md)
- [Tech Stack](docs/tech-stack.md)
- [Release Gate](docs/release-gate.md)

---

## License

MIT — see [LICENSE](LICENSE).
