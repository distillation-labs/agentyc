from __future__ import annotations

from typing import Any

from agentyc.dom.serializer.clickable_elements import ClickableElementDetector
from agentyc.dom.serializer.constants import DISABLED_ELEMENTS, SVG_ELEMENTS
from agentyc.dom.views import EnhancedDOMTreeNode, NodeType, SimplifiedNode


class SerializerTreeMixin:
	def _create_simplified_tree(self: Any, node: EnhancedDOMTreeNode, depth: int = 0) -> SimplifiedNode | None:
		if node.node_type == NodeType.DOCUMENT_NODE:
			for child in node.children_and_shadow_roots:
				simplified_child = self._create_simplified_tree(child, depth + 1)
				if simplified_child:
					return simplified_child
			return None

		if node.node_type == NodeType.DOCUMENT_FRAGMENT_NODE:
			simplified = SimplifiedNode(original_node=node, children=[])
			for child in node.children_and_shadow_roots:
				simplified_child = self._create_simplified_tree(child, depth + 1)
				if simplified_child:
					simplified.children.append(simplified_child)
			return simplified if simplified.children else SimplifiedNode(original_node=node, children=[])

		if node.node_type == NodeType.ELEMENT_NODE:
			if node.node_name.lower() in DISABLED_ELEMENTS:
				return None

			if node.node_name.lower() in SVG_ELEMENTS:
				return None

			attributes = node.attributes or {}
			exclude_attr = None
			if self.session_id:
				session_specific_attr = f'data-agentyc-exclude-{self.session_id}'
				exclude_attr = attributes.get(session_specific_attr)
			if not exclude_attr:
				exclude_attr = attributes.get('data-agentyc-exclude')
			if isinstance(exclude_attr, str) and exclude_attr.lower() == 'true':
				return None

			if node.node_name == 'IFRAME' or node.node_name == 'FRAME':
				if node.content_document:
					simplified = SimplifiedNode(original_node=node, children=[])
					for child in node.content_document.children_nodes or []:
						simplified_child = self._create_simplified_tree(child, depth + 1)
						if simplified_child is not None:
							simplified.children.append(simplified_child)
					return simplified

			is_visible = node.is_visible
			is_scrollable = node.is_actually_scrollable
			has_shadow_content = bool(node.children_and_shadow_roots)
			is_shadow_host = any(child.node_type == NodeType.DOCUMENT_FRAGMENT_NODE for child in node.children_and_shadow_roots)

			if not is_visible and node.attributes:
				has_validation_attrs = any(attr.startswith(('aria-', 'pseudo')) for attr in node.attributes.keys())
				if has_validation_attrs:
					is_visible = True

			is_file_input = (
				node.tag_name and node.tag_name.lower() == 'input' and node.attributes and node.attributes.get('type') == 'file'
			)
			if not is_visible and is_file_input:
				is_visible = True
			if not is_visible and ClickableElementDetector.is_search_entry_control(node):
				is_visible = True

			if is_visible or is_scrollable or has_shadow_content or is_shadow_host:
				simplified = SimplifiedNode(original_node=node, children=[], is_shadow_host=is_shadow_host)
				for child in node.children_and_shadow_roots:
					simplified_child = self._create_simplified_tree(child, depth + 1)
					if simplified_child:
						simplified.children.append(simplified_child)

				self._add_compound_components(simplified, node)

				if is_shadow_host and simplified.children:
					return simplified

				if is_visible or is_scrollable or simplified.children:
					return simplified

		elif node.node_type == NodeType.TEXT_NODE:
			is_visible = node.snapshot_node and node.is_visible
			if is_visible and node.node_value and node.node_value.strip() and len(node.node_value.strip()) > 1:
				return SimplifiedNode(original_node=node, children=[])

		return None

	def _optimize_tree(self: Any, node: SimplifiedNode | None) -> SimplifiedNode | None:
		if not node:
			return None

		optimized_children = []
		for child in node.children:
			optimized_child = self._optimize_tree(child)
			if optimized_child:
				optimized_children.append(optimized_child)

		node.children = optimized_children
		is_visible = node.original_node.snapshot_node and node.original_node.is_visible
		is_file_input = (
			node.original_node.tag_name
			and node.original_node.tag_name.lower() == 'input'
			and node.original_node.attributes
			and node.original_node.attributes.get('type') == 'file'
		)
		is_search_entry_control = ClickableElementDetector.is_search_entry_control(node.original_node)

		if (
			is_visible
			or node.original_node.is_actually_scrollable
			or node.original_node.node_type == NodeType.TEXT_NODE
			or node.children
			or is_file_input
			or is_search_entry_control
		):
			return node

		return None
