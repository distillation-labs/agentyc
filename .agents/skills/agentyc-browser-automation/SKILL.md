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
- Prefer **specific waits** (`browser_wait_for_response`, `browser_wait_for_element`, `browser_wait_for_stable_dom`) before raw sleeps.
- Prefer **deterministic extraction** (`browser_extract_content`) before screenshots or free-form DOM scraping.
- Prefer **storage/cookie tools** before custom JavaScript for auth or workspace state.
- Prefer **network inspection tools** before guessing why an action "did nothing."

Use the full tool chooser in `references/tool-playbook.md` when selecting among the 66 public browser tools.

## Shared Browser Rules

Agentyc supports **same browser process/profile reuse**, not safe co-ownership of one live tab.

- Use `agentyc browser --detach` to start a reusable browser once.
- Use `agentyc mcp --reuse-local-browser` or `AGENTYC_REUSE_LOCAL_BROWSER=1` so another coding agent can attach without copying a CDP URL around.
- Use `--cdp-url` when you want the most explicit attach path.
- Each attached runtime receives its own collaboration tab or window. Do not have multiple agents intentionally operate the same tab.

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
