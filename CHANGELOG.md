# Changelog

## [2.0.0] - 2026-08-23

### Added

- **Agent superpower skill** — added the canonical `agentyc-browser-automation` skill with
  frontend selection guidance, the read → ref → act → verify operating loop, deterministic
  tool routing, stale-reference recovery, iframe/tab workflows, safety rules, and proof standards.
- **Portable plugin bundle** — added `plugins/agentyc-browser-automation/plugin.json` and
  installation documentation for MCP clients and coding-agent hosts.
- **Agent evaluation cases and playbook** — added trigger, functional, performance, and safety
  cases plus a workflow/tool-selection reference for consistent agent behavior.
- **Shared runtime frontends** — added the frontend-neutral `BrowserRuntime` and shared command
  dispatch used by MCP, one-shot CLI, and REPL workflows.
- **Streamable HTTP MCP** — added `agentyc serve` with the MCP endpoint mounted at `/mcp`.

### Changed

- **Native Rust browser architecture** — browser/CDP lifecycle, target attachment, active-tab
  semantics, state inspection, interaction, extraction, storage, frames, tabs, and sessions now
  use the canonical Rust browser session and runtime boundaries.
- **MCP compatibility** — preserved the existing browser tool names, schemas, response shapes, and
  active-tab behavior while adding the shared runtime underneath.
- **Agent-facing guidance** — synchronized the embedded `agentyc init` guide with the installable
  skill and documented MCP, REPL, and CLI usage in the public docs.
- **Observability and deterministic waits** — retained bounded state snapshots, `since_hash`
  polling, explicit network/DOM waits, extraction routes, and optional extended diagnostics.

### Fixed

- **Chrome process ownership** — isolated browser profiles and cleanup now terminate owned Chrome
  browser/helper/renderer processes without touching the user's normal Chrome profile.
- **CDP session routing** — flattened attached page sessions now route page-domain commands through
  the correct session while browser-level target commands remain root-scoped.
- **Lifecycle reliability** — serialized initialization, tab switching, replacement, cleanup, and
  failed-launch recovery to prevent duplicate windows, stale attachments, and leaked browsers.
- **CLI/REPL parsing and state** — fixed shared command parsing, quote handling, tab lifecycle,
  deterministic scrolling, network mocks, stable-DOM/network-idle waits, and browser-scoped state.

### Validation

- Full workspace formatting, Clippy, build, and test gates pass.
- 50 real-Chrome browser automation tests pass.
- 29 runnable end-to-end tests pass; network/live-site cases remain intentionally ignored.
- MCP protocol, stdio, Streamable HTTP, CLI, and REPL smoke tests pass.
- Release performance benchmark passes: MCP overhead p50 ~0.03 ms and sustained no-op throughput
  above 43,000 calls/sec in the optimized local benchmark.

## [Unreleased]

## [0.4.1] - 2026-06-13

### Changed

- **Complete Python elimination** — all Python test harnesses, CI workflows, configuration
  files, skills, and documentation that described the old Python architecture have been
  replaced with Rust equivalents. The runtime has been 100% Rust since 0.4.0; this
  completes the repository.

### Added

- **Shadow DOM support (#1)** — `browser_get_state` now pierces open shadow roots. Shadow
  inputs and buttons appear as stable refs and are reachable by the real `browser_click`,
  `browser_type`, etc. tools. Background: `get_interactive_elements` now recurses every
  shadow host (not just interactive ones) and resolves backendNodeIds via a single pierced
  `DOM.getDocument` walk instead of per-node round-trips.
- **Deterministic dialog handling (#2)** — a background task auto-handles
  `Page.javascriptDialogOpening` events (default: accept). `browser_handle_dialog` sets
  the policy for all subsequent dialogs. Eliminated a deadlock: all `cdp()` helpers now
  clone the client and release the state lock before awaiting, so background tasks can run
  while a CDP call is in flight.
- **Auto-retry on stale/transient refs (#3)** — element resolution retries once after 250 ms
  when the first attempt fails, covering transient layout-not-settled cases.
- **Lean default vs opt-in extended profile (#4 + #7)** — the default tool surface stays 61
  tools. Pass `--extended` (or `AGENTYC_EXTENDED=1`) to add 15 observability tools (console
  logs, network log, network mocks, conditions, request replay, debug bundle, downloads,
  trace). Zero context cost for the common case.
- **Token-bounded outputs (#5)** — `browser_get_html` capped at 40k chars with a marker;
  `browser_extract_content` arrays bounded to 300 items with a "N more" marker.
- **`min` as the default `get_state` mode (#6)** — smallest reliable payload by default;
  agents escalate explicitly.
- **Observability tools (#7)** — the 15 tools previously orphaned in `observability.rs` are
  now wired and functional behind `--extended`: `browser_get_console_logs`,
  `browser_get_network_log`, `browser_inspect_network_entry`, `browser_add_network_mock`,
  `browser_remove_network_mock`, `browser_list_network_mocks`, `browser_set_network_conditions`,
  `browser_get_network_conditions`, `browser_replay_request`, `browser_export_debug_bundle`,
  `browser_get_downloads`, `browser_wait_for_download`, `browser_clear_logs`,
  `browser_start_trace`, `browser_stop_trace`. Console and network events captured in
  bounded ring buffers (500 entries each).
- **Rust integration-test suite** — 2,500 tests across 18 real-world categories, all
  passing headless. Includes a shared `Mcp` harness, a local fixtures HTTP server with 16
  realistic stateful web apps, a data-driven scenario catalog (generated via `build.rs`),
  and a step/check engine driving the real MCP tools.
- **Cargo-based CI** — `test.yaml` (Chrome + `cargo test`), `lint.yml` (fmt + clippy -D
  warnings), `package.yaml` (release binaries for Linux/macOS/Windows), `workflow.yml`
  (release gate + binary publish to GitHub Releases), `claude.yml` (cargo-based agent
  workflow). Docker and PyPI workflows removed.
- **Rust Dockerfile replaced with binary distribution** — prebuilt binaries attached to
  GitHub Releases for all four targets via the release workflow.

## [0.4.0] - 2026-06-11

The first release of the Rust implementation: `agentyc` is a single native binary
that runs a deterministic, CDP-native MCP server for browser automation.

### Highlights

- **Single native binary** — no interpreter, no virtualenv, no install step. Cold
  start in a few milliseconds; idle RSS around 12 MB; binary under 10 MB. Uses
  `mimalloc` as the global allocator.
- **61 MCP tools** over stdio (and Streamable HTTP via `agentyc serve`), built on
  the `rmcp` SDK. Tool names, descriptions, and input schemas are stable.
- **Auto-launches Chrome** — `agentyc mcp` discovers and launches a Chrome/Chromium
  process on the first `browser_navigate` call. No separate browser setup needed.
- **Deterministic HTML extraction** — `browser_extract_content` uses a native Rust
  HTML parser (`scraper`) for tables, links, images, form fields, lists, and
  key-value pairs. No LLM in the loop.
- **Stable element refs** — `browser_get_state` returns `e<backend_node_id>` refs
  that survive re-renders, with `since_hash` polling and `auto`/`full`/`min`/`focus`
  modes.
- **`SKILL.md` embedded** — `agentyc init` writes the skills guide straight from the
  binary via `include_str!`, with no filesystem lookups.

---

Releases prior to `0.4.0` predate the Rust implementation and are available in the
project's git history.


The first release of the Rust implementation: `agentyc` is a single native binary
that runs a deterministic, CDP-native MCP server for browser automation.

### Highlights

- **Single native binary** — no interpreter, no virtualenv, no install step. Cold
  start in a few milliseconds; idle RSS around 12 MB; binary under 10 MB. Uses
  `mimalloc` as the global allocator.
- **61 MCP tools** over stdio (and Streamable HTTP via `agentyc serve`), built on
  the `rmcp` SDK. Tool names, descriptions, and input schemas are stable.
- **Auto-launches Chrome** — `agentyc mcp` discovers and launches a Chrome/Chromium
  process on the first `browser_navigate` call. No separate browser setup needed.
- **Deterministic HTML extraction** — `browser_extract_content` uses a native Rust
  HTML parser (`scraper`) for tables, links, images, form fields, lists, and
  key-value pairs. No LLM in the loop.
- **Stable element refs** — `browser_get_state` returns `e<backend_node_id>` refs
  that survive re-renders, with `since_hash` polling and `auto`/`full`/`min`/`focus`
  modes.
- **`SKILL.md` embedded** — `agentyc init` writes the skills guide straight from the
  binary via `include_str!`, with no filesystem lookups.

### CLI

- `agentyc` / `agentyc mcp [--cdp-url]` — run the stdio MCP server.
- `agentyc serve [--host --port --cdp-url]` — run the MCP server over Streamable HTTP.
- `agentyc init [--output --print --force]` — write the bundled skills guide.
- `agentyc browser [--port --headless --detach]` — launch Chrome with remote
  debugging and print the CDP WebSocket URL.

### Configuration

- Honors `AGENTYC_HEADLESS`, `AGENTYC_ALLOWED_DOMAINS`, `AGENTYC_ACTION_TIMEOUT_S`,
  `AGENTYC_CDP_TIMEOUT_S`, `AGENTYC_PROXY_URL`, `AGENTYC_PROXY_BYPASS`,
  `AGENTYC_PROXY_USERNAME`, `AGENTYC_PROXY_PASSWORD`, and `AGENTYC_LOGGING_LEVEL`.
- Per-session isolated Chrome temp profiles — multiple agents can run independent
  browsers simultaneously without conflicts.

### Workspace

- Crates: `agentyc` (binary/CLI), `agentyc-mcp` (server + tools), `agentyc-cdp`
  (CDP client), `agentyc-browser` (launch/profile/session), `agentyc-dom` (DOM
  serialization, clickable detection, markdown), `agentyc-tools` (deterministic
  extraction routing), and `agentyc-tests` (integration harness + suites).

---

Releases prior to `0.4.0` predate the Rust implementation and are available in the
project's git history.
