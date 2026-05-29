"""MCP helpers for network inspection, frames, and storage tooling."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, cast

from agentyc.mcp.debug_tools import _DEFAULT_BODY_PREVIEW_BYTES, _build_inspected_network_entry, _network_entry_matches
from agentyc.mcp.network_replay import _build_replay_request_expression

if TYPE_CHECKING:
	from agentyc.browser.session import BrowserSession

_MAX_CAPTURED_BODY_BYTES = 64 * 1024
_NO_BROWSER_SESSION_ERROR = 'Error: No browser session active'
_STORAGE_TYPES = ('localStorage', 'sessionStorage')
_VALID_STORAGE_TYPES = frozenset(_STORAGE_TYPES)


def _require_browser_session(self) -> tuple[BrowserSession | None, str | None]:
	browser_session = self.browser_session
	if browser_session is None:
		return None, _NO_BROWSER_SESSION_ERROR
	self._update_session_activity(browser_session.id)
	return browser_session, None


def _invalid_storage_type_error(self) -> str:
	return self._format_action_error('storage_type must be localStorage or sessionStorage.', default_code='invalid_argument')


def _build_frame_entry(frame_id: str, frame_info: dict[str, Any]) -> dict[str, Any]:
	entry: dict[str, Any] = {
		'frame_id': frame_id,
		'url': frame_info.get('url', ''),
		'name': frame_info.get('name') or '',
		'is_cross_origin': bool(frame_info.get('isCrossOrigin')),
	}
	parent_frame_id = frame_info.get('parentFrameId')
	if parent_frame_id:
		entry['parent_frame_id'] = parent_frame_id
	target_id = frame_info.get('frameTargetId')
	if target_id:
		entry['target_tab_id'] = str(target_id)[-4:]
	backend_node_id = frame_info.get('backendNodeId')
	if backend_node_id is not None:
		entry['backend_node_id'] = backend_node_id
	return entry


def _filter_storage_values(values: list[dict[str, Any]], *, key: str | None = None) -> list[dict[str, Any]]:
	if key is None:
		return values
	return [entry for entry in values if entry.get('name') == key]


def _filter_storage_origins(
	origins: list[dict[str, Any]],
	*,
	storage_type: str | None = None,
	key: str | None = None,
) -> list[dict[str, Any]]:
	if storage_type is not None:
		filtered_origins = []
		for item in origins:
			values = _filter_storage_values(list(item.get(storage_type) or []), key=key)
			if values:
				filtered_origins.append({'origin': item.get('origin'), storage_type: values})
		return filtered_origins

	if key is None:
		return origins

	filtered_origins = []
	for item in origins:
		payload: dict[str, Any] = {'origin': item.get('origin')}
		for current_storage_type in _STORAGE_TYPES:
			values = _filter_storage_values(list(item.get(current_storage_type) or []), key=key)
			if values:
				payload[current_storage_type] = values
		if len(payload) > 1:
			filtered_origins.append(payload)
	return filtered_origins


def _build_origin_guarded_script(origin: str, *, script_body: str) -> str:
	return (
		'(function(){'
		f'if (window.location.origin !== {json.dumps(origin)}) return {{ok:false,error:"origin_mismatch",actual_origin:window.location.origin}};'
		f'{script_body}'
		'})()'
	)


def _build_set_storage_script(origin: str, *, storage_type: str, key: str, value: str) -> str:
	return _build_origin_guarded_script(
		origin,
		script_body=(
			f'window.{storage_type}.setItem({json.dumps(key)}, {json.dumps(value)});'
			f'return {{ok:true,storage_type:{json.dumps(storage_type)},key:{json.dumps(key)}}};'
		),
	)


def _build_clear_storage_script(origin: str, *, storage_type: str | None = None, key: str | None = None) -> str:
	if storage_type is None:
		script_body = 'window.localStorage.clear();window.sessionStorage.clear();return {ok:true,storage_type:"all"};'
	elif key is None:
		script_body = f'window.{storage_type}.clear();return {{ok:true,storage_type:{json.dumps(storage_type)}}};'
	else:
		script_body = (
			f'window.{storage_type}.removeItem({json.dumps(key)});'
			f'return {{ok:true,storage_type:{json.dumps(storage_type)},key:{json.dumps(key)}}};'
		)
	return _build_origin_guarded_script(origin, script_body=script_body)


def _format_storage_action_error(self, payload: Any, *, origin: str, action: str) -> str:
	if isinstance(payload, dict):
		if payload.get('error') == 'origin_mismatch':
			actual_origin = payload.get('actual_origin') or 'unknown'
			return self._format_action_error(
				f'Page origin {actual_origin} does not match requested origin {origin} for {action}.',
				default_code='invalid_argument',
			)
		error_text = payload.get('error')
		if error_text:
			return self._format_action_error(
				f'Failed to {action} for origin {origin}: {error_text}',
				default_code='action_failed',
			)
	return self._format_action_error(f'Failed to {action} for origin {origin}', default_code='action_failed')


async def _evaluate_current_page_expression(
	browser_session: BrowserSession,
	*,
	expression: str,
	await_promise: bool = False,
) -> Any:
	cdp_session = await browser_session.get_or_create_cdp_session(target_id=None, focus=False)
	params: Any = {
		'expression': expression,
		'returnByValue': True,
	}
	if await_promise:
		params['awaitPromise'] = True
	return cast(
		Any,
		await cdp_session.cdp_client.send.Runtime.evaluate(
			params=params,
			session_id=cdp_session.session_id,
		),
	)


async def _run_browser_session_json(
	self,
	*,
	runner: Callable[..., Awaitable[Any]],
	default_code: str,
	**kwargs: Any,
) -> str:
	try:
		payload = await runner(**kwargs)
	except Exception as exc:
		return self._format_action_error(str(exc), default_code=default_code)
	return json.dumps(payload)


async def _inspect_network_entry(
	self,
	*,
	request_id: str | None = None,
	url_substring: str | None = None,
	url_regex: str | None = None,
	method: str | None = None,
	resource_type: str | None = None,
	status: int | None = None,
	include_headers: bool = False,
	include_request_body: bool = True,
	include_response_body: bool = True,
	max_body_bytes: int = _DEFAULT_BODY_PREVIEW_BYTES,
	decode_json: bool = True,
) -> str:
	"""Inspect one captured network entry with optional request/response bodies."""
	_, error = _require_browser_session(self)
	if error:
		return error
	if not self._cdp_events_registered:
		try:
			await self._register_cdp_event_listeners()
		except Exception as exc:
			return self._format_action_error(str(exc), default_code='action_failed')
	compiled_regex = None
	if url_regex:
		import re

		try:
			compiled_regex = re.compile(url_regex)
		except re.error as exc:
			return self._format_action_error(f'Invalid url_regex: {exc}', default_code='invalid_argument')
	method_filter = method.upper() if method else None
	resource_type_filter = resource_type.lower() if resource_type else None
	entries = list(self._network_pending.values()) + list(self._network_log_buffer)
	match = None
	if request_id:
		match = next((entry for entry in reversed(entries) if entry.get('request_id') == request_id), None)
	else:
		if not url_substring and compiled_regex is None:
			return self._format_action_error(
				'Provide request_id, url_substring, or url_regex.',
				default_code='invalid_argument',
			)
		for entry in reversed(entries):
			if _network_entry_matches(
				entry,
				url_substring=url_substring,
				url_regex=compiled_regex,
				method=method_filter,
				resource_type=resource_type_filter,
				status=status,
			):
				match = entry
				break
	if match is None:
		return self._format_action_error('No matching network entry found.', default_code='not_found')
	return json.dumps(
		_build_inspected_network_entry(
			match,
			include_headers=include_headers,
			include_request_body=include_request_body,
			include_response_body=include_response_body,
			max_body_bytes=max(max_body_bytes, 1),
			decode_json=decode_json,
		)
	)


async def _list_frames(self) -> str:
	"""List frames visible to the current browser session."""
	browser_session, error = _require_browser_session(self)
	if error:
		return error
	assert browser_session is not None
	all_frames, _ = await browser_session.get_all_frames(include_backend_node_ids=False)
	return json.dumps([_build_frame_entry(frame_id, frame_info) for frame_id, frame_info in all_frames.items()])


async def _get_frame_html(self, frame_id: str) -> str:
	"""Get raw HTML for a specific frame."""
	browser_session, error = _require_browser_session(self)
	if error:
		return error
	assert browser_session is not None
	all_frames, target_sessions = await browser_session.get_all_frames(include_backend_node_ids=False)
	frame_info = all_frames.get(frame_id)
	if frame_info is None:
		return self._format_action_error(f'Unknown frame_id: {frame_id}', default_code='not_found')
	cdp_session = await browser_session.cdp_client_for_frame(
		frame_id,
		all_frames=all_frames,
		target_sessions=target_sessions,
	)
	params: Any = {'frameId': frame_id, 'worldName': 'agentyc-frame-html'}
	world = cast(
		Any,
		await cdp_session.cdp_client.send.Page.createIsolatedWorld(params=params, session_id=cdp_session.session_id),
	)
	context_id = world.get('executionContextId')
	if not context_id:
		return self._format_action_error('Could not resolve frame execution context.', default_code='action_failed')
	result = await cdp_session.cdp_client.send.Runtime.evaluate(
		params={
			'expression': 'document.documentElement ? document.documentElement.outerHTML : null',
			'returnByValue': True,
			'contextId': context_id,
		},
		session_id=cdp_session.session_id,
	)
	html = result.get('result', {}).get('value')
	if html is None:
		return self._format_action_error(f'Could not get HTML for frame {frame_id}', default_code='action_failed')
	return str(html)


async def _get_storage(self, origin: str | None = None, storage_type: str | None = None, key: str | None = None) -> str:
	"""Inspect current browser storage state by origin and storage type."""
	browser_session, error = _require_browser_session(self)
	if error:
		return error
	assert browser_session is not None
	if storage_type is not None and storage_type not in _VALID_STORAGE_TYPES:
		return _invalid_storage_type_error(self)
	origins = await browser_session._cdp_get_origins()
	if origin is not None:
		origins = [item for item in origins if item.get('origin') == origin]
	return json.dumps(_filter_storage_origins(origins, storage_type=storage_type, key=key))


async def _set_storage(self, origin: str, storage_type: str, key: str, value: str) -> str:
	"""Set one storage key for the current origin-scoped page context."""
	browser_session, error = _require_browser_session(self)
	if error:
		return error
	assert browser_session is not None
	if storage_type not in _VALID_STORAGE_TYPES:
		return _invalid_storage_type_error(self)
	result = await _evaluate_current_page_expression(
		browser_session,
		expression=_build_set_storage_script(origin, storage_type=storage_type, key=key, value=value),
	)
	exception_details = result.get('exceptionDetails')
	if exception_details:
		message = exception_details.get('text') or 'Failed to evaluate storage update.'
		return self._format_action_error(str(message), default_code='action_failed')
	payload = result.get('result', {}).get('value')
	if isinstance(payload, dict) and payload.get('ok'):
		return json.dumps(payload)
	return _format_storage_action_error(self, payload, origin=origin, action='set storage')


async def _clear_storage(self, origin: str, storage_type: str | None = None, key: str | None = None) -> str:
	"""Clear storage for the current origin-scoped page context."""
	browser_session, error = _require_browser_session(self)
	if error:
		return error
	assert browser_session is not None
	if storage_type is not None and storage_type not in _VALID_STORAGE_TYPES:
		return _invalid_storage_type_error(self)
	result = await _evaluate_current_page_expression(
		browser_session,
		expression=_build_clear_storage_script(origin, storage_type=storage_type, key=key),
	)
	exception_details = result.get('exceptionDetails')
	if exception_details:
		message = exception_details.get('text') or 'Failed to evaluate storage clear operation.'
		return self._format_action_error(str(message), default_code='action_failed')
	payload = result.get('result', {}).get('value')
	if isinstance(payload, dict) and payload.get('ok'):
		return json.dumps(payload)
	return _format_storage_action_error(self, payload, origin=origin, action='clear storage')


async def _add_network_mock(
	self,
	*,
	url_substring: str | None = None,
	url_regex: str | None = None,
	method: str | None = None,
	resource_type: str | None = None,
	action: str = 'fulfill',
	status: int = 200,
	headers: dict[str, Any] | None = None,
	body: str = '',
	error_reason: str = 'Failed',
) -> str:
	browser_session, error = _require_browser_session(self)
	if error:
		return error
	assert browser_session is not None
	return await _run_browser_session_json(
		self,
		runner=browser_session.add_network_mock,
		default_code='invalid_argument',
		url_substring=url_substring,
		url_regex=url_regex,
		method=method,
		resource_type=resource_type,
		action=action,
		status=status,
		headers=headers,
		body=body,
		error_reason=error_reason,
	)


async def _remove_network_mock(self, mock_id: str | None = None) -> str:
	browser_session, error = _require_browser_session(self)
	if error:
		return error
	assert browser_session is not None
	return await _run_browser_session_json(
		self,
		runner=browser_session.remove_network_mock,
		default_code='action_failed',
		mock_id=mock_id,
	)


async def _list_network_mocks(self) -> str:
	browser_session, error = _require_browser_session(self)
	if error:
		return error
	assert browser_session is not None
	return json.dumps(browser_session.list_network_mocks())


async def _set_network_conditions(
	self,
	*,
	offline: bool = False,
	latency_ms: float = 0.0,
	download_kbps: float | None = None,
	upload_kbps: float | None = None,
	connection_type: str | None = None,
	reset: bool = False,
) -> str:
	browser_session, error = _require_browser_session(self)
	if error:
		return error
	assert browser_session is not None
	return await _run_browser_session_json(
		self,
		runner=browser_session.set_network_conditions,
		default_code='invalid_argument',
		offline=offline,
		latency_ms=latency_ms,
		download_kbps=download_kbps,
		upload_kbps=upload_kbps,
		connection_type=connection_type,
		reset=reset,
	)


async def _get_network_conditions(self) -> str:
	browser_session, error = _require_browser_session(self)
	if error:
		return error
	assert browser_session is not None
	return json.dumps(browser_session.get_network_conditions())


async def _replay_request(
	self,
	*,
	request_id: str | None = None,
	url_substring: str | None = None,
	url_regex: str | None = None,
	method: str | None = None,
	body: str | None = None,
	headers: dict[str, Any] | None = None,
) -> str:
	browser_session, error = _require_browser_session(self)
	if error:
		return error
	assert browser_session is not None
	match_json = await _inspect_network_entry(
		self,
		request_id=request_id,
		url_substring=url_substring,
		url_regex=url_regex,
		method=method,
		include_headers=True,
		include_request_body=True,
		include_response_body=False,
		max_body_bytes=_MAX_CAPTURED_BODY_BYTES,
		decode_json=False,
	)
	if match_json.startswith('Error'):
		return match_json
	match = json.loads(match_json)
	request_url = str(match.get('url') or '')
	if not request_url:
		return self._format_action_error('Matched request is missing a replayable URL.', default_code='action_failed')
	request_method = str(method or match.get('method') or 'GET').upper()
	request_headers = browser_session.sanitize_replay_headers(headers or match.get('req_headers') or {})
	request_body = body
	if request_body is None:
		request_body = (
			(match.get('request_body') or {}).get('text') if isinstance(match.get('request_body'), dict) else None
		) or ''
	result = await _evaluate_current_page_expression(
		browser_session,
		expression=_build_replay_request_expression(
			request_url=request_url,
			request_method=request_method,
			request_headers=request_headers,
			request_body=request_body,
		),
		await_promise=True,
	)
	payload_text = result.get('result', {}).get('value')
	if isinstance(payload_text, str):
		return payload_text
	exception_details = result.get('exceptionDetails')
	if exception_details:
		message = exception_details.get('text') or 'Replay request failed during page evaluation.'
		return self._format_action_error(str(message), default_code='action_failed')
	return self._format_action_error('Replay request returned no result.', default_code='action_failed')
