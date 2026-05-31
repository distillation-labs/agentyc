"""Interaction-focused browser tool dispatch helpers."""

from __future__ import annotations

from typing import Any


async def _dispatch_interaction_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any | None:
	if tool_name == 'browser_navigate':
		return await self._navigate(arguments['url'], arguments.get('new_tab', False))

	if tool_name == 'browser_click':
		return await self._click(
			ref=arguments.get('ref'),
			index=arguments.get('index'),
			label=arguments.get('label'),
			coordinate_x=arguments.get('coordinate_x'),
			coordinate_y=arguments.get('coordinate_y'),
			new_tab=arguments.get('new_tab', False),
			wait_for_download=arguments.get('wait_for_download', False),
			wait_for_tab=arguments.get('wait_for_tab', False),
			wait_for_url_substring=arguments.get('wait_for_url_substring'),
			wait_for_url_regex=arguments.get('wait_for_url_regex'),
			wait_for_request=arguments.get('wait_for_request'),
			wait_for_response=arguments.get('wait_for_response'),
			expected_download_name=arguments.get('expected_download_name'),
			download_timeout_seconds=arguments.get('download_timeout_seconds', 10.0),
			expected_tab_url_substring=arguments.get('expected_tab_url_substring'),
			tab_timeout_seconds=arguments.get('tab_timeout_seconds', 10.0),
			url_timeout_seconds=arguments.get('url_timeout_seconds', 10.0),
		)

	if tool_name == 'browser_type':
		return await self._type_text(
			index=arguments.get('index'),
			ref=arguments.get('ref'),
			label=arguments.get('label'),
			text=arguments['text'],
		)

	if tool_name == 'browser_fill_form':
		return await self._fill_form(arguments['fields'])

	if tool_name == 'browser_upload_file':
		return await self._upload_file(
			path=arguments['path'],
			index=arguments.get('index'),
			ref=arguments.get('ref'),
			label=arguments.get('label'),
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
			label=arguments.get('label'),
			text=arguments['text'],
		)

	if tool_name == 'browser_get_dropdown_options':
		return await self._get_dropdown_options(
			ref=arguments.get('ref'),
			index=arguments.get('index'),
			label=arguments.get('label'),
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

	if tool_name == 'browser_wait_for_url':
		return await self._wait_for_url(
			url_substring=arguments.get('url_substring'),
			url_regex=arguments.get('url_regex'),
			timeout_seconds=arguments.get('timeout_seconds', 10.0),
		)

	if tool_name == 'browser_search_page':
		return await self._search_page(
			pattern=arguments['pattern'],
			regex=arguments.get('regex', False),
			max_results=arguments.get('max_results', 25),
		)

	if tool_name == 'browser_get_focused_element':
		return await self._get_focused_element()

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

	return None


__all__ = ['_dispatch_interaction_tool']
