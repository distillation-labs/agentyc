"""Core browser and session MCP tool schemas."""

from __future__ import annotations

import mcp.types as types

CORE_TOOL_SCHEMAS: list[types.Tool] = [
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
			name='browser_set_intent',
			description='Update the live HUD with a short human-readable intent summary. No raw chain-of-thought.',
			inputSchema={
				'type': 'object',
				'properties': {
					'intent': {'type': 'string', 'description': 'Short intent label, ideally a concise gerund phrase.'},
				},
				'required': ['intent'],
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
			name='browser_list_frames',
			description='List known page frames, including cross-origin frames and their IDs.',
			inputSchema={'type': 'object', 'properties': {}},
		),
		types.Tool(
			name='browser_get_frame_html',
			description='Get raw HTML for a specific frame by frame_id.',
			inputSchema={
				'type': 'object',
				'properties': {
					'frame_id': {'type': 'string', 'description': 'Frame ID from browser_list_frames.'},
				},
				'required': ['frame_id'],
			},
		),
		types.Tool(
			name='browser_get_storage',
			description='Inspect localStorage and sessionStorage by origin, type, or key.',
			inputSchema={
				'type': 'object',
				'properties': {
					'origin': {'type': 'string', 'description': 'Filter to one exact origin.'},
					'storage_type': {
						'type': 'string',
						'enum': ['localStorage', 'sessionStorage'],
						'description': 'Optional storage area filter.',
					},
					'key': {'type': 'string', 'description': 'Optional key filter within matching storage entries.'},
				},
			},
		),
		types.Tool(
			name='browser_set_storage',
			description='Set one localStorage or sessionStorage key for the current origin-scoped page context.',
			inputSchema={
				'type': 'object',
				'properties': {
					'origin': {'type': 'string', 'description': 'Expected current page origin.'},
					'storage_type': {'type': 'string', 'enum': ['localStorage', 'sessionStorage']},
					'key': {'type': 'string'},
					'value': {'type': 'string'},
				},
				'required': ['origin', 'storage_type', 'key', 'value'],
			},
		),
		types.Tool(
			name='browser_clear_storage',
			description='Clear storage for the current origin-scoped page context, optionally by area or key.',
			inputSchema={
				'type': 'object',
				'properties': {
					'origin': {'type': 'string', 'description': 'Expected current page origin.'},
					'storage_type': {
						'type': 'string',
						'enum': ['localStorage', 'sessionStorage'],
						'description': 'Optional storage area to clear.',
					},
					'key': {'type': 'string', 'description': 'Optional single key to remove.'},
				},
				'required': ['origin'],
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
]
