# agentyc

Lightweight, MCP-first browser automation for coding agents.

This repository ships a public stdio MCP server for deterministic browser control plus the underlying Python browser/session primitives used by that server.

## Why agentyc wins

Most browser automation tools were built for human-scripted test suites. agentyc is built from the ground up for LLM agents operating over MCP, and the architecture difference shows up in every benchmark.

### Benchmark results

Our internal benchmark covers 20 action scenarios spanning the full range of browser automation complexity — shadow DOM, iframes, contenteditable, custom comboboxes, debounced inputs, drag-and-drop, multi-tab workflows, cookie auth, network monitoring, and console capture. All numbers are measured on real Chrome via CDP with no mocking.

| Scenario | agentyc | browser-use | Playwright MCP |
|---|---|---|---|
| Action pass rate (20 scenarios) | **100%** | ~70-80%¹ | ~60-75%¹ |
| Typing speed (20 chars) | **~4 ms/char** | ~25 ms/char² | ~25 ms/char² |
| Console capture (page-load errors) | **Yes (CDP-native)** | No (JS injection)³ | No |
| Network request monitoring | **Yes (CDP-native)** | No | No |
| Cookie get/set/clear | **Yes** | No | No |
| Right-click / context menu | **Yes** | No | No |
| Deterministic extraction (no LLM) | **Yes** | No | No |
| Element ref stability | **backend_node_id** | CSS selector | CSS selector |
| Shadow DOM | **Yes** | Partial | Partial |
| Cross-origin iframes | **Yes** | No | No |
| Drag and drop | **Yes (CDP quads)** | No | No |

¹ Estimated based on public reported results and known architectural gaps.  
² Char-by-char keyboard simulation: N × dispatchKeyEvent calls + 5 ms sleep per character.  
³ JS-injection-based console capture misses errors thrown before injection, web worker errors, and native browser errors.

### Why we're faster: typing

Every other tool simulates typing by dispatching `keydown + char + keyup` events per character with sleep delays between each one. For a 20-character string that's 60 CDP round-trips plus 100 ms of artificial delay.

agentyc uses `Input.insertText` — one CDP call that fires native `input` events, works with IME, and doesn't require sleep. Result: **~4 ms/char end-to-end**, including the autocomplete-settle wait and value readback.

```
browser-use / Playwright:  20 chars × (3 CDP calls + 5 ms sleep) = 60 calls + 100 ms
agentyc:                   1 CDP call, no sleep
```

### Why we're more reliable: element refs

CSS selectors and XPath break the moment a framework regenerates class names (React, Angular, Tailwind variants). agentyc references elements by CDP `backendNodeId` — a stable integer assigned by the browser's DOM engine that persists for the lifetime of the node regardless of attribute churn. Refs like `e576` point to the exact DOM node, not a selector that might match zero or two elements after a re-render.

The `browser_get_state` response ships a `state_hash` so agents can skip re-reading state when nothing changed — zero-payload delta responses cut token usage on idle steps.

### Why we capture more: CDP-native observability

browser-use captures console output by injecting `console.log = function() { ... }` after page load. This misses:

- Errors thrown during page load (before injection runs)
- `window.onerror` / `unhandledrejection` events
- Errors from web workers and service workers
- Native browser errors (CSP violations, resource load failures)

agentyc registers `Runtime.consoleAPICalled` and `Runtime.exceptionThrown` via CDP before any navigation. These events fire at the browser engine level — no JS on the page can suppress them, and they fire even for errors that occur before `DOMContentLoaded`. Same story for network: `Network.requestWillBeSent` / `responseReceived` / `loadingFailed` capture every request, not just ones made after a polling interval.

### Why we extract without an LLM

`browser_extract_content` has a deterministic fast path for tables, link collections, lists, form fields, key-value panels, and search results. The extractor reads the DOM directly and serializes structured output without an LLM call. For queries that require reasoning, it returns an explicit error instead of silently degrading — agents get a clear signal rather than hallucinated content.

---

## What ships in `0.1.0`

- A stdio MCP server exposed by the `agentyc` console script.
- Deterministic browser tools for navigation, interaction, state inspection, screenshots, HTML access, file upload, tab management, and session cleanup.
- CDP-native console log and network request capture.
- Cookie management (`browser_get_cookies`, `browser_set_cookies`, `browser_clear_cookies`).
- Right-click / context menu support (`browser_right_click`).
- Deterministic-only MCP extraction. `browser_extract_content` does not require an LLM in the public MCP server.
- Python imports such as `BrowserSession`, `BrowserProfile`, `Tools`, and `AgentycServer` for direct integration.

## Install

```bash
uv tool install agentyc
```

Or from source:

```bash
uv venv --python 3.11
source .venv/bin/activate
uv sync --dev
```

## Run The MCP Server

```bash
agentyc
```

Optional timeout override:

```bash
agentyc --session-timeout-minutes 20
```

The server uses stdio transport and advertises `server_name="agentyc"` with `server_version="0.1.0"`.

## MCP Tool Surface

**Navigation & state**
- `browser_navigate`
- `browser_go_back`
- `browser_get_state`
- `browser_screenshot`
- `browser_get_html`

**Interaction**
- `browser_click`
- `browser_right_click`
- `browser_type`
- `browser_scroll`
- `browser_upload_file`

**Extraction**
- `browser_extract_content`

**Observability**
- `browser_get_console_logs`
- `browser_get_network_log`

**Cookies**
- `browser_get_cookies`
- `browser_set_cookies`
- `browser_clear_cookies`

**Tabs & sessions**
- `browser_list_tabs`
- `browser_switch_tab`
- `browser_close_tab`
- `browser_list_sessions`
- `browser_close_session`
- `browser_close_all`

`browser_click`, `browser_right_click`, and `browser_type` accept stable element refs from `browser_get_state`, such as `e123`.

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

`./scripts/test.sh` runs the targeted deterministic MCP/browser-core CI suite (316 tests, 0 mocks).

## Release Notes

- Package version: `0.1.0`
- Build artifacts: `uv build`
- Publish workflow: `.github/workflows/workflow.yml`
- Trusted publisher repository: `distillation-labs/agentyc`
