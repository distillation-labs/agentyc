# agentyc

<p align="center">
  <em>Deterministic, MCP-first browser automation for coding agents.</em><br>
  No API key needed. No LLM fallback. Just CDP, stdio MCP, live HUD surfaces, and 66 tools.
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
| **Tool count** | 66 | ~15-20 actions | ~20 tools |
| **Console/Network capture** | CDP-native built-in | Limited | Limited |
| **Deterministic extraction** | Tables, lists, forms, links, images, key-value | None (LLM only) | None |
| **Headless by default** | No (visible), flag for headless | Configurable | Configurable |

**agentyc is not a testing framework or an autonomous agent loop.** It is a browser MCP: launch it, give your agent 66 tools, let it inspect and interact — deterministically, compactly, without an LLM in the critical path.

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

The skills guide covers: read→ref→act loop, tool-selection rules, `since_hash` polling, shared-browser reuse with `--reuse-local-browser` / `AGENTYC_REUSE_LOCAL_BROWSER`, same-browser versus same-tab safety, frame listing and frame HTML inspection, storage and cookie workflows, precise network waits, network entry inspection and replay, narrow network mocks, per-tab offline/throttling controls, right-click / double-click / drag flows, debug bundles, dynamic-text waits, error recovery, long-page search, multi-tab handoff, extraction routes, auth persistence, parallel agents, headless release-readiness flows (viewport, DOM stability, downloads, trace/log hygiene, PDF export), dialog acknowledgments after auto-handled prompts, JS evaluation, and a fuller quick-reference tool list.

---

## Live HUD

When `BrowserProfile(demo_mode=True)` is enabled, Agentyc now injects a compact square-border HUD into the controlled browser page. It shows the current step, a short recent timeline, and a `REPORT` menu that copies sanitized context before opening the repo's bug-report, feature-request, or private security flow.

For local operator visibility outside the browser, `agentyc mcp --hud-overlay` opens a small transparent desktop HUD window that mirrors the same sanitized activity stream. Agents can also call `browser_set_intent` to push a short human-readable status label into both HUD surfaces.

---

## MCP Surface: 66 Tools

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
| `browser_screenshot` | Viewport or full-page screenshot (WebP/JPEG/PNG, configurable format, resize, quality) |
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

### Frames & Storage (5 tools)

| Tool | What it does |
|------|-------------|
| `browser_list_frames` | List known frames with frame IDs, URLs, and cross-origin markers |
| `browser_get_frame_html` | Raw HTML for one frame by `frame_id` |
| `browser_get_storage` | Inspect `localStorage` / `sessionStorage` by origin, type, or key |
| `browser_set_storage` | Set one `localStorage` / `sessionStorage` key for the current origin |
| `browser_clear_storage` | Clear one key, one storage area, or all storage for the current origin |

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
| `browser_load_state` | Restore cookies + storage from disk |

### Observability, Network Control & Lifecycle (18 tools)

| Tool | What it does |
|------|-------------|
| `browser_get_console_logs` | CDP-native console capture (log/warn/error) |
| `browser_get_network_log` | CDP-native network log with optional headers |
| `browser_inspect_network_entry` | Inspect one captured request/response with optional bodies |
| `browser_add_network_mock` | Add a narrow URL-matching fulfill/abort rule for the active tab |
| `browser_remove_network_mock` | Remove one mock rule or clear them all |
| `browser_list_network_mocks` | List active mock rules and match counts |
| `browser_set_network_conditions` | Apply offline or throttling conditions to the active tab |
| `browser_get_network_conditions` | List active per-tab network conditions |
| `browser_replay_request` | Replay a captured request with optional header/body overrides |
| `browser_export_debug_bundle` | Bundle state, console, network, trace summary, optional HTML, and screenshot in one round-trip |
| `browser_set_intent` | Publish a short operator-facing intent label into the live HUD surfaces |
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
- **Screenshots**: delivered as MCP image content, not embedded base64. Screenshot format, size, quality, and grayscale are configurable via `BrowserSession` constructor parameters (see [LLM Screenshot Optimization](#llm-screenshot-optimization)).

**Best practice:** Start with `mode="min"`, use `since_hash` for follow-up reads, escalate to `mode="full"` only when compact payload omitted something you need.

---

## LLM Screenshot Optimization

Screenshots are the dominant token cost in browser automation. Agentyc automatically resizes and compresses screenshots before delivering them to the LLM, reducing token consumption by about **8.3×** on the benchmark fixture with no measured regression in the validated headless flows.

| Config field | Default | Description |
|-------------|---------|-------------|
| `llm_screenshot_size` | `(480, 270)` | Target resize in pixels. `None` for full-viewport resolution. |
| `llm_screenshot_format` | `"webp"` | Output format — `"png"`, `"jpeg"`, or `"webp"`. WebP gives best size. |
| `llm_screenshot_quality` | `85` | Compression quality 1–100 (JPEG/WebP). |
| `llm_screenshot_grayscale` | `False` | Convert to grayscale before encoding (saves ~20-30%). |

Set these via the `BrowserSession` constructor:

```python
session = BrowserSession(
    llm_screenshot_size=(480, 270),
    llm_screenshot_format='webp',
    llm_screenshot_quality=85,
)
```

**Benchmark (1280×720 viewport, 3-run median):**

| Format | Size | Base64 | Tokens (est.) | vs Baseline |
|--------|------|--------|--------------|------------|
| PNG (baseline) | 1280×720 | 58,816B | ~23,526 | — |
| JPEG q=85 | 640×360 | 22,748B | ~9,099 | 2.6× smaller |
| WebP q=85 (default) | 480×270 | 7,116B | ~2,846 | **8.3× smaller** |
| Gray JPEG q=60 | 320×180 | 4,460B | ~1,784 | 13.2× smaller |

All resize/encode operations complete in 30–50ms — negligible compared to CDP capture time.

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

# Reuse the latest locally launched Agentyc browser automatically
agentyc mcp --reuse-local-browser

# Attach MCP servers to it
agentyc mcp --cdp-url ws://127.0.0.1:9222/devtools/browser/...
```

**Parallel automation flow:**

1. Primary agent starts a shared browser with `agentyc browser --detach`
2. Each subagent either spawns `agentyc mcp --cdp-url <url>` or uses `agentyc mcp --reuse-local-browser` / `AGENTYC_REUSE_LOCAL_BROWSER=1` — Agentyc claims a dedicated collaboration tab in the shared browser profile
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
- `--reuse-local-browser` — auto-attach to the latest locally launched Agentyc browser without copying a CDP URL around

> Shared-browser reuse means the same browser process and profile. Each runtime still claims its own collaboration tab or window; Chrome does not provide a safe public contract for multiple agents to co-own the exact same tab.

---

## Debugging Loops

- **`browser_export_debug_bundle`** returns one compact artifact with current state, recent console logs, recent network activity, pending requests, trace summary, optional scoped HTML, and an optional screenshot.
- **`browser_wait_for_request` / `browser_wait_for_response`** are the precise sync primitives for API-heavy apps when generic `networkidle` is too blunt.
- **`browser_inspect_network_entry` / `browser_replay_request`** turn captured traffic into a concrete request/response artifact with optional bodies, then let agents reissue the same call with narrow overrides.
- **`browser_add_network_mock` / `browser_set_network_conditions`** provide deterministic per-tab stubbing, offline mode, and throttling without switching to a separate test runner abstraction.
- Network waits, inspection, replay, and mocks all build on the same CDP capture/interception layer, so agents can wait for a specific call and then inspect or control the matching traffic immediately.

---

## Perceived Speed

agentyc helps separate browser work from agent thinking time:

- **MCP log notifications** — emit start/completion/error tool-phase messages through MCP logging when the client enables them.
- **Tool timing** — every result includes `_meta.agentyc/browser_duration_ms` and `agentyc/tool_phase`.
- **Since-hash polling** — unchanged pages avoid resending interactive-element payloads.
- **Compact mode** — `mode="min"` surfaces the 30 most actionable elements with proximity scoring.
- **Agent narration** — agents should narrate intent briefly before a likely pause: "Waiting for validation to finish."

---

## Benchmarks

Measured by the release-gate benchmark suite (`scripts/benchmark_mcp_runtime.py --preset dogfood --release-gate`). The values below are the median of two confirmed headless runs on current `HEAD`:

| Metric | Threshold | Current |
|--------|-----------|---------|
| Python import time | ≤ 2500 ms | 194.4 ms |
| Cold-start session init | ≤ 35000 ms | 1027.4 ms |
| `auto` payload reduction | ≥ 8.0% | 10.2% |
| `auto` element recall | ≥ 0.99 | 1.0 |
| `min` element recall | ≥ 0.99 | 1.0 |
| Deterministic extraction recall | ≥ 0.99 | 1.0 |
| Structured extraction recall | ≥ 0.99 | 1.0 |
| Action success rate | ≥ 1.0 | 1.0 |
| Collaboration check pass rate | ≥ 1.0 | 1.0 |
| Collaboration latency | informational | 1283.1 ms |

Confirmed headless stdio tool-surface median across two runs (`scripts/benchmark_mcp_stdio_e2e.py --targets source --minimum-total-calls 100`):

- success / accuracy / precision: `1.0 / 1.0 / 1.0`
- total duration: `24140.0 ms`
- average / p95 tool latency: `43.3 ms / 161.6 ms`

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
| `--hud-overlay` | off | Show a small transparent desktop HUD for sanitized live activity |
| `--cdp-url` | — | Attach to existing browser |
| `--reuse-local-browser` | off unless enabled explicitly or via env | Reuse the latest locally launched Agentyc browser |
| `--runtime-label` | — | Ownership label for shared browser |
| `--runtime-role` | — | Collaboration role |
| `--shared-browser-mode` | `tab` | `tab` or `window` |
| `--shared-browser-focus-policy` | `preserve` | `preserve` or `activate` |

### BrowserSession config

| Constructor param | Default | Description |
|------------------|---------|-------------|
| `llm_screenshot_size` | `(480, 270)` | Target resize for LLM screenshots. `None` = full resolution. |
| `llm_screenshot_format` | `"webp"` | Output format: `"png"`, `"jpeg"`, or `"webp"`. |
| `llm_screenshot_quality` | `85` | Compression 1–100 (JPEG/WebP). |
| `llm_screenshot_grayscale` | `False` | Grayscale encoding (~20-30% savings). |
| `browser_profile` | required | `BrowserProfile` instance. |
| `headless` | `False` | Override headless mode. |

### Environment variables

`AGENTYC_HEADLESS`, `AGENTYC_HUD_OVERLAY`, `AGENTYC_ALLOWED_DOMAINS`, `AGENTYC_ACTION_TIMEOUT_S`, `AGENTYC_PROXY_*`, `AGENTYC_LOGGING_LEVEL`.

### Browser defaults

`headless=false`, `downloads_path=~/Downloads/agentyc-mcp`, `user_data_dir=~/.config/agentyc/profiles/default`.

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
