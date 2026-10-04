# agentyc

<p align="center">
  <em>Deterministic, host-backed browser task spaces for coding agents.</em><br>
  No API key needed. No LLM fallback. Direct CLI/SDK first, with MCP compatibility.
</p>

<p align="center">
  <img src="https://img.shields.io/badge/rust-≥1.80-orange?style=flat&logo=rust&logoColor=white" alt="Rust ≥1.80">
  <img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="MIT">
  <img src="https://img.shields.io/badge/MCP-stdio-000?style=flat&logo=modelcontextprotocol&logoColor=white" alt="MCP stdio">
  <img src="https://img.shields.io/badge/CDP-native-46BC99?style=flat" alt="CDP-native">
</p>

---

## What It Is

`agentyc` is a single native binary for direct host-backed CLI/SDK access to logical browser task spaces, with an MCP adapter available for compatibility. The existing-Chrome product path uses the enrolled MV3 extension and Native Messaging bridge; it does not launch Chrome or attach to a copied CDP URL. The direct-CDP server remains an explicit legacy compatibility mode. Every operation is deterministic, every response is compact, and no API key is required.

Cold start: **~5ms**. Binary: **~8MB**. Idle RSS: **~3MB**.

```bash
# Download the binary for your platform, then:
agentyc           # starts the direct host-backed CLI
agentyc init      # writes agentyc-skill.md — point your agent at it
# Or install the portable agent plugin bundle:
# plugins/agentyc-browser-automation/plugin.json
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
# Then move the binary onto your PATH and use the direct host-backed CLI:
agentyc host status
agentyc space list
```

**Or build from source:**

```bash
cargo install --git https://github.com/distillation-labs/agentyc agentyc
```

**Optional MCP compatibility (for MCP clients):**

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

Direct host-backed CLI/SDK is the primary interface for task-space workflows. Select the MCP compatibility adapter explicitly with `agentyc mcp`; add `"--extended"` only for its optional observability profile.

**Bootstrap your agent with the skills guide:**

```bash
agentyc init                      # writes agentyc-skill.md
agentyc init --output .agent.md   # custom path
agentyc init --print              # print to stdout
agentyc init --force              # overwrite existing
```

Point your agent at that file. It explains the read→ref→act→verify loop, tool selection, error recovery, frontend choice, safety, and has a full quick-reference. The canonical installable skill and plugin metadata live under `.agents/skills/agentyc-browser-automation/` and `plugins/agentyc-browser-automation/`; see `docs/skills-and-plugins.md`.

## Existing-Chrome task spaces

The host/core contracts, production-shaped MV3 extension package, Native Messaging host, direct CLI/SDK, context/reliability modules, rollout gates, and MCP compatibility adapter are present. Each logical space owns its managed pages and one visual Chrome tab group; leases and epochs, not group membership, authorize mutations. Phase 0 is registered complete; live product and release gates remain tracked in later phases. See the [task-space plan](docs/exec-plans/active/agentyc-browser-task-spaces/README.md) and [release gate](docs/release-gate.md).

---

## How It Compares

|                       | agentyc                                    | browser-use                 | Playwright MCP           |
| --------------------- | ------------------------------------------ | --------------------------- | ------------------------ |
| **Interface**         | Direct host-backed CLI / Node SDK          | Python script + custom loop | MCP wrapper over library |
| **LLM required**      | No                                         | Yes (planner)               | No                       |
| **Extraction**        | Deterministic (7 route families)           | LLM-based                   | Raw page access          |
| **State snapshots**   | Token-aware, compact, `since_hash` polling | Full DOM dump               | Full DOM or AX tree      |
| **Element targeting** | Stable refs (`e123`) survive re-renders    | XPath/CSS selectors         | Playwright locators      |
| **Browser backend**   | Enrolled Chrome extension                  | Playwright                  | Playwright               |
| **Runtime**           | Native binary (~8MB)                       | Python + many deps          | Node + Playwright        |
| **Cold start**        | ~5ms                                       | ~300ms+                     | ~200ms+                  |
| **Legacy MCP tools**  | 61 default / 76 extended                   | ~15–20                      | ~20                      |

---

## Legacy MCP Compatibility Surface: 61 Tools

This section documents the explicit legacy compatibility implementation, not the existing-Chrome task-space product. Select it with `agentyc mcp --legacy-cdp`; pass `--cdp-url <endpoint>` for an operator-supplied browser, or omit it only for the separately selected legacy managed-test lifecycle. The default command uses the host/extension/space architecture described in the task-space plan. Legacy raw tab/target/ref identifiers remain deprecated adapter details and are not the target public identity model.

### Navigation & State (11 tools)

| Tool                            | What it does                                                                           |
| ------------------------------- | -------------------------------------------------------------------------------------- |
| `browser_navigate`              | Navigate to URL; returns page title in the explicit legacy compatibility server.       |
| `browser_go_back`               | History back                                                                           |
| `browser_go_forward`            | History forward                                                                        |
| `browser_refresh`               | Reload current page                                                                    |
| `browser_wait`                  | Wait N seconds (0.1–30s)                                                               |
| `browser_wait_for_url`          | Wait until URL matches substring or regex                                              |
| `browser_wait_for_network_idle` | Wait until network goes quiet                                                          |
| `browser_wait_for_request`      | Wait for a matching outbound request                                                   |
| `browser_wait_for_response`     | Wait for a matching response                                                           |
| `browser_wait_for_stable_dom`   | Wait until DOM mutations settle                                                        |
| `browser_get_state`             | **Primary primitive** — structured DOM with stable refs, `since_hash` polling, 4 modes |

### Page Reading (5 tools)

| Tool                   | What it does                                   |
| ---------------------- | ---------------------------------------------- |
| `browser_get_html`     | Raw HTML (full page or CSS selector)           |
| `browser_screenshot`   | Viewport or full-page screenshot               |
| `browser_save_as_pdf`  | Save current page as PDF via `Page.printToPDF` |
| `browser_set_viewport` | Set viewport width, height, and scale          |
| `browser_evaluate`     | Execute JavaScript and return the result       |

### Interaction (14 tools)

| Tool                           | What it does                                                           |
| ------------------------------ | ---------------------------------------------------------------------- |
| `browser_click`                | Click by ref, index, label, or coordinates; optional URL-wait          |
| `browser_right_click`          | Right-click to open context menu                                       |
| `browser_double_click`         | Double-click                                                           |
| `browser_hover`                | Hover to trigger `:hover` states and menus                             |
| `browser_drag_to`              | Drag source to target                                                  |
| `browser_type`                 | Clear and type into a field (React/Vue-compatible)                     |
| `browser_fill_form`            | Batch text, selects, checkboxes in one round trip                      |
| `browser_press_key`            | Send key or shortcut (`Enter`, `Tab`, `Control+a`)                     |
| `browser_scroll`               | Scroll page or element                                                 |
| `browser_scroll_to_text`       | Bring text into viewport                                               |
| `browser_select_option`        | Pick a `<select>` option by visible text                               |
| `browser_get_dropdown_options` | Inspect all options in a combobox                                      |
| `browser_upload_file`          | Legacy adapter returns a typed denial; no implicit file-input mutation |
| `browser_handle_dialog`        | Accept/dismiss JS dialogs                                              |

### Inspection & Extraction (7 tools)

| Tool                          | What it does                                                              |
| ----------------------------- | ------------------------------------------------------------------------- |
| `browser_extract_content`     | Deterministic extraction — tables, lists, forms, links, images, key-value |
| `browser_find_elements`       | CSS selector search                                                       |
| `browser_search_page`         | Ctrl+F for text or regex                                                  |
| `browser_wait_for_element`    | Poll until text appears or disappears                                     |
| `browser_get_focused_element` | Current keyboard focus                                                    |
| `browser_get_attribute`       | Get attribute by ref/index (`href`, `src`, `value`)                       |

### Frames & Storage (5 tools)

| Tool                     | What it does                                  |
| ------------------------ | --------------------------------------------- |
| `browser_list_frames`    | List frames with IDs and cross-origin markers |
| `browser_get_frame_html` | Raw HTML for a frame by `frame_id`            |
| `browser_get_storage`    | Inspect `localStorage` / `sessionStorage`     |
| `browser_set_storage`    | Set one storage key                           |
| `browser_clear_storage`  | Clear storage key, area, or all               |

### Tabs & Sessions (19 tools)

| Tool                        | What it does                                                   |
| --------------------------- | -------------------------------------------------------------- |
| `browser_new_tab`           | Create tab and switch focus                                    |
| `browser_list_tabs`         | List open tabs                                                 |
| `browser_switch_tab`        | Switch by `tab_id`                                             |
| `browser_close_tab`         | Close by `tab_id`                                              |
| `browser_wait_for_tab`      | Wait for a new tab to appear                                   |
| `browser_get_cookies`       | Read cookies                                                   |
| `browser_set_cookies`       | Inject cookies                                                 |
| `browser_clear_cookies`     | Delete one or all cookies                                      |
| `browser_grant_permissions` | Grant browser permissions (e.g. geolocation)                   |
| `browser_set_geolocation`   | Override geolocation                                           |
| `browser_set_extra_headers` | Set extra HTTP headers                                         |
| `browser_set_user_agent`    | Override user agent                                            |
| `browser_set_timezone`      | Override timezone                                              |
| `browser_set_locale`        | Override locale                                                |
| `browser_emulate_media`     | Emulate `prefers-color-scheme`, `prefers-reduced-motion`, etc. |
| `browser_save_state`        | Persist cookies + storage to disk                              |
| `browser_load_state`        | Restore cookies + storage from disk                            |
| `browser_list_sessions`     | List sessions                                                  |
| `browser_close_all`         | Close all sessions and browser                                 |

---

## Legacy State & Element Targeting

`browser_get_state` is the primary inspection primitive for the legacy MCP adapter. Its raw CDP-derived refs are not the planned task-space identity model.

| Mode    | Behavior                                                   |
| ------- | ---------------------------------------------------------- |
| `auto`  | Full on small pages, compact on dense pages                |
| `full`  | All interactive elements                                   |
| `min`   | Compact ranked subset (9-element budget, proximity-scored) |
| `focus` | Single element                                             |

- **Stable refs**: `e123` derived from CDP backend node IDs — survive re-renders
- **`since_hash`**: Returns `changed=false` when page is unchanged — zero element payload
- **Shadow DOM**: pierced automatically in element discovery

---

## Deterministic Extraction

`browser_extract_content` uses a native HTML parser (no LLM):

| Query                       | Extracts                   |
| --------------------------- | -------------------------- |
| `table rows`                | `<table>` rows and cells   |
| `all links`                 | `<a>` elements with href   |
| `images`                    | `<img>` + alt text         |
| `form fields`               | Inputs, selects, textareas |
| `list items`                | `<ul>` / `<ol>` items      |
| `key-value` / `definitions` | `<dl>` pairs, label panels |

---

## Configuration

### CLI flags

| Flag                        | Default   | Description                   |
| --------------------------- | --------- | ----------------------------- |
| `--cdp-url`                 | —         | Attach to an existing browser |
| `--session-timeout-minutes` | 0 (never) | Auto-close idle sessions      |

### Environment variables

| Variable                   | Description                              |
| -------------------------- | ---------------------------------------- |
| `AGENTYC_HEADLESS`         | `1` to run Chrome headless               |
| `AGENTYC_ALLOWED_DOMAINS`  | Comma-separated domain allowlist         |
| `AGENTYC_ACTION_TIMEOUT_S` | Per-action CDP timeout (default 180s)    |
| `AGENTYC_CDP_TIMEOUT_S`    | CDP response timeout (default 60s)       |
| `AGENTYC_PROXY_URL`        | Proxy server URL                         |
| `AGENTYC_PROXY_USERNAME`   | Proxy username                           |
| `AGENTYC_PROXY_PASSWORD`   | Proxy password                           |
| `AGENTYC_LOGGING_LEVEL`    | Log level (e.g. `warn`, `info`, `debug`) |

### Legacy Chrome defaults

These defaults apply only to the current legacy managed-browser compatibility path; they are not the existing-Chrome task-space product defaults.

- `headless=false` (visible browser)
- Downloads path: `~/Downloads/agentyc-mcp`
- Per-session isolated temp profile

---

## Legacy MCP Performance Measurements

These are legacy MCP/process measurements, not live existing-Chrome task-space, context-token, coexistence, or SOTA release evidence. Phase 0 remains blocked only by the independently enrolled existing-Chrome coexistence/Native Messaging host lane; the managed live performance and macOS disposable lifecycle lanes are captured separately under `artifacts/p0-performance/` and `artifacts/p0-installation/`.

| Metric                              | Value             |
| ----------------------------------- | ----------------- |
| Cold start (spawn → first response) | ~5ms              |
| `tools/list` p50                    | ~0.9ms            |
| Tool call overhead p50              | ~70µs             |
| Peak throughput                     | ~17,000 calls/sec |
| Binary size                         | ~8MB              |
| Idle RSS                            | ~3MB              |

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
