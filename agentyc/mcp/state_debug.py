"""Debug payload helpers for MCP state serialization."""

from __future__ import annotations

import json
from typing import Any, Literal

from agentyc.browser.views import BrowserStateSummary
from agentyc.mcp.state_compaction import truncate_text
from agentyc.mcp.state_refs import make_element_ref

StateMode = Literal['auto', 'full', 'min', 'focus']

_MAX_DEBUG_ERRORS = 5
_MAX_DEBUG_PENDING_REQUESTS = 5
_MAX_DEBUG_RECENT_EVENTS = 5
_MAX_DEBUG_POPUP_MESSAGES = 5
_IGNORED_RECENT_EVENT_TYPES = {'BrowserStateRequestEvent'}


def _build_unchanged_state_payload(
	*,
	state: BrowserStateSummary,
	mode: StateMode,
	effective_mode: Literal['full', 'min', 'focus'],
	state_hash: str,
	focus_index: int | None,
	current_tab: dict[str, Any] | None,
	serialized_current_tab_id: str | None,
	interactive_element_count: int,
	debug_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
	result: dict[str, Any] = {
		'url': state.url,
		'title': state.title,
		'mode': mode,
		'effective_mode': effective_mode,
		'state_hash': state_hash,
		'changed': False,
		'interactive_element_count': interactive_element_count,
		'interactive_elements': [],
	}
	if serialized_current_tab_id is not None:
		result['current_tab_id'] = serialized_current_tab_id
	elif current_tab is not None and 'tab_id' in current_tab:
		result['current_tab_id'] = current_tab['tab_id']
	if focus_index is not None:
		result['focus_ref'] = make_element_ref(focus_index)
	# Always include scroll position so agents know where they are even when elements haven't changed
	if state.page_info and (state.page_info.scroll_x != 0 or state.page_info.scroll_y != 0):
		result['scroll'] = {'x': state.page_info.scroll_x, 'y': state.page_info.scroll_y}
	if debug_payload is not None:
		result['debug'] = debug_payload
	else:
		built_debug = _build_debug_payload(state)
		if built_debug is not None:
			result['debug'] = built_debug
	return result


def _truncate_debug_list(items: list[str], *, max_items: int, max_length: int = 200) -> tuple[list[str], int]:
	trimmed = [truncate_text(str(item), max_length=max_length) for item in items[:max_items]]
	return trimmed, max(0, len(items) - len(trimmed))


def _serialize_recent_events(recent_events: str | None) -> tuple[list[dict[str, Any]], int]:
	if not recent_events:
		return [], 0
	try:
		parsed = json.loads(recent_events)
	except Exception:
		return [], 0
	if not isinstance(parsed, list):
		return [], 0
	serialized: list[dict[str, Any]] = []
	seen_signatures: set[tuple[str, str, str, str]] = set()
	retained_count = 0
	for item in parsed:
		if not isinstance(item, dict):
			continue
		event_type = truncate_text(str(item.get('event_type') or ''), max_length=120)
		if not event_type or event_type in _IGNORED_RECENT_EVENT_TYPES:
			continue
		url = truncate_text(str(item.get('url') or ''), max_length=160)
		target_id = truncate_text(str(item.get('target_id') or ''), max_length=160)
		error_message = truncate_text(str(item.get('error_message') or ''), max_length=200)
		signature = (event_type, url, target_id, error_message)
		if signature in seen_signatures:
			continue
		seen_signatures.add(signature)
		entry: dict[str, Any] = {}
		entry['event_type'] = event_type
		timestamp = item.get('timestamp')
		if timestamp:
			entry['timestamp'] = truncate_text(str(timestamp), max_length=160)
		if url:
			entry['url'] = url
		if target_id:
			entry['target_id'] = target_id
		if error_message:
			entry['error_message'] = error_message
		if entry:
			retained_count += 1
			if len(serialized) < _MAX_DEBUG_RECENT_EVENTS:
				serialized.append(entry)
	return serialized, max(0, retained_count - len(serialized))


def _serialize_pending_requests(pending_requests: list[Any]) -> tuple[list[dict[str, Any]], int]:
	serialized: list[dict[str, Any]] = []
	for request in pending_requests[:_MAX_DEBUG_PENDING_REQUESTS]:
		entry: dict[str, Any] = {
			'url': truncate_text(str(getattr(request, 'url', '')), max_length=200),
			'method': getattr(request, 'method', 'GET') or 'GET',
			'loading_duration_ms': round(float(getattr(request, 'loading_duration_ms', 0.0) or 0.0), 1),
		}
		resource_type = getattr(request, 'resource_type', None)
		if resource_type:
			entry['resource_type'] = resource_type
		serialized.append(entry)
	return serialized, max(0, len(pending_requests) - len(serialized))


def _build_debug_payload(state: BrowserStateSummary) -> dict[str, Any] | None:
	return _build_debug_payload_with_options(state, include_recent_events=True)


def _build_debug_payload_with_options(
	state: BrowserStateSummary,
	*,
	include_recent_events: bool = True,
) -> dict[str, Any] | None:
	debug: dict[str, Any] = {}
	browser_errors = list(getattr(state, 'browser_errors', []) or [])
	pending_network_requests = list(getattr(state, 'pending_network_requests', []) or [])
	recent_events_raw = getattr(state, 'recent_events', None)
	closed_popup_messages = list(getattr(state, 'closed_popup_messages', []) or [])

	if browser_errors:
		errors, truncated = _truncate_debug_list(browser_errors, max_items=_MAX_DEBUG_ERRORS)
		debug['browser_errors'] = errors
		if truncated:
			debug['browser_errors_remaining'] = truncated

	if pending_network_requests:
		pending, truncated = _serialize_pending_requests(pending_network_requests)
		if pending:
			debug['pending_network_requests'] = pending
			if truncated:
				debug['pending_network_requests_remaining'] = truncated

	if include_recent_events and recent_events_raw:
		recent_events, truncated = _serialize_recent_events(recent_events_raw)
		if recent_events:
			debug['recent_events'] = recent_events
			if truncated:
				debug['recent_events_remaining'] = truncated

	if closed_popup_messages:
		popup_messages, truncated = _truncate_debug_list(
			closed_popup_messages,
			max_items=_MAX_DEBUG_POPUP_MESSAGES,
			max_length=160,
		)
		debug['closed_popup_messages'] = popup_messages
		if truncated:
			debug['closed_popup_messages_remaining'] = truncated

	return debug or None


__all__ = ['_build_debug_payload', '_build_debug_payload_with_options', '_build_unchanged_state_payload']
