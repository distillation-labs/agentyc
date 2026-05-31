"""Element compaction and hashing helpers for MCP state payloads."""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from typing import Any, Literal

from agentyc.browser.views import BrowserStateSummary
from agentyc.dom.views import EnhancedDOMTreeNode
from agentyc.mcp.state_refs import make_element_ref

StateMode = Literal['auto', 'full', 'min', 'focus']

_DEFAULT_MIN_ELEMENTS = 9
# Auto can safely use the compact element schema on medium pages because it keeps
# the highest-signal subset once the interactive element count crosses the cap.
_DEFAULT_AUTO_FULL_THRESHOLD = 10
_DEFAULT_MIN_RELATIVE_SCORE_FLOOR = 0.7
_DEFAULT_MIN_KEEP_ELEMENTS = 4
_MAX_DUPLICATES_PER_SIGNATURE = 3
_DIGITS_PATTERN = re.compile(r'\d+')
_NAVIGATION_LINK_HINTS = ('help', 'support', 'runbook', 'reference', 'quickstart', 'authentication')
_SECONDARY_CONTROL_PREFIXES = ('enable ', 'require ', 'schedule ', 'include ', 'export ', 'focus ')
_SECONDARY_CONTROL_PHRASES = ('open issue template',)
_SECONDARY_CONTROL_PENALTY = 32

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

_AX_STATE_PROPS = ('checked', 'expanded', 'selected', 'pressed', 'required', 'readonly', 'invalid', 'multiselectable')


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
	sorted_scored_elements = sorted(scored_elements, key=lambda item: (-item[0], item[1]))
	selected_backend_node_ids: set[int] = set()

	for scored_element in sorted_scored_elements:
		_, _, backend_node_id, element = scored_element
		if not _should_pin_min_mode_element(element):
			continue
		signature = compaction_signature(element)
		if selected_signature_counts[signature] >= _MAX_DUPLICATES_PER_SIGNATURE:
			continue
		selected_elements.append(scored_element)
		selected_signature_counts[signature] += 1
		selected_backend_node_ids.add(backend_node_id)
		if len(selected_elements) >= max_elements:
			break

	for scored_element in sorted_scored_elements:
		_, _, _, element = scored_element
		backend_node_id = scored_element[2]
		if backend_node_id in selected_backend_node_ids:
			continue
		signature = compaction_signature(element)
		if selected_signature_counts[signature] >= _MAX_DUPLICATES_PER_SIGNATURE:
			continue
		selected_elements.append(scored_element)
		selected_signature_counts[signature] += 1
		if len(selected_elements) >= max_elements:
			break

	if max_elements <= _DEFAULT_MIN_ELEMENTS:
		selected_elements = _trim_selected_min_mode_elements(selected_elements)

	return [element for _, _, _, element in sorted(selected_elements, key=lambda item: item[1])]


def _should_pin_min_mode_element(element: EnhancedDOMTreeNode) -> bool:
	tag = normalize_text(element.tag_name)
	if tag not in {'input', 'textarea'}:
		return False
	attributes = element.attributes or {}
	input_type = normalize_text(attributes.get('type', ''))
	role = normalize_text(element.ax_node.role if element.ax_node and element.ax_node.role else '')
	search_like_text = normalize_text(
		' '.join(
			str(candidate)
			for candidate in (
				attributes.get('aria-label'),
				attributes.get('placeholder'),
				attributes.get('name'),
			)
			if candidate
		)
	)
	return input_type == 'search' or role == 'searchbox' or 'search' in search_like_text


def _trim_selected_min_mode_elements(
	selected_elements: list[tuple[float, int, int, EnhancedDOMTreeNode]],
) -> list[tuple[float, int, int, EnhancedDOMTreeNode]]:
	if len(selected_elements) <= _DEFAULT_MIN_KEEP_ELEMENTS:
		return selected_elements

	threshold = selected_elements[0][0] * _DEFAULT_MIN_RELATIVE_SCORE_FLOOR
	trimmed = [item for item in selected_elements if item[0] >= threshold]
	min_keep = min(_DEFAULT_MIN_KEEP_ELEMENTS, len(selected_elements))
	if len(trimmed) < min_keep:
		return selected_elements[:min_keep]
	return trimmed


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
	if tag == 'a' and any(token in text for token in _NAVIGATION_LINK_HINTS):
		score += 30
	if input_type in {'email', 'password', 'search', 'url'}:
		score += 10
	# Prefer the primary task path over optional/export/follow-up controls on dense pages.
	if any(text.startswith(prefix) for prefix in _SECONDARY_CONTROL_PREFIXES) or any(
		phrase in text for phrase in _SECONDARY_CONTROL_PHRASES
	):
		score -= _SECONDARY_CONTROL_PENALTY
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


def _fast_build_hash_input(state: BrowserStateSummary) -> str:
	parts: list[str] = [state.url, state.title or '']
	pi = state.page_info
	if pi:
		parts.extend(
			[
				str(pi.viewport_width),
				str(pi.viewport_height),
				str(pi.page_width),
				str(pi.page_height),
				str(pi.scroll_x),
				str(pi.scroll_y),
			]
		)
	for backend_node_id, element in sorted(state.dom_state.selector_map.items()):
		parts.append(str(backend_node_id))
		parts.append(str(compute_element_signature(element)))
	return '|'.join(parts)


def compute_browser_state_hash(state: BrowserStateSummary) -> str:
	if not state.dom_state.selector_map:
		pi = state.page_info
		if pi is not None:
			key = f'{state.url}|{state.title or ""}|{pi.viewport_width},{pi.viewport_height},{pi.scroll_x},{pi.scroll_y}'
		else:
			key = f'{state.url}|{state.title or ""}||||'
	else:
		key = _fast_build_hash_input(state)
	digest = hashlib.md5(key.encode('utf-8')).hexdigest()
	return digest[:16]


def compute_element_signature(element: EnhancedDOMTreeNode) -> int:
	if hasattr(element, 'compute_stable_hash') and callable(element.compute_stable_hash):
		return element.compute_stable_hash()

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


__all__ = [
	'_DEFAULT_MIN_ELEMENTS',
	'compute_browser_state_hash',
	'compute_element_signature',
	'derive_element_context',
	'normalize_signature_text',
	'normalize_text',
	'resolve_effective_mode',
	'select_elements_for_min_mode',
	'summarize_interactive_element',
	'truncate_text',
]
