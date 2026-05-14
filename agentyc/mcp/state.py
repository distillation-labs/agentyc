from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from typing import Any, Literal, cast

from agentyc.browser.views import BrowserStateSummary
from agentyc.dom.views import EnhancedDOMTreeNode

StateMode = Literal['auto', 'full', 'min', 'focus']

_DEFAULT_MIN_ELEMENTS = 25
_DEFAULT_AUTO_FULL_THRESHOLD = 18
_MAX_DUPLICATES_PER_SIGNATURE = 3
_DIGITS_PATTERN = re.compile(r'\d+')
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


def make_element_ref(backend_node_id: int) -> str:
	return f'e{backend_node_id}'


def parse_element_ref(ref: str) -> int:
	normalized = ref.strip().lower()
	match = _ELEMENT_REF_PATTERN.fullmatch(normalized)
	if not match:
		raise ValueError(f'Invalid element ref: {ref!r}. Expected e123 or 123.')
	return int(match.group(1))


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
	result: dict[str, Any] = {
		'url': state.url,
		'title': state.title,
		'tabs': [{'url': tab.url, 'title': tab.title} for tab in state.tabs],
		'mode': mode,
		'effective_mode': effective_mode,
		'state_hash': state_hash,
		'changed': since_hash != state_hash,
		'interactive_element_count': len(selector_map),
		'interactive_elements': [],
	}

	if focus_index is not None:
		result['focus_ref'] = make_element_ref(focus_index)

	if state.page_info:
		pi = state.page_info
		result['viewport'] = {
			'width': pi.viewport_width,
			'height': pi.viewport_height,
		}
		result['page'] = {
			'width': pi.page_width,
			'height': pi.page_height,
		}
		result['scroll'] = {
			'x': pi.scroll_x,
			'y': pi.scroll_y,
		}

	if since_hash == state_hash:
		return result

	selected_elements: list[EnhancedDOMTreeNode]
	if effective_mode == 'focus' and focus_index is not None:
		selected_elements = [selector_map[focus_index]]
	elif effective_mode == 'min':
		selected_elements = select_elements_for_min_mode(selector_map=selector_map, max_elements=max_min_elements)
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
		summarize_interactive_element(element, include_index=include_index) for element in selected_elements
	]
	return result


def resolve_effective_mode(
	*, mode: StateMode, interactive_element_count: int, max_min_elements: int
) -> Literal['full', 'min', 'focus']:
	if mode == 'auto':
		if interactive_element_count > max(max_min_elements, _DEFAULT_AUTO_FULL_THRESHOLD):
			return 'min'
		return 'full'
	if mode == 'focus':
		return 'focus'
	return mode


def select_elements_for_min_mode(
	*,
	selector_map: dict[int, EnhancedDOMTreeNode],
	max_elements: int = _DEFAULT_MIN_ELEMENTS,
) -> list[EnhancedDOMTreeNode]:
	signature_counts: defaultdict[str, int] = defaultdict(int)
	scored_elements: list[tuple[float, int, int, EnhancedDOMTreeNode]] = []
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


def summarize_interactive_element(element: EnhancedDOMTreeNode, *, include_index: bool = True) -> dict[str, Any]:
	text = truncate_text(element.get_meaningful_text_for_llm())
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
	if element.ax_node and element.ax_node.role:
		info['role'] = element.ax_node.role
	if element.attributes.get('placeholder'):
		info['placeholder'] = element.attributes['placeholder']
	if element.attributes.get('href'):
		info['href'] = element.attributes['href']
	if element.attributes.get('type'):
		info['type'] = element.attributes['type']
	# Include current value for input-like elements so agents can see pre-filled form state
	tag = element.tag_name.lower()
	if tag in {'input', 'textarea', 'select'} or 'contenteditable' in element.attributes:
		value = element.attributes.get('value') or element.node_value
		if value and value.strip():
			info['value'] = truncate_text(value, max_length=200)
	if 'disabled' in element.attributes:
		info['disabled'] = True
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
