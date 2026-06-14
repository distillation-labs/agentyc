# agentyc

<p align="center">
  <em>Deterministic, MCP-first browser automation for coding agents.</em><br>
  No API key needed. No LLM fallback. Just CDP, stdio MCP, and 61 tools.
</p>

<p align="center">
  <img src="https://img.shields.io/badge/rust-≥1.80-orange?style=flat&logo=rust&logoColor=white" alt="Rust ≥1.80">
  <img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="MIT">
  <img src="https://img.shields.io/badge/MCP-stdio-000?style=flat&logo=modelcontextprotocol&logoColor=white" alt="MCP stdio">
  <img src="https://img.shields.io/badge/CDP-native-46BC99?style=flat" alt="CDP-native">
</p>

---

## What It Is

`agentyc` is a single native binary that runs a stdio MCP server for browser automation. It talks CDP directly to Chrome — no Playwright, no Python, no LLM in the loop. Every tool is deterministic, every response is compact, and the default path works with zero API keys.

Cold start: **~5ms**. Binary: **~8MB**. Idle RSS: **~3MB**.

```bash
# Download the binary for your platform, then:
agentyc           # starts the MCP server — that's it
agentyc init      # writes agentyc-skill.md — point your agent at it
```

---

## Quick Start

**Download the prebuilt binary (fastest — no Rust toolchain needed):**

```bash
# macOS arm64
curl -L https://github.com/distillation-labs/agentyc/releases/latest/download/agentyc-aarch64-apple-darwin.tar.gz | tar xz
# macOS x86_64
curl -L https://github.com/distillation-labs/agentyc/releases/latest/download/agentyc-x86_64-apple-darwin.tar.gz | tar xz
# Linux x86_64
curl -L https://github.com/distillation-labs/agentyc/releases/latest/download/agentyc-x86_64-unknown-linux-gnu.tar.gz | tar xz
# Then move the binary onto your PATH and start the server:
agentyc mcp
```

**Or build from source:**

```bash
cargo install --git https://github.com/distillation-labs/agentyc agentyc
```

**Cursor / Claude Code / any MCP client:**

```json
{
  "mcp": {
    "agentyc": {
      "type": "local",
      "command": ["agentyc", "mcp"]
    }
  }
}
```

Add `"--extended"` to `args` to expose 15 additional observability tools (console/network logs, mocks, debug bundle, trace). Omit it for the lean 61-tool default.

**Bootstrap your agent with the skills guide:**

```bash
agentyc init                      # writes agentyc-skill.md
agentyc init --output .agent.md   # custom path
agentyc init --print              # print to stdout
agentyc init --force              # overwrite existing
```

Point your agent at that file. It explains the read→ref→act loop, tool selection, error recovery, and has a full quick-reference.

---

## How It Compares

| | agentyc | browser-use | Playwright MCP |
|---|---|---|---|
| **Protocol** | stdio MCP (native) | Python script + custom loop | MCP wrapper over library |
| **LLM required** | No | Yes (planner) | No |
| **Extraction** | Deterministic (7 route families) | LLM-based | Raw page access |
| **State snapshots** | Token-aware, compact, `since_hash` polling | Full DOM dump | Full DOM or AX tree |
| **Element targeting** | Stable refs (`e123`) survive re-renders | XPath/CSS selectors | Playwright locators |
| **Browser backend** | CDP direct | Playwright | Playwright |
| **Runtime** | Native binary (~8MB) | Python + many deps | Node + Playwright |
| **Cold start** | ~5ms | ~300ms+ | ~200ms+ |
| **Tool count** | 61 default / 76 extended | ~15–20 | ~20 |

---

## MCP Surface: 61 Tools

### Navigation & State (11 tools)

| Tool | What it does |
|------|-------------|
| `browser_navigate` | Navigate to URL; returns page title. Auto-launches Chrome. |
| `browser_go_back` | History back |
| `browser_go_forward` | History forward |
| `browser_refresh` | Reload current page |
| `browser_wait` | Wait N seconds (0.1–30s) |
| `browser_wait_for_url` | Wait until URL matches substring or regex |
| `browser_wait_for_network_idle` | Wait until network goes quiet |
| `browser_wait_for_request` | Wait for a matching outbound request |
| `browser_wait_for_response` | Wait for a matching response |
| `browser_wait_for_stable_dom` | Wait until DOM mutations settle |
| `browser_get_state` | **Primary primitive** — structured DOM with stable refs, `since_hash` polling, 4 modes |

### Page Reading (5 tools)

| Tool | What it does |
|------|-------------|
| `browser_get_html` | Raw HTML (full page or CSS selector) |
| `browser_screenshot` | Viewport or full-page screenshot |
| `browser_save_as_pdf` | Save current page as PDF via `Page.printToPDF` |
| `browser_set_viewport` | Set viewport width, height, and scale |
| `browser_evaluate` | Execute JavaScript and return the result |

### Interaction (14 tools)

| Tool | What it does |
|------|-------------|
| `browser_click` | Click by ref, index, label, or coordinates; optional URL-wait |
| `browser_right_click` | Right-click to open context menu |
| `browser_double_click` | Double-click |
| `browser_hover` | Hover to trigger `:hover` states and menus |
| `browser_drag_to` | Drag source to target |
| `browser_type` | Clear and type into a field (React/Vue-compatible) |
| `browser_fill_form` | Batch text, selects, checkboxes in one round trip |
| `browser_press_key` | Send key or shortcut (`Enter`, `Tab`, `Control+a`) |
| `browser_scroll` | Scroll page or element |
| `browser_scroll_to_text` | Bring text into viewport |
| `browser_select_option` | Pick a `<select>` option by visible text |
| `browser_get_dropdown_options` | Inspect all options in a combobox |
| `browser_upload_file` | Upload a file to a file input |
| `browser_handle_dialog` | Accept/dismiss JS dialogs |

### Inspection & Extraction (7 tools)

| Tool | What it does |
|------|-------------|
| `browser_extract_content` | Deterministic extraction — tables, lists, forms, links, images, key-value |
| `browser_find_elements` | CSS selector search |
| `browser_search_page` | Ctrl+F for text or regex |
| `browser_wait_for_element` | Poll until text appears or disappears |
| `browser_get_focused_element` | Current keyboard focus |
| `browser_get_attribute` | Get attribute by ref/index (`href`, `src`, `value`) |

### Frames & Storage (5 tools)

| Tool | What it does |
|------|-------------|
| `browser_list_frames` | List frames with IDs and cross-origin markers |
| `browser_get_frame_html` | Raw HTML for a frame by `frame_id` |
| `browser_get_storage` | Inspect `localStorage` / `sessionStorage` |
| `browser_set_storage` | Set one storage key |
| `browser_clear_storage` | Clear storage key, area, or all |

### Tabs & Sessions (19 tools)

| Tool | What it does |
|------|-------------|
| `browser_new_tab` | Create tab and switch focus |
| `browser_list_tabs` | List open tabs |
| `browser_switch_tab` | Switch by `tab_id` |
| `browser_close_tab` | Close by `tab_id` |
| `browser_wait_for_tab` | Wait for a new tab to appear |
| `browser_get_cookies` | Read cookies |
| `browser_set_cookies` | Inject cookies |
| `browser_clear_cookies` | Delete one or all cookies |
| `browser_grant_permissions` | Grant browser permissions (e.g. geolocation) |
| `browser_set_geolocation` | Override geolocation |
| `browser_set_extra_headers` | Set extra HTTP headers |
| `browser_set_user_agent` | Override user agent |
| `browser_set_timezone` | Override timezone |
| `browser_set_locale` | Override locale |
| `browser_emulate_media` | Emulate `prefers-color-scheme`, `prefers-reduced-motion`, etc. |
| `browser_save_state` | Persist cookies + storage to disk |
| `browser_load_state` | Restore cookies + storage from disk |
| `browser_list_sessions` | List sessions |
| `browser_close_all` | Close all sessions and browser |

---

## State & Element Targeting

`browser_get_state` is the primary inspection primitive.

| Mode | Behavior |
|------|----------|
| `auto` | Full on small pages, compact on dense pages |
| `full` | All interactive elements |
| `min` | Compact ranked subset (9-element budget, proximity-scored) |
| `focus` | Single element |

- **Stable refs**: `e123` derived from CDP backend node IDs — survive re-renders
- **`since_hash`**: Returns `changed=false` when page is unchanged — zero element payload
- **Shadow DOM**: pierced automatically in element discovery

---

## Deterministic Extraction

`browser_extract_content` uses a native HTML parser (no LLM):

| Query | Extracts |
|-------|---------|
| `table rows` | `<table>` rows and cells |
| `all links` | `<a>` elements with href |
| `images` | `<img>` + alt text |
| `form fields` | Inputs, selects, textareas |
| `list items` | `<ul>` / `<ol>` items |
| `key-value` / `definitions` | `<dl>` pairs, label panels |

---

## Configuration

### CLI flags

| Flag | Default | Description |
|------|---------|-------------|
| `--cdp-url` | — | Attach to an existing browser |
| `--session-timeout-minutes` | 0 (never) | Auto-close idle sessions |

### Environment variables

| Variable | Description |
|----------|-------------|
| `AGENTYC_HEADLESS` | `1` to run Chrome headless |
| `AGENTYC_ALLOWED_DOMAINS` | Comma-separated domain allowlist |
| `AGENTYC_ACTION_TIMEOUT_S` | Per-action CDP timeout (default 180s) |
| `AGENTYC_CDP_TIMEOUT_S` | CDP response timeout (default 60s) |
| `AGENTYC_PROXY_URL` | Proxy server URL |
| `AGENTYC_PROXY_USERNAME` | Proxy username |
| `AGENTYC_PROXY_PASSWORD` | Proxy password |
| `AGENTYC_LOGGING_LEVEL` | Log level (e.g. `warn`, `info`, `debug`) |

### Chrome defaults

- `headless=false` (visible browser)
- Downloads path: `~/Downloads/agentyc-mcp`
- Per-session isolated temp profile

---

## Performance

| Metric | Value |
|--------|-------|
| Cold start (spawn → first response) | ~5ms |
| `tools/list` p50 | ~0.9ms |
| Tool call overhead p50 | ~70µs |
| Peak throughput | ~17,000 calls/sec |
| Binary size | ~8MB |
| Idle RSS | ~3MB |

---

## Docs

- [Overview](docs/overview.md)
- [Features](docs/features.md)
- [Architecture](docs/architecture.md)
- [API Reference](docs/api.md)
- [Configuration](docs/configuration.md)
- [Release Gate](docs/release-gate.md)

---

## License

MIT — see [LICENSE](LICENSE).
