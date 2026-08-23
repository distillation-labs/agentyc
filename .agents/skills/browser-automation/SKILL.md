---
name: agentyc-browser-automation
description: >
  Guides coding agents to use Agentyc's browser MCP tools for end-to-end web automation,
  debugging, extraction, auth persistence, shared-browser collaboration, and release-readiness
  checks. Use when a user wants to automate a site with Agentyc, asks which browser_* tool to
  use, needs a browser workflow plan, or needs troubleshooting for MCP-driven browser work.
metadata:
  version: "1.0.0"
  category: browser-automation
  mcp-server: agentyc
  tags: [agentyc, mcp, browser, automation, tabs, network, extraction, debugging]
license: MIT
---

# Agentyc Browser Automation

Use Agentyc as a deterministic browser runtime, not as a vague "browse the web" surface.
Prefer the smallest browser tool that answers the question or performs the action safely.

## Optimize For

- user-visible task completion with explicit browser evidence
- minimal tool calls and input tokens per step
- low-latency read -> act -> verify loops
- human-like sequencing over DOM-script shortcuts
- reliable recovery from modals, frames, downloads, SPAs, auth, and shared-browser reuse

## Benchmark Surfaces

- `AGENTYC_HEADLESS=1 cargo test -p agentyc-tests --test benchmark -- --nocapture`
  for cold-start, tools/list latency, per-call overhead, and sustained throughput
- `AGENTYC_HEADLESS=1 cargo test --workspace` for representative browser flows such as forms,
  dialogs, SPAs, iframes, auth, and infinite scroll (browser tests need Chrome)

## Core Workflow

1. **Read first** with `browser_get_state(mode="min")`.
2. **Resolve stable refs** and act by `ref` whenever possible.
3. **Verify immediately** with `since_hash`, a focused read, or an explicit wait tool.

Default escalation:

1. `browser_get_state(mode="min")`
2. `browser_get_state(mode="full")` if the target is missing
3. `browser_list_frames()` if iframe ownership is unclear
4. `browser_find_elements(...)` or `browser_search_page(...)` when you know the DOM/text shape
5. `browser_get_html(...)`, `browser_get_frame_html(...)`, or `browser_evaluate(...)` for a specific DOM question
6. `browser_screenshot()` only for genuine visual confirmation

## Tool-Selection Rules

- Prefer a **dedicated tool** before `browser_evaluate(...)`.
- Reuse `since_hash` and `mode="focus"` / `mode="min"` before escalating to broad reads.
- Prefer **specific waits** (`browser_wait_for_response`, `browser_wait_for_element`, `browser_wait_for_stable_dom`) before raw sleeps.
- Prefer **deterministic extraction** (`browser_extract_content`) before screenshots or free-form DOM scraping.
- Prefer **storage/cookie tools** before custom JavaScript for auth or workspace state.
- Prefer **network inspection tools** before guessing why an action "did nothing."
- Only use `browser_evaluate(...)` for questions that the public browser tools cannot answer deterministically.

Use the full tool chooser in `references/tool-playbook.md` when selecting among the 66 public browser tools.

## Shared Browser Rules

Agentyc supports **same browser process/profile reuse**, not safe co-ownership of one live tab.

- Use `agentyc browser --detach` to start a reusable browser once.
- Use `agentyc mcp --reuse-local-browser` or `AGENTYC_REUSE_LOCAL_BROWSER=1` so another coding agent can attach without copying a CDP URL around.
- Use `--cdp-url` when you want the most explicit attach path.
- Each attached runtime receives its own collaboration tab or window. Do not have multiple agents intentionally operate the same tab.

## Human-Like Automation Rules

- Stay on the visible UI path first: click, hover, type, select, upload, drag, and keypress before
  DOM mutation or custom JS.
- Respect browser causality: after an action, wait on the specific DOM, navigation, or network
  signal that action should trigger.
- Handle popups, dialogs, downloads, auth, and iframe ownership explicitly rather than assuming the
  main page kept control.
- If state becomes stale or the task is interrupted, re-read current state and continue from fresh
  evidence instead of replaying the whole plan blindly.

## Anti-Fake-Win Rules

- Do not claim success because a click returned, a selector existed, or the URL changed without
  confirming the user-visible outcome.
- Do not use screenshot-only proof when state, HTML, network, console, or downloads provide a more
  deterministic answer.
- Do not hide failures behind blind retries; inspect browser evidence first.
- Do not replace stable tool flows with brittle site-specific JS when a dedicated browser tool fits.

## Composition Rule

- use `breakthrough-autoresearch` when the task is benchmark chasing or the right browser strategy
  is still unknown
- use `cdp-browser-engineer` when the issue is about BrowserSession, target/session plumbing, DOM
  serialization, network interception, or watchdog behavior
- use `dev-contextro-mcp` when you need low-token codebase or runtime discovery before touching the
  browser
- use `llm-provider-engineer` when browser-task quality is limited by model routing, structured
  output, or token accounting

## Output Format

Return:
1. objective and success signal
2. minimal read plan
3. chosen tools and why
4. browser evidence used for verification
5. result or blocker
6. next action or stop reason

## Examples

Example 1: Form submission with explicit verification.
User says: "Fill out this settings form, upload the policy PDF, and confirm the save worked."
Actions:
- read with `browser_get_state(mode="min")`
- use `browser_click`, `browser_type`, `browser_select_option`, and `browser_upload_file`
- verify with `browser_wait_for_element(...)` or `browser_wait_for_response(...)`
Result: the form flow is driven by stable refs and validated with explicit browser evidence

Example 2: Debugging a failing checkout.
User says: "The checkout button does nothing. Find the real failure."
Actions:
- read the page state first
- inspect `browser_get_console_logs` and `browser_get_network_log`
- use `browser_wait_for_request` / `browser_wait_for_response` around the click
- inspect one request with `browser_inspect_network_entry` and bundle evidence with `browser_export_debug_bundle`
Result: the failure is explained from browser evidence instead of guesswork

Example 3: Shared browser across projects.
User says: "Use the browser that another coding agent already opened and keep auth."
Actions:
- attach with `agentyc mcp --reuse-local-browser` or `--cdp-url`
- confirm the owned tab with `browser_list_tabs`
- work from that runtime's tab while reusing the shared profile's cookies and storage
Result: agents collaborate through one browser profile without fighting over a single tab

## Troubleshooting

- If a `ref` becomes stale, re-read state and resolve a fresh `ref`.
- If an action appears to do nothing, inspect console and network before retrying blindly.
- If content may live in an iframe, use `browser_list_frames` and `browser_get_frame_html` explicitly.
- If you need browser state, prefer `browser_get_storage`, `browser_get_cookies`, `browser_save_state`, or `browser_load_state` before custom JS.
- If a task needs many browser tools, narrate intent briefly and keep the loop: read -> ref -> act -> verify.

## References

- `references/tool-playbook.md`
- `references/eval-rubric.md`
- `evals/cases.yaml`
