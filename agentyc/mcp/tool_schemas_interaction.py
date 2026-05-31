"""Interaction-focused MCP tool schemas."""

from __future__ import annotations

import mcp.types as types

EARLY_INTERACTION_TOOL_SCHEMAS: list[types.Tool] = [
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
		description='Click an element (ref preferred) or viewport coordinates. Optionally wait for a matching download, new tab, URL change, request, or response triggered by the click.',
		inputSchema={
			'type': 'object',
			'properties': {
				'ref': {'type': 'string', 'description': 'Stable ref from browser_get_state (e.g. "e123")'},
				'index': {'type': 'integer', 'description': 'Backend node id (legacy). Use ref instead.'},
				'label': {
					'type': 'string',
					'description': 'Deterministic label/text match to resolve when ref/index is omitted.',
				},
				'coordinate_x': {'type': 'integer', 'description': 'Viewport X — use with coordinate_y'},
				'coordinate_y': {'type': 'integer', 'description': 'Viewport Y — use with coordinate_x'},
				'new_tab': {'type': 'boolean', 'default': False},
				'wait_for_download': {
					'type': 'boolean',
					'default': False,
					'description': 'Wait for a download triggered by the click before returning.',
				},
				'wait_for_tab': {
					'type': 'boolean',
					'default': False,
					'description': 'Wait for a newly opened tab triggered by the click and switch focus to it.',
				},
				'wait_for_url_substring': {
					'type': 'string',
					'description': 'Optional URL substring the current tab must reach after the click.',
				},
				'wait_for_url_regex': {
					'type': 'string',
					'description': 'Optional URL regex the current tab must match after the click.',
				},
				'wait_for_request': {
					'type': 'object',
					'description': 'Optional network-request wait criteria to arm before clicking.',
					'properties': {
						'url_substring': {'type': 'string'},
						'url_regex': {'type': 'string'},
						'method': {'type': 'string'},
						'resource_type': {'type': 'string'},
						'timeout_seconds': {'type': 'number', 'default': 10.0},
						'include_headers': {'type': 'boolean', 'default': False},
					},
				},
				'wait_for_response': {
					'type': 'object',
					'description': 'Optional network-response wait criteria to arm before clicking.',
					'properties': {
						'url_substring': {'type': 'string'},
						'url_regex': {'type': 'string'},
						'method': {'type': 'string'},
						'resource_type': {'type': 'string'},
						'status': {'type': 'integer'},
						'timeout_seconds': {'type': 'number', 'default': 10.0},
						'include_headers': {'type': 'boolean', 'default': False},
					},
				},
				'expected_download_name': {
					'type': 'string',
					'description': 'Exact file name to wait for when wait_for_download is true.',
				},
				'download_timeout_seconds': {
					'type': 'number',
					'default': 10.0,
					'description': 'Maximum wait time for the requested download.',
				},
				'expected_tab_url_substring': {
					'type': 'string',
					'description': 'Optional URL substring the new tab must contain when wait_for_tab is true.',
				},
				'tab_timeout_seconds': {
					'type': 'number',
					'default': 10.0,
					'description': 'Maximum wait time for the requested new tab.',
				},
				'url_timeout_seconds': {
					'type': 'number',
					'default': 10.0,
					'description': 'Maximum wait time for the requested URL change.',
				},
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
				'label': {
					'type': 'string',
					'description': 'Deterministic label/text match to resolve when ref/index is omitted.',
				},
				'text': {'type': 'string', 'description': 'Text to type. Empty string clears the field.'},
			},
			'required': ['text'],
		},
	),
	types.Tool(
		name='browser_fill_form',
		description='Fill multiple fields in a single round trip. Each field item targets a ref, index, or label and provides exactly one of text, option_text, path, or checked.',
		inputSchema={
			'type': 'object',
			'properties': {
				'fields': {
					'type': 'array',
					'minItems': 1,
					'description': 'Form entries to apply in order.',
					'items': {
						'type': 'object',
						'properties': {
							'ref': {'type': 'string', 'description': 'Stable ref (e.g. "e123")'},
							'index': {'type': 'integer'},
							'label': {
								'type': 'string',
								'description': 'Optional human-readable label for lookup and clearer errors when ref/index is omitted.',
							},
							'text': {'type': 'string', 'description': 'Text to type into an input or textarea.'},
							'option_text': {'type': 'string', 'description': 'Visible option text to select in a dropdown.'},
							'path': {'type': 'string', 'description': 'Absolute local file path to upload.'},
							'checked': {
								'type': 'boolean',
								'description': 'Desired checked state for checkbox, radio, or switch controls.',
							},
						},
					},
				},
			},
			'required': ['fields'],
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
				'label': {
					'type': 'string',
					'description': 'Deterministic label/text match to resolve when ref/index is omitted.',
				},
				'path': {'type': 'string', 'description': 'Absolute local file path to upload.'},
			},
			'required': ['path'],
		},
	),
]

LATE_INTERACTION_TOOL_SCHEMAS: list[types.Tool] = [
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
	types.Tool(name='browser_refresh', description='Reload the current page.', inputSchema={'type': 'object', 'properties': {}}),
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
				'label': {
					'type': 'string',
					'description': 'Deterministic label/text match to resolve when ref/index is omitted.',
				},
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
				'label': {
					'type': 'string',
					'description': 'Deterministic label/text match to resolve when ref/index is omitted.',
				},
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
]

__all__ = [
	'EARLY_INTERACTION_TOOL_SCHEMAS',
	'LATE_INTERACTION_TOOL_SCHEMAS',
]
