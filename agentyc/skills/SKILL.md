# agentyc MCP - Skills Guide

`agentyc init` writes this guide to `agentyc-skill.md` so your coding agent can use the MCP server effectively.

agentyc exposes a Chrome browser as deterministic MCP tools. The goal is not "browse like a human." The goal is to **read state precisely, act by stable refs, and verify the result with the smallest reliable follow-up call**.

---

## Core Mental Model

Every interaction follows a **read -> ref -> act** loop:

1. Call `browser_get_state` to get interactive elements and stable refs like `e123`.
2. Act by `ref` whenever possible.
3. Re-read with `since_hash` or a focused follow-up tool to verify the result.

Refs are stable within a page load. They become stale after navigation or a full reload.

Default read preference:

1. `browser_get_state(mode="min")`
2. `browser_get_state(mode="focus", focus_ref="...")`
3. `browser_get_state(mode="full")` only when `min` omitted something you really need

When a human is waiting on the run, narrate intent before a likely pause:

- `Opening the release page and checking publish state.`
- `Waiting for validation to finish.`
- `Uploading the fixture and checking the status message.`

Keep those lines short and concrete. They are progress narration, not chain-of-thought.

If the live HUD is enabled, prefer `browser_set_intent(intent="...")` for major step changes so the operator sees a stable current-status label without exposing raw reasoning.

---

## Escalation Ladder When Something Is Missing

Use this order instead of jumping straight to screenshots or brittle selectors:

1. `browser_get_state(mode="min")`
2. `browser_get_state(mode="full")` if the target is missing
3. `browser_list_frames()` when the target may live inside an iframe or OOPIF
4. `browser_find_elements(selector="...")` when you know the DOM shape and need a raw query
5. `browser_search_page(pattern="...")` when the page is long and you need a text hit quickly
6. `browser_get_html(selector="...")`, `browser_get_frame_html(frame_id="...")`, or `browser_evaluate(...)` only for a specific DOM question
7. `browser_screenshot()` only when you truly need visual confirmation

After `browser_hover`, `browser_press_key("Tab")`, or any other action that changes focus or visibility, re-read state. Do not keep using the old element list blindly.

---

## Handle Errors As Control Flow

If a tool call returns MCP `isError=true` or text like `Error [stale_ref]: ...`, **branch immediately** instead of retrying the same call blindly.

Common recoveries:

- `stale_ref` -> call `browser_get_state(...)` again and resolve a fresh `ref`
- element missing in `min` mode -> escalate to `full`, then `browser_find_elements(...)`
- blocked or modal-like failure -> dismiss the dialog or modal, then re-read state
- `browser_handle_dialog` returns `Dialog already auto-handled by runtime: ...` -> treat that as a success-shaped acknowledgment that the popup watchdog already accepted or dismissed the dialog
- post-tab-close confusion -> call `browser_list_tabs()` or `browser_get_state(mode="min")` to confirm the surviving active tab before the next action

Use the hint in the error payload. It is there to tell you the next safe move.

---

## Workflow Patterns

### Navigate and act

```python
state = browser_get_state(mode="min")
browser_navigate(url="https://example.com")
state = browser_get_state(mode="min")
browser_click(ref="e42")
browser_get_state(mode="min", since_hash=state["state_hash"])
```

Use `since_hash` after the action when the next step depends on a state change.

### Type into a field

```python
state = browser_get_state(mode="min")
browser_click(ref="e17")
browser_type(text="hello", ref="e17")
```

If the page uses keyboard navigation heavily, follow with:

```python
browser_press_key(key="Tab")
browser_get_focused_element()
```

### Select from a dropdown

Inspect first, then select by visible text:

```python
state = browser_get_state(mode="min")
browser_get_dropdown_options(ref="e8")
browser_select_option(text="Production", ref="e8")
```

`browser_get_dropdown_options` returns **human-readable text**, not JSON. Use the exact visible option text with `browser_select_option`.

### Upload a file

```python
state = browser_get_state(mode="full")
browser_upload_file(path="/absolute/path/report.pdf", ref="e5")
browser_wait_for_element(text="report.pdf")
```

`browser_upload_file` requires a file input `ref` or `index`. If the input is hard to spot, use `mode="full"` or `browser_find_elements(selector="input[type=file]")`.

### Wait for dynamic content

Prefer `since_hash` polling over fixed sleeps when you only need to know whether **anything** changed:

```python
state = browser_get_state(mode="min")
# ... trigger an action ...
browser_get_state(mode="min", since_hash=state["state_hash"])
```

Use:

- `browser_wait_for_request(...)` when one specific API call should fire
- `browser_wait_for_response(...)` when you need the matching response or HTTP failure
- `browser_wait_for_network_idle()` after navigation or broad XHR-heavy actions
- `browser_wait_for_element(text="Success")` when you know the exact success text or control
- `browser_wait(seconds=1)` only as a last resort

### Inspect frames directly

When an iframe or OOPIF matters, inspect the frame surface explicitly instead of guessing from the merged DOM snapshot:

```python
frames = browser_list_frames()
browser_get_frame_html(frame_id="...")
```

Use this when:

- a page embeds important content inside one iframe and the parent DOM is not enough
- you need to confirm which frame URL or frame id owns a nested document
- an element appears to live in a different target and you want the raw frame HTML before acting

### Inspect or modify storage

Prefer dedicated storage tools over ad hoc `browser_evaluate(...)` when the task is specifically about browser state:

```python
browser_get_storage(origin="https://app.example.com")
browser_set_storage(
    origin="https://app.example.com",
    storage_type="localStorage",
    key="workspace",
    value="release-train",
)
browser_clear_storage(origin="https://app.example.com", storage_type="sessionStorage")
```

Use these when you need to verify login state, feature flags, workspace selection, or any other persisted client-side value without crafting custom JavaScript first.

`browser_wait_for_element(text="...")` matches **visible page text**, not just interactive controls. Use it for:

- toast banners
- upload status text and filenames
- validation messages
- non-interactive success or error copy

Examples:

```python
browser_upload_file(path="/absolute/path/report.pdf", ref="e5")
browser_wait_for_element(text="report.pdf")
browser_wait_for_element(text="Uploading...", appear=False)
```

### Verify a headless release-readiness flow

Use the observability tools as a tight bundle when you need to prove a browser workflow is ready for release:

```python
browser_new_tab(url="https://app.example.com/release-readiness")
browser_set_viewport(width=1280, height=800)
browser_wait_for_stable_dom(timeout_seconds=5, quiet_ms=250)

state = browser_get_state(mode="full")
browser_get_attribute(name="href", ref="e_download")

browser_start_trace()
browser_click(ref="e_download")
browser_get_downloads()
browser_stop_trace()

browser_get_console_logs(max_entries=20)
browser_get_network_log(max_entries=20)
browser_clear_logs(console=True, network=True)
browser_save_as_pdf(file_name="release-readiness.pdf")
```

Use this pattern for release checklists, admin dashboards, QA signoff pages, and any headless flow where you need a download, a PDF artifact, trace data, and a clean console/network snapshot in one pass.

### Work with dialogs

The runtime auto-handles blocking JavaScript dialogs immediately so the agent does not stall.

```python
browser_click(ref="e42")
browser_handle_dialog(accept=True)
```

If the dialog already got handled by the popup watchdog, `browser_handle_dialog` may return:

```text
Dialog already auto-handled by runtime: [confirm] Delete this branch? (accepted automatically)
```

Treat that as a confirmation of what happened, not as a failure that needs a retry.

### Live HUD and reporting

- `BrowserProfile(demo_mode=True)` enables the in-browser HUD.
- `agentyc mcp --hud-overlay` enables the transparent desktop HUD.
- Use `browser_set_intent` for short operator-facing status updates such as `Reviewing failed checkout step`.
- The browser HUD's `REPORT` menu copies sanitized context and opens the repo's bug, feature, or private security destination. Do not paste secrets, cookies, raw headers, or full auth URLs into those reports.

### Extract structured data

Use `browser_extract_content` for deterministic extraction:

```python
browser_extract_content(query="all product names and prices")
browser_extract_content(query="navigation links", extract_links=True)
browser_extract_content(
    query="user table",
    output_schema={
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "email": {"type": "string"},
            },
        },
    },
)
```

Supported route families include **links**, **link-collections**, **images**, **tables**, **lists**, **form-fields**, and **key-value panels**.

### Use `browser_find_elements` when the DOM shape matters

```python
browser_find_elements(selector="form input, form select, form button")
browser_find_elements(selector="article.card a", attributes=["href"])
```

Use this when:

- `mode="min"` omitted an element you know exists
- you need a CSS-selector-based audit
- you want accessible or form labels surfaced for controls with little or no visible text

### Work with multiple tabs

```python
browser_new_tab()
browser_new_tab(url="https://example.com")
browser_list_tabs()
browser_switch_tab(tab_id="t2")
browser_close_tab(tab_id="t2")
```

Use `browser_new_tab` when you want to **work in that tab immediately**.

Use:

```python
browser_navigate(url="https://example.com", new_tab=True)
```

when you want to open a page in the background without switching away.

Typical detail-tab workflow:

```python
browser_click(ref="e21")
tabs = browser_list_tabs()
browser_switch_tab(tab_id="t2")
browser_get_state(mode="min")
```

After closing the active detail tab, do not assume the old refs are still safe:

```python
browser_close_tab(tab_id="t2")
browser_get_state(mode="min")
```

Treat the post-close state as a fresh surface and resolve refs again before acting.

### Work on long docs and search-heavy pages

Use the page search and text tools together instead of brute-force scrolling:

```python
state = browser_get_state(mode="min")
browser_type(text="webhook retries", ref="e1")
browser_wait_for_element(text="Webhook retries")
browser_scroll_to_text(text="Webhook retries")
browser_get_state(mode="min")
```

Good defaults:

- use `browser_search_page(pattern="...")` when you need a quick text hit
- use `browser_scroll_to_text(text="...")` when you want the viewport moved to a known section
- use `browser_find_elements(...)` if the page is long and you know the DOM pattern better than the visible text
- re-read state after deep scroll before acting

High-value global controls like search inputs are supposed to stay discoverable in compact state, but still escalate to `full` or `browser_find_elements(...)` if `min` omitted the one control you need.

### Persist and restore authentication

```python
browser_save_state(path="~/.agentyc/auth/myapp.json")
browser_load_state(path="~/.agentyc/auth/myapp.json")
browser_navigate(url="https://myapp.example.com/dashboard")
```

This persists cookies, localStorage, and sessionStorage so the agent can skip repeated login flows.

### Debug with console, network, and focused state

```python
browser_get_console_logs(level="error")
browser_get_network_log(status_filter="errors")
browser_get_network_log(type_filter="XHR")
browser_get_network_log(include_headers=True)
browser_wait_for_request(url_substring="/api/submit", method="POST")
browser_wait_for_response(url_substring="/api/submit", status=200)
browser_inspect_network_entry(url_substring="/api/submit", method="POST", include_headers=True)
browser_replay_request(request_id="...")
browser_add_network_mock(url_substring="/api/submit", body='{"ok":true}')
browser_list_network_mocks()
browser_set_network_conditions(offline=True)
browser_get_network_conditions()
browser_export_debug_bundle()
browser_get_focused_element()
```

If an action "did nothing," check console errors first, then network errors, then re-read state.

Network triage pattern:

1. `browser_wait_for_request(...)` or `browser_wait_for_response(...)`
2. `browser_inspect_network_entry(...)` to inspect bodies or headers
3. `browser_replay_request(...)` if you need to reissue the exact call with narrow overrides
4. `browser_add_network_mock(...)` or `browser_set_network_conditions(...)` only when you intentionally want to control the page's network behavior

For a compact one-shot artifact to hand back to a parent agent or human, use:

```python
browser_export_debug_bundle(
    state_mode="min",
    include_screenshot=True,
    include_headers=False,
    console_max_entries=20,
    network_max_entries=20,
)
```

`browser_export_debug_bundle` returns:

- the nested `browser_get_state` payload
- recent console logs
- recent network log entries
- pending requests
- trace summary
- optional scoped HTML
- optional screenshot image

Use it when you need a single debugging bundle instead of several separate tool calls.

---

## State Modes and `since_hash`

`browser_get_state` accepts a `mode` parameter:

| Mode | What you get |
|------|-------------|
| `auto` (default) | Smart mix of full or compact based on page complexity |
| `full` | All interactive elements, no truncation |
| `min` | Compact ranked subset of interactive elements |
| `focus` | Single referenced element from `focus_ref` |

`browser_get_state` returns a `state_hash`. Pass it back as `since_hash`:

- `changed: false` -> state is identical; no need to re-process the page
- `changed: true` -> state changed; use the new refs and new `state_hash`

Polling pattern:

```python
state = browser_get_state(mode="min")
state_hash = state["state_hash"]
while True:
    delta = browser_get_state(mode="min", since_hash=state_hash)
    if delta["changed"]:
        state_hash = delta["state_hash"]
        break
    browser_wait(seconds=1)
```

Do **not** compare `since_hash` against a pre-action state when the action intentionally changes the page. Re-read once after the intentional change, then start your unchanged polling from the fresh hash.

---

## Parallel Agents with Shared Browser

Multiple agents can share one Chrome instance. Each attached runtime gets a dedicated owned tab in the same shared browser profile.

### Setup

```bash
cdp_url=$(agentyc browser --port 9222 --detach)

agentyc mcp --cdp-url "$cdp_url" --runtime-label "Agent-1"
agentyc mcp --cdp-url "$cdp_url" --runtime-label "Agent-2"
```

Each attached runtime already receives its own collaboration tab automatically.

Open another tab only when one runtime needs more than its default owned tab:

```python
browser_new_tab()
browser_navigate(url="https://example.com")
```

Coordination rules:

- each agent should work from its own tab
- auth, cookies, localStorage, and sessionStorage are shared across attached runtimes because they stay in the same browser profile
- use `browser_list_tabs` to inspect the shared browser surface
- use display titles, URLs, and runtime metadata to confirm you are in the right tab before acting
- do not call `browser_close_all` in a shared-browser session
- default shared-browser focus policy preserves the human's focus

---

## Evaluating JavaScript

Use `browser_evaluate` only when a dedicated tool does not already cover the task:

```python
browser_evaluate(code="(function(){ return document.querySelectorAll('.item').length; })()")
browser_evaluate(code="(function(){ return localStorage.getItem('token'); })()")
```

Wrap the code in an IIFE when returning a value. Keep the script focused and narrow. Prefer dedicated MCP tools for actions, waits, uploads, tabs, extraction, and logs.

---

## Common Mistakes

**Do not target actions by text.** Read state, resolve the `ref`, then act.

**Do not default to `mode="full"`.** Start with `min`, then escalate only if needed.

**Do not keep stale refs after navigation or reload.** Re-read state.

**Do not treat `browser_get_dropdown_options` as JSON.** It is a human-readable options list.

**Do not use screenshots for structured extraction.** Use `browser_extract_content`.

**Do not guess with long sleeps.** Use `since_hash`, `browser_wait_for_element`, `browser_wait_for_request`, `browser_wait_for_response`, or `browser_wait_for_network_idle`.

**Do not use `browser_navigate(new_tab=True)` when you need to act in the new tab immediately.** Use `browser_new_tab`.

**Do not use `browser_wait_for_network_idle` when one specific API call matters.** Use `browser_wait_for_request` or `browser_wait_for_response`.

**Do not keep acting after an MCP tool error.** Read the error code and hint, then recover with fresh state or tabs.

**Do not ignore console and network errors.** They are the fastest way to explain silent failures.

---

## Quick Reference

```python
# Read
browser_get_state()
browser_get_state(mode="min")
browser_get_state(mode="full")
browser_get_state(mode="focus", focus_ref="e42")
browser_get_state(mode="min", since_hash="...")
browser_list_frames()
browser_get_frame_html(frame_id="...")
browser_get_focused_element()
browser_get_attribute(name="href", ref="e42")
browser_get_html()
browser_screenshot()
browser_get_downloads()
browser_get_storage(origin="...")
browser_set_storage(origin="...", storage_type="localStorage", key="...", value="...")
browser_clear_storage(origin="...", storage_type="sessionStorage")

# Navigate and tabs
browser_navigate(url="...")
browser_navigate(url="...", new_tab=True)
browser_new_tab()
browser_new_tab(url="...")
browser_set_viewport(width=1280, height=800)
browser_go_back()
browser_go_forward()
browser_refresh()
browser_list_tabs()
browser_switch_tab(tab_id="...")
browser_close_tab(tab_id="...")

# Interact
browser_click(ref="e42")
browser_hover(ref="e42")
browser_type(text="...", ref="e17")
browser_press_key(key="Enter")
browser_scroll(direction="down", pages=2)
browser_scroll_to_text(text="Webhook retries")
browser_select_option(text="...", ref="e8")
browser_get_dropdown_options(ref="e8")
browser_upload_file(path="/absolute/path/file.pdf", ref="e5")
browser_handle_dialog(accept=True)

# Extract and inspect
browser_extract_content(query="...")
browser_extract_content(query="...", extract_links=True)
browser_extract_content(query="...", output_schema={...})
browser_find_elements(selector=".row")
browser_search_page(pattern="Error", regex=True)

# Wait and debug
browser_wait_for_network_idle()
browser_wait_for_request(url_substring="/api/...")
browser_wait_for_response(url_substring="/api/...", status=200)
browser_wait_for_stable_dom(timeout_seconds=5, quiet_ms=250)
browser_wait_for_element(text="Success")
browser_wait_for_element(text="Saving...", appear=False)
browser_wait(seconds=1)
browser_get_console_logs(level="error")
browser_get_network_log(status_filter="errors")
browser_get_network_log(type_filter="Fetch")
browser_inspect_network_entry(url_substring="/api/...", method="POST")
browser_replay_request(request_id="...")
browser_add_network_mock(url_substring="/api/...", body="stubbed")
browser_remove_network_mock(mock_id="...")
browser_list_network_mocks()
browser_set_network_conditions(offline=True)
browser_get_network_conditions()
browser_clear_logs(console=True, network=True)
browser_start_trace()
browser_stop_trace()
browser_export_debug_bundle()
browser_save_as_pdf(file_name="report.pdf")

# State and sessions
browser_save_state(path="...")
browser_load_state(path="...")
browser_list_sessions()
browser_close_session(session_id="...")
```
