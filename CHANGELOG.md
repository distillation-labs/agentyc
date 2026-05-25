# Changelog

## [0.2.17] - 2026-05-25

### Added

- **LLM screenshot optimization pipeline** — `BrowserSession` now supports config-controlled screenshot resize, format, quality, and grayscale via `llm_screenshot_size`, `llm_screenshot_format`, `llm_screenshot_quality`, and `llm_screenshot_grayscale` constructor params. Default: WebP at 480×270 q=85 reduces screenshot token cost **8.3×** (58,816B → 7,116B base64, ~23,526 → ~2,846 estimated tokens) with no measured regression in the benchmark fixture used for UI understanding.
- **`resize_screenshot_for_llm()` static method** on `BrowserSession` for programmatic screenshot conversion supporting PNG, JPEG, WebP, and grayscale.
- **Dynamic MCP ImageContent mimeType** — `browser_get_state`, `browser_screenshot`, and `browser_export_debug_bundle` now return `image/webp` (or `image/jpeg`) instead of hardcoded `image/png` when the session format is configured accordingly.
- **`include_screenshot` default changed to `False`** for `browser_export_debug_bundle` to reduce unnecessary token consumption.

### Changed

- **Direct action pacing is leaner across hot MCP interaction paths** — `browser_click`, `browser_scroll`, and `browser_drag_to` now avoid conservative fixed sleeps that were adding latency without improving reliability on the validated headless workflows.
- **History navigation now waits for actual page readiness instead of a blind half-second sleep** — `browser_go_back` and `browser_go_forward` now settle on URL change plus DOM readiness, cutting the benchmarked history-navigation latency from ~`505 ms` to ~`55 ms`.
- **Benchmark baseline refreshed** — the current confirmed two-run headless medians are now runtime `import_ms=220.0`, `session_init_ms=1531.3`, `collaboration_latency_ms=1598.6`, plus stdio `success=1.0`, `accuracy=1.0`, `precision=1.0`, `duration_ms=45146.9`, `avg_ms=41.4`, `p95_ms=155.1`.
- **Packaged skills guide refreshed** — `agentyc init` now teaches release-readiness workflows that combine viewport control, DOM stability waits, downloads, trace/log hygiene, and PDF export, and it explains that `browser_handle_dialog` may return a success-shaped acknowledgment when the runtime already auto-handled a blocking popup.

### Tests

- **New history-navigation readiness coverage** — `test_go_back_waits_for_history_navigation_readiness_instead_of_fixed_sleep` verifies that back-navigation no longer relies on the old fixed `0.5s` delay.
- **New headless release-readiness workflow coverage** — `tests/ci/browser/test_headless_release_readiness.py` exercises download, PDF export, trace, log, viewport, DOM stability, and confirm-dialog behavior together in headless mode.
- **New dialog-acknowledgment coverage** — `tests/ci/browser/test_new_mcp_tools.py` now verifies that `browser_handle_dialog` acknowledges a recently auto-handled confirm dialog instead of only surfacing `No dialog is showing`.

## [0.2.16] - 2026-05-23

### Changed

- **Click/navigation recovery helpers now live in `agentyc.mcp.navigation_runtime`** — this keeps `agentyc/mcp/action_runtime.py` under the enforced release file-size guard without changing MCP click, wait, or recovery behavior.
- **The published package now carries forward the Home Depot headless reliability fixes from `0.2.15`** — transient navigation retries, click neterror recovery, and hidden responsive search deduping all ship in the released build.

## [0.2.15] - 2026-05-23

### Fixed

- **Headless navigation now retries transient transport failures** — `browser_navigate` is more resilient to flaky upstream errors such as `ERR_HTTP2_PROTOCOL_ERROR`, which was causing intermittent failures on sites like Home Depot in headless mode.
- **Click-driven navigations no longer report false success on browser neterror pages** — when a link click lands on Chrome’s interstitial error page, Agentyc now retries/reclassifies the navigation instead of returning a success-shaped click result against an unusable page.
- **Hidden responsive search duplicates are no longer exposed as interactive refs** — state serialization now keeps real rendered search inputs while excluding hidden mobile/tablet variants that were misleading agents on responsive sites like Home Depot.

### Tests

- **New navigation retry coverage** — `tests/ci/browser/test_navigation_retries.py` verifies bounded retries for transient document-load failures.
- **New click recovery coverage** — `tests/ci/test_click_navigation_recovery.py` verifies that click-triggered HTTP navigations retry or return `site_unavailable` instead of a false success.
- **New responsive-search visibility coverage** — `tests/ci/browser/test_state_compaction.py` now checks that hidden duplicate search controls are excluded while the real search input is preserved.

## [0.2.14] - 2026-05-22

### Added

- **`browser_wait_for_request` / `browser_wait_for_response`** — precise network synchronization tools that wait for a matching request or response by URL, method, optional resource type, and optional status. They are intended for API-heavy apps where `browser_wait_for_network_idle` is too broad.
- **`browser_export_debug_bundle`** — one-shot debugging bundle that packages the current state payload, recent console logs, recent network activity, pending requests, trace summary, optional scoped HTML, and optional screenshot for agents and operators.

### Changed

- **Tool surface expanded from 50 to 53** — the public MCP server now includes precise request/response waits plus a compact debug bundle primitive.
- **Network capture now records wall-clock observation timestamps** — `browser_wait_for_network_idle`, `browser_wait_for_request`, and `browser_wait_for_response` now share a consistent baseline for deciding which requests belong to the active wait window.

### Tests

- **New real-browser MCP coverage** — `tests/ci/browser/test_new_mcp_tools.py` now covers the debug bundle plus precise request/response waits.
- **Headless stdio benchmark coverage expanded** — `scripts/benchmark_mcp_stdio_e2e.py` now exercises the new debug/network tools as part of the public-surface headless run.

## [0.2.13] - 2026-05-22

### Changed

- **Shared-browser subagents now stay in the same browser profile by default** — runtimes launched with `agentyc mcp --cdp-url ...` still claim a dedicated collaboration tab automatically, but they no longer create a per-runtime Chrome `BrowserContext` during attach. This keeps cookies, local storage, and authenticated workspace state available across parallel subagents while preserving tab-scoped refs, console logs, and network logs.
- **Benchmark baseline refreshed** — the public benchmark references now use the confirmed two-run headless medians: runtime `import_ms=241.5`, `session_init_ms=1371.4`, `collaboration_latency_ms=1456.8`, plus stdio `success=1.0`, `accuracy=1.0`, `precision=1.0`, `duration_ms=53942.6`, `avg_ms=50.2`, `p95_ms=202.5`.

### Tests

- **New real-browser parallel-subagent coverage** — `tests/ci/browser/test_parallel_subagent_tabs.py` verifies that multiple attached subagents automatically get distinct owned tabs, share browser auth/storage state, and can run parallel actions without cross-tab bleed.

## [0.2.12] - 2026-05-19

### Added

- **Default tab grouping by owner/runtime** — `browser_list_tabs` and `browser_get_state` now return a `tab_groups` array alongside the flat `tabs` list. Groups are ordered by `owner_kind` priority (agent → runtime → human) and labeled by `runtime_label`/`display_label`. Each group includes `group_id`, `owner_kind`, `tab_count`, `runtime_id`, and the contained tabs. The flat `tabs` list is preserved for backward compatibility.
- **`build_tab_groups_payload()` helper** — groups serialized tabs by `runtime_id` with deterministic sort order. Human-owned tabs go into a `human` group, unknown-ownership tabs fall into `ungrouped`.
- **`state_hash` now tracks tab ownership metadata** — the hash includes `tab_id`, `url`, `title`, `display_title`, `owner_kind`, `display_label`, `runtime_id`, `runtime_label`, and `current_tab_id`. This means `since_hash` polling correctly detects when a tab is claimed or released by an agent.

### Changed

- **`browser_list_tabs` response changed** — from a bare JSON array to `{"tabs": […], "tab_groups": […]}` envelope. All in-repo consumers (benchmarks, tab-recovery tests) updated to read `['tabs']` from the envelope.
- **`browser_get_state` now includes `tab_groups`** — the default state payload includes the grouped tab view so operators and parent agents can see tab distribution at a glance.

### Tests

- **Tab grouping unit test** — `test_build_tab_groups_payload_groups_tabs_by_runtime_and_human_owner` in `test_shared_browser_collaboration.py`.
- **State-hash ownership-change test** — `test_browser_state_hash_changes_when_tab_ownership_changes` in `test_mcp_runtime_optimizations.py`.
- **Updated existing tests** — `test_browser_state_includes_ownership_and_tab_groups`, `test_browser_list_tabs_includes_collaboration_metadata`, and `test_browser_state_hash_matches_min_payload` extended to verify `tab_groups` in output.

## [0.2.11] - 2026-05-19

### Added

- **`browser_save_as_pdf`** — Save the current page as a PDF file via CDP `Page.printToPDF`. Supports custom filename, paper format (Letter/A4/A3/Legal/Tabloid), landscape, scale, and background graphics.
- **`browser_get_downloads`** — List files downloaded during the current browser session (path, name, size).
- **`browser_set_viewport`** — Dynamically set browser viewport width, height, and device scale factor.
- **`browser_wait_for_stable_dom`** — Wait until DOM mutations settle for a configurable quiet period using a `MutationObserver` — more reliable than fixed waits after SPA transitions or AJAX calls.
- **`browser_handle_dialog`** — Accept or dismiss JavaScript dialogs (alert, confirm, prompt, beforeunload) that would otherwise block agent interaction.
- **`browser_get_attribute`** — Get a specific attribute value from an element by ref or index (e.g. `href`, `src`, `value`, `disabled`).
- **`browser_clear_logs`** — Clear console and/or network log buffers to free memory during long sessions.
- **`browser_start_trace`** / **`browser_stop_trace`** — Start and stop CDP performance traces for diagnosing page performance issues; collected trace events returned as JSON.

### Changed

- **Tool surface expanded from 41 to 50** — 9 new MCP tools covering PDF export, file download tracking, viewport control, DOM stability waits, dialog handling, attribute extraction, log buffer management, and CDP performance tracing.
- **CDP Tracing listener auto-registered** — `_register_cdp_event_listeners` now wires `Tracing.dataCollected` so trace events accumulate in the in-memory buffer.

### Tests

- **New test fixtures** added in `tests/ci/browser/test_new_mcp_tools.py` covering all 9 new tools.
- **Autonomous agent workflow tests** in `tests/ci/browser/test_autonomous_agent_workflows.py`.

## [0.2.10] - 2026-05-18

### Fixed

- **No stray blank window after closing the last tab** - Agentyc no longer recreates a background `about:blank` page with the `Starting agent ...` title after the final browser tab is closed. When no tabs remain, focus stays empty until a later action explicitly needs a page, at which point recovery creates a new tab on demand instead of respawning one in the background.

## [0.2.9] - 2026-05-18

### Fixed

- **Last-tab close no longer respawns a stray browser surface** - closing the final Agentyc-controlled tab no longer causes a delayed `about:blank` window or `Starting agent ...` page to reappear after the user is done with automation.
- **Tab recovery is now demand-driven when no pages remain** - the runtime stops background-creating replacement tabs on last-tab close. If a later tool action truly needs a page, focus recovery creates a fresh `about:blank` tab at that point instead of racing the user with proactive recovery.

## [0.2.8] - 2026-05-18

### Changed

- **Distributed skills guide refresh** - `agentyc init` now writes a stronger packaged guide for coding agents, covering structured error recovery, visible-text waits, long-page search workflows, and safer multi-tab handoff after opening or closing tabs.

### Fixed

- **Visible-text waits for dynamic UI copy** - `browser_wait_for_element` now detects live visible text updates such as toasts, upload filenames, and validation messages instead of only depending on interactive controls.
- **Active-tab close recovery** - tab switching now recovers to the surviving page correctly after closing the focused tab, so follow-up actions can continue on the remaining tab.
- **Fresh tab titles and structured MCP failures** - current page titles refresh from the live page before state serialization, and textual tool failures now surface as MCP `isError` responses.
- **Long-page compact-state control retention** - compact `browser_get_state` payloads keep high-value search entry controls discoverable after deep scroll on long documentation-style pages.

## [0.2.7] - 2026-05-18

### Changed

- **Packaged skills guide refresh** - `agentyc init` now emits a more actionable MCP usage guide for coding agents, including escalation steps when refs are missing, explicit dropdown and file-upload recipes, keyboard and focus workflows, multi-tab guidance, and sharper debugging patterns.

## [0.2.6] - 2026-05-18

### Fixed

- **Dense-catalog benchmark stability** — the stdio heavy-workload harness now captures a fresh post-action `state_hash` before checking `since_hash` unchanged responses. This removes false failures after intentional scroll actions without weakening runtime state semantics.
- **Form-control discoverability in headed and headless runs** — `browser_find_elements` and `browser_get_state` now preserve accessible labels for unlabeled controls and file inputs, so upload and workflow fixtures stay discoverable across benchmark modes.
- **Local browser restart cleanup** — `session_runtime.kill()` and `stop()` now preserve the owned local-browser watchdog long enough to tear down the previous browser after stop/reset, preventing restart-path instability in benchmark coverage and real MCP sessions.

## [0.2.5] - 2026-05-16

### Changed

- **Release-gate cold-start ceiling** — the publish workflow now treats `session_init_ms <= 35000` as the measured CI cold-start ceiling. Recent GitHub Actions runs reached `32181.6` during browser/extension bootstrap on fresh runners, so the previous `25000` threshold was rejecting non-regression cold starts.

## [0.2.4] - 2026-05-16

### Fixed

- **Compact shared-browser state payloads** — `browser_get_state` in compact (`min`/auto-compacted) mode no longer repeats the current tab's `url` and `title` inside `current_tab` when that page identity is already present at the top level. This preserves collaboration metadata while restoring enough payload-reduction headroom for CI release gating.

## [0.2.3] - 2026-05-16

### Fixed

- **Issue-queue structured extraction aliases** — table projection now recognizes `issues` collections, `issue_count`/row-count aliases, and `Issue`/title-style field aliases so the release-gate issue-triage fixture extracts correctly without benchmark-specific special cases.
- **Shared-browser peer tab reconciliation** — `get_tabs()` now backfills missing page targets from root `Target.getTargets()` discovery and restores runtime ownership from title metadata, fixing collaboration checks when one runtime attaches after another has already opened tabs.
- **Medium-page auto compaction** — `browser_get_state` now switches `auto` to compact serialization at 10 interactive elements while still preserving the full element set under the min-mode cap, raising release-gate payload reduction back above threshold without hurting recall.

## [0.2.2] - 2026-05-16

### Changed

- Benchmark optimization work and release grooming after the 0.2.1 release cut.

## [0.2.1] - 2026-05-16

### Added

- **`browser_new_tab`** — Creates a new browser tab and switches focus to it in a single call. Accepts an optional `url` argument (default: `about:blank`). The recommended primitive for parallel automation: each subagent calls `browser_new_tab` immediately after attaching via `--cdp-url` to get its own isolated tab without disturbing other agents or the human operator.
- **Request and response headers in `browser_get_network_log`** — Pass `include_headers: true` to include raw request headers and response headers in each network log entry. Headers are captured via CDP and stored in the in-memory buffer; omitted by default to keep token usage low.
- **Auto-enable CDP Network domain in `browser_wait_for_network_idle` and `browser_get_network_log`** — Both tools now register CDP event listeners automatically if they have not been initialized yet, so agents no longer need to explicitly warm up the capture buffer before calling these tools.

### Changed

- **Drag step timing reduced** — Mouse movement steps in `browser_drag_to` now pause 10 ms instead of 20 ms between steps, and the post-press delay drops from 50 ms to 30 ms. A 10-step drag is roughly 130 ms faster.

### Fixed

- **Stale Chrome browser accumulation** — `_execute_tool` now detects an in-progress WebSocket reconnect and waits up to 20 s for it to finish before deciding to kill the session and spawn a new Chrome process. Previously, any tool call that arrived while the WS was reconnecting immediately abandoned the session and launched another Chrome, causing visible windows to accumulate on the desktop.
- **Parallel tab test stability** — `test_two_agents_operate_on_independent_tabs` and `test_parallel_actions_do_not_interfere` now poll for interactive elements instead of sleeping a fixed 0.3 s, eliminating flakiness when the event loop is loaded with background tasks from a full test suite run.
- **Shared-browser startup attach race** — shared runtimes now enable root `Target.setAutoAttach` before session monitoring starts and avoid manually re-attaching existing page targets during bootstrap. This removes duplicate startup sessions for the same page, stabilizing concurrent navigation across attached MCP runtimes.

### Token savings

- `browser_get_state` responses were already compact-serialized in 0.2.0. No additional serialization changes in this release.
- `browser_get_network_log` continues to omit `req_headers` and `resp_headers` by default, keeping baseline log payloads small.

---

## [0.2.0] - 2026-05-15

### Added

- **`agentyc init` CLI command** — writes an `agentyc-skill.md` guide to your project directory so coding agents bootstrap correct usage patterns automatically. Use `--print` to emit to stdout or `--force` to overwrite an existing file.
- **Skills guide** ships as package data (`agentyc/skills/SKILL.md`); covers the read→ref→act loop, `since_hash` polling, state modes, extraction routes, auth save/restore, parallel agents, JS evaluation, and common mistakes.
- **Post-action navigation context** — `browser_navigate` and `browser_click` results now include the destination page title (e.g. `Navigated to: https://example.com | "Page Title"`), letting agents skip a follow-up `browser_get_state` after navigation.
- **Scroll position in unchanged-state responses** — `browser_get_state` with `since_hash` now includes the current scroll position even when `changed=false`.
- **Viewport proximity scoring** in `min` mode — elements within 2× the viewport height of current scroll receive a score boost, surfacing the most immediately actionable elements first.
- **Actionable error codes with hints** — all action errors carry a structured `[error_code]` and a `Hint:` line (e.g. `Error [stale_ref]: ... Hint: Call browser_get_state() to get fresh refs before retrying.`).
- **Shared-browser cookie/session isolation** — each MCP server instance attached via `--cdp-url` now gets its own Chrome browser context (`Target.createBrowserContext`), preventing cookie and storage bleed between parallel agents.
- **Human-readable tab title prefix** — shared-browser tabs are stamped `[Agent-1] Page Title` instead of the opaque `[agtyc:a1b2] Page Title` hash.
- **Network idle JS Performance API fallback** — `browser_wait_for_network_idle` falls back to `performance.getEntriesByType(‘resource’)` for detecting AJAX idle on pages where CDP lifecycle events are stale.
- **Partial text matching for stale-ref recovery** — the semantic element matcher now handles transitional text (e.g. "Submit" → "Submitting…") using substring matching.
- **Expanded extraction hint vocabulary** — `browser_extract_content` recognises 150+ query phrases across all deterministic routes (up from ~50).
- **Explicit extraction error with route examples** — unrecognised queries return a structured error listing supported routes and example queries.

### Changed

- **Compact JSON responses** — all tool responses switched from `indent=2` to compact serialisation; 42% smaller state payloads, 35% smaller on average.
- **Implicit ARIA role omission** — `browser_get_state` omits `role` when it is implied by `tag`+`type` (e.g. `tag=button` implies `role=button`). Saves 10–20 tokens per element.
- **Tool schema trimmed 30%** — all tool descriptions tightened; removes ~1,500 tokens of per-turn overhead.
- **Scroll and page fields omitted when trivial** — `scroll` is omitted at origin `{x:0, y:0}`; `page` is omitted when the full page fits within the viewport.
- **`min` mode element cap raised from 25 to 30** — reduces follow-up scroll+re-read cycles on dense pages.
- **`session_timeout_minutes` default changed from 10 to 0** — sessions no longer auto-close on idle. Pass `--session-timeout-minutes N` to restore the previous behaviour.
- **Overlay ribbon removed** — the in-page `[agtyc]` ownership ribbon injected in shared-browser sessions has been removed. Tab title prefix remains the sole ownership indicator.

### Fixed

- Network idle detection was using stale CDP lifecycle events from the initial page load rather than post-action events.
- Cross-agent tab ownership detection bypassed the `detected_runtime` title-based cache and always probes `window.__agentycCollaboration` for the full runtime UUID.

### Session token savings (typical 20-turn session)

| Source | Savings |
|---|---|
| Compact JSON (40 state calls) | ~12,000 tokens |
| Tool schema (20 turns × 1,500 tokens) | ~30,000 tokens |
| Implicit role omission | ~2,000 tokens |
| **Total** | **~44,000 tokens** |

---

## [0.1.0] - initial release

- Added explicit release-gate evaluation to `scripts/benchmark_mcp_runtime.py`, including threshold checks and non-zero exit behavior without requiring GitHub issue creation.
- Added `scripts/check_release_guards.py` and a publish-time file-size guard for oversized Python modules, plus a watchlist artifact for modules above the preferred modularity target.
- Added `docs/release-gate.md` documenting benchmark thresholds, dogfood environment variables, and CI publish gating.
- Documented the current shared-browser runtime contract across README and public docs, including runtime labels, ownership metadata, window mode, window bounds, and focus policy behavior.
- Extended `scripts/dogfood.sh` so release-gate thresholds can be driven from environment variables in CI or local dogfooding.
- Updated `.github/workflows/workflow.yml` so publishing is blocked on benchmark and modularity/file-size release gates.
- Replaced the stale internal `agentyc/README.md` with contributor-facing guidance.
