"""Action runtime helpers and action-oriented MCP methods."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, cast


def _ensure_extract_runtime(self) -> None:
	if self.file_system is None:
		from agentyc.filesystem.file_system import FileSystem

		base_dir = self._file_system_base_dir or Path('~/.agentyc-mcp').expanduser()
		self.file_system = FileSystem(base_dir=base_dir)


def _resolve_element_index(self, index: int | None = None, ref: str | None = None) -> int:
	if ref is not None:
		from agentyc.mcp.state import parse_element_ref

		return parse_element_ref(ref)
	if index is None:
		raise ValueError('Provide either ref or index.')
	return index


def _cache_state_payload(self, payload: dict[str, Any]) -> None:
	payload_url = payload.get('url')
	if isinstance(payload_url, str) and payload_url != self._last_state_cache_url:
		self._last_state_elements_by_ref = {}
		self._last_state_cache_url = payload_url
	elements = payload.get('interactive_elements')
	if not isinstance(elements, list) or not elements:
		return
	self._last_state_elements_by_ref.update(
		{str(element['ref']): element for element in elements if isinstance(element, dict) and element.get('ref')}
	)


async def _refresh_selector_map(self) -> None:
	if self.browser_session is None:
		return
	await self.browser_session.get_browser_state_summary(include_screenshot=False)


async def _resolve_live_element(
	self,
	*,
	index: int | None = None,
	ref: str | None = None,
) -> tuple[Any | None, int, bool]:
	if self.browser_session is None:
		raise RuntimeError('No browser session active')

	from agentyc.mcp.state import make_element_ref, summarize_interactive_element

	resolved_index = self._resolve_element_index(index=index, ref=ref)
	if ref is not None and self._last_state_elements_by_ref:
		await self._refresh_selector_map()
	element = await self.browser_session.get_dom_element_by_index(resolved_index)
	if element is not None:
		return element, resolved_index, False

	await self._refresh_selector_map()
	element = await self.browser_session.get_dom_element_by_index(resolved_index)
	if element is not None:
		return element, resolved_index, False

	reference_summary = self._last_state_elements_by_ref.get(make_element_ref(resolved_index))
	if reference_summary is None:
		return None, resolved_index, False

	selector_map = await self.browser_session.get_selector_map()
	best_candidate = None
	best_score = 0
	for candidate in selector_map.values():
		candidate_summary = summarize_interactive_element(candidate)
		score = 0
		strong_match = False
		if reference_summary.get('tag') == candidate_summary.get('tag'):
			score += 1
		for field_name, weight in (('text', 6), ('placeholder', 4), ('href', 5), ('context', 3), ('type', 2)):
			reference_value = str(reference_summary.get(field_name, '')).strip().lower()
			candidate_value = str(candidate_summary.get(field_name, '')).strip().lower()
			if not reference_value or not candidate_value:
				continue
			if reference_value == candidate_value:
				score += weight
				if field_name in {'text', 'placeholder', 'href'}:
					strong_match = True
		if reference_summary.get('disabled') == candidate_summary.get('disabled'):
			score += 1
		if strong_match and score > best_score:
			best_candidate = candidate
			best_score = score

	if best_candidate is None or best_score < 6:
		return None, resolved_index, False
	return best_candidate, best_candidate.backend_node_id, True


def _resolve_upload_available_file_paths(self, path: str) -> list[str]:
	available_file_paths = [path]
	if os.path.exists(path):
		return available_file_paths
	if self.file_system is not None:
		file_obj = self.file_system.get_file(path)
		if file_obj is not None:
			return [str(self.file_system.get_dir() / file_obj.full_name)]
	return available_file_paths


def _validate_actionable_element(self, element: Any, *, action_name: str) -> tuple[str, str] | None:
	if not getattr(element, 'is_visible', True):
		return 'target_not_visible', f'Element <{element.tag_name}> is not visible enough to interact with.'
	attributes = getattr(element, 'attributes', {}) or {}
	if 'disabled' in attributes or str(attributes.get('aria-disabled', '')).strip().lower() == 'true':
		return 'target_disabled', f'Element <{element.tag_name}> is disabled and cannot be used yet.'
	if action_name == 'type':
		tag_name = str(getattr(element, 'tag_name', '') or '').lower()
		is_text_like = tag_name in {'input', 'textarea'} or 'contenteditable' in attributes
		if not is_text_like:
			return 'invalid_target', f'Element <{element.tag_name}> does not accept typed text.'
	return None


def _classify_action_error(self, message: str, *, default_code: str) -> str:
	normalized = message.lower()
	if 'not found' in normalized or 'page may have changed' in normalized or 'stale' in normalized:
		return 'stale_ref'
	if 'disabled' in normalized:
		return 'target_disabled'
	if 'not visible' in normalized or 'interactable or visible' in normalized:
		return 'target_not_visible'
	if 'select' in normalized or 'file input' in normalized:
		return 'invalid_target'
	if 'timeout' in normalized or 'timed out' in normalized:
		return 'navigation_timeout'
	if 'blocked' in normalized or 'overlay' in normalized or 'dialog' in normalized:
		return 'target_blocked'
	if 'connection' in normalized or 'cdp' in normalized:
		return 'browser_connection'
	if 'site unavailable' in normalized or 'err_' in normalized or 'net::' in normalized:
		return 'site_unavailable'
	return default_code


def _format_action_error(self, message: str, *, default_code: str) -> str:
	error_code = self._classify_action_error(message, default_code=default_code)
	return f'Error [{error_code}]: {message}'


async def _run_tool_action(
	self,
	action_name: str,
	payload: dict[str, Any],
	available_file_paths: list[str] | None = None,
) -> Any:
	if not self.browser_session:
		raise RuntimeError('No browser session active')
	if not self.tools:
		raise RuntimeError('Tools not initialized')

	from pydantic import create_model

	from agentyc.actions import ActionModel

	DynamicAction = self._action_model_cache.get(action_name)
	if DynamicAction is None:
		DynamicAction = cast(
			type[ActionModel],
			cast(Any, create_model)(
				f'MCP_{action_name}_Action',
				__base__=ActionModel,
				**{action_name: (dict[str, Any], ...)},
			),
		)
		self._action_model_cache[action_name] = DynamicAction
	action = DynamicAction.model_validate({action_name: payload})
	return await self.tools.act(
		action=action,
		browser_session=self.browser_session,
		page_extraction_llm=None,
		available_file_paths=available_file_paths,
		file_system=self.file_system,
	)


def _inject_extraction_metadata(self, extracted_content: str, metadata: dict[str, Any] | None) -> str:
	if not metadata:
		return extracted_content
	visible_metadata = {
		'route': metadata.get('route') or metadata.get('strategy'),
		'llm_used': bool(metadata.get('llm_used', False)),
		'is_partial': bool(metadata.get('is_partial', False)),
		'structured_extraction': bool(metadata.get('structured_extraction', False)),
		'deterministic_extraction': bool(metadata.get('deterministic_extraction', False)),
	}
	if metadata.get('next_start_char') is not None:
		visible_metadata['next_start_char'] = metadata.get('next_start_char')
	return f'{extracted_content}\n<extraction_metadata>\n{json.dumps(visible_metadata, sort_keys=True)}\n</extraction_metadata>'


async def _navigate(self, url: str, new_tab: bool = False) -> str:
	"""Navigate to a URL."""
	if not self.browser_session:
		return 'Error: No browser session active'

	self._update_session_activity(self.browser_session.id)
	before_tabs = len(await self.browser_session.get_tabs())
	action_result = await self._run_tool_action('navigate', {'url': url, 'new_tab': new_tab})
	if action_result.error:
		return self._format_action_error(action_result.error, default_code='navigation_failed')
	if new_tab:
		after_tabs = len(await self.browser_session.get_tabs())
		if after_tabs <= before_tabs:
			return self._format_action_error(
				f'Navigation to {url} did not open a new tab as requested.',
				default_code='postcondition_failed',
			)
		return f'Opened new tab with URL: {url}'
	if self._cdp_client_for_runtime:
		try:
			_cdp_s = await self.browser_session.get_or_create_cdp_session(target_id=None, focus=False)
			await self._cdp_client_for_runtime.send.Runtime.enable(session_id=_cdp_s.session_id)
		except Exception:
			pass
	after_url = await self.browser_session.get_current_page_url()
	return f'Navigated to: {after_url}'


async def _click(
	self,
	ref: str | None = None,
	index: int | None = None,
	coordinate_x: int | None = None,
	coordinate_y: int | None = None,
	new_tab: bool = False,
) -> str:
	"""Click an element by index or at viewport coordinates."""
	if not self.browser_session:
		return 'Error: No browser session active'

	self._update_session_activity(self.browser_session.id)

	if coordinate_x is not None and coordinate_y is not None:
		action_result = await self._run_tool_action('click', {'coordinate_x': coordinate_x, 'coordinate_y': coordinate_y})
		if action_result.error:
			return self._format_action_error(action_result.error, default_code='click_failed')
		return f'Clicked at coordinates ({coordinate_x}, {coordinate_y})'

	if ref is None and index is None:
		return 'Error: Provide either ref, index, or both coordinate_x and coordinate_y'

	element, resolved_index, drift_recovered = await self._resolve_live_element(index=index, ref=ref)
	if not element:
		return self._format_action_error(
			f'Element with ref/index {ref or resolved_index} was not found. Refresh browser state before retrying.',
			default_code='stale_ref',
		)
	validation_error = self._validate_actionable_element(element, action_name='click')
	if validation_error is not None:
		error_code, error_message = validation_error
		return f'Error [{error_code}]: {error_message}'

	if new_tab:
		href = element.attributes.get('href')
		if href:
			current_url = await self.browser_session.get_current_page_url()
			if href.startswith('/'):
				from urllib.parse import urlparse

				parsed = urlparse(current_url)
				full_url = f'{parsed.scheme}://{parsed.netloc}{href}'
			else:
				full_url = href

			before_tabs = len(await self.browser_session.get_tabs())
			action_result = await self._run_tool_action('navigate', {'url': full_url, 'new_tab': True})
			if action_result.error:
				return self._format_action_error(action_result.error, default_code='click_failed')
			after_tabs = len(await self.browser_session.get_tabs())
			if after_tabs <= before_tabs:
				return self._format_action_error(
					f'Click on element {ref or resolved_index} did not open a new tab.',
					default_code='postcondition_failed',
				)
			return f'Clicked element {ref or resolved_index} and opened in new tab {full_url[:20]}...'
		return self._format_action_error(
			f'Element {ref or resolved_index} does not support opening in a new tab because it has no href target.',
			default_code='invalid_target',
		)

	action_result = await self._run_tool_action('click', {'index': resolved_index})
	if action_result.error:
		return self._format_action_error(action_result.error, default_code='click_failed')
	if drift_recovered:
		return f'Clicked element {ref or resolved_index} (recovered after DOM drift)'
	return f'Clicked element {ref or resolved_index}'


async def _type_text(self, text: str, index: int | None = None, ref: str | None = None) -> str:
	"""Type text into an element."""
	if not self.browser_session:
		return 'Error: No browser session active'

	element, resolved_index, drift_recovered = await self._resolve_live_element(index=index, ref=ref)
	if not element:
		return self._format_action_error(
			f'Element with ref/index {ref or resolved_index} was not found. Refresh browser state before retrying.',
			default_code='stale_ref',
		)
	validation_error = self._validate_actionable_element(element, action_name='type')
	if validation_error is not None:
		error_code, error_message = validation_error
		return f'Error [{error_code}]: {error_message}'

	from agentyc.browser.events import TypeTextEvent

	is_potentially_sensitive = len(text) >= 6 and (
		('@' in text and '.' in text.split('@')[-1] if '@' in text else False)
		or (
			len(text) >= 16
			and any(char.isdigit() for char in text)
			and any(char.isalpha() for char in text)
			and any(char in '.-_' for char in text)
		)
	)

	sensitive_key_name = None
	if is_potentially_sensitive:
		if '@' in text and '.' in text.split('@')[-1]:
			sensitive_key_name = 'email'
		else:
			sensitive_key_name = 'credential'

	event = self.browser_session.event_bus.dispatch(
		TypeTextEvent(node=element, text=text, is_sensitive=is_potentially_sensitive, sensitive_key_name=sensitive_key_name)
	)
	await event
	try:
		input_metadata = await event.event_result(raise_if_any=True, raise_if_none=False)
	except Exception as exc:
		return self._format_action_error(str(exc), default_code='type_failed')

	actual_value = None
	if isinstance(input_metadata, dict):
		actual_value = input_metadata.get('actual_value')
	if actual_value is not None and actual_value != text:
		if is_potentially_sensitive:
			return self._format_action_error(
				f'Element {ref or resolved_index} did not retain the typed sensitive value — the field may be read-only or disabled.',
				default_code='postcondition_failed',
			)
		import re as _re

		_strip = lambda s: _re.sub(r'[^a-zA-Z0-9]', '', s).lower()
		if not (_strip(text) and _strip(text) != _strip(actual_value)):
			pass
		else:
			return self._format_action_error(
				f"Element {ref or resolved_index} ended with value '{actual_value}' after typing '{text}' — the field may have transformed or rejected the input.",
				default_code='postcondition_failed',
			)

	if is_potentially_sensitive:
		if sensitive_key_name:
			prefix = f'Typed <{sensitive_key_name}> into element {ref or resolved_index}'
		else:
			prefix = f'Typed <sensitive> into element {ref or resolved_index}'
	else:
		prefix = f"Typed '{text}' into element {ref or resolved_index}"
	if drift_recovered:
		return f'{prefix} (recovered after DOM drift)'
	return prefix


async def _upload_file(self, path: str, index: int | None = None, ref: str | None = None) -> str:
	"""Upload a file to a file input or nearby upload control."""
	if not self.browser_session:
		return 'Error: No browser session active'
	if not self.tools:
		return 'Error: Tools not initialized'
	if index is None and ref is None:
		return 'Error: Provide either ref or index'

	self._update_session_activity(self.browser_session.id)
	self._ensure_extract_runtime()

	element, resolved_index, drift_recovered = await self._resolve_live_element(index=index, ref=ref)
	if not element:
		return self._format_action_error(
			f'Element with ref/index {ref or resolved_index} was not found. Refresh browser state before retrying.',
			default_code='stale_ref',
		)

	action_result = await self._run_tool_action(
		'upload_file',
		{'index': resolved_index, 'path': path},
		available_file_paths=self._resolve_upload_available_file_paths(path),
	)
	if action_result.error:
		return self._format_action_error(action_result.error, default_code='upload_failed')

	message = action_result.extracted_content or f'Uploaded file {path} to element {ref or resolved_index}'
	if drift_recovered:
		message = f'{message} (recovered after DOM drift)'
	return message


async def _get_browser_state(
	self,
	include_screenshot: bool = False,
	mode: str = 'auto',
	focus_ref: str | None = None,
	since_hash: str | None = None,
) -> tuple[str, str | None]:
	"""Get current browser state. Returns (state_json, screenshot_b64 | None)."""
	if not self.browser_session:
		return 'Error: No browser session active', None

	from agentyc.mcp.state import build_browser_state_payload, make_element_ref

	async def _fetch_state_payload(resolved_focus_ref: str | None) -> tuple[Any, dict[str, Any]]:
		state = await self.browser_session.get_browser_state_summary(include_screenshot=include_screenshot)
		try:
			result = build_browser_state_payload(
				state,
				mode=mode,
				focus_ref=resolved_focus_ref,
				since_hash=since_hash,
			)
		except ValueError:
			if mode != 'focus' or resolved_focus_ref is None:
				raise
			element, resolved_index, _ = await self._resolve_live_element(ref=resolved_focus_ref)
			if element is None:
				raise
			recovered_focus_ref = make_element_ref(resolved_index)
			state = await self.browser_session.get_browser_state_summary(include_screenshot=include_screenshot)
			result = build_browser_state_payload(
				state,
				mode=mode,
				focus_ref=recovered_focus_ref,
				since_hash=since_hash,
			)
		return state, result

	state, result = await _fetch_state_payload(focus_ref)

	current_tab = result.get('current_tab') if isinstance(result, dict) else None
	current_tab_has_ownership = isinstance(current_tab, dict) and isinstance(current_tab.get('ownership'), dict)
	has_interactive_elements = bool(result.get('interactive_elements')) if isinstance(result, dict) else False
	is_live_http_page = str(getattr(state, 'url', '') or '').startswith(('http://', 'https://'))
	if (not current_tab_has_ownership and len(getattr(state, 'tabs', []) or []) > 1) or (is_live_http_page and not has_interactive_elements):
		await asyncio.sleep(0.1)
		state, result = await _fetch_state_payload(focus_ref)

	screenshot_b64 = None
	if include_screenshot and state.screenshot:
		screenshot_b64 = state.screenshot
		if state.page_info:
			result['screenshot_dimensions'] = {
				'width': state.page_info.viewport_width,
				'height': state.page_info.viewport_height,
			}

	self._cache_state_payload(result)
	return json.dumps(result, indent=2), screenshot_b64


async def _extract_content(
	self,
	query: str,
	extract_links: bool = False,
	output_schema: dict[str, Any] | None = None,
) -> str:
	"""Extract content from current page."""
	if not self.browser_session:
		return 'Error: No browser session active'

	if not self.tools:
		return 'Error: Tools not initialized'

	self._ensure_extract_runtime()
	if not self.file_system:
		return 'Error: FileSystem not initialized'

	from pydantic import create_model

	from agentyc.actions import ActionModel

	ExtractAction = create_model(
		'ExtractAction',
		__base__=ActionModel,
		extract=dict[str, Any],
	)

	action = ExtractAction.model_validate(
		{
			'extract': {'query': query, 'extract_links': extract_links, 'output_schema': output_schema},
		}
	)
	action_result = await self.tools.act(
		action=action,
		browser_session=self.browser_session,
		page_extraction_llm=None,
		file_system=self.file_system,
	)
	if action_result.error:
		return self._format_action_error(action_result.error, default_code='extraction_failed')
	extracted_content = action_result.extracted_content or 'No content extracted'
	return self._inject_extraction_metadata(extracted_content, action_result.metadata)


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
	interval = 0.5
	elapsed = 0.0

	from agentyc.mcp.state import parse_element_ref

	ref_index: int | None = None
	if ref:
		try:
			ref_index = parse_element_ref(ref)
		except ValueError as e:
			return f'Error: {e}'

	while elapsed < timeout:
		state = await self.browser_session.get_browser_state_summary(include_screenshot=False)
		selector_map = state.dom_state.selector_map

		if ref_index is not None:
			found = ref_index in selector_map
		else:
			assert text is not None
			needle = text.lower()
			found = any(needle in (element.get_meaningful_text_for_llm() or '').lower() for element in selector_map.values())

		if found == appear:
			verb = 'appeared' if appear else 'disappeared'
			target = ref or f'"{text}"'
			return f'Element {target} {verb} after {elapsed:.1f}s'

		await asyncio.sleep(interval)
		elapsed += interval

	verb = 'appear' if appear else 'disappear'
	target = ref or f'"{text}"'
	return f'Error [timeout]: Element {target} did not {verb} within {timeout:.0f}s'


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
