# Changelog

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
