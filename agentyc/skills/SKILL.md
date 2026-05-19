# agentyc MCP — Skills Guide

`agentyc init` writes this guide to `agentyc-skill.md` so your coding agent can use the MCP server effectively.

agentyc exposes a Chrome browser as deterministic MCP tools. The goal is not "browse like a human." The goal is to **read state precisely, act by stable refs, and verify the result with the smallest reliable follow-up call**.

---

## Core Mental Model

Every interaction follows a **read → ref → act** loop:

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

---

## Escalation Ladder When Something Is Missing

Use this order instead of jumping straight to screenshots or brittle selectors:

1. `browser_get_state(mode="min")`
2. `browser_get_state(mode="full")` if the target is missing
3. `browser_find_elements(selector="...")` when you know the DOM shape and need a raw query
4. `browser_search_page(pattern="...")` when the page is long and you need a text hit quickly
5. `browser_get_html(selector="...")` or `browser_evaluate(...)` only for a specific DOM question
6. `browser_screenshot()` only when you truly need visual confirmation

After `browser_hover`, `browser_press_key("Tab")`, or any other action that changes focus/visibility, re-read state. Do not keep using the old element list blindly.

When working with a human in the loop, narrate intent before a likely pause:

- `Opening the release page and checking publish state.`
- `Waiting for validation to finish.`
- `Looking for the Documentation link.`

Keep these status lines short and concrete. They are progress narration, not chain-of-thought.

---

## Workflow Patterns

### Navigate and act

```python
state = browser_get_state(mode="min")
browser_navigate(url="https://example.com")
browser_get_state(mode="min")          # → captures refs, state_hash with a lighter payload
browser_click(ref="e42")     # submit button
browser_get_state(since_hash="<state_hash>")  # → only changed elements
```

Use `since_hash` after the action when the next step depends on a state change.

### Type into a field

```
browser_get_state(mode="min")          # find the input's ref
browser_click(ref="e17")     # focus it
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

`browser_upload_file` requires a file input `ref` or `index`. If the input is hard to spot, use `mode="full"` or `browser_find_elements("input[type=file]")`.

### Wait for dynamic content

Prefer `since_hash` polling over fixed sleeps:

```
state = browser_get_state(mode="min")
# … trigger an action …
browser_get_state(since_hash=state["state_hash"])
```

Use:

- `browser_wait_for_network_idle()` after navigation or XHR-heavy actions
- `browser_wait_for_element(text="Success")` when you know the success text or control
- `browser_wait(seconds=1)` only as a last resort

### Extract structured data

Use `browser_extract_content` for deterministic extraction:

```python
browser_extract_content(query="all product names and prices")
browser_extract_content(query="navigation links", extract_links=true)
browser_extract_content(
    query="user table",
    output_schema={
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "email": {"type": "string"}
            }
        }
    }
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
- you want accessible/form labels surfaced for controls with little or no visible text

### Work with multiple tabs

```python
browser_new_tab()                          # create tab and switch into it
browser_new_tab(url="https://example.com") # create tab, navigate, and switch
browser_list_tabs()
browser_switch_tab(tab_id="t2")
browser_close_tab(tab_id="t2")
```

Use `browser_new_tab` when you want to **work in that tab immediately**.

Use:

```python
browser_navigate(url="https://example.com", new_tab=true)
```

when you want to open a page in the background without switching away.

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
browser_get_network_log(include_headers=true)
browser_get_focused_element()
```

If an action "did nothing," check console errors first, then network errors, then re-read state.

---

## State Modes and `since_hash`

`browser_get_state` accepts a `mode` parameter:

| Mode | What you get |
|------|-------------|
| `auto` (default) | Smart mix of full/compact based on page complexity |
| `full` | All interactive elements, no truncation |
| `min` | Compact ranked subset of interactive elements |
| `focus` | Single referenced element from `focus_ref` |

`browser_get_state` returns a `state_hash`. Pass it back as `since_hash`:

Default preference order for agents:

1. `mode="min"` for routine reads
2. `mode="focus"` when you already have a ref and only need one element
3. `mode="full"` only when `min` omitted something you actually need

---

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

Multiple agents can share one Chrome instance, each in its own isolated tab and browser context.

### Setup

```bash
cdp_url=$(agentyc browser --port 9222 --detach)

agentyc mcp --cdp-url "$cdp_url" --runtime-label "Agent-1"
agentyc mcp --cdp-url "$cdp_url" --runtime-label "Agent-2"
```

Each agent should immediately claim a tab:

```python
browser_new_tab()
browser_navigate(url="https://example.com")
```

Coordination rules:

- Each agent should work from its own tab
- Use `browser_list_tabs` to inspect the shared browser surface
- Do not call `browser_close_all` in a shared-browser session
- Default shared-browser focus policy preserves the human's focus

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

**Don't default to `browser_get_state(mode="full")`.** Start with `mode="min"` and escalate to `full` only when necessary.

**Don't go silent before a long operation.** Add a brief status line like `Waiting for upload to finish.` so humans do not mistake agent reasoning for browser slowness.

**Don't ignore console errors.** If an action silently fails, `browser_get_console_logs(level="error")` almost always shows why.

---

## Quick Reference

```python
# Read
browser_get_state()                          # auto mode
browser_get_state(mode="min")               # preferred default for lightweight reads
browser_get_state(since_hash="…")           # delta — changed=true/false
browser_get_state(mode="focus", focus_ref="e42")  # only the referenced element
browser_get_state(mode="full")              # full tree when min/focus is insufficient
browser_screenshot()
browser_get_html()
browser_screenshot()

# Navigate and tabs
browser_navigate(url="...")
browser_navigate(url="...", new_tab=true)
browser_new_tab()
browser_new_tab(url="...")
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
browser_select_option(text="...", ref="e8")
browser_get_dropdown_options(ref="e8")
browser_upload_file(path="/absolute/path/file.pdf", ref="e5")

# Extract and inspect
browser_extract_content(query="...")
browser_extract_content(query="...", extract_links=true)
browser_extract_content(query="...", output_schema={...})
browser_find_elements(selector=".row")
browser_search_page(pattern="Error", regex=true)

# Wait and debug
browser_wait_for_network_idle()
browser_wait_for_element(text="Success")
browser_wait(seconds=1)
browser_get_console_logs(level="error")
browser_get_network_log(status_filter="errors")
browser_get_network_log(type_filter="Fetch")

# State and sessions
browser_save_state(path="...")
browser_load_state(path="...")
browser_list_sessions()
browser_close_session(session_id="...")
```
