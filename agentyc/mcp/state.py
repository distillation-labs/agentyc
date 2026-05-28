from __future__ import annotations

from typing import Any, Literal

from agentyc.browser.views import BrowserStateSummary
from agentyc.dom.views import EnhancedDOMTreeNode
from agentyc.mcp.state_compaction import (
	_DEFAULT_MIN_ELEMENTS,
	compute_browser_state_hash,
	compute_element_signature,
	derive_element_context,
	normalize_signature_text,
	normalize_text,
	resolve_effective_mode,
	select_elements_for_min_mode,
	summarize_interactive_element,
	truncate_text,
)
from agentyc.mcp.state_debug import (
	_build_debug_payload,
	_build_debug_payload_with_options,
	_build_unchanged_state_payload,
)
from agentyc.mcp.state_refs import make_element_ref, parse_element_ref
from agentyc.mcp.state_tabs import (
	_resolve_current_tab_payload,
	_serialize_tab_id,
	build_tab_groups_payload,
	serialize_tab_info,
)

StateMode = Literal['auto', 'full', 'min', 'focus']


def build_browser_state_payload(
	state: BrowserStateSummary,
	*,
	mode: StateMode = 'auto',
	focus_ref: str | None = None,
	since_hash: str | None = None,
	max_min_elements: int = _DEFAULT_MIN_ELEMENTS,
	include_recent_events: bool = True,
) -> dict[str, Any]:
	if mode not in {'auto', 'full', 'min', 'focus'}:
		raise ValueError(f'Invalid browser_get_state mode: {mode!r}. Expected auto, full, min, or focus.')

	selector_map = state.dom_state.selector_map
	focus_index = parse_element_ref(focus_ref) if focus_ref else None
	if focus_index is not None and focus_index not in selector_map:
		raise ValueError(f'Element ref {focus_ref!r} was not found in the current page state.')

	effective_mode = resolve_effective_mode(
		mode=mode, interactive_element_count=len(selector_map), max_min_elements=max_min_elements
	)
	tabs_payload: list[dict[str, Any]] = [serialize_tab_info(tab) for tab in state.tabs]
	tab_groups_payload = build_tab_groups_payload(
		tabs_payload, current_tab_id=_serialize_tab_id(getattr(state, 'current_tab_id', None))
	)
	state_hash = getattr(state, 'state_hash', None)
	if state_hash is None:
		state_hash = compute_browser_state_hash(state)
		state.state_hash = state_hash
	result: dict[str, Any] = {
		'url': state.url,
		'title': state.title,
		'tabs': tabs_payload,
		'tab_groups': tab_groups_payload,
		'mode': mode,
		'effective_mode': effective_mode,
		'state_hash': state_hash,
		'changed': since_hash != state_hash,
		'interactive_element_count': len(selector_map),
		'interactive_elements': [],
	}

	current_tab_id = getattr(state, 'current_tab_id', None)
	serialized_current_tab_id = _serialize_tab_id(current_tab_id)
	if serialized_current_tab_id is not None:
		result['current_tab_id'] = serialized_current_tab_id
	current_tab = _resolve_current_tab_payload(
		tabs=state.tabs,
		serialized_tabs=tabs_payload,
		current_tab_id=current_tab_id,
		current_url=state.url,
		current_title=state.title,
		include_page_identity=effective_mode != 'min',
	)
	if current_tab is not None:
		result['current_tab'] = current_tab
		if 'current_tab_id' not in result and 'tab_id' in current_tab:
			result['current_tab_id'] = current_tab['tab_id']

	if focus_index is not None:
		result['focus_ref'] = make_element_ref(focus_index)
	debug_payload = _build_debug_payload_with_options(state, include_recent_events=include_recent_events)
	if debug_payload is not None:
		result['debug'] = debug_payload

	if since_hash == state_hash:
		return _build_unchanged_state_payload(
			state=state,
			mode=mode,
			effective_mode=effective_mode,
			state_hash=state_hash,
			focus_index=focus_index,
			current_tab=current_tab,
			serialized_current_tab_id=serialized_current_tab_id,
			interactive_element_count=len(selector_map),
			debug_payload=debug_payload,
		)

	scroll_y: int | None = None
	viewport_height: int | None = None
	viewport_width: int | None = None
	if state.page_info:
		pi = state.page_info
		scroll_y = pi.scroll_y
		viewport_height = pi.viewport_height
		viewport_width = pi.viewport_width
		result['viewport'] = {
			'width': pi.viewport_width,
			'height': pi.viewport_height,
		}
		# Omit page dimensions when page fits in viewport (no scrolling needed)
		if pi.page_width > pi.viewport_width or pi.page_height > pi.viewport_height:
			result['page'] = {
				'width': pi.page_width,
				'height': pi.page_height,
			}
		# Omit scroll when at origin — default position, no information for the agent
		if pi.scroll_x != 0 or pi.scroll_y != 0:
			result['scroll'] = {
				'x': pi.scroll_x,
				'y': pi.scroll_y,
			}

	selected_elements: list[EnhancedDOMTreeNode]
	if effective_mode == 'focus' and focus_index is not None:
		selected_elements = [selector_map[focus_index]]
	elif effective_mode == 'min':
		selected_elements = select_elements_for_min_mode(
			selector_map=selector_map,
			max_elements=max_min_elements,
			scroll_y=scroll_y,
			viewport_height=viewport_height,
		)
		if len(selector_map) > len(selected_elements):
			result['interactive_elements_truncated'] = True
			result['interactive_elements_remaining'] = len(selector_map) - len(selected_elements)
			result['compaction_strategy'] = 'ranked-min'
	else:
		selected_elements = list(selector_map.values())

	# Compact modes keep the stable ref and omit the redundant numeric index.
	# Full/focus modes retain it for compatibility and easier debugging.
	include_index = effective_mode != 'min'
	result['interactive_elements'] = [
		summarize_interactive_element(
			element, include_index=include_index, viewport_width=viewport_width, viewport_height=viewport_height
		)
		for element in selected_elements
	]
	return result


__all__ = [
	'StateMode',
	'_build_debug_payload',
	'_build_debug_payload_with_options',
	'build_browser_state_payload',
	'build_tab_groups_payload',
	'compute_browser_state_hash',
	'compute_element_signature',
	'derive_element_context',
	'make_element_ref',
	'normalize_signature_text',
	'normalize_text',
	'parse_element_ref',
	'resolve_effective_mode',
	'select_elements_for_min_mode',
	'serialize_tab_info',
	'summarize_interactive_element',
	'truncate_text',
]
