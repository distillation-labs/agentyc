"""Miscellaneous action-oriented MCP helpers."""

from __future__ import annotations

import asyncio
from typing import Any


async def _scroll(
	self,
	direction: str = 'down',
	pages: float = 1.0,
	ref: str | None = None,
	index: int | None = None,
) -> str:
	"""Scroll the page or a specific element."""
	if not self.browser_session:
		return 'Error: No browser session active'

	self._update_session_activity(self.browser_session.id)
	self._mark_browser_state_cache_dirty()

	payload: dict[str, Any] = {'down': direction == 'down', 'pages': pages}
	if ref is not None or index is not None:
		element, resolved_index, _ = await self._resolve_live_element(index=index, ref=ref)
		if element is None:
			return self._format_action_error(
				f'Element {ref or index} not found for scroll.',
				default_code='stale_ref',
			)
		payload['index'] = resolved_index

	action_result = await self._run_tool_action('scroll', payload)
	if action_result.error:
		return self._format_action_error(action_result.error, default_code='scroll_failed')
	return action_result.extracted_content or f'Scrolled {direction}'


async def _go_back(self) -> str:
	"""Go back in browser history."""
	if not self.browser_session:
		return 'Error: No browser session active'

	from agentyc.browser.events import GoBackEvent

	self._mark_browser_state_cache_dirty()

	event = self.browser_session.event_bus.dispatch(GoBackEvent())
	await event
	await event.event_result(raise_if_any=True, raise_if_none=False)
	after_url = await self.browser_session.get_current_page_url()
	return f'Navigated back to: {after_url}'


async def _go_forward(self) -> str:
	"""Go forward in browser history."""
	if not self.browser_session:
		return 'Error: No browser session active'

	from agentyc.browser.events import GoForwardEvent

	self._mark_browser_state_cache_dirty()

	event = self.browser_session.event_bus.dispatch(GoForwardEvent())
	await event
	await event.event_result(raise_if_any=True, raise_if_none=False)
	after_url = await self.browser_session.get_current_page_url()
	return f'Navigated forward to: {after_url}'


async def _refresh(self) -> str:
	"""Refresh the current page."""
	if not self.browser_session:
		return 'Error: No browser session active'

	from agentyc.browser.events import RefreshEvent

	self._update_session_activity(self.browser_session.id)
	self._mark_browser_state_cache_dirty()
	event = self.browser_session.event_bus.dispatch(RefreshEvent())
	await event
	await event.event_result(raise_if_any=True, raise_if_none=False)
	after_url = await self.browser_session.get_current_page_url()
	return f'Refreshed page: {after_url}'


async def _press_key(self, key: str) -> str:
	"""Send a keyboard key or shortcut."""
	if not self.browser_session:
		return 'Error: No browser session active'

	from agentyc.browser.events import SendKeysEvent

	self._update_session_activity(self.browser_session.id)
	self._mark_browser_state_cache_dirty()
	event = self.browser_session.event_bus.dispatch(SendKeysEvent(keys=key))
	await event
	await event.event_result(raise_if_any=True, raise_if_none=False)
	return f'Pressed key: {key}'


async def _wait(self, seconds: float = 2) -> str:
	"""Wait for a number of seconds (max 30)."""
	actual = min(max(float(seconds), 0), 30)
	await asyncio.sleep(actual)
	return f'Waited {actual:.1f}s'


async def _evaluate(self, code: str) -> str:
	"""Execute JavaScript in the page context."""
	if not self.browser_session:
		return 'Error: No browser session active'

	self._update_session_activity(self.browser_session.id)
	self._mark_browser_state_cache_dirty()
	action_result = await self._run_tool_action('evaluate', {'code': code})
	if action_result.error:
		return self._format_action_error(action_result.error, default_code='evaluate_failed')
	return action_result.extracted_content or 'undefined'


async def _select_option(self, text: str, ref: str | None = None, index: int | None = None) -> str:
	"""Select an option in a <select> or ARIA dropdown."""
	if not self.browser_session:
		return 'Error: No browser session active'
	if ref is None and index is None:
		return 'Error: Provide either ref or index'

	self._update_session_activity(self.browser_session.id)
	self._mark_browser_state_cache_dirty()
	element, resolved_index, drift_recovered = await self._resolve_live_element(index=index, ref=ref)
	if element is None:
		return self._format_action_error(
			f'Element {ref or index} not found. Refresh browser state before retrying.',
			default_code='stale_ref',
		)

	action_result = await self._run_tool_action('select_dropdown', {'index': resolved_index, 'text': text})
	if action_result.error:
		return self._format_action_error(action_result.error, default_code='select_failed')
	msg = action_result.extracted_content or f"Selected '{text}' in element {ref or resolved_index}"
	if drift_recovered:
		msg = f'{msg} (recovered after DOM drift)'
	return msg


async def _get_dropdown_options(self, ref: str | None = None, index: int | None = None) -> str:
	"""Get all options from a dropdown element."""
	if not self.browser_session:
		return 'Error: No browser session active'
	if ref is None and index is None:
		return 'Error: Provide either ref or index'

	self._update_session_activity(self.browser_session.id)
	element, resolved_index, _ = await self._resolve_live_element(index=index, ref=ref)
	if element is None:
		return self._format_action_error(
			f'Element {ref or index} not found. Refresh browser state before retrying.',
			default_code='stale_ref',
		)

	action_result = await self._run_tool_action('dropdown_options', {'index': resolved_index})
	if action_result.error:
		return self._format_action_error(action_result.error, default_code='dropdown_failed')
	return action_result.extracted_content or 'No options found'


async def _find_elements(
	self,
	selector: str,
	attributes: list[str] | None = None,
	max_results: int = 50,
) -> str:
	"""Find elements by CSS selector."""
	if not self.browser_session:
		return 'Error: No browser session active'

	self._update_session_activity(self.browser_session.id)
	payload: dict[str, Any] = {'selector': selector, 'max_results': max_results}
	if attributes:
		payload['attributes'] = attributes

	action_result = await self._run_tool_action('find_elements', payload)
	if action_result.error:
		return self._format_action_error(action_result.error, default_code='find_elements_failed')
	return action_result.extracted_content or 'No elements found'


async def _wait_for_element(
	self,
	text: str | None = None,
	ref: str | None = None,
	appear: bool = True,
	timeout_seconds: float = 10,
) -> str:
	"""Poll until an element matching text or ref appears or disappears."""
	if not self.browser_session:
		return 'Error: No browser session active'
	if not text and not ref:
		return 'Error: Provide either text or ref to wait for'

	timeout = min(max(float(timeout_seconds), 0.5), 30)
	interval = 0.1
	elapsed = 0.0

	from agentyc.mcp.state import parse_element_ref

	ref_index: int | None = None
	if ref:
		try:
			ref_index = parse_element_ref(ref)
		except ValueError as e:
			return f'Error: {e}'

	while elapsed < timeout:
		if ref_index is not None:
			state = await self.browser_session.get_browser_state_summary(include_screenshot=False)
			selector_map = state.dom_state.selector_map
			found = ref_index in selector_map
		else:
			assert text is not None
			found = await self._page_contains_visible_text(text)

		if found == appear:
			verb = 'appeared' if appear else 'disappeared'
			target = ref or f'"{text}"'
			return f'Element {target} {verb} after {elapsed:.1f}s'

		await asyncio.sleep(interval)
		elapsed += interval

	verb = 'appear' if appear else 'disappear'
	target = ref or f'"{text}"'
	return f'Error [timeout]: Element {target} did not {verb} within {timeout:.0f}s'


async def _save_as_pdf(
	self,
	file_name: str | None = None,
	print_background: bool = True,
	landscape: bool = False,
	scale: float = 1.0,
	paper_format: str = 'Letter',
) -> str:
	"""Save the current page as a PDF file."""
	if not self.browser_session:
		return 'Error: No browser session active'

	self._ensure_extract_runtime()
	payload: dict[str, Any] = {
		'print_background': print_background,
		'landscape': landscape,
		'scale': scale,
		'paper_format': paper_format,
	}
	if file_name:
		payload['file_name'] = file_name

	action_result = await self._run_tool_action('save_as_pdf', payload)
	if action_result.error:
		return self._format_action_error(action_result.error, default_code='pdf_failed')
	if action_result.attachments:
		return f'PDF saved: {action_result.attachments[0]}'
	return action_result.extracted_content or 'PDF saved successfully'


async def _search_page(self, pattern: str, regex: bool = False, max_results: int = 25) -> str:
	"""Search for text or regex pattern on the current page."""
	if not self.browser_session:
		return 'Error: No browser session active'

	self._update_session_activity(self.browser_session.id)
	action_result = await self._run_tool_action(
		'search_page',
		{'pattern': pattern, 'regex': regex, 'max_results': max_results, 'context_chars': 150},
	)
	if action_result.error:
		return self._format_action_error(action_result.error, default_code='search_failed')
	return action_result.extracted_content or f'No matches found for: {pattern}'


__all__ = [
	'_evaluate',
	'_find_elements',
	'_get_dropdown_options',
	'_go_back',
	'_go_forward',
	'_press_key',
	'_refresh',
	'_save_as_pdf',
	'_scroll',
	'_search_page',
	'_select_option',
	'_wait',
	'_wait_for_element',
]
