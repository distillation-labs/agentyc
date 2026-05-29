# Agentyc Browser Tool Playbook

This reference is the full chooser for the public browser MCP surface. For each tool:

- **Use when** tells you the primary job for the tool.
- **Example** gives a minimal invocation shape.
- Prefer the most specific tool before falling back to `browser_evaluate(...)`.

## Navigation And Page State

| Tool | Use when | Example |
|---|---|---|
| `browser_navigate` | Load a URL in the current tab, or open a background tab with `new_tab=true`. | `browser_navigate(url="https://example.com")` |
| `browser_go_back` | Use browser history back navigation. | `browser_go_back()` |
| `browser_go_forward` | Use browser history forward navigation. | `browser_go_forward()` |
| `browser_refresh` | Reload the active page. | `browser_refresh()` |
| `browser_wait` | Use only as a last-resort fixed delay. | `browser_wait(seconds=1)` |
| `browser_wait_for_network_idle` | Wait until the page settles after broad XHR-heavy work. | `browser_wait_for_network_idle(timeout_seconds=10)` |
| `browser_wait_for_request` | Confirm that a specific request fired. | `browser_wait_for_request(url_substring="/api/save", method="POST")` |
| `browser_wait_for_response` | Wait for one specific response or failure code. | `browser_wait_for_response(url_substring="/api/save", status=200)` |
| `browser_wait_for_stable_dom` | Wait for DOM mutations to stop after dynamic rendering. | `browser_wait_for_stable_dom(timeout_seconds=5, quiet_ms=500)` |
| `browser_save_as_pdf` | Capture the current page as a PDF artifact. | `browser_save_as_pdf(file_name="report.pdf")` |
| `browser_set_viewport` | Normalize viewport size for reproducible layout behavior. | `browser_set_viewport(width=1280, height=800)` |
| `browser_get_state` | Primary read primitive for refs, tabs, scroll, and page metadata. | `browser_get_state(mode="min")` |
| `browser_get_html` | Get raw HTML for the page or one selector-scoped region. | `browser_get_html(selector="main")` |
| `browser_screenshot` | Use for visual confirmation only after cheaper reads fail. | `browser_screenshot(full_page=True)` |

## Frames, Storage, And Origin State

| Tool | Use when | Example |
|---|---|---|
| `browser_list_frames` | Enumerate page frames and identify iframe ownership. | `browser_list_frames()` |
| `browser_get_frame_html` | Inspect one frame's raw HTML by `frame_id`. | `browser_get_frame_html(frame_id="frame-123")` |
| `browser_get_storage` | Inspect `localStorage` or `sessionStorage` for one origin. | `browser_get_storage(origin="https://app.example.com")` |
| `browser_set_storage` | Set one storage key deterministically. | `browser_set_storage(origin="https://app.example.com", storage_type="localStorage", key="workspace", value="prod")` |
| `browser_clear_storage` | Remove one key, one storage area, or all storage for an origin. | `browser_clear_storage(origin="https://app.example.com", storage_type="sessionStorage")` |

## Interaction

| Tool | Use when | Example |
|---|---|---|
| `browser_set_intent` | Publish a short operator-facing status label to the HUD. | `browser_set_intent(intent="Reviewing checkout failure")` |
| `browser_click` | Primary click action by `ref`; use `new_tab=true` for background-open links. | `browser_click(ref="e42")` |
| `browser_right_click` | Open a context menu or invoke right-click behavior. | `browser_right_click(ref="e42")` |
| `browser_double_click` | Trigger double-click behavior or open file-like entries. | `browser_double_click(ref="e42")` |
| `browser_hover` | Reveal hover-only menus, tooltips, or controls. | `browser_hover(ref="e42")` |
| `browser_drag_to` | Perform drag-and-drop between refs or coordinates. | `browser_drag_to(source_ref="e5", target_ref="e9")` |
| `browser_type` | Type into an input, textarea, or editable field. | `browser_type(ref="e17", text="hello")` |
| `browser_press_key` | Send a key or shortcut like `Tab`, `Enter`, or `Control+a`. | `browser_press_key(key="Tab")` |
| `browser_scroll` | Scroll the page or one scroll container. | `browser_scroll(direction="down", pages=2)` |
| `browser_scroll_to_text` | Move the viewport to a visible text section quickly. | `browser_scroll_to_text(text="Webhook retries")` |
| `browser_select_option` | Select a `<select>` option by visible text. | `browser_select_option(ref="e8", text="Production")` |
| `browser_get_dropdown_options` | Inspect the visible labels in a dropdown before selecting. | `browser_get_dropdown_options(ref="e8")` |
| `browser_upload_file` | Upload a local file through a file input. | `browser_upload_file(ref="e5", path="/absolute/path/report.pdf")` |
| `browser_handle_dialog` | Accept or dismiss a JavaScript dialog if it is still pending. | `browser_handle_dialog(accept=True)` |

## Inspection And Extraction

| Tool | Use when | Example |
|---|---|---|
| `browser_extract_content` | Deterministically extract tables, lists, links, images, forms, or key-value panels. | `browser_extract_content(query="all product names and prices")` |
| `browser_find_elements` | Query the DOM directly with CSS when the shape is known. | `browser_find_elements(selector="form input, form button")` |
| `browser_search_page` | Search for text or regex in a long page quickly. | `browser_search_page(pattern="rate limit")` |
| `browser_wait_for_element` | Wait for visible text or a specific ref to appear or disappear. | `browser_wait_for_element(text="Saved")` |
| `browser_get_focused_element` | Confirm which element currently has keyboard focus. | `browser_get_focused_element()` |
| `browser_get_attribute` | Read one attribute like `href`, `src`, `value`, or `disabled`. | `browser_get_attribute(name="href", ref="e42")` |
| `browser_evaluate` | Use targeted JavaScript only when no dedicated tool fits. | `browser_evaluate(code="(function(){ return document.title; })()")` |

## Tabs, Cookies, And Session Persistence

| Tool | Use when | Example |
|---|---|---|
| `browser_new_tab` | Open a new tab and switch into it immediately. | `browser_new_tab(url="https://example.com")` |
| `browser_list_tabs` | Inspect current tabs and identify the active or owned tab. | `browser_list_tabs()` |
| `browser_switch_tab` | Move focus to a specific `tab_id`. | `browser_switch_tab(tab_id="a1b2")` |
| `browser_close_tab` | Close one known tab. | `browser_close_tab(tab_id="a1b2")` |
| `browser_get_cookies` | Inspect cookies for the current page scope. | `browser_get_cookies()` |
| `browser_set_cookies` | Inject cookies for auth or session restoration. | `browser_set_cookies(cookies=[{"name":"session","value":"token"}])` |
| `browser_clear_cookies` | Delete one cookie or clear cookies for the current domain. | `browser_clear_cookies(name="session")` |
| `browser_save_state` | Persist cookies and storage to disk for later reuse. | `browser_save_state(path="~/.agentyc/auth/app.json")` |
| `browser_load_state` | Restore cookies and storage from disk. | `browser_load_state(path="~/.agentyc/auth/app.json")` |

## Network, Debugging, And Session Administration

| Tool | Use when | Example |
|---|---|---|
| `browser_get_console_logs` | Read recent browser console logs. | `browser_get_console_logs(level="error", max_entries=20)` |
| `browser_get_network_log` | Inspect recent network traffic by type or status. | `browser_get_network_log(status_filter="errors", max_entries=20)` |
| `browser_inspect_network_entry` | Inspect one request/response with headers or bodies. | `browser_inspect_network_entry(url_substring="/api/submit", method="POST")` |
| `browser_add_network_mock` | Add a narrow fulfill or abort rule for the active tab. | `browser_add_network_mock(url_substring="/api/submit", body="{\"ok\":true}")` |
| `browser_remove_network_mock` | Remove one mock or clear all mocks. | `browser_remove_network_mock(mock_id="mock-1")` |
| `browser_list_network_mocks` | Inspect active mock rules and match counts. | `browser_list_network_mocks()` |
| `browser_set_network_conditions` | Apply offline or throttling conditions to the active tab. | `browser_set_network_conditions(offline=True)` |
| `browser_get_network_conditions` | Inspect active network throttling/offline settings. | `browser_get_network_conditions()` |
| `browser_replay_request` | Reissue a captured request with narrow overrides. | `browser_replay_request(request_id="req-1")` |
| `browser_export_debug_bundle` | Capture state, logs, network, and optional screenshot in one artifact. | `browser_export_debug_bundle(state_mode="min", include_screenshot=True)` |
| `browser_get_downloads` | Inspect files downloaded in the current browser session. | `browser_get_downloads()` |
| `browser_clear_logs` | Reset console and/or network logs between steps. | `browser_clear_logs(console=True, network=True)` |
| `browser_start_trace` | Start a performance trace before the action under test. | `browser_start_trace()` |
| `browser_stop_trace` | Stop the trace and inspect collected events. | `browser_stop_trace()` |
| `browser_list_sessions` | Inspect tracked browser sessions. | `browser_list_sessions()` |
| `browser_close_session` | Close one browser session by `session_id`. | `browser_close_session(session_id="session-1")` |
| `browser_close_all` | Close all browser sessions; avoid this in shared-browser collaboration. | `browser_close_all()` |

## Real-World Patterns

### Shared browser across projects

1. Start one browser: `agentyc browser --port 9222 --detach`
2. Reuse it from another project with `agentyc mcp --reuse-local-browser`
3. Confirm tab ownership with `browser_list_tabs()`
4. Work from the owned tab; do not intentionally co-own the same tab

### Upload, save, and verify

1. `browser_get_state(mode="min")`
2. `browser_upload_file(...)`
3. `browser_click(...)`
4. `browser_wait_for_response(...)`
5. `browser_wait_for_element(text="Saved")`

### API failure triage

1. `browser_get_console_logs(level="error")`
2. `browser_wait_for_request(...)`
3. `browser_wait_for_response(...)`
4. `browser_inspect_network_entry(...)`
5. `browser_export_debug_bundle(...)`
