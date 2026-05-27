"""Debug bundle composition and precise network wait helpers."""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any

_MAX_DEBUG_PENDING_REQUESTS = 5
_DEFAULT_BODY_PREVIEW_BYTES = 2048


def _isoformat_timestamp(timestamp: float | None) -> str | None:
	if timestamp is None:
		return None
	try:
		return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(timestamp))
	except (OverflowError, OSError, ValueError):
		return None


def _network_entry_started_since(entry: dict[str, Any], baseline_started_at: float) -> bool:
	observed_at = entry.get('observed_at')
	if observed_at is not None:
		try:
			return float(observed_at) >= baseline_started_at
		except (TypeError, ValueError):
			return True
	start_time = entry.get('start_time')
	if start_time is None:
		return True
	try:
		return float(start_time) >= baseline_started_at
	except (TypeError, ValueError):
		return True


def _network_entry_sort_key(entry: dict[str, Any]) -> tuple[float, float, float, float]:
	def _coerce(key: str) -> float:
		value = entry.get(key)
		if value is None:
			return 0.0
		try:
			return float(value)
		except (TypeError, ValueError):
			return 0.0

	return (
		_coerce('observed_at'),
		_coerce('response_at'),
		_coerce('finished_at'),
		_coerce('start_time'),
	)


def _serialize_network_entry(entry: dict[str, Any], *, include_headers: bool = False) -> dict[str, Any]:
	payload = {}
	for key, value in entry.items():
		if value is None:
			continue
		if key in {
			'request_id',
			'start_time',
			'observed_at',
			'response_at',
			'finished_at',
			'response_body_bytes',
			'response_body_base64',
		}:
			continue
		if not include_headers and key in {'req_headers', 'resp_headers'}:
			continue
		payload[key] = value
	return payload


def _truncate_text_bytes(text: str, *, max_bytes: int) -> tuple[str, bool]:
	encoded = text.encode('utf-8')
	if len(encoded) <= max_bytes:
		return text, False
	truncated = encoded[:max_bytes].decode('utf-8', errors='replace')
	return truncated, True


def _decode_entry_body(
	entry: dict[str, Any],
	*,
	body_key: str,
	base64_key: str,
	max_body_bytes: int,
	decode_json: bool,
) -> dict[str, Any] | None:
	body_text = entry.get(body_key)
	if isinstance(body_text, str):
		preview, truncated = _truncate_text_bytes(body_text, max_bytes=max_body_bytes)
		payload: dict[str, Any] = {
			'text': preview,
			'truncated': truncated,
			'encoding': 'utf-8',
			'size_bytes': len(body_text.encode('utf-8')),
		}
		if decode_json:
			try:
				payload['json'] = json.loads(preview)
			except Exception:
				pass
		return payload
	body_b64 = entry.get(base64_key)
	if not isinstance(body_b64, str) or not body_b64:
		return None
	try:
		import base64

		decoded = base64.b64decode(body_b64)
	except Exception:
		return {'base64': body_b64, 'truncated': False, 'encoding': 'base64'}
	truncated = len(decoded) > max_body_bytes
	preview_bytes = decoded[:max_body_bytes]
	try:
		preview_text = preview_bytes.decode('utf-8')
		payload = {
			'text': preview_text,
			'truncated': truncated,
			'encoding': 'utf-8',
			'size_bytes': len(decoded),
		}
		if decode_json:
			try:
				payload['json'] = json.loads(preview_text)
			except Exception:
				pass
		return payload
	except UnicodeDecodeError:
		import base64

		return {
			'base64': base64.b64encode(preview_bytes).decode('ascii'),
			'truncated': truncated,
			'encoding': 'base64',
			'size_bytes': len(decoded),
		}


def _entry_target_tab_id(entry: dict[str, Any]) -> str | None:
	target_id = entry.get('target_id')
	if not target_id:
		return None
	return str(target_id)[-4:]


def _build_inspected_network_entry(
	entry: dict[str, Any],
	*,
	include_headers: bool,
	include_request_body: bool,
	include_response_body: bool,
	max_body_bytes: int,
	decode_json: bool,
) -> dict[str, Any]:
	payload = _serialize_network_entry(entry, include_headers=include_headers)
	tab_id = _entry_target_tab_id(entry)
	if tab_id is not None:
		payload['target_tab_id'] = tab_id
	payload['request_id'] = entry.get('request_id')
	if include_request_body:
		request_body = _decode_entry_body(
			entry,
			body_key='post_data',
			base64_key='post_data_base64',
			max_body_bytes=max_body_bytes,
			decode_json=decode_json,
		)
		if request_body is not None:
			payload['request_body'] = request_body
	if include_response_body:
		response_body = _decode_entry_body(
			entry,
			body_key='response_body_text',
			base64_key='response_body_base64',
			max_body_bytes=max_body_bytes,
			decode_json=decode_json,
		)
		if response_body is not None:
			payload['response_body'] = response_body
	return payload


def _network_entry_matches(
	entry: dict[str, Any],
	*,
	url_substring: str | None,
	url_regex: re.Pattern[str] | None,
	method: str | None,
	resource_type: str | None,
	status: int | None = None,
) -> bool:
	url = str(entry.get('url') or '')
	if url_substring and url_substring not in url:
		return False
	if url_regex and url_regex.search(url) is None:
		return False
	if method and str(entry.get('method') or '').upper() != method:
		return False
	if resource_type and str(entry.get('type') or '').lower() != resource_type:
		return False
	if status is not None and entry.get('status') != status:
		return False
	return True


def _network_entry_has_response(entry: dict[str, Any]) -> bool:
	return entry.get('status') is not None or entry.get('error') is not None


def _describe_network_match(
	*,
	url_substring: str | None,
	url_regex: str | None,
	method: str | None,
	resource_type: str | None,
	status: int | None = None,
) -> str:
	parts = []
	if url_substring:
		parts.append(f'url_substring={url_substring!r}')
	if url_regex:
		parts.append(f'url_regex={url_regex!r}')
	if method:
		parts.append(f'method={method!r}')
	if resource_type:
		parts.append(f'resource_type={resource_type!r}')
	if status is not None:
		parts.append(f'status={status}')
	return ', '.join(parts)


def _compile_network_regex(url_regex: str | None) -> re.Pattern[str] | None:
	if url_regex is None:
		return None
	try:
		return re.compile(url_regex)
	except re.error as exc:
		raise ValueError(f'Invalid url_regex: {exc}') from exc


def _coerce_timeout(timeout_seconds: float) -> float:
	try:
		return min(max(float(timeout_seconds), 0.1), 30.0)
	except (TypeError, ValueError):
		return 10.0


def _build_trace_summary(self) -> dict[str, Any]:
	trace_summary: dict[str, Any] = {
		'active': bool(getattr(self, '_trace_active', False)),
		'event_count': len(getattr(self, '_trace_events', [])),
	}
	categories = getattr(self, '_trace_categories', None)
	if categories:
		trace_summary['categories'] = categories
	started_at = _isoformat_timestamp(getattr(self, '_trace_started_at', None))
	if started_at is not None:
		trace_summary['started_at'] = started_at
	last_completed = getattr(self, '_last_trace_summary', None)
	if isinstance(last_completed, dict):
		completed_payload = dict(last_completed)
		for key in ('started_at', 'stopped_at'):
			if key in completed_payload:
				completed_payload[key] = _isoformat_timestamp(completed_payload.get(key))
		trace_summary['last_completed'] = completed_payload
	return trace_summary


async def _wait_for_request(
	self,
	*,
	url_substring: str | None = None,
	url_regex: str | None = None,
	method: str | None = None,
	resource_type: str | None = None,
	timeout_seconds: float = 10.0,
	include_headers: bool = False,
) -> str:
	"""Wait for a network request matching URL and optional filters."""
	if not self.browser_session:
		return 'Error: No browser session active'
	if not url_substring and not url_regex:
		return self._format_action_error('Provide url_substring or url_regex.', default_code='invalid_argument')
	self._update_session_activity(self.browser_session.id)
	try:
		pattern = _compile_network_regex(url_regex)
	except ValueError as exc:
		return self._format_action_error(str(exc), default_code='invalid_argument')
	if not self._cdp_events_registered:
		try:
			await self._register_cdp_event_listeners()
		except Exception as exc:
			return self._format_action_error(str(exc), default_code='action_failed')
	timeout = _coerce_timeout(timeout_seconds)
	method_filter = method.upper() if method else None
	resource_type_filter = resource_type.lower() if resource_type else None
	baseline_started_at = time.time()
	deadline = time.monotonic() + timeout
	while time.monotonic() < deadline:
		pending_matches = [
			entry
			for entry in self._network_pending.values()
			if _network_entry_started_since(entry, baseline_started_at)
			and _network_entry_matches(
				entry,
				url_substring=url_substring,
				url_regex=pattern,
				method=method_filter,
				resource_type=resource_type_filter,
			)
		]
		if pending_matches:
			pending_matches.sort(key=_network_entry_sort_key)
			return json.dumps(_serialize_network_entry(pending_matches[0], include_headers=include_headers))
		completed_matches = [
			entry
			for entry in self._network_log_buffer
			if _network_entry_started_since(entry, baseline_started_at)
			and _network_entry_matches(
				entry,
				url_substring=url_substring,
				url_regex=pattern,
				method=method_filter,
				resource_type=resource_type_filter,
			)
		]
		if completed_matches:
			completed_matches.sort(key=_network_entry_sort_key)
			return json.dumps(_serialize_network_entry(completed_matches[0], include_headers=include_headers))
		await asyncio.sleep(0.05)
	match_description = _describe_network_match(
		url_substring=url_substring,
		url_regex=url_regex,
		method=method_filter,
		resource_type=resource_type_filter,
	)
	return f'Error [timeout]: Timed out after {timeout:.1f}s waiting for request ({match_description})'


async def _wait_for_response(
	self,
	*,
	url_substring: str | None = None,
	url_regex: str | None = None,
	method: str | None = None,
	resource_type: str | None = None,
	status: int | None = None,
	timeout_seconds: float = 10.0,
	include_headers: bool = False,
) -> str:
	"""Wait for a network response matching URL and optional filters."""
	if not self.browser_session:
		return 'Error: No browser session active'
	if not url_substring and not url_regex:
		return self._format_action_error('Provide url_substring or url_regex.', default_code='invalid_argument')
	self._update_session_activity(self.browser_session.id)
	try:
		pattern = _compile_network_regex(url_regex)
	except ValueError as exc:
		return self._format_action_error(str(exc), default_code='invalid_argument')
	if not self._cdp_events_registered:
		try:
			await self._register_cdp_event_listeners()
		except Exception as exc:
			return self._format_action_error(str(exc), default_code='action_failed')
	timeout = _coerce_timeout(timeout_seconds)
	method_filter = method.upper() if method else None
	resource_type_filter = resource_type.lower() if resource_type else None
	baseline_started_at = time.time()
	deadline = time.monotonic() + timeout
	while time.monotonic() < deadline:
		pending_matches = [
			entry
			for entry in self._network_pending.values()
			if _network_entry_started_since(entry, baseline_started_at)
			and _network_entry_has_response(entry)
			and _network_entry_matches(
				entry,
				url_substring=url_substring,
				url_regex=pattern,
				method=method_filter,
				resource_type=resource_type_filter,
				status=status,
			)
		]
		if pending_matches:
			pending_matches.sort(key=_network_entry_sort_key)
			return json.dumps(_serialize_network_entry(pending_matches[0], include_headers=include_headers))
		completed_matches = [
			entry
			for entry in self._network_log_buffer
			if _network_entry_started_since(entry, baseline_started_at)
			and _network_entry_matches(
				entry,
				url_substring=url_substring,
				url_regex=pattern,
				method=method_filter,
				resource_type=resource_type_filter,
				status=status,
			)
		]
		if completed_matches:
			completed_matches.sort(key=_network_entry_sort_key)
			return json.dumps(_serialize_network_entry(completed_matches[0], include_headers=include_headers))
		await asyncio.sleep(0.05)
	match_description = _describe_network_match(
		url_substring=url_substring,
		url_regex=url_regex,
		method=method_filter,
		resource_type=resource_type_filter,
		status=status,
	)
	return f'Error [timeout]: Timed out after {timeout:.1f}s waiting for response ({match_description})'


async def _export_debug_bundle(
	self,
	*,
	state_mode: str = 'min',
	focus_ref: str | None = None,
	since_hash: str | None = None,
	include_screenshot: bool = False,
	include_headers: bool = False,
	include_html: bool = False,
	html_selector: str | None = None,
	console_max_entries: int = 20,
	network_max_entries: int = 20,
	network_status_filter: str = 'all',
) -> tuple[str, str | None]:
	"""Return a compact debug bundle with state, logs, and optional screenshot."""
	if not self.browser_session:
		return 'Error: No browser session active', None
	self._update_session_activity(self.browser_session.id)
	self._mark_browser_state_cache_dirty()
	if not self._cdp_events_registered:
		try:
			await self._register_cdp_event_listeners()
		except Exception as exc:
			return self._format_action_error(str(exc), default_code='action_failed'), None
	state_json, screenshot_b64 = await self._get_browser_state(
		include_screenshot=include_screenshot,
		mode=state_mode,
		focus_ref=focus_ref,
		since_hash=since_hash,
		include_recent_events=True,
	)
	try:
		state_payload = json.loads(state_json)
	except json.JSONDecodeError:
		return state_json, screenshot_b64
	console_payload = await self._get_console_logs(max_entries=max(console_max_entries, 1))
	network_payload = await self._get_network_log(
		status_filter=network_status_filter,
		max_entries=max(network_max_entries, 1),
		include_headers=include_headers,
	)
	try:
		console_logs = json.loads(console_payload)
		network_log = json.loads(network_payload)
	except json.JSONDecodeError as exc:
		return self._format_action_error(str(exc), default_code='action_failed'), screenshot_b64
	pending_entries = sorted(self._network_pending.values(), key=_network_entry_sort_key)
	pending_requests = [
		_serialize_network_entry(entry, include_headers=include_headers)
		for entry in pending_entries[-_MAX_DEBUG_PENDING_REQUESTS:]
	]
	bundle: dict[str, Any] = {
		'generated_at': _isoformat_timestamp(time.time()),
		'state': state_payload,
		'console_logs': console_logs,
		'network_log': network_log,
		'pending_requests': pending_requests,
		'trace': _build_trace_summary(self),
		'summary': {
			'console_error_count': sum(1 for entry in console_logs if entry.get('level') == 'error'),
			'console_warn_count': sum(1 for entry in console_logs if entry.get('level') == 'warn'),
			'network_error_count': sum(1 for entry in network_log if entry.get('error') or int(entry.get('status') or 0) >= 400),
			'pending_request_count': len(self._network_pending),
			'screenshot_included': screenshot_b64 is not None,
		},
	}
	if include_html or html_selector is not None:
		bundle['html'] = await self._get_html(html_selector)
	return json.dumps(bundle), screenshot_b64
