from __future__ import annotations

import time
from typing import Any

import mcp.types as types

from agentyc.browser.hud_stream import HudEvent, HudEventKind, HudStream

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
_HUD_CHATTTY_TOOLS = {
	'browser_find_elements',
	'browser_get_attribute',
	'browser_get_dropdown_options',
	'browser_get_focused_element',
	'browser_get_html',
	'browser_get_state',
	'browser_search_page',
}
_HUD_CHATTTY_DURATION_MS = 500.0


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
		return 'Clicking page element'
	if tool_name == 'browser_set_intent':
		return 'Updating live intent'
	if tool_name == 'browser_type':
		return 'Typing into focused field'
	if tool_name == 'browser_wait_for_element':
		return 'Waiting for page element to change'
	if tool_name == 'browser_wait_for_network_idle':
		return 'Waiting for network to go idle'
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
	"""Send an MCP log message notification for a tool action."""
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
		await ctx.session.send_log_message(
			level=level,
			data=message,
			logger='agentyc',
		)
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
				types.TextContent(
					type='text',
					text=item.text,
					annotations=item.annotations,
					_meta=merged_meta,
				)
			)
			attached = True
		else:
			updated_content.append(item)
	if not attached:
		updated_content.insert(
			0,
			types.TextContent(type='text', text='', _meta=metadata),
		)
	return updated_content


def _should_publish_hud_event(kind: HudEventKind, tool_name: str, duration_ms: float | None, error: str | None) -> bool:
	if error:
		return True
	if tool_name not in _HUD_CHATTTY_TOOLS:
		return True
	if kind == 'tool_start':
		return False
	return duration_ms is not None and duration_ms >= _HUD_CHATTTY_DURATION_MS


def _publish_hud_event(
	self,
	kind: HudEventKind,
	tool_name: str,
	arguments: dict[str, Any],
	*,
	duration: float | None = None,
	error: str | None = None,
) -> None:
	if not tool_name.startswith('browser_'):
		return
	if tool_name == 'browser_set_intent':
		return
	session_id = getattr(getattr(self, 'browser_session', None), 'id', None)
	if session_id is None:
		return
	duration_ms = round(duration * 1000, 1) if duration is not None else None
	if not _should_publish_hud_event(kind, tool_name, duration_ms, error):
		return
	label = self._tool_phase_message(tool_name, arguments)
	if kind == 'tool_error' and error:
		label = f'{label} failed'
	HudStream.get().publish(
		HudEvent(
			kind=kind,
			label=label,
			session_id=session_id,
			tool_name=tool_name,
			duration_ms=duration_ms,
			error=error,
		)
	)


__all__ = [
	'_attach_tool_result_metadata',
	'_extract_tool_error_message',
	'_publish_hud_event',
	'_send_log_notification',
	'_should_log',
	'_should_publish_hud_event',
	'_tool_output_is_error',
	'_tool_phase_message',
	'_tool_text_is_error',
]
