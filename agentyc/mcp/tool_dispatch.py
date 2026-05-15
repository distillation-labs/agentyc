"""Tool dispatch routing for the agentyc MCP server."""

from __future__ import annotations

from typing import Any

import mcp.types as types


async def _execute_tool(
	self, tool_name: str, arguments: dict[str, Any]
) -> str | list[types.TextContent | types.ImageContent]:
	"""Execute a agentyc tool. Returns str for most tools, or a content list for tools with image output."""

	if tool_name == 'browser_list_sessions':
		return await self._list_sessions()

	if tool_name == 'browser_close_session':
		return await self._close_session(arguments['session_id'])

	if tool_name == 'browser_close_all':
		return await self._close_all_sessions()

	if tool_name.startswith('browser_'):
		if not self.browser_session:
			await self._init_browser_session()

		if tool_name == 'browser_navigate':
			return await self._navigate(arguments['url'], arguments.get('new_tab', False))

		if tool_name == 'browser_click':
			return await self._click(
				ref=arguments.get('ref'),
				index=arguments.get('index'),
				coordinate_x=arguments.get('coordinate_x'),
				coordinate_y=arguments.get('coordinate_y'),
				new_tab=arguments.get('new_tab', False),
			)

		if tool_name == 'browser_type':
			return await self._type_text(index=arguments.get('index'), ref=arguments.get('ref'), text=arguments['text'])

		if tool_name == 'browser_upload_file':
			return await self._upload_file(path=arguments['path'], index=arguments.get('index'), ref=arguments.get('ref'))

		if tool_name == 'browser_get_state':
			state_json, screenshot_b64 = await self._get_browser_state(
				include_screenshot=arguments.get('include_screenshot', False),
				mode=arguments.get('mode', 'auto'),
				focus_ref=arguments.get('focus_ref'),
				since_hash=arguments.get('since_hash'),
			)
			content: list[types.TextContent | types.ImageContent] = [types.TextContent(type='text', text=state_json)]
			if screenshot_b64:
				content.append(types.ImageContent(type='image', data=screenshot_b64, mimeType='image/png'))
			return content

		if tool_name == 'browser_get_html':
			return await self._get_html(arguments.get('selector'))

		if tool_name == 'browser_screenshot':
			meta_json, screenshot_b64 = await self._screenshot(arguments.get('full_page', False))
			content: list[types.TextContent | types.ImageContent] = [types.TextContent(type='text', text=meta_json)]
			if screenshot_b64:
				content.append(types.ImageContent(type='image', data=screenshot_b64, mimeType='image/png'))
			return content

		if tool_name == 'browser_extract_content':
			return await self._extract_content(
				arguments['query'],
				arguments.get('extract_links', False),
				arguments.get('output_schema'),
			)

		if tool_name == 'browser_scroll':
			return await self._scroll(
				direction=arguments.get('direction', 'down'),
				pages=arguments.get('pages', 1.0),
				ref=arguments.get('ref'),
				index=arguments.get('index'),
			)

		if tool_name == 'browser_go_back':
			return await self._go_back()

		if tool_name == 'browser_go_forward':
			return await self._go_forward()

		if tool_name == 'browser_refresh':
			return await self._refresh()

		if tool_name == 'browser_press_key':
			return await self._press_key(arguments['key'])

		if tool_name == 'browser_wait':
			return await self._wait(arguments.get('seconds', 2))

		if tool_name == 'browser_evaluate':
			return await self._evaluate(arguments['code'])

		if tool_name == 'browser_select_option':
			return await self._select_option(
				ref=arguments.get('ref'),
				index=arguments.get('index'),
				text=arguments['text'],
			)

		if tool_name == 'browser_get_dropdown_options':
			return await self._get_dropdown_options(
				ref=arguments.get('ref'),
				index=arguments.get('index'),
			)

		if tool_name == 'browser_find_elements':
			return await self._find_elements(
				selector=arguments['selector'],
				attributes=arguments.get('attributes'),
				max_results=arguments.get('max_results', 50),
			)

		if tool_name == 'browser_wait_for_element':
			return await self._wait_for_element(
				text=arguments.get('text'),
				ref=arguments.get('ref'),
				appear=arguments.get('appear', True),
				timeout_seconds=arguments.get('timeout_seconds', 10),
			)

		if tool_name == 'browser_search_page':
			return await self._search_page(
				pattern=arguments['pattern'],
				regex=arguments.get('regex', False),
				max_results=arguments.get('max_results', 25),
			)

		if tool_name == 'browser_get_focused_element':
			return await self._get_focused_element()

		if tool_name == 'browser_list_tabs':
			return await self._list_tabs()

		if tool_name == 'browser_switch_tab':
			return await self._switch_tab(arguments['tab_id'])

		if tool_name == 'browser_close_tab':
			return await self._close_tab(arguments['tab_id'])

		if tool_name == 'browser_hover':
			return await self._hover(
				ref=arguments.get('ref'),
				index=arguments.get('index'),
				coordinate_x=arguments.get('coordinate_x'),
				coordinate_y=arguments.get('coordinate_y'),
			)

		if tool_name == 'browser_double_click':
			return await self._double_click(
				ref=arguments.get('ref'),
				index=arguments.get('index'),
				coordinate_x=arguments.get('coordinate_x'),
				coordinate_y=arguments.get('coordinate_y'),
			)

		if tool_name == 'browser_drag_to':
			return await self._drag_to(
				source_ref=arguments.get('source_ref'),
				target_ref=arguments.get('target_ref'),
				source_x=arguments.get('source_x'),
				source_y=arguments.get('source_y'),
				target_x=arguments.get('target_x'),
				target_y=arguments.get('target_y'),
				steps=arguments.get('steps', 10),
			)

		if tool_name == 'browser_scroll_to_text':
			return await self._scroll_to_text(arguments['text'])

		if tool_name == 'browser_save_state':
			return await self._save_state(path=arguments.get('path'))

		if tool_name == 'browser_load_state':
			return await self._load_state(path=arguments['path'])

		if tool_name == 'browser_wait_for_network_idle':
			return await self._wait_for_network_idle(
				timeout_seconds=arguments.get('timeout_seconds', 10.0),
				idle_duration_ms=arguments.get('idle_duration_ms', 500),
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

		if tool_name == 'browser_get_console_logs':
			return await self._get_console_logs(level=arguments.get('level', 'all'), max_entries=arguments.get('max_entries', 50))

		if tool_name == 'browser_get_network_log':
			return await self._get_network_log(
				type_filter=arguments.get('type_filter', 'all'),
				status_filter=arguments.get('status_filter', 'all'),
				max_entries=arguments.get('max_entries', 50),
			)

	return f'Unknown tool: {tool_name}'
