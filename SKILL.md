# agentyc MCP - Skills Guide

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

---

## Escalation Ladder When Something Is Missing

1. `browser_get_state(mode="min")`
2. `browser_get_state(mode="full")` if the target is missing
3. `browser_list_frames()` when the target may live inside an iframe or OOPIF
4. `browser_find_elements(selector="...")` when you know the DOM shape
5. `browser_search_page(pattern="...")` when the page is long and you need a text hit quickly
6. `browser_get_html(selector="...")`, `browser_get_frame_html(frame_id="...")`, or `browser_evaluate(...)` only for a specific DOM question
7. `browser_screenshot()` only when you truly need visual confirmation

After `browser_hover`, `browser_press_key("Tab")`, or any action that changes focus or visibility, re-read state.

---

## Tool Selection Playbook

- **Read the page** → `browser_get_state`, `browser_find_elements`, `browser_search_page`, `browser_get_html`
- **Work inside frames** → `browser_list_frames`, `browser_get_frame_html`
- **Act on controls** → `browser_click`, `browser_type`, `browser_select_option`, `browser_upload_file`, `browser_press_key`
- **Wait for change** → `since_hash`, `browser_wait_for_element`, `browser_wait_for_request`, `browser_wait_for_response`, `browser_wait_for_stable_dom`
- **Inspect browser state** → `browser_get_storage`, `browser_get_cookies`, `browser_get_focused_element`
- **Control tabs and sessions** → `browser_new_tab`, `browser_list_tabs`, `browser_switch_tab`, `browser_list_sessions`

Only reach for `browser_evaluate(...)` when no dedicated tool already covers the job.

---

## Handle Errors As Control Flow

All tool errors return `isError=true` with a structured message. **Branch immediately** instead of retrying the same call blindly.

Common error codes and recoveries:

| Error text | Recovery |
|---|---|
| `No node with given id` / stale ref | Call `browser_get_state()` for fresh refs |
| `No browser connected` | Will auto-launch on next `browser_navigate` |
| `No box model for element` | Element off-screen or in Shadow DOM — use coordinates or `browser_evaluate` |
| `Timeout waiting for URL match` | Page took too long; check with `browser_evaluate("location.href")` |
| `blocked: host ... not in AGENTYC_ALLOWED_DOMAINS` | Domain not whitelisted |

Use the error message to determine the next safe move. Never ignore `isError=true`.

---

## Workflow Patterns

### Navigate and act

```
browser_navigate(url="https://example.com")
# navigate returns: "Navigated to: https://example.com | \"Page Title\""
state = browser_get_state(mode="min")
browser_click(ref="e42")
browser_get_state(mode="min", since_hash=state["state_hash"])
```

### Type into a field

```
state = browser_get_state(mode="min")
browser_click(ref="e17")
browser_type(text="hello", ref="e17")
```

For React/Vue controlled inputs where `browser_type` sets value but the framework doesn't pick it up, use:

```
browser_evaluate(code="""
(function() {
    var el = document.querySelector("input[name=q]");
    var d = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value");
    d.set.call(el, "search text");
    el.dispatchEvent(new Event("input", {bubbles: true}));
    return el.value;
})()
""")
```

### Select from a dropdown

```
state = browser_get_state(mode="min")
browser_get_dropdown_options(ref="e8")
browser_select_option(text="Production", ref="e8")
```

### Upload a file

```
state = browser_get_state(mode="full")
browser_upload_file(path="/absolute/path/report.pdf", ref="e5")
browser_wait_for_element(text="report.pdf")
```

### Wait for dynamic content

Prefer `since_hash` polling over fixed sleeps:

```
state = browser_get_state(mode="min")
# ... trigger an action ...
browser_get_state(mode="min", since_hash=state["state_hash"])
```

Use:

- `browser_wait_for_request(url_substring="/api/submit")` when a specific API call should fire
- `browser_wait_for_response(url_substring="/api/submit", status=200)` when you need the matching response
- `browser_wait_for_network_idle()` after navigation or broad XHR-heavy actions
- `browser_wait_for_element(text="Success")` when you know the exact success text
- `browser_wait(seconds=1)` only as a last resort

### Inspect frames

```
frames = browser_list_frames()
browser_get_frame_html(frame_id="...")
```

### Inspect or modify storage

```
browser_get_storage(origin="https://app.example.com")
browser_set_storage(
    origin="https://app.example.com",
    storage_type="localStorage",
    key="workspace",
    value="release-train",
)
browser_clear_storage(origin="https://app.example.com", storage_type="sessionStorage")
```

### Work with multiple tabs

```
browser_new_tab(url="https://example.com")
browser_list_tabs()
browser_switch_tab(tab_id="...")
browser_close_tab(tab_id="...")
```

After closing the active tab, always re-read state:

```
browser_close_tab(tab_id="...")
browser_get_state(mode="min")
```

### Extract structured data

```
browser_extract_content(query="table rows")
browser_extract_content(query="all links", extract_links=True)
browser_extract_content(query="form fields")
```

Supported routes: **links**, **images**, **tables**, **lists**, **form-fields**, **key-value/definitions**.

### Persist and restore authentication

```
browser_save_state(path="~/.agentyc/auth/myapp.json")
browser_load_state(path="~/.agentyc/auth/myapp.json")
browser_navigate(url="https://myapp.example.com/dashboard")
```

### Long-page search

```
browser_search_page(pattern="Terms of Service")
browser_scroll_to_text(text="Terms of Service")
browser_get_state(mode="min")
```

### Work with dialogs

```
browser_click(ref="e42")
browser_handle_dialog(accept=True)
```

---

## State Modes and `since_hash`

| Mode | What you get |
|------|-------------|
| `auto` (default) | Smart mix based on page complexity |
| `full` | All interactive elements, no truncation |
| `min` | Compact ranked subset of interactive elements |
| `focus` | Single referenced element |

`browser_get_state` returns a `state_hash`. Pass it back as `since_hash`:

- `changed: false` → state is identical; skip re-processing
- `changed: true` → use the new refs and new `state_hash`

---

## Evaluating JavaScript

Use `browser_evaluate` when no dedicated tool covers the job:

```
browser_evaluate(code="(function(){ return document.querySelectorAll('.item').length; })()")
browser_evaluate(code="(function(){ return localStorage.getItem('token'); })()")
```

Wrap in an IIFE when returning a value.

---

## Common Mistakes

**Do not target actions by text.** Read state, resolve the `ref`, then act.

**Do not default to `mode="full"`.** Start with `min`, escalate only if needed.

**Do not keep stale refs after navigation or reload.** Re-read state.

**Do not use long sleeps.** Use `since_hash`, `browser_wait_for_element`, `browser_wait_for_request`, or `browser_wait_for_network_idle`.

**Do not keep acting after `isError=true`.** Read the error, recover with fresh state.

**Do not use `browser_navigate(new_tab=True)` when you need to act in the new tab immediately.** Use `browser_new_tab`.

---

## Quick Reference

```
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
browser_get_storage(origin="...")
browser_set_storage(origin="...", storage_type="localStorage", key="...", value="...")
browser_clear_storage(origin="...", storage_type="sessionStorage")
browser_get_cookies()
browser_set_cookies(cookies=[{"name": "session", "value": "..."}])
browser_clear_cookies(name="session")

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
browser_wait_for_tab(url_substring="...")
browser_close_all()

# Interact
browser_click(ref="e42")
browser_click(ref="e42", wait_for_url_substring="/dashboard")
browser_right_click(ref="e42")
browser_double_click(ref="e42")
browser_hover(ref="e42")
browser_drag_to(source_ref="e5", target_ref="e9")
browser_type(text="...", ref="e17")
browser_fill_form(fields=[{"ref": "e1", "text": "..."}, {"ref": "e2", "option_text": "..."}])
browser_press_key(key="Enter")
browser_press_key(key="Control+a")
browser_scroll(direction="down", pages=2)
browser_scroll_to_text(text="...")
browser_select_option(text="...", ref="e8")
browser_get_dropdown_options(ref="e8")
browser_upload_file(path="/absolute/path/file.pdf", ref="e5")
browser_handle_dialog(accept=True)

# Extract and inspect
browser_extract_content(query="table rows")
browser_extract_content(query="all links", extract_links=True)
browser_find_elements(selector=".row")
browser_find_elements(selector="article a", attributes=["href"])
browser_search_page(pattern="Error", regex=True)
browser_evaluate(code="(function(){ return document.title; })()")

# Wait
browser_wait_for_network_idle()
browser_wait_for_request(url_substring="/api/...")
browser_wait_for_response(url_substring="/api/...", status=200)
browser_wait_for_stable_dom(timeout_seconds=5, quiet_ms=250)
browser_wait_for_element(text="Success")
browser_wait_for_element(text="Saving...", appear=False)
browser_wait_for_url(url_substring="/dashboard")
browser_wait(seconds=1)

# PDF and viewport
browser_save_as_pdf(file_name="report.pdf")
browser_set_viewport(width=1280, height=800)

# Emulation
browser_set_user_agent(user_agent="...")
browser_set_timezone(timezone_id="America/New_York")
browser_set_locale(locale="fr-FR")
browser_emulate_media(color_scheme="dark")
browser_set_geolocation(latitude=37.77, longitude=-122.41)
browser_grant_permissions(permissions=["geolocation"])
browser_set_extra_headers(headers={"Authorization": "Bearer ..."})

# State and sessions
browser_save_state(path="...")
browser_load_state(path="...")
browser_list_sessions()
browser_close_session(session_id="...")
browser_close_all()
```
