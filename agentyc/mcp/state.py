from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from typing import Any, Literal, cast

from agentyc.browser.views import BrowserStateSummary
from agentyc.dom.views import EnhancedDOMTreeNode

StateMode = Literal['auto', 'full', 'min', 'focus']

_DEFAULT_MIN_ELEMENTS = 24
# Auto can safely use the compact element schema on medium pages because min mode
# still preserves the full element set until the min-element cap is reached.
_DEFAULT_AUTO_FULL_THRESHOLD = 10
_MAX_DUPLICATES_PER_SIGNATURE = 3
_DIGITS_PATTERN = re.compile(r'\d+')

# (tag, input_type) → implied ARIA role. Omit from serialized element to save tokens.
_IMPLICIT_ROLE_MAP: dict[tuple[str, str], str] = {
	('button', ''): 'button',
	('a', ''): 'link',
	('select', ''): 'combobox',
	('textarea', ''): 'textbox',
	('input', ''): 'textbox',
	('input', 'text'): 'textbox',
	('input', 'email'): 'textbox',
	('input', 'password'): 'textbox',
	('input', 'tel'): 'textbox',
	('input', 'url'): 'textbox',
	('input', 'number'): 'spinbutton',
	('input', 'range'): 'slider',
	('input', 'checkbox'): 'checkbox',
	('input', 'radio'): 'radio',
	('input', 'search'): 'searchbox',
	('img', ''): 'img',
}

_GENERIC_ACTION_TEXTS = {
	'add to cart',
	'details',
	'learn more',
	'open',
	'open details',
	'read more',
	'select',
	'view',
	'view details',
}
_ELEMENT_REF_PATTERN = re.compile(r'^(?:e)?([1-9]\d*)$')

# AX properties that represent element state — surfaced to agents for accuracy
_AX_STATE_PROPS = ('checked', 'expanded', 'selected', 'pressed', 'required', 'readonly', 'invalid', 'multiselectable')

_MAX_DEBUG_ERRORS = 5
_MAX_DEBUG_PENDING_REQUESTS = 5
_MAX_DEBUG_RECENT_EVENTS = 5
_MAX_DEBUG_POPUP_MESSAGES = 5


def _get_ax_prop(element: EnhancedDOMTreeNode, name: str) -> Any:
	"""Return the value of an AX property by name, or None if absent."""
	ax = element.ax_node
	if not ax:
		return None
	properties = getattr(ax, 'properties', None)
	if not properties:
		return None
	for prop in properties:
		if getattr(prop, 'name', None) == name:
			return prop.value
	return None


def make_element_ref(backend_node_id: int) -> str:
	return f'e{backend_node_id}'


def parse_element_ref(ref: str) -> int:
	normalized = ref.strip().lower()
	match = _ELEMENT_REF_PATTERN.fullmatch(normalized)
	if not match:
		raise ValueError(f'Invalid element ref: {ref!r}. Expected e123 or 123.')
	return int(match.group(1))


def _serialize_tab_id(target_id: Any) -> str | None:
	if target_id is None:
		return None
	target = str(target_id)
	return target[-4:] if target else None


def _serialize_optional_model(value: Any, *, by_alias: bool = False) -> Any:
	if value is None:
		return None
	if hasattr(value, 'model_dump'):
		return cast(Any, value).model_dump(mode='json', by_alias=by_alias, exclude_none=True)
	if isinstance(value, dict):
		return value
	return value


def serialize_tab_info(tab: Any) -> dict[str, Any]:
	if hasattr(tab, 'model_dump'):
		payload = cast(Any, tab).model_dump(mode='json', by_alias=True, exclude_none=True)
	else:
		payload: dict[str, Any] = {
			'url': getattr(tab, 'url', ''),
			'title': getattr(tab, 'title', '') or '',
		}
		tab_id = _serialize_tab_id(getattr(tab, 'target_id', None))
		if tab_id is not None:
			payload['tab_id'] = tab_id
		parent_tab_id = _serialize_tab_id(getattr(tab, 'parent_target_id', None))
		if parent_tab_id is not None:
			payload['parent_tab_id'] = parent_tab_id
		display_title = getattr(tab, 'display_title', None)
		if display_title is not None:
			payload['display_title'] = display_title
		ownership = _serialize_optional_model(getattr(tab, 'ownership', None))
		if ownership is not None:
			payload['ownership'] = ownership
		window_bounds = _serialize_optional_model(getattr(tab, 'window_bounds', None), by_alias=True)
		if window_bounds is not None:
			payload['window_bounds'] = window_bounds

	if payload.get('title') is None:
		payload['title'] = ''
	return payload


def _build_current_tab_payload(tab_payload: dict[str, Any], *, include_page_identity: bool = True) -> dict[str, Any] | None:
	current_tab: dict[str, Any] = {}
	keys = ('tab_id', 'parent_tab_id', 'display_title', 'ownership', 'window_bounds')
	if include_page_identity:
		keys = keys + ('url', 'title')
	for key in keys:
		value = tab_payload.get(key)
		if value is not None:
			current_tab[key] = value
	return current_tab or None


def _resolve_current_tab_payload(
	*,
	tabs: list[Any],
	serialized_tabs: list[dict[str, Any]],
	current_tab_id: str | None,
	current_url: str,
	current_title: str,
	include_page_identity: bool = True,
) -> dict[str, Any] | None:
	if current_tab_id is not None:
		for tab, tab_payload in zip(tabs, serialized_tabs):
			if str(getattr(tab, 'target_id', '')) == current_tab_id:
				return _build_current_tab_payload(tab_payload, include_page_identity=include_page_identity)

	matching_tabs = [
		_build_current_tab_payload(tab_payload, include_page_identity=include_page_identity)
		for tab, tab_payload in zip(tabs, serialized_tabs)
		if getattr(tab, 'url', None) == current_url and getattr(tab, 'title', None) == current_title
	]
	matching_tabs = [payload for payload in matching_tabs if payload is not None]
	if len(matching_tabs) == 1:
		return matching_tabs[0]
	if len(serialized_tabs) == 1:
		return _build_current_tab_payload(serialized_tabs[0], include_page_identity=include_page_identity)
	return None


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
	debug_payload = _build_debug_payload(state)
	if debug_payload is not None:
		result['debug'] = debug_payload
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
	for item in parsed[:_MAX_DEBUG_RECENT_EVENTS]:
		if not isinstance(item, dict):
			continue
		entry: dict[str, Any] = {}
		for key in ('event_type', 'timestamp', 'url', 'target_id'):
			value = item.get(key)
			if value:
				entry[key] = truncate_text(str(value), max_length=160)
		error_message = item.get('error_message')
		if error_message:
			entry['error_message'] = truncate_text(str(error_message), max_length=200)
		if entry:
			serialized.append(entry)
	return serialized, max(0, len(parsed) - len(serialized))


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

	if recent_events_raw:
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


def build_browser_state_payload(
	state: BrowserStateSummary,
	*,
	mode: StateMode = 'auto',
	focus_ref: str | None = None,
	since_hash: str | None = None,
	max_min_elements: int = _DEFAULT_MIN_ELEMENTS,
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
	state_hash = compute_browser_state_hash(state)
	tabs_payload = [serialize_tab_info(tab) for tab in state.tabs]
	result: dict[str, Any] = {
		'url': state.url,
		'title': state.title,
		'tabs': tabs_payload,
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
		if 'ownership' in current_tab:
			result['ownership'] = current_tab['ownership']
			runtime_payload = current_tab['ownership'].get('runtime') if isinstance(current_tab['ownership'], dict) else None
			if runtime_payload is not None:
				result['runtime'] = runtime_payload
		if 'current_tab_id' not in result and 'tab_id' in current_tab:
			result['current_tab_id'] = current_tab['tab_id']

	if focus_index is not None:
		result['focus_ref'] = make_element_ref(focus_index)
	debug_payload = _build_debug_payload(state)
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


def resolve_effective_mode(
	*, mode: StateMode, interactive_element_count: int, max_min_elements: int
) -> Literal['full', 'min', 'focus']:
	if mode == 'auto':
		if interactive_element_count >= _DEFAULT_AUTO_FULL_THRESHOLD:
			return 'min'
		return 'full'
	if mode == 'focus':
		return 'focus'
	return mode


def select_elements_for_min_mode(
	*,
	selector_map: dict[int, EnhancedDOMTreeNode],
	max_elements: int = _DEFAULT_MIN_ELEMENTS,
	scroll_y: int | None = None,
	viewport_height: int | None = None,
) -> list[EnhancedDOMTreeNode]:
	signature_counts: defaultdict[str, int] = defaultdict(int)
	scored_elements: list[tuple[float, int, int, EnhancedDOMTreeNode]] = []
	# Proximity scoring window: boost elements within 2× viewport height of current scroll
	prox_near = (scroll_y or 0) + (viewport_height or 900) * 1
	prox_far = (scroll_y or 0) + (viewport_height or 900) * 2
	for order, (backend_node_id, element) in enumerate(selector_map.items()):
		signature = compaction_signature(element)
		duplicate_index = signature_counts[signature]
		signature_counts[signature] += 1
		score = score_element_for_compaction(element)
		score += max(0.0, 16 - (order * 0.35))
		if duplicate_index == 0:
			score += 4
		if duplicate_index > 0:
			score -= duplicate_index * 6
		if duplicate_index >= _MAX_DUPLICATES_PER_SIGNATURE:
			score -= 50
		# Boost elements near the current viewport — most likely targets for the next action
		_snap = getattr(element, 'snapshot_node', None)
		if _snap is not None and scroll_y is not None and viewport_height is not None:
			rects = getattr(_snap, 'clientRects', None)
			if rects is not None:
				el_abs_y = (scroll_y or 0) + getattr(rects, 'y', 9999)
				if el_abs_y <= prox_near:
					score += 18
				elif el_abs_y <= prox_far:
					score += 9
		scored_elements.append((score, order, backend_node_id, element))

	selected_elements: list[tuple[float, int, int, EnhancedDOMTreeNode]] = []
	selected_signature_counts: defaultdict[str, int] = defaultdict(int)
	for scored_element in sorted(scored_elements, key=lambda item: (-item[0], item[1])):
		_, _, _, element = scored_element
		signature = compaction_signature(element)
		if selected_signature_counts[signature] >= _MAX_DUPLICATES_PER_SIGNATURE:
			continue
		selected_elements.append(scored_element)
		selected_signature_counts[signature] += 1
		if len(selected_elements) >= max_elements:
			break

	return [element for _, _, _, element in sorted(selected_elements, key=lambda item: item[1])]


def score_element_for_compaction(element: EnhancedDOMTreeNode) -> float:
	text = normalize_text(element.get_meaningful_text_for_llm())
	role = normalize_text(element.ax_node.role if element.ax_node and element.ax_node.role else '')
	tag = normalize_text(element.tag_name)
	input_type = normalize_text(element.attributes.get('type', ''))

	score = 0.0
	if tag in {'input', 'textarea', 'select'}:
		score += 50
	if tag == 'button':
		score += 42
	if tag == 'a':
		score += 18
	if role in {'button', 'link', 'checkbox', 'radio', 'switch', 'tab', 'combobox'}:
		score += 12
	if element.attributes.get('placeholder'):
		score += 16
	if element.attributes.get('aria-label'):
		score += 14
	if element.attributes.get('name'):
		score += 8
	if element.attributes.get('href'):
		score += 6
	if text:
		score += min(len(text), 40) / 3
	if input_type in {'email', 'password', 'search', 'url'}:
		score += 10
	if 'disabled' in element.attributes:
		score -= 12
	if input_type == 'hidden':
		score -= 100
	if text in _GENERIC_ACTION_TEXTS:
		score -= 10
	if text.startswith('add ') and ' to cart' in text:
		score -= 18
	if text.startswith('documentation topic '):
		score -= 8
	if text.startswith('featured product ') and tag == 'a':
		score += 8
	return score


def compaction_signature(element: EnhancedDOMTreeNode) -> str:
	text = normalize_text(element.get_meaningful_text_for_llm())
	role = normalize_text(element.ax_node.role if element.ax_node and element.ax_node.role else '')
	tag = normalize_text(element.tag_name)
	input_type = normalize_text(element.attributes.get('type', ''))
	placeholder = normalize_text(element.attributes.get('placeholder', ''))
	signature_text = normalize_signature_text(placeholder or text)
	return '|'.join([tag, role, input_type, signature_text])


def summarize_interactive_element(
	element: EnhancedDOMTreeNode,
	*,
	include_index: bool = True,
	viewport_width: int | None = None,
	viewport_height: int | None = None,
) -> dict[str, Any]:
	text = truncate_text(element.get_meaningful_text_for_llm())
	if not text:
		attributes = element.attributes or {}
		tag = (element.tag_name or '').lower()
		input_type = attributes.get('type', '').lower()
		for candidate in (
			attributes.get('aria-label'),
			attributes.get('placeholder'),
			attributes.get('title'),
			attributes.get('value') if tag == 'input' and input_type in {'button', 'submit', 'reset'} else None,
		):
			if candidate and str(candidate).strip():
				text = truncate_text(str(candidate))
				break
	info: dict[str, Any] = {
		'ref': make_element_ref(element.backend_node_id),
		'tag': element.tag_name,
	}
	if include_index:
		info['index'] = element.backend_node_id
	if text:
		info['text'] = text
	context = derive_element_context(element)
	if context:
		info['context'] = context
	tag = element.tag_name.lower()
	if element.ax_node and element.ax_node.role:
		role = element.ax_node.role
		input_type_lower = element.attributes.get('type', '').lower()
		implied = _IMPLICIT_ROLE_MAP.get((tag, input_type_lower)) or _IMPLICIT_ROLE_MAP.get((tag, ''))
		if role.lower() != implied:
			info['role'] = role
	if element.attributes.get('placeholder'):
		info['placeholder'] = element.attributes['placeholder']
	if element.attributes.get('href'):
		info['href'] = element.attributes['href']
	if element.attributes.get('type'):
		info['type'] = element.attributes['type']
	# Current value for input-like elements — agents need pre-filled state.
	# AX node.value is authoritative: it reflects the live DOM property (typed text),
	# whereas element.attributes['value'] is the static HTML attribute (initial value only).
	if tag in {'input', 'textarea', 'select'} or 'contenteditable' in element.attributes:
		ax_value = getattr(element.ax_node, 'value', None) if element.ax_node else None
		value = ax_value or element.attributes.get('value') or element.node_value
		if value and str(value).strip():
			info['value'] = truncate_text(str(value), max_length=200)
	# AX state properties — what agents need to interact correctly
	for prop_name in _AX_STATE_PROPS:
		val = _get_ax_prop(element, prop_name)
		if val is not None and val is not False:
			info[prop_name] = val
	# AX description provides additional context (e.g. validation messages, hint text from aria-describedby/aria-errormessage)
	if element.ax_node and getattr(element.ax_node, 'description', None):
		desc = truncate_text(element.ax_node.description or '', max_length=120)
		existing_context = info.get('context', '')
		if desc and desc != existing_context and desc != info.get('text', ''):
			info['description'] = desc
	if 'disabled' in element.attributes:
		info['disabled'] = True
	# Form constraints — helps agents format input correctly and avoid validation errors
	for attr in ('pattern', 'minlength', 'maxlength', 'min', 'max', 'step', 'accept', 'multiple'):
		v = element.attributes.get(attr)
		if v is not None:
			info[attr] = v
	# accesskey — keyboard shortcut hint
	if element.attributes.get('accesskey'):
		info['keyboard_shortcut'] = f'Alt+{element.attributes["accesskey"].upper()}'
	# Viewport visibility — surface when element is off-screen so agents know to scroll first
	_snap = getattr(element, 'snapshot_node', None)
	if viewport_width and viewport_height and _snap and _snap.clientRects:
		r = _snap.clientRects
		in_vp = r.x < viewport_width and r.y < viewport_height and (r.x + r.width) > 0 and (r.y + r.height) > 0
		if not in_vp:
			# Compute scroll direction needed
			if r.y + r.height <= 0:
				info['off_screen'] = 'above'
			elif r.y >= viewport_height:
				info['off_screen'] = 'below'
			elif r.x + r.width <= 0:
				info['off_screen'] = 'left'
			else:
				info['off_screen'] = 'right'
	return info


def derive_element_context(element: EnhancedDOMTreeNode, max_depth: int = 4) -> str | None:
	element_text = normalize_text(element.get_meaningful_text_for_llm())
	placeholder_text = normalize_text(element.attributes.get('placeholder', ''))
	seen: set[str] = set()
	current = element.parent_node
	depth = 0
	while current is not None and depth < max_depth:
		for candidate in (
			current.attributes.get('aria-label'),
			getattr(current.ax_node, 'name', None) if current.ax_node else None,
			_heading_text_from_ancestor(current),
		):
			normalized = truncate_text(' '.join((candidate or '').split()), max_length=80)
			if not normalized:
				continue
			normalized_key = normalize_text(normalized)
			if normalized_key in seen:
				continue
			seen.add(normalized_key)
			if normalized_key in {'', element_text, placeholder_text}:
				continue
			if len(normalized_key) < 4:
				continue
			return normalized
		current = current.parent_node
		depth += 1
	return None


def _heading_text_from_ancestor(node: EnhancedDOMTreeNode) -> str | None:
	for child in node.children:
		if child.tag_name in {'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'legend'}:
			text = ' '.join(child.get_all_children_text().split())
			if text:
				return text
	return None


def compute_browser_state_hash(state: BrowserStateSummary) -> str:
	element_signatures = [
		{
			'backend_node_id': backend_node_id,
			'stable_hash': compute_element_signature(element),
		}
		for backend_node_id, element in sorted(state.dom_state.selector_map.items())
	]
	page_signature = {
		'url': state.url,
		'title': state.title,
		'viewport': ((state.page_info.viewport_width, state.page_info.viewport_height) if state.page_info else None),
		'page': ((state.page_info.page_width, state.page_info.page_height) if state.page_info else None),
		'scroll': ((state.page_info.scroll_x, state.page_info.scroll_y) if state.page_info else None),
		'elements': element_signatures,
	}
	digest = hashlib.sha256(json.dumps(page_signature, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()
	return digest[:16]


def compute_element_signature(element: EnhancedDOMTreeNode) -> int:
	compute_stable_hash = getattr(element, 'compute_stable_hash', None)
	if callable(compute_stable_hash):
		return int(cast(Any, compute_stable_hash)())

	fallback_signature = {
		'tag': element.tag_name,
		'text': element.get_meaningful_text_for_llm(),
		'attrs': {
			key: value
			for key, value in sorted(element.attributes.items())
			if key in {'aria-label', 'href', 'id', 'name', 'placeholder'}
		},
	}
	fallback_hash = hashlib.sha256(json.dumps(fallback_signature, sort_keys=True).encode('utf-8')).hexdigest()
	return int(fallback_hash[:16], 16)


def normalize_text(text: str) -> str:
	return ' '.join(text.lower().split())


def normalize_signature_text(text: str) -> str:
	return _DIGITS_PATTERN.sub('#', normalize_text(text))


def truncate_text(text: str, max_length: int = 100) -> str:
	compacted = ' '.join(text.split())
	if len(compacted) <= max_length:
		return compacted
	return compacted[: max_length - 3].rstrip() + '...'
