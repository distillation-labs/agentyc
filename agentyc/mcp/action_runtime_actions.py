"""Navigation, click, type, upload, and extraction runtime helpers."""

from __future__ import annotations

import time
from typing import Any, cast

from agentyc.mcp.action_runtime_click_waits import (
	_finalize_click_wait_result,
)
from agentyc.mcp.action_runtime_helpers import _inject_extraction_metadata, _new_tab_postcondition_satisfied
from agentyc.mcp.action_runtime_targeting import _resolve_target_by_label
from agentyc.mcp.navigation_runtime import _element_triggers_form_navigation


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


async def _navigate(self, url: str, new_tab: bool = False) -> str:
	"""Navigate to a URL."""
	if not self.browser_session:
		return 'Error: No browser session active'

	self._update_session_activity(self.browser_session.id)
	self._mark_browser_state_cache_dirty()
	before_tabs = await self.browser_session.get_tabs() if new_tab else None
	before_focus_target_id = self.browser_session.agent_focus_target_id if new_tab else None
	action_result = await self._run_tool_action('navigate', {'url': url, 'new_tab': new_tab})
	if action_result.error:
		return self._format_action_error(action_result.error, default_code='navigation_failed')
	if new_tab:
		assert before_tabs is not None
		if not self._new_tab_postcondition_satisfied(
			before_tabs=before_tabs,
			before_focus_target_id=before_focus_target_id,
		):
			return self._format_action_error(
				f'Navigation to {url} did not open a new tab as requested.',
				default_code='postcondition_failed',
			)
		return f'Opened new tab with URL: {url}'
	if self._cdp_client_for_runtime:
		try:
			_cdp_s = await self.browser_session.get_or_create_cdp_session(target_id=None, focus=False)
			await self._cdp_client_for_runtime.send.Runtime.enable(session_id=_cdp_s.session_id)
			await self._cdp_client_for_runtime.send.Network.enable(session_id=_cdp_s.session_id)
		except Exception:
			pass
	from agentyc.mcp.state import truncate_text

	after_url = await self.browser_session.get_current_page_url()
	after_title = await self.browser_session.get_current_page_title()
	title_hint = f' | "{truncate_text(after_title, 60)}"' if after_title and after_title != 'Unknown page title' else ''
	return f'Navigated to: {after_url}{title_hint}'


async def _click(
	self,
	ref: str | None = None,
	index: int | None = None,
	label: str | None = None,
	coordinate_x: int | None = None,
	coordinate_y: int | None = None,
	new_tab: bool = False,
	wait_for_download: bool = False,
	wait_for_tab: bool = False,
	wait_for_url_substring: str | None = None,
	wait_for_url_regex: str | None = None,
	wait_for_request: dict[str, object] | None = None,
	wait_for_response: dict[str, object] | None = None,
	expected_download_name: str | None = None,
	download_timeout_seconds: float = 10.0,
	expected_tab_url_substring: str | None = None,
	tab_timeout_seconds: float = 10.0,
	url_timeout_seconds: float = 10.0,
) -> str:
	"""Click an element by index or at viewport coordinates."""
	if not self.browser_session:
		return 'Error: No browser session active'
	url_wait_requested = bool(wait_for_url_substring or wait_for_url_regex)
	request_wait_requested = wait_for_request is not None
	response_wait_requested = wait_for_response is not None
	if wait_for_download and new_tab:
		return 'Error [invalid_argument]: browser_click cannot wait for a download while opening a new tab'
	if wait_for_download and wait_for_tab:
		return 'Error [invalid_argument]: browser_click cannot wait for a download and a new tab at the same time'
	if wait_for_download and url_wait_requested:
		return 'Error [invalid_argument]: browser_click cannot wait for a download and a URL change at the same time'
	if wait_for_download and request_wait_requested:
		return 'Error [invalid_argument]: browser_click cannot wait for a download and a request at the same time'
	if wait_for_download and response_wait_requested:
		return 'Error [invalid_argument]: browser_click cannot wait for a download and a response at the same time'
	if wait_for_tab and new_tab:
		return 'Error [invalid_argument]: browser_click cannot use wait_for_tab together with new_tab'
	if wait_for_tab and url_wait_requested:
		return 'Error [invalid_argument]: browser_click cannot wait for a new tab and a URL change at the same time'
	if wait_for_tab and request_wait_requested:
		return 'Error [invalid_argument]: browser_click cannot wait for a new tab and a request at the same time'
	if wait_for_tab and response_wait_requested:
		return 'Error [invalid_argument]: browser_click cannot wait for a new tab and a response at the same time'
	if url_wait_requested and new_tab:
		return 'Error [invalid_argument]: browser_click cannot use URL waiting together with new_tab'
	if url_wait_requested and request_wait_requested:
		return 'Error [invalid_argument]: browser_click cannot wait for a URL change and a request at the same time'
	if url_wait_requested and response_wait_requested:
		return 'Error [invalid_argument]: browser_click cannot wait for a URL change and a response at the same time'
	if request_wait_requested and new_tab:
		return 'Error [invalid_argument]: browser_click cannot use request waiting together with new_tab'
	if request_wait_requested and response_wait_requested:
		return 'Error [invalid_argument]: browser_click cannot wait for a request and a response at the same time'
	if wait_for_url_substring and wait_for_url_regex:
		return 'Error [invalid_argument]: browser_click accepts only one of wait_for_url_substring or wait_for_url_regex'

	self._update_session_activity(self.browser_session.id)
	self._mark_browser_state_cache_dirty()
	network_wait_started_at: float | None = None
	if request_wait_requested or response_wait_requested:
		if not self._cdp_events_registered:
			try:
				await self._register_cdp_event_listeners()
			except Exception as exc:
				return self._format_action_error(str(exc), default_code='action_failed')
		network_wait_started_at = time.time()

	if coordinate_x is not None and coordinate_y is not None:
		before_tabs = await self.browser_session.get_tabs() if wait_for_tab else []
		action_result = await self._run_tool_action('click', {'coordinate_x': coordinate_x, 'coordinate_y': coordinate_y})
		if action_result.error:
			return self._format_action_error(action_result.error, default_code='click_failed')
		base_msg = f'Clicked at coordinates ({coordinate_x}, {coordinate_y})'
		return await _finalize_click_wait_result(
			self,
			base_msg=base_msg,
			wait_for_download=wait_for_download,
			expected_download_name=expected_download_name,
			download_timeout_seconds=download_timeout_seconds,
			wait_for_tab=wait_for_tab,
			before_target_ids={tab.target_id for tab in before_tabs},
			expected_tab_url_substring=expected_tab_url_substring,
			tab_timeout_seconds=tab_timeout_seconds,
			wait_for_url_substring=wait_for_url_substring,
			wait_for_url_regex=wait_for_url_regex,
			url_timeout_seconds=url_timeout_seconds,
			wait_for_request=wait_for_request,
			wait_for_response=wait_for_response,
			network_wait_started_at=network_wait_started_at,
		)

	resolved_label = label.strip() if isinstance(label, str) else None
	if ref is None and index is None and resolved_label:
		try:
			ref, index, resolved_label = await _resolve_target_by_label(
				self,
				label=resolved_label,
				operation='click',
				error_prefix='Element',
			)
		except ValueError as error:
			return f'Error [invalid_argument]: {error}'
	if ref is None and index is None:
		return 'Error: Provide ref, index, label, or both coordinate_x and coordinate_y'

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

			before_tabs = await self.browser_session.get_tabs()
			before_focus_target_id = self.browser_session.agent_focus_target_id
			action_result = await self._run_tool_action('navigate', {'url': full_url, 'new_tab': True})
			if action_result.error:
				return self._format_action_error(action_result.error, default_code='click_failed')
			if not self._new_tab_postcondition_satisfied(
				before_tabs=before_tabs,
				before_focus_target_id=before_focus_target_id,
			):
				return self._format_action_error(
					f'Click on element {ref or resolved_index} did not open a new tab.',
					default_code='postcondition_failed',
				)
			target_label = resolved_label or str(ref or resolved_index)
			return f'Clicked element {target_label} and opened in new tab {full_url[:20]}...'
		return self._format_action_error(
			f'Element {resolved_label or ref or resolved_index} does not support opening in a new tab because it has no href target.',
			default_code='invalid_target',
		)

	pre_click_url = await self.browser_session.get_current_page_url()
	before_tabs = await self.browser_session.get_tabs() if wait_for_tab else []
	action_result = await self._run_tool_action('click', {'index': resolved_index})
	if action_result.error:
		return self._format_action_error(action_result.error, default_code='click_failed')
	after_url = await self.browser_session.get_current_page_url()
	target_label = resolved_label or str(ref or resolved_index)
	base_msg = f'Clicked element {target_label}'
	if drift_recovered:
		base_msg += ' (recovered after DOM drift)'
	wait_for_submit_navigation = _element_triggers_form_navigation(element)
	if wait_for_submit_navigation:
		try:
			settled_url = await self._wait_for_click_navigation_settle(pre_click_url=pre_click_url)
		except Exception as error:
			return self._format_action_error(
				f'Click triggered navigation that did not settle: {error}',
				default_code='click_failed',
			)
		if settled_url:
			after_url = settled_url
	if after_url and after_url != pre_click_url:
		recovery_error = await self._recover_click_navigation_if_unavailable(target_url=after_url)
		if recovery_error is not None:
			return self._format_action_error(recovery_error, default_code='click_failed')
		from agentyc.mcp.state import truncate_text

		after_title = await self.browser_session.get_current_page_title()
		title_hint = f' | "{truncate_text(after_title, 60)}"' if after_title and after_title != 'Unknown page title' else ''
		base_msg = f'{base_msg} → {after_url}{title_hint}'
	return await _finalize_click_wait_result(
		self,
		base_msg=base_msg,
		wait_for_download=wait_for_download,
		expected_download_name=expected_download_name,
		download_timeout_seconds=download_timeout_seconds,
		wait_for_tab=wait_for_tab,
		before_target_ids={tab.target_id for tab in before_tabs},
		expected_tab_url_substring=expected_tab_url_substring,
		tab_timeout_seconds=tab_timeout_seconds,
		wait_for_url_substring=wait_for_url_substring,
		wait_for_url_regex=wait_for_url_regex,
		url_timeout_seconds=url_timeout_seconds,
		wait_for_request=wait_for_request,
		wait_for_response=wait_for_response,
		network_wait_started_at=network_wait_started_at,
	)


async def _type_text(self, text: str, index: int | None = None, ref: str | None = None, label: str | None = None) -> str:
	"""Type text into an element."""
	if not self.browser_session:
		return 'Error: No browser session active'
	self._mark_browser_state_cache_dirty()

	resolved_label = label.strip() if isinstance(label, str) else None
	if ref is None and index is None and resolved_label:
		try:
			ref, index, resolved_label = await _resolve_target_by_label(
				self,
				label=resolved_label,
				operation='text',
				error_prefix='Type target',
			)
		except ValueError as error:
			return f'Error [invalid_argument]: {error}'

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


async def _upload_file(
	self,
	path: str,
	index: int | None = None,
	ref: str | None = None,
	label: str | None = None,
) -> str:
	"""Upload a file to a file input or nearby upload control."""
	if not self.browser_session:
		return 'Error: No browser session active'
	if not self.tools:
		return 'Error: Tools not initialized'
	resolved_label = label.strip() if isinstance(label, str) else None
	if index is None and ref is None and resolved_label:
		try:
			ref, index, resolved_label = await _resolve_target_by_label(
				self,
				label=resolved_label,
				operation='path',
				error_prefix='Upload target',
			)
		except ValueError as error:
			return f'Error [invalid_argument]: {error}'
	if index is None and ref is None:
		return 'Error: Provide either ref, index, or label'

	self._update_session_activity(self.browser_session.id)
	self._mark_browser_state_cache_dirty()
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

	message = action_result.extracted_content or f'Uploaded file {path} to element {resolved_label or ref or resolved_index}'
	if drift_recovered:
		message = f'{message} (recovered after DOM drift)'
	return message


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


__all__ = [
	'_click',
	'_extract_content',
	'_inject_extraction_metadata',
	'_navigate',
	'_new_tab_postcondition_satisfied',
	'_run_tool_action',
	'_type_text',
	'_upload_file',
]
