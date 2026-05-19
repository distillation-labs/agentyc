# Changelog

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
