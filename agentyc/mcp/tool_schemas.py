"""MCP tool schema catalog for the agentyc server."""

from __future__ import annotations

import mcp.types as types


def get_tool_schemas() -> list[types.Tool]:
	"""Return the public MCP tool catalog."""
	return [
		types.Tool(
			name='browser_navigate',
			description='Navigate to a URL',
			inputSchema={
				'type': 'object',
				'properties': {
					'url': {'type': 'string'},
					'new_tab': {'type': 'boolean', 'default': False},
				},
				'required': ['url'],
			},
		),
		types.Tool(
			name='browser_click',
			description='Click an element (ref preferred) or viewport coordinates.',
			inputSchema={
				'type': 'object',
				'properties': {
					'ref': {'type': 'string', 'description': 'Stable ref from browser_get_state (e.g. "e123")'},
					'index': {'type': 'integer', 'description': 'Backend node id (legacy). Use ref instead.'},
					'coordinate_x': {'type': 'integer', 'description': 'Viewport X — use with coordinate_y'},
					'coordinate_y': {'type': 'integer', 'description': 'Viewport Y — use with coordinate_x'},
					'new_tab': {'type': 'boolean', 'default': False},
				},
			},
		),
		types.Tool(
			name='browser_type',
			description='Type text into a field. Clears existing text first. Use text="" to clear only.',
			inputSchema={
				'type': 'object',
				'properties': {
					'ref': {'type': 'string', 'description': 'Stable ref (e.g. "e123")'},
					'index': {'type': 'integer'},
					'text': {'type': 'string', 'description': 'Text to type. Empty string clears the field.'},
				},
				'required': ['text'],
			},
		),
		types.Tool(
			name='browser_upload_file',
			description='Upload a local file to a file input element.',
			inputSchema={
				'type': 'object',
				'properties': {
					'ref': {'type': 'string', 'description': 'Stable ref (e.g. "e123")'},
					'index': {'type': 'integer'},
					'path': {'type': 'string', 'description': 'Absolute local file path to upload.'},
				},
				'required': ['path'],
			},
		),
		types.Tool(
			name='browser_get_state',
			description='Get the current page state: URL, title, interactive elements with stable refs. Pass since_hash to skip unchanged re-reads.',
			inputSchema={
				'type': 'object',
				'properties': {
					'include_screenshot': {'type': 'boolean', 'default': False},
					'mode': {
						'type': 'string',
						'enum': ['auto', 'full', 'min', 'focus'],
						'description': 'auto: full on small pages, ranked subset on large. full: all elements. min: compact ranked subset. focus: single element.',
						'default': 'auto',
					},
					'focus_ref': {'type': 'string', 'description': 'Element ref for mode=focus.'},
					'since_hash': {
						'type': 'string',
						'description': 'Previous state_hash. Returns changed=false (no elements) if page unchanged — saves tokens.',
					},
				},
			},
		),
		types.Tool(
			name='browser_extract_content',
			description='Deterministically extract structured content (tables, lists, links, images, form fields, key-values) by query. No LLM fallback.',
			inputSchema={
				'type': 'object',
				'properties': {
					'query': {'type': 'string', 'description': 'What to extract (e.g. "table rows", "all links", "form fields")'},
					'extract_links': {'type': 'boolean', 'default': False},
					'output_schema': {
						'type': 'object',
						'additionalProperties': True,
						'description': 'Optional JSON Schema for structured output.',
					},
				},
				'required': ['query'],
			},
		),
		types.Tool(
			name='browser_get_html',
			description='Get raw HTML of the current page or a CSS-selected element.',
			inputSchema={
				'type': 'object',
				'properties': {
					'selector': {'type': 'string', 'description': 'CSS selector. Omit for full page.'},
				},
			},
		),
		types.Tool(
			name='browser_screenshot',
			description='Take a screenshot. Returns viewport metadata (text) and image.',
			inputSchema={
				'type': 'object',
				'properties': {
					'full_page': {'type': 'boolean', 'description': 'Capture full scrollable page', 'default': False},
				},
			},
		),
		types.Tool(name='browser_list_tabs', description='List all open tabs.', inputSchema={'type': 'object', 'properties': {}}),
		types.Tool(
			name='browser_new_tab',
			description='Create a new browser tab and switch focus to it. Use for parallel automation: each subagent calls this to get its own tab without disturbing others.',
			inputSchema={
				'type': 'object',
				'properties': {
					'url': {
						'type': 'string',
						'description': 'URL to open. Omit or pass "about:blank" for an empty tab.',
						'default': 'about:blank',
					},
				},
			},
		),
		types.Tool(
			name='browser_switch_tab',
			description='Switch to a tab by its 4-char tab_id.',
			inputSchema={
				'type': 'object',
				'properties': {'tab_id': {'type': 'string'}},
				'required': ['tab_id'],
			},
		),
		types.Tool(
			name='browser_close_tab',
			description='Close a tab by its 4-char tab_id.',
			inputSchema={
				'type': 'object',
				'properties': {'tab_id': {'type': 'string'}},
				'required': ['tab_id'],
			},
		),
		types.Tool(
			name='browser_list_sessions',
			description='List active browser sessions with status and last activity.',
			inputSchema={'type': 'object', 'properties': {}},
		),
		types.Tool(
			name='browser_close_session',
			description='Close a browser session by ID (from browser_list_sessions).',
			inputSchema={
				'type': 'object',
				'properties': {'session_id': {'type': 'string'}},
				'required': ['session_id'],
			},
		),
		types.Tool(
			name='browser_close_all',
			description='Close all browser sessions.',
			inputSchema={'type': 'object', 'properties': {}},
		),
		types.Tool(
			name='browser_scroll',
			description='Scroll the page or an element. pages=10 reaches the bottom fast.',
			inputSchema={
				'type': 'object',
				'properties': {
					'direction': {'type': 'string', 'enum': ['up', 'down'], 'default': 'down'},
					'pages': {'type': 'number', 'description': '0.5=half, 1=full (default), 10=to end', 'default': 1.0},
					'ref': {'type': 'string', 'description': 'Scroll within element ref. Omit for page scroll.'},
					'index': {'type': 'integer'},
				},
			},
		),
		types.Tool(
			name='browser_go_back', description='Go back in browser history.', inputSchema={'type': 'object', 'properties': {}}
		),
		types.Tool(
			name='browser_go_forward',
			description='Go forward in browser history.',
			inputSchema={'type': 'object', 'properties': {}},
		),
		types.Tool(
			name='browser_refresh', description='Reload the current page.', inputSchema={'type': 'object', 'properties': {}}
		),
		types.Tool(
			name='browser_press_key',
			description='Send a key or shortcut (e.g. "Enter", "Tab", "Control+a", "Meta+r").',
			inputSchema={
				'type': 'object',
				'properties': {'key': {'type': 'string', 'description': 'Key name or chord'}},
				'required': ['key'],
			},
		),
		types.Tool(
			name='browser_wait',
			description='Wait N seconds. Prefer since_hash polling for dynamic content.',
			inputSchema={
				'type': 'object',
				'properties': {'seconds': {'type': 'number', 'default': 2}},
			},
		),
		types.Tool(
			name='browser_evaluate',
			description='Execute JavaScript in the page and return the result. Wrap in IIFE: (function(){ ... })()',
			inputSchema={
				'type': 'object',
				'properties': {'code': {'type': 'string'}},
				'required': ['code'],
			},
		),
		types.Tool(
			name='browser_select_option',
			description='Select an option in a <select> dropdown by its visible label.',
			inputSchema={
				'type': 'object',
				'properties': {
					'ref': {'type': 'string', 'description': 'Stable ref of the <select>'},
					'index': {'type': 'integer'},
					'text': {'type': 'string', 'description': 'Exact visible option text'},
				},
				'required': ['text'],
			},
		),
		types.Tool(
			name='browser_get_dropdown_options',
			description='List all options in a <select> or ARIA combobox.',
			inputSchema={
				'type': 'object',
				'properties': {
					'ref': {'type': 'string'},
					'index': {'type': 'integer'},
				},
			},
		),
		types.Tool(
			name='browser_find_elements',
			description='Find elements by CSS selector. Returns tag, text, and requested attributes.',
			inputSchema={
				'type': 'object',
				'properties': {
					'selector': {'type': 'string', 'description': 'CSS selector (e.g. "table tr", "input[type=email]")'},
					'attributes': {'type': 'array', 'items': {'type': 'string'}, 'description': 'Attributes to extract.'},
					'max_results': {'type': 'integer', 'default': 50},
				},
				'required': ['selector'],
			},
		),
		types.Tool(
			name='browser_wait_for_element',
			description='Poll until an element (by text or ref) appears or disappears. Use for async content and action confirmation.',
			inputSchema={
				'type': 'object',
				'properties': {
					'text': {'type': 'string', 'description': 'Text the element must contain (case-insensitive).'},
					'ref': {'type': 'string', 'description': 'Element ref to wait for.'},
					'appear': {'type': 'boolean', 'description': 'true=wait to appear, false=wait to disappear', 'default': True},
					'timeout_seconds': {'type': 'number', 'default': 10},
				},
			},
		),
		types.Tool(
			name='browser_search_page',
			description='Search for text or regex on the page with surrounding context (like Ctrl+F).',
			inputSchema={
				'type': 'object',
				'properties': {
					'pattern': {'type': 'string'},
					'regex': {'type': 'boolean', 'default': False},
					'max_results': {'type': 'integer', 'default': 25},
				},
				'required': ['pattern'],
			},
		),
		types.Tool(
			name='browser_get_focused_element',
			description='Return the element with keyboard focus. Useful after Tab or click to confirm which field is active.',
			inputSchema={'type': 'object', 'properties': {}},
		),
		types.Tool(
			name='browser_hover',
			description='Hover over an element to trigger :hover states and mouseover handlers. Use before browser_get_state to reveal dropdown menus.',
			inputSchema={
				'type': 'object',
				'properties': {
					'ref': {'type': 'string'},
					'index': {'type': 'integer'},
					'coordinate_x': {'type': 'integer'},
					'coordinate_y': {'type': 'integer'},
				},
			},
		),
		types.Tool(
			name='browser_double_click',
			description='Double-click an element or coordinates. Use for text selection, file open, or double-click handlers.',
			inputSchema={
				'type': 'object',
				'properties': {
					'ref': {'type': 'string'},
					'index': {'type': 'integer'},
					'coordinate_x': {'type': 'integer'},
					'coordinate_y': {'type': 'integer'},
				},
			},
		),
		types.Tool(
			name='browser_drag_to',
			description='Drag from source to target (kanban, sortable lists, sliders, file drop zones).',
			inputSchema={
				'type': 'object',
				'properties': {
					'source_ref': {'type': 'string'},
					'target_ref': {'type': 'string'},
					'source_x': {'type': 'integer'},
					'source_y': {'type': 'integer'},
					'target_x': {'type': 'integer'},
					'target_y': {'type': 'integer'},
					'steps': {'type': 'integer', 'default': 10},
				},
			},
		),
		types.Tool(
			name='browser_scroll_to_text',
			description='Scroll until the given text is visible in the viewport.',
			inputSchema={
				'type': 'object',
				'properties': {'text': {'type': 'string'}},
				'required': ['text'],
			},
		),
		types.Tool(
			name='browser_save_state',
			description='Save cookies, localStorage, and sessionStorage to a file for auth persistence across sessions.',
			inputSchema={
				'type': 'object',
				'properties': {
					'path': {'type': 'string', 'description': 'File path. Default: ~/.agentyc-mcp/browser-state.json'},
				},
			},
		),
		types.Tool(
			name='browser_load_state',
			description='Restore browser state (cookies, localStorage) from a file saved by browser_save_state.',
			inputSchema={
				'type': 'object',
				'properties': {'path': {'type': 'string'}},
				'required': ['path'],
			},
		),
		types.Tool(
			name='browser_wait_for_network_idle',
			description='Wait until no network requests are pending. Use after AJAX calls, form submissions, or SPA navigation.',
			inputSchema={
				'type': 'object',
				'properties': {
					'timeout_seconds': {'type': 'number', 'default': 10},
					'idle_duration_ms': {'type': 'integer', 'description': 'Quiet period required (ms)', 'default': 500},
				},
			},
		),
		types.Tool(
			name='browser_wait_for_request',
			description='Wait until a matching network request is observed. Use after clicks or JS actions that trigger fetch/XHR.',
			inputSchema={
				'type': 'object',
				'properties': {
					'url_substring': {'type': 'string', 'description': 'Match requests whose URL contains this substring.'},
					'url_regex': {'type': 'string', 'description': 'Regex alternative to url_substring.'},
					'method': {'type': 'string', 'description': 'Optional HTTP method filter (e.g. POST).'},
					'resource_type': {
						'type': 'string',
						'description': 'Optional CDP resource type filter such as XHR, Fetch, Document, Script, Stylesheet, or Image.',
					},
					'timeout_seconds': {'type': 'number', 'default': 10.0},
					'include_headers': {'type': 'boolean', 'default': False},
				},
			},
		),
		types.Tool(
			name='browser_wait_for_response',
			description='Wait until a matching network response arrives, optionally filtered by status. More precise than network-idle for API debugging.',
			inputSchema={
				'type': 'object',
				'properties': {
					'url_substring': {'type': 'string', 'description': 'Match responses whose URL contains this substring.'},
					'url_regex': {'type': 'string', 'description': 'Regex alternative to url_substring.'},
					'method': {'type': 'string', 'description': 'Optional HTTP method filter (e.g. POST).'},
					'resource_type': {
						'type': 'string',
						'description': 'Optional CDP resource type filter such as XHR, Fetch, Document, Script, Stylesheet, or Image.',
					},
					'status': {'type': 'integer', 'description': 'Optional exact HTTP status filter.'},
					'timeout_seconds': {'type': 'number', 'default': 10.0},
					'include_headers': {'type': 'boolean', 'default': False},
				},
			},
		),
		types.Tool(
			name='browser_right_click',
			description='Right-click to open a context menu.',
			inputSchema={
				'type': 'object',
				'properties': {
					'ref': {'type': 'string'},
					'index': {'type': 'integer'},
					'coordinate_x': {'type': 'number'},
					'coordinate_y': {'type': 'number'},
				},
			},
		),
		types.Tool(
			name='browser_get_cookies',
			description='Get cookies for the current page (name, value, domain, path, flags).',
			inputSchema={'type': 'object', 'properties': {}},
		),
		types.Tool(
			name='browser_set_cookies',
			description='Set cookies. Use to inject auth tokens before navigating to a protected URL.',
			inputSchema={
				'type': 'object',
				'properties': {
					'cookies': {
						'type': 'array',
						'items': {
							'type': 'object',
							'properties': {
								'name': {'type': 'string'},
								'value': {'type': 'string'},
								'domain': {'type': 'string'},
								'path': {'type': 'string', 'default': '/'},
								'secure': {'type': 'boolean', 'default': False},
								'httpOnly': {'type': 'boolean', 'default': False},
							},
							'required': ['name', 'value'],
						},
					},
				},
				'required': ['cookies'],
			},
		),
		types.Tool(
			name='browser_clear_cookies',
			description='Clear cookies. Omit name to clear all for current domain; pass name to delete one.',
			inputSchema={
				'type': 'object',
				'properties': {'name': {'type': 'string', 'description': 'Specific cookie name to delete.'}},
			},
		),
		types.Tool(
			name='browser_save_as_pdf',
			description='Save the current page as a PDF file and return the file path. Uses CDP Page.printToPDF.',
			inputSchema={
				'type': 'object',
				'properties': {
					'file_name': {
						'type': 'string',
						'description': 'Output PDF filename (without path). Defaults to page title.',
					},
					'print_background': {'type': 'boolean', 'default': True, 'description': 'Include background graphics.'},
					'landscape': {'type': 'boolean', 'default': False, 'description': 'Use landscape orientation.'},
					'scale': {
						'type': 'number',
						'default': 1.0,
						'description': 'Scale of the webpage rendering (0.1 to 2.0).',
					},
					'paper_format': {
						'type': 'string',
						'default': 'Letter',
						'description': 'Paper size: Letter, Legal, A4, A3, or Tabloid.',
					},
				},
			},
		),
		types.Tool(
			name='browser_get_downloads',
			description='List files that have been downloaded during the current browser session.',
			inputSchema={'type': 'object', 'properties': {}},
		),
		types.Tool(
			name='browser_set_viewport',
			description='Set the browser viewport size (width x height). Applies to the current tab.',
			inputSchema={
				'type': 'object',
				'properties': {
					'width': {'type': 'integer', 'description': 'Viewport width in pixels.'},
					'height': {'type': 'integer', 'description': 'Viewport height in pixels.'},
					'device_scale_factor': {'type': 'number', 'default': 1.0, 'description': 'Device pixel ratio.'},
				},
				'required': ['width', 'height'],
			},
		),
		types.Tool(
			name='browser_wait_for_stable_dom',
			description='Wait until the DOM has been stable (no mutations) for a quiet period. Use after AJAX calls, form submissions, or SPA navigation to let the page finish rendering before reading state.',
			inputSchema={
				'type': 'object',
				'properties': {
					'timeout_seconds': {'type': 'number', 'default': 10.0, 'description': 'Maximum wait time.'},
					'quiet_ms': {
						'type': 'integer',
						'default': 500,
						'description': 'Required quiet period (ms) with no DOM mutations.',
					},
				},
			},
		),
		types.Tool(
			name='browser_handle_dialog',
			description='Accept or dismiss a JavaScript dialog (alert, confirm, prompt, beforeunload). Use when a dialog is blocking further interaction.',
			inputSchema={
				'type': 'object',
				'properties': {
					'accept': {
						'type': 'boolean',
						'default': True,
						'description': 'True to accept (OK), False to dismiss (Cancel).',
					},
					'prompt_text': {'type': 'string', 'description': 'Text to enter for prompt dialogs (accept must be True).'},
				},
			},
		),
		types.Tool(
			name='browser_get_attribute',
			description='Get a specific attribute value from a page element by ref or index.',
			inputSchema={
				'type': 'object',
				'properties': {
					'name': {'type': 'string', 'description': 'Attribute name (e.g. "href", "src", "value", "disabled").'},
					'ref': {'type': 'string', 'description': 'Stable ref from browser_get_state.'},
					'index': {'type': 'integer', 'description': 'Legacy element index.'},
				},
				'required': ['name'],
			},
		),
		types.Tool(
			name='browser_clear_logs',
			description='Clear console and/or network log buffers to free memory.',
			inputSchema={
				'type': 'object',
				'properties': {
					'console': {'type': 'boolean', 'default': True, 'description': 'Clear console log buffer.'},
					'network': {'type': 'boolean', 'default': True, 'description': 'Clear network log buffer.'},
				},
			},
		),
		types.Tool(
			name='browser_start_trace',
			description='Start a CDP performance trace for diagnosing page performance issues.',
			inputSchema={
				'type': 'object',
				'properties': {
					'categories': {
						'type': 'string',
						'default': '-*,disabled-by-default-devtools.timeline,devtools.timeline,loading,net,network',
						'description': 'Comma-separated tracing categories.',
					},
				},
			},
		),
		types.Tool(
			name='browser_stop_trace',
			description='Stop the active CDP performance trace and return collected data as JSON.',
			inputSchema={'type': 'object', 'properties': {}},
		),
		types.Tool(
			name='browser_get_console_logs',
			description='Return browser console messages (log/warn/error/info). Captured via CDP — includes page-load errors.',
			inputSchema={
				'type': 'object',
				'properties': {
					'level': {'type': 'string', 'description': 'all, log, warn, error, info', 'default': 'all'},
					'max_entries': {'type': 'integer', 'default': 50},
				},
			},
		),
		types.Tool(
			name='browser_get_network_log',
			description='Return captured network requests (XHR/Fetch, status codes, timing, optional headers). Essential for debugging SPA data flows and API failures.',
			inputSchema={
				'type': 'object',
				'properties': {
					'type_filter': {
						'type': 'string',
						'description': 'all, XHR, Fetch, Document, Script, Stylesheet, Image',
						'default': 'all',
					},
					'status_filter': {
						'type': 'string',
						'description': 'all, errors (4xx/5xx), success (2xx/3xx)',
						'default': 'all',
					},
					'max_entries': {'type': 'integer', 'default': 50},
					'include_headers': {
						'type': 'boolean',
						'description': 'Include request and response headers in each entry. Increases output size significantly.',
						'default': False,
					},
				},
			},
		),
		types.Tool(
			name='browser_export_debug_bundle',
			description='Return a compact debug bundle with current state, recent console logs, recent network activity, trace summary, and an optional screenshot.',
			inputSchema={
				'type': 'object',
				'properties': {
					'state_mode': {'type': 'string', 'enum': ['auto', 'full', 'min', 'focus'], 'default': 'min'},
					'focus_ref': {'type': 'string', 'description': 'Element ref for state_mode=focus.'},
					'since_hash': {
						'type': 'string',
						'description': 'Optional previous state hash for unchanged-state optimization.',
					},
					'include_screenshot': {'type': 'boolean', 'default': False},
					'include_headers': {'type': 'boolean', 'default': False},
					'include_html': {'type': 'boolean', 'default': False},
					'html_selector': {'type': 'string', 'description': 'Optional CSS selector for scoped HTML capture.'},
					'console_max_entries': {'type': 'integer', 'default': 20},
					'network_max_entries': {'type': 'integer', 'default': 20},
					'network_status_filter': {
						'type': 'string',
						'enum': ['all', 'errors', 'success'],
						'default': 'all',
					},
				},
			},
		),
	]
