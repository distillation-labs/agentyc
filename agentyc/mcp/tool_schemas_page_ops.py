"""Page operation MCP tool schemas."""

from __future__ import annotations

import mcp.types as types

PAGE_OPERATION_TOOL_SCHEMAS: list[types.Tool] = [
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
]
