"""MCP tool schema catalog for the agentyc server."""

from __future__ import annotations

import mcp.types as types


def get_tool_schemas() -> list[types.Tool]:
	"""Return the public MCP tool catalog."""
	return [
		# Direct browser control tools
		types.Tool(
			name='browser_navigate',
			description='Navigate to a URL in the browser',
			inputSchema={
				'type': 'object',
				'properties': {
					'url': {'type': 'string', 'description': 'The URL to navigate to'},
					'new_tab': {'type': 'boolean', 'description': 'Whether to open in a new tab', 'default': False},
				},
				'required': ['url'],
			},
		),
		types.Tool(
			name='browser_click',
			description='Click an element by ref or index, or at specific viewport coordinates. Prefer ref from browser_get_state for stable targeting.',
			inputSchema={
				'type': 'object',
				'properties': {
					'ref': {
						'type': 'string',
						'description': 'Stable element ref from browser_get_state (for example "e123"). Provide this OR index OR coordinate_x+coordinate_y.',
					},
					'index': {
						'type': 'integer',
						'description': 'Legacy numeric element index from browser_get_state. Equivalent to the backend node id. Provide this OR ref OR coordinate_x+coordinate_y.',
					},
					'coordinate_x': {
						'type': 'integer',
						'description': 'X coordinate in pixels from the left edge of the viewport. Must be used together with coordinate_y. Provide this OR ref/index.',
					},
					'coordinate_y': {
						'type': 'integer',
						'description': 'Y coordinate in pixels from the top edge of the viewport. Must be used together with coordinate_x. Provide this OR ref/index.',
					},
					'new_tab': {
						'type': 'boolean',
						'description': 'Whether to open any resulting navigation in a new tab',
						'default': False,
					},
				},
			},
		),
		types.Tool(
			name='browser_type',
			description='Type text into an input field. Prefer ref from browser_get_state for stable targeting. Clears existing text by default; pass text="" to clear only.',
			inputSchema={
				'type': 'object',
				'properties': {
					'ref': {
						'type': 'string',
						'description': 'Stable element ref from browser_get_state (for example "e123"). Provide this OR index.',
					},
					'index': {
						'type': 'integer',
						'description': 'Legacy numeric element index from browser_get_state. Equivalent to the backend node id.',
					},
					'text': {
						'type': 'string',
						'description': 'The text to type. Pass an empty string ("") to clear the field without typing.',
					},
				},
				'required': ['text'],
			},
		),
		types.Tool(
			name='browser_upload_file',
			description='Upload a local file to a file input or upload control. Provide ref or index for the target control, and a path that is accessible to the MCP server.',
			inputSchema={
				'type': 'object',
				'properties': {
					'ref': {
						'type': 'string',
						'description': 'Stable element ref from browser_get_state (for example "e123"). Provide this OR index.',
					},
					'index': {
						'type': 'integer',
						'description': 'Legacy numeric element index from browser_get_state. Equivalent to the backend node id. Provide this OR ref.',
					},
					'path': {
						'type': 'string',
						'description': 'Local file path to upload. Use an absolute path for arbitrary files, or a filename from the agentyc file system.',
					},
				},
				'required': ['path'],
			},
		),
		types.Tool(
			name='browser_get_state',
			description='Get the current state of the page. Supports compact modes, stable refs, and lightweight unchanged-state checks.',
			inputSchema={
				'type': 'object',
				'properties': {
					'include_screenshot': {
						'type': 'boolean',
						'description': 'Whether to include a screenshot of the current page',
						'default': False,
					},
					'mode': {
						'type': 'string',
						'enum': ['auto', 'full', 'min', 'focus'],
						'description': 'State detail level. auto prefers full on small pages and ranked compaction on large ones. full returns all interactive elements, min returns a compact ranked subset, focus returns a single referenced element.',
						'default': 'auto',
					},
					'focus_ref': {
						'type': 'string',
						'description': 'Element ref to focus on when mode=focus.',
					},
					'since_hash': {
						'type': 'string',
						'description': 'Previous state_hash from browser_get_state. If unchanged, returns changed=false with no interactive element payload.',
					},
				},
			},
		),
		types.Tool(
			name='browser_extract_content',
			description='Deterministically extract compatible content from the current page based on a query. This MCP tool does not use an LLM fallback.',
			inputSchema={
				'type': 'object',
				'properties': {
					'query': {'type': 'string', 'description': 'What information to extract from the page'},
					'extract_links': {
						'type': 'boolean',
						'description': 'Whether to include links in the extraction',
						'default': False,
					},
					'output_schema': {
						'type': 'object',
						'description': 'Optional JSON Schema for deterministic structured extraction. Compatible table, list, key-value, link-collection, form-field, and image queries can be answered without an LLM.',
						'additionalProperties': True,
					},
				},
				'required': ['query'],
			},
		),
		types.Tool(
			name='browser_get_html',
			description='Get the raw HTML of the current page or a specific element by CSS selector',
			inputSchema={
				'type': 'object',
				'properties': {
					'selector': {
						'type': 'string',
						'description': 'Optional CSS selector to get HTML of a specific element. If omitted, returns full page HTML.',
					},
				},
			},
		),
		types.Tool(
			name='browser_screenshot',
			description='Take a screenshot of the current page. Returns viewport metadata as text and the screenshot as an image.',
			inputSchema={
				'type': 'object',
				'properties': {
					'full_page': {
						'type': 'boolean',
						'description': 'Whether to capture the full scrollable page or just the visible viewport',
						'default': False,
					},
				},
			},
		),
		# Tab management
		types.Tool(name='browser_list_tabs', description='List all open tabs', inputSchema={'type': 'object', 'properties': {}}),
		types.Tool(
			name='browser_switch_tab',
			description='Switch to a different tab',
			inputSchema={
				'type': 'object',
				'properties': {'tab_id': {'type': 'string', 'description': '4 Character Tab ID of the tab to switch to'}},
				'required': ['tab_id'],
			},
		),
		types.Tool(
			name='browser_close_tab',
			description='Close a tab',
			inputSchema={
				'type': 'object',
				'properties': {'tab_id': {'type': 'string', 'description': '4 Character Tab ID of the tab to close'}},
				'required': ['tab_id'],
			},
		),
		# Browser session management tools
		types.Tool(
			name='browser_list_sessions',
			description='List all active browser sessions with their details and last activity time',
			inputSchema={'type': 'object', 'properties': {}},
		),
		types.Tool(
			name='browser_close_session',
			description='Close a specific browser session by its ID',
			inputSchema={
				'type': 'object',
				'properties': {
					'session_id': {
						'type': 'string',
						'description': 'The browser session ID to close (get from browser_list_sessions)',
					}
				},
				'required': ['session_id'],
			},
		),
		types.Tool(
			name='browser_close_all',
			description='Close all active browser sessions and clean up resources',
			inputSchema={'type': 'object', 'properties': {}},
		),
		# Enhanced interaction tools
		types.Tool(
			name='browser_scroll',
			description='Scroll the page or a specific element. pages=10 reaches the bottom quickly.',
			inputSchema={
				'type': 'object',
				'properties': {
					'direction': {'type': 'string', 'enum': ['up', 'down'], 'default': 'down'},
					'pages': {
						'type': 'number',
						'description': '0.5=half, 1=full page (default), 10=to bottom/top',
						'default': 1.0,
					},
					'ref': {'type': 'string', 'description': 'Scroll within element ref (e.g. "e123"). Omit for page scroll.'},
					'index': {'type': 'integer', 'description': 'Scroll within element by backend node id. Provide this OR ref.'},
				},
			},
		),
		types.Tool(
			name='browser_go_back',
			description='Go back to the previous page in browser history',
			inputSchema={'type': 'object', 'properties': {}},
		),
		types.Tool(
			name='browser_go_forward',
			description='Go forward to the next page in browser history',
			inputSchema={'type': 'object', 'properties': {}},
		),
		types.Tool(
			name='browser_refresh',
			description='Refresh/reload the current page',
			inputSchema={'type': 'object', 'properties': {}},
		),
		types.Tool(
			name='browser_press_key',
			description='Send a keyboard key or shortcut. Examples: "Enter", "Tab", "Escape", "ArrowDown", "Control+a", "Meta+r".',
			inputSchema={
				'type': 'object',
				'properties': {'key': {'type': 'string', 'description': 'Key or shortcut (e.g. "Enter", "Tab", "Control+a")'}},
				'required': ['key'],
			},
		),
		types.Tool(
			name='browser_wait',
			description='Wait for a number of seconds. Prefer since_hash polling for dynamic content.',
			inputSchema={
				'type': 'object',
				'properties': {'seconds': {'type': 'number', 'description': 'Seconds to wait (max 30)', 'default': 2}},
			},
		),
		types.Tool(
			name='browser_evaluate',
			description='Execute JavaScript in the page context and return the result. Wrap in IIFE: (function(){ ... })(). Browser APIs only.',
			inputSchema={
				'type': 'object',
				'properties': {'code': {'type': 'string', 'description': 'JavaScript to evaluate'}},
				'required': ['code'],
			},
		),
		types.Tool(
			name='browser_select_option',
			description='Select an option in a <select> dropdown by its visible text.',
			inputSchema={
				'type': 'object',
				'properties': {
					'ref': {'type': 'string', 'description': 'Stable ref of the <select> (e.g. "e123"). Provide this OR index.'},
					'index': {'type': 'integer', 'description': 'Backend node id of the <select>. Provide this OR ref.'},
					'text': {'type': 'string', 'description': 'Exact visible text of the option to select'},
				},
				'required': ['text'],
			},
		),
		types.Tool(
			name='browser_get_dropdown_options',
			description='Get all available options from a <select> or ARIA combobox element.',
			inputSchema={
				'type': 'object',
				'properties': {
					'ref': {'type': 'string', 'description': 'Stable ref (e.g. "e123"). Provide this OR index.'},
					'index': {'type': 'integer', 'description': 'Backend node id. Provide this OR ref.'},
				},
			},
		),
		types.Tool(
			name='browser_find_elements',
			description='Find elements by CSS selector. Returns tag, text, and optionally attributes for each match.',
			inputSchema={
				'type': 'object',
				'properties': {
					'selector': {'type': 'string', 'description': 'CSS selector (e.g. "table tr", "input[type=email]")'},
					'attributes': {
						'type': 'array',
						'items': {'type': 'string'},
						'description': 'Attributes to extract per element.',
					},
					'max_results': {'type': 'integer', 'default': 50},
				},
				'required': ['selector'],
			},
		),
		types.Tool(
			name='browser_wait_for_element',
			description='Poll until an element matching text or ref appears (or disappears). Use for dynamic content and post-action confirmation.',
			inputSchema={
				'type': 'object',
				'properties': {
					'text': {'type': 'string', 'description': 'Text the element must contain (case-insensitive).'},
					'ref': {'type': 'string', 'description': 'Element ref that must appear (e.g. "e123").'},
					'appear': {
						'type': 'boolean',
						'description': 'True=wait for element to appear, False=wait to disappear',
						'default': True,
					},
					'timeout_seconds': {
						'type': 'number',
						'description': 'Max seconds to wait (default 10, max 30)',
						'default': 10,
					},
				},
			},
		),
		types.Tool(
			name='browser_search_page',
			description='Search for text or a regex pattern on the current page. Returns matches with surrounding context. Equivalent to Ctrl+F.',
			inputSchema={
				'type': 'object',
				'properties': {
					'pattern': {'type': 'string', 'description': 'Text or regex pattern to search for'},
					'regex': {'type': 'boolean', 'description': 'Treat pattern as a regular expression', 'default': False},
					'max_results': {'type': 'integer', 'description': 'Maximum matches to return', 'default': 25},
				},
				'required': ['pattern'],
			},
		),
		types.Tool(
			name='browser_get_focused_element',
			description='Return the element that currently has keyboard focus. Useful after Tab or click to confirm which field is active.',
			inputSchema={'type': 'object', 'properties': {}},
		),
		types.Tool(
			name='browser_hover',
			description='Hover over an element to trigger CSS :hover states and JS mouseover/mouseenter handlers. Essential for opening dropdown menus, tooltips, and hover-based UI. Use browser_get_state after hovering to see new elements.',
			inputSchema={
				'type': 'object',
				'properties': {
					'ref': {'type': 'string', 'description': 'Element ref (e.g. e123)'},
					'index': {'type': 'integer', 'description': 'Element index'},
					'coordinate_x': {'type': 'integer', 'description': 'X viewport coordinate'},
					'coordinate_y': {'type': 'integer', 'description': 'Y viewport coordinate'},
				},
			},
		),
		types.Tool(
			name='browser_double_click',
			description='Double-click an element or viewport coordinates. Use for text selection, opening files/folders, or activating double-click handlers in rich editors and file managers.',
			inputSchema={
				'type': 'object',
				'properties': {
					'ref': {'type': 'string', 'description': 'Element ref (e.g. e123)'},
					'index': {'type': 'integer', 'description': 'Element index from browser_state'},
					'coordinate_x': {'type': 'integer'},
					'coordinate_y': {'type': 'integer'},
				},
			},
		),
		types.Tool(
			name='browser_drag_to',
			description='Drag from one element or coordinate to another. Use for drag-and-drop in kanban boards, sortable lists, sliders, and file drop zones.',
			inputSchema={
				'type': 'object',
				'properties': {
					'source_ref': {'type': 'string', 'description': 'Source element ref (e.g. e123)'},
					'target_ref': {'type': 'string', 'description': 'Target element ref'},
					'source_x': {'type': 'integer'},
					'source_y': {'type': 'integer'},
					'target_x': {'type': 'integer'},
					'target_y': {'type': 'integer'},
					'steps': {
						'type': 'integer',
						'description': 'Mouse movement interpolation steps (default: 10)',
						'default': 10,
					},
				},
			},
		),
		types.Tool(
			name='browser_scroll_to_text',
			description='Scroll the page until the given text string is visible in the viewport. Useful for locating content before interacting with it or verifying it exists.',
			inputSchema={
				'type': 'object',
				'properties': {
					'text': {'type': 'string', 'description': 'Text to scroll to'},
				},
				'required': ['text'],
			},
		),
		types.Tool(
			name='browser_save_state',
			description='Save the current browser session state (cookies, localStorage, sessionStorage) to a file. Use to persist authentication between sessions. Pass the returned path to browser_load_state in a future session.',
			inputSchema={
				'type': 'object',
				'properties': {
					'path': {
						'type': 'string',
						'description': 'File path to save state to (e.g. /tmp/auth-state.json). Defaults to ~/.agentyc-mcp/browser-state.json',
					},
				},
			},
		),
		types.Tool(
			name='browser_load_state',
			description='Restore browser session state (cookies, localStorage) from a file previously saved with browser_save_state. Call this early in a session to restore authentication.',
			inputSchema={
				'type': 'object',
				'properties': {
					'path': {'type': 'string', 'description': 'File path to load state from'},
				},
				'required': ['path'],
			},
		),
		types.Tool(
			name='browser_wait_for_network_idle',
			description='Wait until the browser has no pending network requests for a specified duration. Use after triggering AJAX calls, form submissions, or SPA navigation to ensure data has loaded before reading state.',
			inputSchema={
				'type': 'object',
				'properties': {
					'timeout_seconds': {
						'type': 'number',
						'description': 'Maximum time to wait (default: 10, max: 30)',
						'default': 10,
					},
					'idle_duration_ms': {
						'type': 'integer',
						'description': 'How long network must be idle (ms, default: 500)',
						'default': 500,
					},
				},
			},
		),
		types.Tool(
			name='browser_right_click',
			description='Right-click an element or at specific coordinates to open a context menu. Use ref from browser_get_state for stable targeting.',
			inputSchema={
				'type': 'object',
				'properties': {
					'ref': {'type': 'string', 'description': 'Stable element ref from browser_get_state (e.g. e42)'},
					'index': {'type': 'integer', 'description': 'Element index (backend_node_id)'},
					'coordinate_x': {'type': 'number', 'description': 'Viewport X coordinate'},
					'coordinate_y': {'type': 'number', 'description': 'Viewport Y coordinate'},
				},
			},
		),
		types.Tool(
			name='browser_get_cookies',
			description='Get all cookies for the current page URL. Returns name, value, domain, path, and flags. Useful for reading auth tokens and session state.',
			inputSchema={'type': 'object', 'properties': {}},
		),
		types.Tool(
			name='browser_set_cookies',
			description='Set one or more cookies. Use to inject auth tokens or session cookies before navigating to a protected URL.',
			inputSchema={
				'type': 'object',
				'properties': {
					'cookies': {
						'type': 'array',
						'description': 'List of cookie objects to set',
						'items': {
							'type': 'object',
							'properties': {
								'name': {'type': 'string'},
								'value': {'type': 'string'},
								'domain': {'type': 'string', 'description': 'Cookie domain (e.g. .example.com)'},
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
			description='Clear cookies. Without arguments clears all cookies for the current page domain; pass a name to delete a specific cookie.',
			inputSchema={
				'type': 'object',
				'properties': {
					'name': {
						'type': 'string',
						'description': 'Name of a specific cookie to delete (omit to clear all for current domain)',
					},
				},
			},
		),
		types.Tool(
			name='browser_get_console_logs',
			description='Return recent browser console messages (log, warn, error, info). Captured natively via CDP Runtime domain - includes errors from page load, not just after JS injection. Essential for debugging JavaScript errors and SPA state issues.',
			inputSchema={
				'type': 'object',
				'properties': {
					'level': {
						'type': 'string',
						'description': 'Filter by level: all, log, warn, error, info (default: all)',
						'default': 'all',
					},
					'max_entries': {
						'type': 'integer',
						'description': 'Maximum number of entries to return (default: 50)',
						'default': 50,
					},
				},
			},
		),
		types.Tool(
			name='browser_get_network_log',
			description='Return recent network requests captured via CDP Network domain. Shows XHR/Fetch API calls, their HTTP status codes, and timing - essential for debugging SPA data flows, API failures, and understanding what a form submission actually does.',
			inputSchema={
				'type': 'object',
				'properties': {
					'type_filter': {
						'type': 'string',
						'description': 'Filter by request type: all, XHR, Fetch, Document, Script, Stylesheet, Image (default: all)',
						'default': 'all',
					},
					'status_filter': {
						'type': 'string',
						'description': 'Filter by status: all, errors (4xx/5xx/failed), success (2xx/3xx) (default: all)',
						'default': 'all',
					},
					'max_entries': {
						'type': 'integer',
						'description': 'Maximum number of entries to return (default: 50)',
						'default': 50,
					},
				},
			},
		),
	]
