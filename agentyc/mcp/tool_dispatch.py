"""Tool dispatch routing for the agentyc MCP server."""

from __future__ import annotations

import asyncio
from typing import Any

import mcp.types as types

from agentyc.mcp.tool_dispatch_interactions import _dispatch_interaction_tool


async def _execute_tool(self, tool_name: str, arguments: dict[str, Any]) -> str | list[types.TextContent | types.ImageContent]:
	"""Execute a agentyc tool. Returns str for most tools, or a content list for tools with image output."""

	if tool_name == 'browser_list_sessions':
		return await self._list_sessions()

	if tool_name == 'browser_close_session':
		return await self._close_session(arguments['session_id'])

	if tool_name == 'browser_close_all':
		return await self._close_all_sessions()

	if tool_name == 'browser_set_intent':
		return await self._set_intent(arguments['intent'])

	if tool_name.startswith('browser_'):
		if not self._browser_runtime_is_ready():
			# If a WebSocket reconnect is already in progress, wait for it to
			# finish (up to 20 s) before deciding to spawn a new Chrome process.
			# Without this guard, any tool call that arrives while the WS is
			# re-connecting immediately kills the session and launches another
			# Chrome, causing the stale-browser accumulation seen in practice.
			if self.browser_session is not None and getattr(self.browser_session, 'is_reconnecting', False):
				try:
					await asyncio.wait_for(
						self.browser_session._reconnect_event.wait(),
						timeout=20.0,
					)
				except TimeoutError:
					pass
			if not self._browser_runtime_is_ready():
				await self._reset_broken_browser_runtime()
				await self._init_browser_session()

		interaction_result = await _dispatch_interaction_tool(self, tool_name, arguments)
		if interaction_result is not None:
			return interaction_result

		if tool_name == 'browser_get_state':
			state_json, screenshot_b64 = await self._get_browser_state(
				include_screenshot=arguments.get('include_screenshot', False),
				mode=arguments.get('mode', 'auto'),
				focus_ref=arguments.get('focus_ref'),
				since_hash=arguments.get('since_hash'),
			)
			fmt = getattr(getattr(self, 'browser_session', None), 'llm_screenshot_format', 'png')
			mime = f'image/{fmt}' if fmt in ('png', 'jpeg', 'webp') else 'image/png'
			content: list[types.TextContent | types.ImageContent] = [types.TextContent(type='text', text=state_json)]
			if screenshot_b64:
				content.append(types.ImageContent(type='image', data=screenshot_b64, mimeType=mime))
			return content

		if tool_name == 'browser_get_html':
			return await self._get_html(arguments.get('selector'))

		if tool_name == 'browser_list_frames':
			return await self._list_frames()

		if tool_name == 'browser_get_frame_html':
			return await self._get_frame_html(arguments['frame_id'])

		if tool_name == 'browser_get_storage':
			return await self._get_storage(
				origin=arguments.get('origin'),
				storage_type=arguments.get('storage_type'),
				key=arguments.get('key'),
			)

		if tool_name == 'browser_set_storage':
			return await self._set_storage(
				origin=arguments['origin'],
				storage_type=arguments['storage_type'],
				key=arguments['key'],
				value=arguments['value'],
			)

		if tool_name == 'browser_clear_storage':
			return await self._clear_storage(
				origin=arguments['origin'],
				storage_type=arguments.get('storage_type'),
				key=arguments.get('key'),
			)

		if tool_name == 'browser_screenshot':
			meta_json, screenshot_b64 = await self._screenshot(arguments.get('full_page', False))
			fmt = getattr(getattr(self, 'browser_session', None), 'llm_screenshot_format', 'png')
			mime = f'image/{fmt}' if fmt in ('png', 'jpeg', 'webp') else 'image/png'
			content: list[types.TextContent | types.ImageContent] = [types.TextContent(type='text', text=meta_json)]
			if screenshot_b64:
				content.append(types.ImageContent(type='image', data=screenshot_b64, mimeType=mime))
			return content

		if tool_name == 'browser_extract_content':
			return await self._extract_content(
				arguments['query'],
				arguments.get('extract_links', False),
				arguments.get('output_schema'),
			)

		if tool_name == 'browser_wait_for_download':
			return await self._wait_for_download(
				expected_name=arguments.get('expected_name'),
				timeout_seconds=arguments.get('timeout_seconds', 10.0),
			)

		if tool_name == 'browser_wait_for_tab':
			return await self._wait_for_tab(
				url_substring=arguments.get('url_substring'),
				url_regex=arguments.get('url_regex'),
				timeout_seconds=arguments.get('timeout_seconds', 10.0),
				switch_focus=arguments.get('switch_focus', True),
			)

		if tool_name == 'browser_grant_permissions':
			return await self._grant_permissions(
				permissions=arguments.get('permissions', []),
				origin=arguments.get('origin'),
			)

		if tool_name == 'browser_set_geolocation':
			return await self._set_geolocation(
				latitude=arguments['latitude'],
				longitude=arguments['longitude'],
				accuracy=arguments.get('accuracy', 100.0),
			)

		if tool_name == 'browser_set_extra_headers':
			return await self._set_extra_headers(headers=arguments.get('headers', {}))

		if tool_name == 'browser_set_user_agent':
			return await self._set_user_agent(
				user_agent=arguments['user_agent'],
				accept_language=arguments.get('accept_language'),
				platform=arguments.get('platform'),
			)

		if tool_name == 'browser_set_timezone':
			return await self._set_timezone(timezone_id=arguments.get('timezone_id', ''))

		if tool_name == 'browser_set_locale':
			return await self._set_locale(locale=arguments.get('locale'))

		if tool_name == 'browser_emulate_media':
			return await self._emulate_media(
				media=arguments.get('media'),
				color_scheme=arguments.get('color_scheme'),
				reduced_motion=arguments.get('reduced_motion'),
				forced_colors=arguments.get('forced_colors'),
			)

		if tool_name == 'browser_list_tabs':
			return await self._list_tabs()

		if tool_name == 'browser_new_tab':
			return await self._new_tab(url=arguments.get('url', 'about:blank'))

		if tool_name == 'browser_switch_tab':
			return await self._switch_tab(arguments['tab_id'])

		if tool_name == 'browser_close_tab':
			return await self._close_tab(arguments['tab_id'])

		if tool_name == 'browser_save_state':
			return await self._save_state(path=arguments.get('path'))

		if tool_name == 'browser_load_state':
			return await self._load_state(path=arguments['path'])

		if tool_name == 'browser_wait_for_network_idle':
			return await self._wait_for_network_idle(
				timeout_seconds=arguments.get('timeout_seconds', 10.0),
				idle_duration_ms=arguments.get('idle_duration_ms', 500),
			)

		if tool_name == 'browser_wait_for_request':
			return await self._wait_for_request(
				url_substring=arguments.get('url_substring'),
				url_regex=arguments.get('url_regex'),
				method=arguments.get('method'),
				resource_type=arguments.get('resource_type'),
				timeout_seconds=arguments.get('timeout_seconds', 10.0),
				include_headers=arguments.get('include_headers', False),
			)

		if tool_name == 'browser_wait_for_response':
			return await self._wait_for_response(
				url_substring=arguments.get('url_substring'),
				url_regex=arguments.get('url_regex'),
				method=arguments.get('method'),
				resource_type=arguments.get('resource_type'),
				status=arguments.get('status'),
				timeout_seconds=arguments.get('timeout_seconds', 10.0),
				include_headers=arguments.get('include_headers', False),
			)

		if tool_name == 'browser_right_click':
			return await self._right_click(
				ref=arguments.get('ref'),
				index=arguments.get('index'),
				coordinate_x=arguments.get('coordinate_x'),
				coordinate_y=arguments.get('coordinate_y'),
			)

		if tool_name == 'browser_get_cookies':
			return await self._get_cookies()

		if tool_name == 'browser_set_cookies':
			return await self._set_cookies(arguments['cookies'])

		if tool_name == 'browser_clear_cookies':
			return await self._clear_cookies(name=arguments.get('name'))

		if tool_name == 'browser_save_as_pdf':
			return await self._save_as_pdf(
				file_name=arguments.get('file_name'),
				print_background=arguments.get('print_background', True),
				landscape=arguments.get('landscape', False),
				scale=arguments.get('scale', 1.0),
				paper_format=arguments.get('paper_format', 'Letter'),
			)

		if tool_name == 'browser_get_downloads':
			return await self._get_downloads()

		if tool_name == 'browser_set_viewport':
			return await self._set_viewport(
				width=arguments['width'],
				height=arguments['height'],
				device_scale_factor=arguments.get('device_scale_factor', 1.0),
			)

		if tool_name == 'browser_wait_for_stable_dom':
			return await self._wait_for_stable_dom(
				timeout_seconds=arguments.get('timeout_seconds', 10.0),
				quiet_ms=arguments.get('quiet_ms', 500),
			)

		if tool_name == 'browser_handle_dialog':
			return await self._handle_dialog(
				accept=arguments.get('accept', True),
				prompt_text=arguments.get('prompt_text'),
			)

		if tool_name == 'browser_get_attribute':
			return await self._get_attribute(
				name=arguments['name'],
				ref=arguments.get('ref'),
				index=arguments.get('index'),
			)

		if tool_name == 'browser_clear_logs':
			return await self._clear_logs(
				console=arguments.get('console', True),
				network=arguments.get('network', True),
			)

		if tool_name == 'browser_start_trace':
			return await self._start_trace(
				categories=arguments.get('categories'),
			)

		if tool_name == 'browser_stop_trace':
			return await self._stop_trace()

		if tool_name == 'browser_get_console_logs':
			return await self._get_console_logs(level=arguments.get('level', 'all'), max_entries=arguments.get('max_entries', 50))

		if tool_name == 'browser_get_network_log':
			return await self._get_network_log(
				type_filter=arguments.get('type_filter', 'all'),
				status_filter=arguments.get('status_filter', 'all'),
				max_entries=arguments.get('max_entries', 50),
				include_headers=arguments.get('include_headers', False),
			)

		if tool_name == 'browser_inspect_network_entry':
			return await self._inspect_network_entry(
				request_id=arguments.get('request_id'),
				url_substring=arguments.get('url_substring'),
				url_regex=arguments.get('url_regex'),
				method=arguments.get('method'),
				resource_type=arguments.get('resource_type'),
				status=arguments.get('status'),
				include_headers=arguments.get('include_headers', False),
				include_request_body=arguments.get('include_request_body', True),
				include_response_body=arguments.get('include_response_body', True),
				max_body_bytes=arguments.get('max_body_bytes', 2048),
				decode_json=arguments.get('decode_json', True),
			)

		if tool_name == 'browser_add_network_mock':
			return await self._add_network_mock(
				url_substring=arguments.get('url_substring'),
				url_regex=arguments.get('url_regex'),
				method=arguments.get('method'),
				resource_type=arguments.get('resource_type'),
				action=arguments.get('action', 'fulfill'),
				status=arguments.get('status', 200),
				headers=arguments.get('headers'),
				body=arguments.get('body', ''),
				error_reason=arguments.get('error_reason', 'Failed'),
			)

		if tool_name == 'browser_remove_network_mock':
			return await self._remove_network_mock(mock_id=arguments.get('mock_id'))

		if tool_name == 'browser_list_network_mocks':
			return await self._list_network_mocks()

		if tool_name == 'browser_set_network_conditions':
			return await self._set_network_conditions(
				offline=arguments.get('offline', False),
				latency_ms=arguments.get('latency_ms', 0.0),
				download_kbps=arguments.get('download_kbps'),
				upload_kbps=arguments.get('upload_kbps'),
				connection_type=arguments.get('connection_type'),
				reset=arguments.get('reset', False),
			)

		if tool_name == 'browser_get_network_conditions':
			return await self._get_network_conditions()

		if tool_name == 'browser_replay_request':
			return await self._replay_request(
				request_id=arguments.get('request_id'),
				url_substring=arguments.get('url_substring'),
				url_regex=arguments.get('url_regex'),
				method=arguments.get('method'),
				body=arguments.get('body'),
				headers=arguments.get('headers'),
			)

		if tool_name == 'browser_export_debug_bundle':
			bundle_json, screenshot_b64 = await self._export_debug_bundle(
				state_mode=arguments.get('state_mode', 'min'),
				focus_ref=arguments.get('focus_ref'),
				since_hash=arguments.get('since_hash'),
				include_screenshot=arguments.get('include_screenshot', False),
				include_headers=arguments.get('include_headers', False),
				include_html=arguments.get('include_html', False),
				html_selector=arguments.get('html_selector'),
				console_max_entries=arguments.get('console_max_entries', 20),
				network_max_entries=arguments.get('network_max_entries', 20),
				network_status_filter=arguments.get('network_status_filter', 'all'),
			)
			fmt = getattr(getattr(self, 'browser_session', None), 'llm_screenshot_format', 'png')
			mime = f'image/{fmt}' if fmt in ('png', 'jpeg', 'webp') else 'image/png'
			content: list[types.TextContent | types.ImageContent] = [types.TextContent(type='text', text=bundle_json)]
			if screenshot_b64:
				content.append(types.ImageContent(type='image', data=screenshot_b64, mimeType=mime))
			return content

	return f'Unknown tool: {tool_name}'
