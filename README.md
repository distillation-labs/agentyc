# agentyc

<p align="center">
  <em>Deterministic, host-backed browser task spaces for coding agents.</em><br>
  No API key needed. No LLM fallback. Direct CLI/SDK is the primary interface; MCP is compatibility-only.
</p>

<p align="center">
  <img src="https://img.shields.io/badge/rust-≥1.80-orange?style=flat&logo=rust&logoColor=white" alt="Rust ≥1.80">
  <img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="MIT">
  <img src="https://img.shields.io/badge/CLI%2FSDK-primary-success?style=flat" alt="CLI/SDK Primary">
  <img src="https://img.shields.io/badge/MCP-compatibility-informational?style=flat&logo=modelcontextprotocol&logoColor=white" alt="MCP Compatibility">
</p>

---

## What It Is

`agentyc` is a single native binary providing direct host-backed CLI and Node SDK access to logical browser task spaces and managed pages. The direct CLI/SDK is the primary interface; MCP is compatibility-only.

Key architectural boundaries:

- **Logical task spaces and pages:** The primary object model uses logical identifiers (`space_id`, `page_id`) and durable labels. Raw browser target IDs, session IDs, and tab IDs are never public identity and are not exposed.
- **Explicit shared-profile disclosure:** Automation operates inside the user's existing Chrome profile (sharing cookies, sessions, and storage), not an isolated sandbox. Creating a task space strictly requires explicit acknowledgement (`--accept-shared-profile-disclosure` in the CLI or `acceptSharedProfileDisclosure: true` in the SDK).
- **No implicit Chrome launch or download:** Agentyc does not launch Chrome or download Chromium binaries automatically. Live automation connects to an existing Chrome browser with the enrolled Agentyc MV3 extension and Native Messaging host.
- **Deterministic fake-host seam:** For testing and CI without a live browser, `--offline` (or `AGENTYC_FAKE_HOST=1`) executes host broker operations deterministically in-process.
- **Current CLI per-invocation model:** Direct CLI commands run per invocation against the host ledger. In offline mode, each process invocation starts a fresh broker instance, which fences active leases from previous runs. A long-lived host transport is the continuity mechanism for the SDK.
- **Live validation limits:** Live browser control requires an enrolled Chrome extension and Native Messaging host on macOS. When the extension is absent, commands return typed errors (`extension_not_connected` or `capability_unavailable`) rather than falling back to an unverified runtime.

Cold start: **~5ms**. Binary: **~8MB**. Idle RSS: **~3MB**.

```bash
# Direct host-backed CLI commands:
agentyc host status   # inspects host status and bridge capabilities
agentyc space list    # lists logical task spaces
agentyc init          # writes agentyc-skill.md — point your agent at it
# Portable agent plugin bundle:
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
# Move the binary onto your PATH
```

**Or build from source:**

```bash
cargo install --git https://github.com/distillation-labs/agentyc agentyc
```

### Runnable tested example (using deterministic offline seam)

You can execute direct task-space commands immediately without live Chrome using `--offline`:

```bash
# Inspect host lifecycle and capabilities:
agentyc --state-dir /tmp/agentyc-state --offline --json host status

# Create a task space with explicit shared-profile disclosure:
agentyc --state-dir /tmp/agentyc-state --offline --json space create \
  --label research \
  --accept-shared-profile-disclosure

# List logical task spaces:
agentyc --state-dir /tmp/agentyc-state --offline --json space list
```

### Bootstrap your agent with the skills guide

```bash
agentyc init                      # writes agentyc-skill.md
agentyc init --output .agent.md   # custom path
agentyc init --print              # print to stdout
agentyc init --force              # overwrite existing
```

Point your agent at that file. It teaches the read→ref→act→verify loop, supported direct operations, error reconciliation, and safety. The canonical installable skill and plugin metadata live under `.agents/skills/agentyc-browser-automation/` and `plugins/agentyc-browser-automation/`; see `docs/skills-and-plugins.md`.

### Optional MCP compatibility adapter (for MCP clients only)

The direct host-backed CLI/SDK is the primary interface. MCP is compatibility-only. To connect an MCP client:

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

Add `"--extended"` to `agentyc mcp` only when optional legacy observability tools are needed.

---

## Existing-Chrome task spaces

The host/core contracts, MV3 extension package, Native Messaging host, direct CLI/SDK, context/reliability modules, rollout gates, and MCP compatibility adapter are present in the repository. Each logical space owns its managed pages; leases and epochs authorize mutations. Live product and release gates remain tracked across execution phases. See the [task-space plan](docs/exec-plans/active/agentyc-browser-task-spaces/README.md) and [release gate](docs/release-gate.md).

---

## How It Compares

|                       | agentyc                                     | browser-use                 | Playwright MCP           |
| --------------------- | ------------------------------------------- | --------------------------- | ------------------------ |
| **Interface**         | Direct host-backed CLI / Node SDK (primary) | Python script + custom loop | MCP wrapper over library |
| **LLM required**      | No                                          | Yes (planner)               | No                       |
| **Extraction**        | Deterministic (structured routes)           | LLM-based                   | Raw page access          |
| **State snapshots**   | Token-aware, compact, normalized hash       | Full DOM dump               | Full DOM or AX tree      |
| **Element targeting** | Stable logical refs (`ref_`)                | XPath/CSS selectors         | Playwright locators      |
| **Browser backend**   | Enrolled Chrome extension                   | Playwright                  | Playwright               |
| **Runtime**           | Native binary (~8MB)                        | Python + many deps          | Node + Playwright        |
| **Cold start**        | ~5ms                                        | ~300ms+                     | ~200ms+                  |
| **Compatibility MCP** | 61 default / 76 extended adapter tools      | ~15–20                      | ~20                      |

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

### Direct CLI options

| Flag                   | Description                                                                            |
| ---------------------- | -------------------------------------------------------------------------------------- |
| `--state-dir <PATH>`   | Durable state directory (defaults to `$AGENTYC_STATE_DIR` or `~/.agentyc/state`).      |
| `--principal <ID>`     | Logical principal identifier (defaults to `principal_cli`).                            |
| `--profile-binding-id` | Enrolled profile binding suffix or complete identity.                                  |
| `--offline`            | Use the deterministic in-process fake-host seam for testing/CI without live Chrome.    |
| `--json`               | Emit compact structured JSON on stdout (default emits pretty-printed structured JSON). |

### Environment variables

| Variable                  | Description                                                            |
| ------------------------- | ---------------------------------------------------------------------- |
| `AGENTYC_STATE_DIR`       | State directory for host ledger and leases.                            |
| `AGENTYC_PRINCIPAL`       | Logical principal identity override.                                   |
| `AGENTYC_FAKE_HOST`       | Set to `1` to select the deterministic fake-host test seam.            |
| `AGENTYC_ALLOWED_DOMAINS` | Comma-separated domain allowlist for constrained workflows.            |
| `AGENTYC_LOGGING_LEVEL`   | Log level for diagnostics emitted to stderr (`warn`, `info`, `debug`). |

### Legacy compatibility options

| Flag                        | Default   | Description                                                |
| --------------------------- | --------- | ---------------------------------------------------------- |
| `--cdp-url`                 | —         | Attach to an existing browser (legacy compatibility only). |
| `--session-timeout-minutes` | 0 (never) | Auto-close idle sessions in legacy runtime.                |
| `AGENTYC_HEADLESS`          | `0`       | `1` to run legacy Chrome headless.                         |
| `AGENTYC_ACTION_TIMEOUT_S`  | `180`     | Per-action timeout for legacy CDP adapter.                 |
| `AGENTYC_CDP_TIMEOUT_S`     | `60`      | CDP response timeout for legacy CDP adapter.               |
| `AGENTYC_PROXY_URL`         | —         | Proxy server URL for legacy browser launch.                |

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
