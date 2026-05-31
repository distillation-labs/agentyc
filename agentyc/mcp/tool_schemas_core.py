"""Core browser and session MCP tool schemas."""

from __future__ import annotations

import mcp.types as types

from agentyc.mcp.tool_schemas_interaction import EARLY_INTERACTION_TOOL_SCHEMAS, LATE_INTERACTION_TOOL_SCHEMAS

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
	*EARLY_INTERACTION_TOOL_SCHEMAS,
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
	*LATE_INTERACTION_TOOL_SCHEMAS,
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
