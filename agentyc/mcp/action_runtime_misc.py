"""Miscellaneous action-oriented MCP helpers."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from agentyc.mcp.action_runtime_targeting import _resolve_target_by_label


def _download_entry(path: str) -> dict[str, Any]:
	download_path = Path(path)
	return {
		'path': str(download_path),
		'name': download_path.name,
		'size_bytes': download_path.stat().st_size if download_path.exists() else 0,
	}


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


async def _wait_for_download(self, expected_name: str | None = None, timeout_seconds: float = 10.0) -> str:
	"""Wait until a download is available in the current session and return its metadata."""
	if not self.browser_session:
		return 'Error: No browser session active'

	self._update_session_activity(self.browser_session.id)
	timeout = min(max(float(timeout_seconds), 0.5), 60.0)
	expected = str(expected_name).strip() if expected_name else None
	loop = asyncio.get_running_loop()
	deadline = loop.time() + timeout
	existing_entries = [_download_entry(path) for path in self.browser_session.downloaded_files]
	if expected:
		for entry in existing_entries:
			if entry['name'] == expected:
				return json.dumps(entry)
	elif existing_entries:
		return json.dumps(existing_entries[-1])

	watchdog = getattr(self.browser_session, '_downloads_watchdog', None)
	if watchdog is not None and hasattr(watchdog, 'register_download_callbacks'):
		future: asyncio.Future[dict[str, Any]] = loop.create_future()

		def _on_complete(info: Any) -> None:
			if not isinstance(info, dict):
				return
			path = str(info.get('path') or '')
			if not path:
				return
			entry = _download_entry(path)
			if expected and entry['name'] != expected:
				return
			if future.done():
				return
			loop.call_soon_threadsafe(future.set_result, entry)

		watchdog.register_download_callbacks(on_complete=_on_complete)
		try:
			remaining = max(0.0, deadline - loop.time())
			if remaining > 0:
				return json.dumps(await asyncio.wait_for(future, timeout=remaining))
		except TimeoutError:
			pass
		finally:
			watchdog.unregister_download_callbacks(on_complete=_on_complete)

	seen_paths = {entry['path'] for entry in existing_entries}
	while loop.time() < deadline:
		entries = [_download_entry(path) for path in self.browser_session.downloaded_files]
		if expected:
			match = next((entry for entry in entries if entry['name'] == expected), None)
		else:
			match = next((entry for entry in reversed(entries) if entry['path'] not in seen_paths), None)
			if match is None and entries and not seen_paths:
				match = entries[-1]
		if match is not None:
			return json.dumps(match)
		await asyncio.sleep(0.1)

	name_hint = f' {expected!r}' if expected else ''
	return self._format_action_error(
		f'No download{name_hint} completed within {timeout:.1f}s.',
		default_code='timeout',
	)


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


async def _select_option(
	self,
	text: str,
	ref: str | None = None,
	index: int | None = None,
	label: str | None = None,
) -> str:
	"""Select an option in a <select> or ARIA dropdown."""
	if not self.browser_session:
		return 'Error: No browser session active'
	resolved_label = label.strip() if isinstance(label, str) else None
	if ref is None and index is None and resolved_label:
		try:
			ref, index, resolved_label = await _resolve_target_by_label(
				self,
				label=resolved_label,
				operation='option_text',
				error_prefix='Dropdown target',
			)
		except ValueError as error:
			return f'Error [invalid_argument]: {error}'
	if ref is None and index is None:
		return 'Error: Provide either ref, index, or label'

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
	msg = action_result.extracted_content or f"Selected '{text}' in element {resolved_label or ref or resolved_index}"
	if drift_recovered:
		msg = f'{msg} (recovered after DOM drift)'
	return msg


async def _get_dropdown_options(self, ref: str | None = None, index: int | None = None, label: str | None = None) -> str:
	"""Get all options from a dropdown element."""
	if not self.browser_session:
		return 'Error: No browser session active'
	resolved_label = label.strip() if isinstance(label, str) else None
	if ref is None and index is None and resolved_label:
		try:
			ref, index, resolved_label = await _resolve_target_by_label(
				self,
				label=resolved_label,
				operation='option_text',
				error_prefix='Dropdown target',
			)
		except ValueError as error:
			return f'Error [invalid_argument]: {error}'
	if ref is None and index is None:
		return 'Error: Provide either ref, index, or label'

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


async def _wait_for_url(
	self, url_substring: str | None = None, url_regex: str | None = None, timeout_seconds: float = 10.0
) -> str:
	"""Poll until the current page URL matches the requested substring or regex."""
	if not self.browser_session:
		return 'Error: No browser session active'
	if not url_substring and not url_regex:
		return 'Error: Provide either url_substring or url_regex to wait for'
	if url_substring and url_regex:
		return 'Error [invalid_argument]: Provide only one of url_substring or url_regex'

	self._update_session_activity(self.browser_session.id)
	timeout = min(max(float(timeout_seconds), 0.5), 60.0)
	interval = 0.05
	elapsed = 0.0
	regex = None
	if url_regex:
		import re

		try:
			regex = re.compile(url_regex)
		except re.error as error:
			return f'Error [invalid_argument]: Invalid url_regex: {error}'

	while elapsed < timeout:
		current_url = await self.browser_session.get_current_page_url()
		page = await self.browser_session.get_current_page()
		if page is not None:
			try:
				live_url = await page.evaluate('() => window.location.href')
			except RuntimeError:
				live_url = ''
			if live_url:
				current_url = live_url
		matched = (url_substring in current_url) if url_substring else bool(regex and regex.search(current_url))
		if matched:
			return f'URL matched after {elapsed:.1f}s: {current_url}'
		await asyncio.sleep(interval)
		elapsed += interval

	target = f'"{url_substring}"' if url_substring else f'/{url_regex}/'
	return f'Error [timeout]: URL did not match {target} within {timeout:.0f}s'


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
	'_wait_for_download',
	'_wait_for_element',
	'_wait_for_url',
]
