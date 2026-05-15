from __future__ import annotations

from typing import Any

from agentyc.dom.views import EnhancedDOMTreeNode
from agentyc.tools.extraction.common import normalize_text


def extract_form_fields(selector_map: dict[int, EnhancedDOMTreeNode]) -> list[dict[str, Any]]:
	fields: list[dict[str, Any]] = []
	seen: set[tuple[str, str, str, tuple[str, ...]]] = set()
	for _, element in sorted(selector_map.items()):
		if element.tag_name not in {'input', 'select', 'textarea'}:
			continue
		field_type = normalize_text(element.attributes.get('type', '')) or element.tag_name
		if field_type == 'hidden':
			continue
		label = form_field_label(element)
		placeholder = ' '.join(element.attributes.get('placeholder', '').split())
		options = extract_option_texts(element) if element.tag_name == 'select' else []
		signature = (label, field_type, placeholder, tuple(options))
		if signature in seen:
			continue
		seen.add(signature)
		fields.append(
			{
				'label': label,
				'field_type': field_type,
				'required': is_required_field(element),
				'placeholder': placeholder,
				'options': options,
			}
		)
	return fields


def form_field_label(element: EnhancedDOMTreeNode) -> str:
	for candidate in (
		element.attributes.get('aria-label'),
		getattr(element.ax_node, 'name', None) if element.ax_node else None,
		parent_label_text(element),
		element.attributes.get('name'),
		element.attributes.get('placeholder'),
		element.attributes.get('id'),
		element.get_meaningful_text_for_llm(),
	):
		normalized = ' '.join((candidate or '').split())
		if normalized:
			return normalized
	return f'{element.tag_name} field'


def parent_label_text(element: EnhancedDOMTreeNode) -> str | None:
	parent = element.parent_node
	while parent is not None:
		if parent.tag_name == 'label':
			text = ' '.join(parent.get_all_children_text().split())
			if text:
				return text
			break
		parent = parent.parent_node
	return None


def extract_option_texts(element: EnhancedDOMTreeNode) -> list[str]:
	options: list[str] = []
	for child in walk_descendants(element):
		if child.tag_name != 'option':
			continue
		text = ' '.join(child.get_all_children_text().split()) or ' '.join(child.attributes.get('value', '').split())
		if text and text not in options:
			options.append(text)
	return options


def walk_descendants(node: EnhancedDOMTreeNode) -> list[EnhancedDOMTreeNode]:
	results: list[EnhancedDOMTreeNode] = []
	for child in node.children:
		results.append(child)
		results.extend(walk_descendants(child))
	return results


def is_required_field(element: EnhancedDOMTreeNode) -> bool:
	if 'required' in element.attributes:
		return True
	return normalize_text(element.attributes.get('aria-required', '')) == 'true'
