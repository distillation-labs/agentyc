from __future__ import annotations

import time
from typing import Any

import mcp.types as types

_LOG_LEVEL_RANK = {
	'debug': 0,
	'info': 1,
	'notice': 2,
	'warning': 3,
	'error': 4,
	'critical': 5,
	'alert': 6,
	'emergency': 7,
}


def _tool_phase_message(self, tool_name: str, arguments: dict[str, Any]) -> str:
	if tool_name == 'browser_navigate':
		return f'Navigating to {arguments.get("url", "page")}'
	if tool_name == 'browser_get_state':
		mode = arguments.get('mode', 'auto')
		if arguments.get('since_hash'):
			return f'Checking page state delta ({mode})'
		return f'Reading page state ({mode})'
	if tool_name == 'browser_click':
		if arguments.get('new_tab'):
			return 'Opening link in new tab'
		if arguments.get('wait_for_download'):
			return 'Clicking and waiting for download'
		if arguments.get('wait_for_tab'):
			return 'Clicking and waiting for new tab'
		if arguments.get('wait_for_url_substring') or arguments.get('wait_for_url_regex'):
			return 'Clicking and waiting for URL change'
		if arguments.get('wait_for_request'):
			return 'Clicking and waiting for request'
		if arguments.get('wait_for_response'):
			return 'Clicking and waiting for response'
		return 'Clicking page element'
	if tool_name == 'browser_set_intent':
		return 'Updating live intent'
	if tool_name == 'browser_type':
		return 'Typing into focused field'
	if tool_name == 'browser_fill_form':
		return 'Filling form fields'
	if tool_name == 'browser_screenshot':
		return 'Capturing screenshot'
	if tool_name == 'browser_extract_content':
		return 'Extracting structured page content'
	if tool_name == 'browser_switch_tab':
		return 'Switching browser tab'
	if tool_name == 'browser_close_tab':
		return 'Closing browser tab'
	return f'Running {tool_name}'


def _should_log(self, level: types.LoggingLevel) -> bool:
	try:
		return _LOG_LEVEL_RANK.get(level, 99) >= _LOG_LEVEL_RANK.get(self._min_log_level, 1)
	except Exception:
		return True


async def _send_log_notification(
	self,
	level: types.LoggingLevel,
	tool_name: str,
	arguments: dict[str, Any],
	*,
	duration: float | None = None,
	completed: bool = False,
	error: str | None = None,
) -> None:
	if not self._should_log(level):
		return
	try:
		message = self._tool_phase_message(tool_name, arguments)
		if error:
			message = f'{message} — Error: {error}'
		elif completed and duration is not None:
			ms = round(duration * 1000)
			message = f'{message} — done ({ms}ms)'
		ctx = self.server.request_context
		await ctx.session.send_log_message(level=level, data=message, logger='agentyc')
	except Exception:
		pass


def _tool_text_is_error(self, text: str) -> bool:
	return text.startswith('Error:') or text.startswith('Error [')


def _tool_output_is_error(self, content: list[types.TextContent | types.ImageContent]) -> bool:
	for item in content:
		if isinstance(item, types.TextContent) and item.text:
			return self._tool_text_is_error(item.text)
	return False


def _extract_tool_error_message(content: list[types.TextContent | types.ImageContent]) -> str | None:
	for item in content:
		if isinstance(item, types.TextContent) and item.text:
			return item.text
	return None


def _attach_tool_result_metadata(
	self,
	*,
	name: str,
	arguments: dict[str, Any],
	content: list[types.TextContent | types.ImageContent],
	started_at: float,
	is_error: bool,
) -> list[types.TextContent | types.ImageContent]:
	duration_ms = round((time.time() - started_at) * 1000, 1)
	phase_message = self._tool_phase_message(name, arguments)
	metadata = {
		'agentyc/tool_name': name,
		'agentyc/tool_phase': phase_message,
		'agentyc/browser_duration_ms': duration_ms,
		'agentyc/is_error': is_error,
	}
	updated_content: list[types.TextContent | types.ImageContent] = []
	attached = False
	for item in content:
		if not attached and isinstance(item, types.TextContent):
			merged_meta = dict(getattr(item, 'meta', None) or {})
			merged_meta.update(metadata)
			updated_content.append(
				types.TextContent(type='text', text=item.text, annotations=item.annotations, _meta=merged_meta)
			)
			attached = True
		else:
			updated_content.append(item)
	if not attached:
		updated_content.insert(0, types.TextContent(type='text', text='', _meta=metadata))
	return updated_content


def _publish_hud_event(self, kind: str, tool_name: str, arguments: dict[str, Any], *, duration: float | None = None, error: str | None = None) -> None:
	pass  # HUD removed


__all__ = [
	'_attach_tool_result_metadata',
	'_extract_tool_error_message',
	'_publish_hud_event',
	'_send_log_notification',
	'_should_log',
	'_tool_output_is_error',
	'_tool_phase_message',
	'_tool_text_is_error',
]
