# traverse (MCP Edition)

Lightweight, deterministic browser automation over MCP for coding agents.

This repository is intentionally MCP-first: no autonomous agent loop, no standalone interactive CLI surface. The primary interface is an MCP stdio server exposing direct browser actions.

## What you get

- Deterministic browser tools: navigate, click (index or coordinates), type, upload files, scroll, back, tabs, screenshot, HTML, structured extract.
- Session management tools: list/close sessions and auto-cleanup for idle sessions.
- Browser/session primitives (`BrowserSession`, `BrowserProfile`) and tool registry (`Tools`) for direct Python integration.

## Quickstart

```bash
uv venv --python 3.11
source .venv/bin/activate
uv sync
```

Run the MCP server on stdio:

```bash
uv run traverse
```

Optional timeout override:

```bash
uv run traverse --session-timeout-minutes 20
```

## MCP tool surface

- `browser_navigate`
- `browser_click`
- `browser_type`
- `browser_upload_file`
- `browser_get_state`
- `browser_extract_content`
- `browser_get_html`
- `browser_screenshot`
- `browser_scroll`
- `browser_go_back`
- `browser_list_tabs`
- `browser_switch_tab`
- `browser_close_tab`
- `browser_list_sessions`
- `browser_close_session`
- `browser_close_all`

`browser_upload_file` takes a local path plus a ref or index for the upload control.

## Token-efficient MCP patterns

- `browser_get_state` supports `mode=auto|full|min|focus`, stable element refs like `e123`, `effective_mode`, optional label `context`, and `since_hash` for cheap unchanged-state checks.
- `mode=auto` is the default: it keeps full state on small pages and switches to ranked compact state on large pages.
- `browser_click` and `browser_type` accept those refs directly, so agents do not have to resend large state payloads just to keep targeting stable.
- `browser_extract_content` can answer low-ambiguity links, tables, list/checklist items, form fields, key-value panels, and search-result/link-collection queries deterministically; link-collection routes preserve URLs automatically when needed.
- `browser_extract_content` also accepts an optional `output_schema` through MCP; compatible direct table/list/form/key-value/link-collection queries can return validated JSON without an LLM round-trip.
- `browser_extract_content` keeps free-text summary/action queries on full markdown for small pages, but switches large pages to a compact action-summary context; `<extraction_metadata>` includes route, `llm_used`, deterministic/structured flags, partial markers, and `context_mode` so agents can tell what path ran.
- `browser_click` and `browser_type` fail with explicit machine-readable prefixes like `Error [target_disabled]`; typing now also fails on value postcondition mismatches, and ref-based actions do mild live-ref recovery for small DOM drift instead of silently missing.

## Development

```bash
./scripts/lint.sh
./scripts/test.sh
uv run python scripts/benchmark_mcp_runtime.py
uv run python scripts/benchmark_mcp_runtime.py --fixture dense-catalog --fixture long-docs
uv run python scripts/benchmark_mcp_runtime.py --fixture workflow-form --fixture pricing-table --fixture triage-checklist
uv run python scripts/benchmark_mcp_runtime.py --fixture delayed-release --fixture modal-wizard --fixture drift-recovery --fixture repeated-actions --fixture tab-workspace --fixture accessibility-panel
```

Current test script targets deterministic browser-core tests used by the MCP runtime, and the benchmark pack now gates compaction, deterministic extraction, and action reliability.

## Distribution

```bash
uv build
uvx traverse
pipx install traverse
```

Release publishing is handled by the GitHub `publish` workflow with trusted publishing on `0.1.0`.

## Dogfooding

```bash
./scripts/dogfood.sh
./scripts/dogfood.sh --json
DOGFOOD_OPEN_ISSUES=1 ./scripts/dogfood.sh
```

The dogfood runner exercises the harder browser workflows in the benchmark corpus: dense pages, long docs, forms, dialogs, iframes, shadow DOM, and comboboxes.
When `DOGFOOD_OPEN_ISSUES=1`, it also captures a per-run artifact bundle under `~/.traverse/dogfood/...` and opens a GitHub issue automatically for any regression it detects. Optional env vars: `DOGFOOD_ARTIFACT_DIR`, `DOGFOOD_ISSUE_REPO`, `DOGFOOD_ISSUE_LABELS`, `DOGFOOD_ISSUE_TITLE_PREFIX`.
Auto-issue mode requires `gh` to be installed and authenticated.
