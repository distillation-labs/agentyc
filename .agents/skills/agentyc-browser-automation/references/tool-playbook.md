# Agentyc Tool Playbook

Use this as a routing table, not as a checklist. Pick the narrowest deterministic tool that can answer the current question.

| Goal | First choice | Escalate when |
|---|---|---|
| Discover controls | `browser_get_state(mode="min")` | Use `full` when the target is omitted; `focus` for one known ref |
| Read text | `browser_search_page` or `browser_get_html` | Use extraction routes for structured content |
| Click/type/select | `browser_click`, `browser_type`, `browser_select_option` | Re-read state after navigation, focus, or DOM changes |
| Submit and verify | Action + `browser_wait_for_response`/`browser_wait_for_element` | Inspect network or console if no result appears |
| Search long pages | `browser_search_page` | Use `browser_scroll_to_text` for visual context |
| Extract data | `browser_extract_content` | Use `browser_get_html` only for unsupported structures |
| Inspect a frame | `browser_list_frames` | Then `browser_get_frame_html(frame_id=...)` |
| Persist auth | `browser_save_state` / `browser_load_state` | Use cookies/storage tools for a single value |
| Diagnose failure | `browser_get_console_logs` + `browser_get_network_log` | Inspect one request and export a debug bundle |
| Manage tabs | `browser_list_tabs` / `browser_switch_tab` | Wait for a tab before switching if a click opens one |
| Wait for change | `since_hash` or a specific wait tool | Use a short fixed wait only when no signal exists |
| Run arbitrary JS | `browser_evaluate` | Only when no dedicated tool expresses the operation |

## High-value sequences

### Search, click, verify

```text
browser_get_state(mode="min")
# resolve the target ref
browser_click(ref="e42")
browser_wait_for_element(text="Expected result")
browser_get_state(mode="min")
```

### API-backed submit

```text
browser_get_state(mode="min")
browser_wait_for_request(url_substring="/api/save")
# perform the click/type action
browser_wait_for_response(url_substring="/api/save", status=200)
browser_wait_for_element(text="Saved")
```

Start the request/response wait immediately before the action that triggers it when the client supports concurrent requests; otherwise use `browser_wait_for_network_idle` after the action.

### Stale reference recovery

```text
# action returns isError=true with a stale-ref or missing-node message
browser_get_state(mode="min")
# resolve a fresh ref; never replay the stale ref blindly
```

### Iframe recovery

```text
browser_list_frames()
browser_get_frame_html(frame_id="...")
# use the frame-specific evidence to choose the next action
```

### Dense-page polling

```text
first = browser_get_state(mode="min")
# perform an action
next = browser_get_state(mode="min", since_hash=first.state_hash)
# changed=false means no new action is required
```

### Multi-tab handoff

```text
browser_new_tab(url="https://example.com")
browser_list_tabs()
browser_switch_tab(tab_id="...")
# act only after confirming the active tab's URL/title
```

## CLI and REPL parity

Use the CLI for one-shot commands:

```bash
agentyc run --headless=true navigate https://example.com
agentyc run --headless=true evaluate 'document.title'
```

Use REPL when several commands should share one runtime:

```text
agentyc repl --headless=true
navigate https://example.com
evaluate document.title
state
exit
```

The CLI opens and closes a runtime per invocation. The REPL keeps one runtime alive until `exit`; MCP is the preferred long-lived interface for coding agents.
