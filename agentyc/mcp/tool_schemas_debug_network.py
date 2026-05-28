"""Debugging and network MCP tool schemas."""

from __future__ import annotations

import mcp.types as types

DEBUG_NETWORK_TOOL_SCHEMAS: list[types.Tool] = [
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
		name='browser_inspect_network_entry',
		description='Inspect one captured network entry, including optional request and response bodies.',
		inputSchema={
			'type': 'object',
			'properties': {
				'request_id': {'type': 'string', 'description': 'Exact captured request ID.'},
				'url_substring': {'type': 'string', 'description': 'Match a captured request by URL substring.'},
				'url_regex': {'type': 'string', 'description': 'Regex alternative to url_substring.'},
				'method': {'type': 'string', 'description': 'Optional HTTP method filter.'},
				'resource_type': {'type': 'string', 'description': 'Optional CDP resource type filter.'},
				'status': {'type': 'integer', 'description': 'Optional exact HTTP status filter.'},
				'include_headers': {'type': 'boolean', 'default': False},
				'include_request_body': {'type': 'boolean', 'default': True},
				'include_response_body': {'type': 'boolean', 'default': True},
				'max_body_bytes': {'type': 'integer', 'default': 2048},
				'decode_json': {'type': 'boolean', 'default': True},
			},
		},
	),
	types.Tool(
		name='browser_add_network_mock',
		description='Add a URL-matching network mock rule for the active tab.',
		inputSchema={
			'type': 'object',
			'properties': {
				'url_substring': {'type': 'string', 'description': 'URL substring matcher.'},
				'url_regex': {'type': 'string', 'description': 'Regex URL matcher.'},
				'method': {'type': 'string', 'description': 'Optional HTTP method filter.'},
				'resource_type': {'type': 'string', 'description': 'Optional CDP resource type filter.'},
				'action': {'type': 'string', 'enum': ['fulfill', 'abort'], 'default': 'fulfill'},
				'status': {'type': 'integer', 'default': 200},
				'headers': {
					'type': 'object',
					'additionalProperties': True,
					'description': 'Response headers for fulfill mocks.',
				},
				'body': {'type': 'string', 'default': '', 'description': 'Response body for fulfill mocks.'},
				'error_reason': {'type': 'string', 'default': 'Failed', 'description': 'CDP error reason for abort mocks.'},
			},
		},
	),
	types.Tool(
		name='browser_remove_network_mock',
		description='Remove one network mock by mock_id, or all mocks when omitted.',
		inputSchema={
			'type': 'object',
			'properties': {
				'mock_id': {'type': 'string', 'description': 'Specific mock ID from browser_list_network_mocks.'},
			},
		},
	),
	types.Tool(
		name='browser_list_network_mocks',
		description='List active network mock rules for the current browser session.',
		inputSchema={'type': 'object', 'properties': {}},
	),
	types.Tool(
		name='browser_set_network_conditions',
		description='Apply offline or throttling conditions to the active tab.',
		inputSchema={
			'type': 'object',
			'properties': {
				'offline': {'type': 'boolean', 'default': False},
				'latency_ms': {'type': 'number', 'default': 0.0},
				'download_kbps': {'type': 'number'},
				'upload_kbps': {'type': 'number'},
				'connection_type': {'type': 'string', 'description': 'Optional CDP connection type label.'},
				'reset': {'type': 'boolean', 'default': False},
			},
		},
	),
	types.Tool(
		name='browser_get_network_conditions',
		description='List active per-tab network conditions configured in this session.',
		inputSchema={'type': 'object', 'properties': {}},
	),
	types.Tool(
		name='browser_replay_request',
		description='Replay a captured request with optional header or body overrides.',
		inputSchema={
			'type': 'object',
			'properties': {
				'request_id': {'type': 'string', 'description': 'Exact captured request ID.'},
				'url_substring': {'type': 'string', 'description': 'Match a captured request by URL substring.'},
				'url_regex': {'type': 'string', 'description': 'Regex alternative to url_substring.'},
				'method': {'type': 'string', 'description': 'Optional method override or filter.'},
				'body': {'type': 'string', 'description': 'Optional request body override.'},
				'headers': {
					'type': 'object',
					'additionalProperties': True,
					'description': 'Optional request header overrides.',
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
