from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from agentyc.dom.constants import DEFAULT_INCLUDE_ATTRIBUTES
from agentyc.dom.node import EnhancedDOMTreeNode
from agentyc.observability import observe_debug


@dataclass(slots=True)
class SimplifiedNode:
	"""Simplified tree node used by DOM serialization passes."""

	original_node: EnhancedDOMTreeNode
	children: list[SimplifiedNode]
	should_display: bool = True
	is_interactive: bool = False
	is_new: bool = False
	ignored_by_paint_order: bool = False
	excluded_by_parent: bool = False
	is_shadow_host: bool = False
	is_compound_component: bool = False

	def _clean_original_node_json(self, node_json: dict) -> dict:
		if 'children_nodes' in node_json:
			del node_json['children_nodes']
		if 'shadow_roots' in node_json:
			del node_json['shadow_roots']

		if node_json.get('content_document'):
			node_json['content_document'] = self._clean_original_node_json(node_json['content_document'])

		return node_json

	def __json__(self) -> dict:
		original_node_json = self.original_node.__json__()
		cleaned_original_node_json = self._clean_original_node_json(original_node_json)
		return {
			'should_display': self.should_display,
			'is_interactive': self.is_interactive,
			'ignored_by_paint_order': self.ignored_by_paint_order,
			'excluded_by_parent': self.excluded_by_parent,
			'original_node': cleaned_original_node_json,
			'children': [c.__json__() for c in self.children],
		}


DOMSelectorMap: TypeAlias = dict[int, 'EnhancedDOMTreeNode']


@dataclass
class SerializedDOMState:
	_root: SimplifiedNode | None
	selector_map: DOMSelectorMap

	@observe_debug(ignore_input=True, ignore_output=True, name='llm_representation')
	def llm_representation(self, include_attributes: list[str] | None = None) -> str:
		from agentyc.dom.serializer.serializer import DOMTreeSerializer

		if not self._root:
			return 'Empty DOM tree (you might have to wait for the page to load)'

		include_attributes = include_attributes or DEFAULT_INCLUDE_ATTRIBUTES
		return DOMTreeSerializer.serialize_tree(self._root, include_attributes)

	@observe_debug(ignore_input=True, ignore_output=True, name='eval_representation')
	def eval_representation(self, include_attributes: list[str] | None = None) -> str:
		from agentyc.dom.serializer.eval_serializer import DOMEvalSerializer

		if not self._root:
			return 'Empty DOM tree (you might have to wait for the page to load)'

		include_attributes = include_attributes or DEFAULT_INCLUDE_ATTRIBUTES
		return DOMEvalSerializer.serialize_tree(self._root, include_attributes)
