# agentyc MCP — Skills Guide

agentyc exposes a Chrome browser as a set of MCP tools. This guide teaches you the patterns that unlock its full capability.

---

## Core Mental Model

Every interaction follows a **read → ref → act** loop:

1. Call `browser_get_state` to get a snapshot of interactive elements, each with a stable `ref` like `e123`.
2. Target actions by `ref` — never by text, XPath, or CSS selector.
3. Call `browser_get_state` again with `since_hash` to get only what changed.

Refs are stable within a page load. They become invalid after navigation or a full reload.

---

## Workflow Patterns

### Navigate and act

```
browser_navigate(url="https://example.com")
browser_get_state()          # → captures refs, state_hash
browser_click(ref="e42")     # submit button
browser_get_state(since_hash="<state_hash>")  # → only changed elements
```

### Type into a field

```
browser_get_state()          # find the input's ref
browser_click(ref="e17")     # focus it
browser_type(text="hello", ref="e17")
```

### Wait for dynamic content

Prefer `since_hash` polling over `browser_wait`:

```
state = browser_get_state()
# … trigger an action …
browser_get_state(since_hash=state["state_hash"])
# Returns changed=true once new content appears; changed=false means no update yet
```

Use `browser_wait_for_network_idle` when a page loads resources after navigation. Use `browser_wait_for_element(text="Success")` to block until specific text appears.

### Extract structured data

Use `browser_extract_content` for deterministic data extraction — no screenshot needed:

```
browser_extract_content(query="all product names and prices")
browser_extract_content(query="navigation links", extract_links=true)
browser_extract_content(query="user table", output_schema={"type":"array","items":{"type":"object","properties":{"name":{"type":"string"},"email":{"type":"string"}}}})
```

Supported route families: **links**, **link-collections**, **images**, **tables**, **lists**, **form-fields**, **key-value panels**.

### Screenshot for visual verification

```
browser_screenshot()              # current viewport
browser_screenshot(full_page=true) # full-page scroll capture
```

Only use screenshots when you need visual confirmation. Prefer `browser_extract_content` for data.

### Debug with console and network logs

```
browser_get_console_logs(level="error")
browser_get_network_log(status_filter="errors")
browser_get_network_log(type_filter="XHR")
browser_get_network_log(include_headers=true)   # includes request and response headers
```

Use these when an action silently fails. `status_filter` supports values like `all`, `errors`, and `success`. `type_filter` accepts one request type per call, such as `XHR` or `Fetch`. Pass `include_headers=true` when you need to inspect auth headers, content types, or API tokens sent in requests.

### Persist and restore authentication

After logging in manually or via automation, save the session:

```
browser_save_state(path="~/.agentyc/auth/myapp.json")
```

Restore it at the start of a task:

```
browser_load_state(path="~/.agentyc/auth/myapp.json")
browser_navigate(url="https://myapp.example.com/dashboard")
```

This saves cookies, localStorage, and sessionStorage. Skip the login flow on every run.

### Work with multiple tabs

```
browser_new_tab()                         # create a blank tab and switch to it
browser_new_tab(url="https://...")        # create tab and navigate immediately
browser_list_tabs()                       # see open tabs and their tab_id
browser_switch_tab(tab_id="t2")          # focus a different tab
browser_navigate(url="...", new_tab=true) # open URL in a new tab (stays on current tab)
browser_close_tab(tab_id="t2")
```

Use `browser_new_tab` when you need to work in a fresh tab immediately — it creates the tab and switches focus in one call. Use `browser_navigate(new_tab=true)` when you want to open a URL in the background without switching away.

---

## State Modes

`browser_get_state` accepts a `mode` parameter:

| Mode | What you get |
|------|-------------|
| `auto` (default) | Smart mix of full/compact based on page complexity |
| `full` | All interactive elements, no truncation |
| `min` | Compact ranked subset of interactive elements |
| `focus` | Single referenced element from `focus_ref` |

Use `mode="min"` when you want a lighter-weight state read with the most relevant interactive elements. Use `mode="focus"` when you want just one specific element.

---

## Efficient State Polling with `since_hash`

`browser_get_state` returns a `state_hash`. Pass it as `since_hash` on the next call:

- `changed: false` — page state is identical, no new refs
- `changed: true` — new snapshot with updated elements

This avoids re-processing the full element tree on every call. Use it inside polling loops:

```python
result = browser_get_state()
hash_ = result["state_hash"]
while True:
    result = browser_get_state(since_hash=hash_)
    if result["changed"]:
        hash_ = result["state_hash"]
        # process new elements
        break
    browser_wait(seconds=1)
```

---

## Parallel Agents with Shared Browser

Multiple agents can share one Chrome instance, each in its own isolated tab with a separate cookie jar.

### Setup

```bash
# Start Chrome once
cdp_url=$(agentyc browser --port 9222 --detach)

# Start two agents in separate MCP server processes
agentyc mcp --cdp-url "$cdp_url" --runtime-label "Agent-1"
agentyc mcp --cdp-url "$cdp_url" --runtime-label "Agent-2"
```

Each agent gets its own tab, labeled `[Agent-1]` and `[Agent-2]` in the Chrome tab bar. Each tab has an isolated browser context (separate cookies, localStorage, sessionStorage) — agents can hold independent authenticated sessions on the same domain simultaneously.

### Per-agent tab setup

After attaching, each agent should call `browser_new_tab` to get its own isolated workspace:

```
browser_new_tab()           # creates a blank tab and switches focus to it
browser_navigate(url="…")   # now navigate within this agent's own tab
```

This is more reliable than `browser_navigate(new_tab=true)` for agents, because it creates the tab and switches focus atomically. Each agent's subsequent `browser_get_state` calls and refs are scoped to its own tab.

### Coordination rules

- Each agent sees only its own tab by default via `browser_get_state`.
- Use `browser_list_tabs` to see all tabs including those owned by other agents.
- Do not call `browser_close_all` in a shared-browser session — it closes all agents' tabs.
- The human's focus is preserved by default (`--shared-browser-focus-policy=preserve`).

---

## Evaluating JavaScript

Use `browser_evaluate` for operations not covered by a tool:

```
browser_evaluate(code="(function(){ return document.querySelectorAll('.item').length; })()")
browser_evaluate(code="(function(){ window.scrollTo(0, document.body.scrollHeight); return 'scrolled'; })()")
browser_evaluate(code="(function(){ return localStorage.getItem('token'); })()")
```

Wrap code in an IIFE when returning a value. Results are returned as JSON-compatible text. Errors surface as tool errors.

---

## Common Mistakes

**Don't use text to target elements.** `browser_click(text="Submit")` is fragile. Always get a state snapshot first and use the `ref`.

**Don't screenshot for data extraction.** Screenshots cost tokens and are imprecise. Use `browser_extract_content` for structured data.

**Don't assume refs persist across navigation.** After `browser_navigate` or a full page reload, call `browser_get_state` again to get fresh refs.

**Don't poll with fixed waits.** `browser_wait(seconds=3)` is a guess. Use `since_hash` polling or `browser_wait_for_element` instead.

**Don't ignore console errors.** If an action silently fails, `browser_get_console_logs(level="error")` almost always shows why.

---

## Quick Reference

```
# Read
browser_get_state()                          # full snapshot, returns state_hash + refs
browser_get_state(since_hash="…")           # delta — changed=true/false
browser_get_state(mode="min")               # compact ranked subset of interactive elements
browser_get_state(mode="focus", focus_ref="e42")  # only the referenced element
browser_screenshot()
browser_get_html()
browser_extract_content(query="…")

# Navigate
browser_navigate(url="…")
browser_navigate(url="…", new_tab=true)
browser_new_tab()                        # create blank tab + switch focus
browser_new_tab(url="…")                 # create tab + navigate + switch focus
browser_go_back() / browser_go_forward() / browser_refresh()

# Interact
browser_click(ref="e42")
browser_type(text="…", ref="e17")
browser_press_key(key="Enter")
browser_scroll(direction="down", pages=2)
browser_select_option(text="…", ref="e8")
browser_upload_file(path="/local/file.pdf", ref="e5")

# Wait
browser_wait_for_network_idle()
browser_wait_for_element(text="Success")
browser_wait(seconds=1)    # last resort

# Extract
browser_extract_content(query="…")
browser_extract_content(query="…", extract_links=true)
browser_extract_content(query="…", output_schema={…})
browser_find_elements(selector=".row")
browser_search_page(pattern="Error", regex=true)

# State
browser_save_state(path="…")
browser_load_state(path="…")

# Debug
browser_get_console_logs(level="error")
browser_get_network_log(status_filter="errors")
browser_get_network_log(type_filter="Fetch")

# Tabs and sessions
browser_list_tabs()
browser_switch_tab(tab_id="…")
browser_list_sessions()
browser_close_session(session_id="…")
```
