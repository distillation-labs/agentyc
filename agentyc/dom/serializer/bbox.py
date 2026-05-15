from __future__ import annotations

import logging
from typing import Any

from agentyc.dom.views import DOMRect, NodeType, PropagatingBounds, SimplifiedNode


class SerializerBoundingBoxMixin:
	def _apply_bounding_box_filtering(self: Any, node: SimplifiedNode | None) -> SimplifiedNode | None:
		if not node:
			return None

		self._filter_tree_recursive(node, active_bounds=None, depth=0)
		excluded_count = self._count_excluded_nodes(node)
		if excluded_count > 0:
			logging.debug(f'BBox filtering excluded {excluded_count} nodes')
		return node

	def _filter_tree_recursive(self: Any, node: SimplifiedNode, active_bounds: PropagatingBounds | None = None, depth: int = 0):
		if active_bounds and self._should_exclude_child(node, active_bounds):
			node.excluded_by_parent = True

		new_bounds = None
		tag = node.original_node.tag_name.lower()
		role = node.original_node.attributes.get('role') if node.original_node.attributes else None
		attributes = {'tag': tag, 'role': role}
		if self._is_propagating_element(attributes):
			if node.original_node.snapshot_node and node.original_node.snapshot_node.bounds:
				new_bounds = PropagatingBounds(
					tag=tag,
					bounds=node.original_node.snapshot_node.bounds,
					node_id=node.original_node.node_id,
					depth=depth,
				)

		propagate_bounds = new_bounds if new_bounds else active_bounds
		for child in node.children:
			self._filter_tree_recursive(child, propagate_bounds, depth + 1)

	def _should_exclude_child(self: Any, node: SimplifiedNode, active_bounds: PropagatingBounds) -> bool:
		if node.original_node.node_type == NodeType.TEXT_NODE:
			return False

		if not node.original_node.snapshot_node or not node.original_node.snapshot_node.bounds:
			return False

		child_bounds = node.original_node.snapshot_node.bounds
		if not self._is_contained(child_bounds, active_bounds.bounds, self.containment_threshold):
			return False

		child_tag = node.original_node.tag_name.lower()
		child_role = node.original_node.attributes.get('role') if node.original_node.attributes else None
		child_attributes = {'tag': child_tag, 'role': child_role}

		if child_tag in ['input', 'select', 'textarea', 'label']:
			return False

		if self._is_propagating_element(child_attributes):
			return False

		if node.original_node.attributes and 'onclick' in node.original_node.attributes:
			return False

		if node.original_node.attributes:
			aria_label = node.original_node.attributes.get('aria-label')
			if aria_label and aria_label.strip():
				return False
			role = node.original_node.attributes.get('role')
			if role in ['button', 'link', 'checkbox', 'radio', 'tab', 'menuitem', 'option']:
				return False

		return True

	def _is_contained(self: Any, child: DOMRect, parent: DOMRect, threshold: float) -> bool:
		x_overlap = max(0, min(child.x + child.width, parent.x + parent.width) - max(child.x, parent.x))
		y_overlap = max(0, min(child.y + child.height, parent.y + parent.height) - max(child.y, parent.y))
		intersection_area = x_overlap * y_overlap
		child_area = child.width * child.height
		if child_area == 0:
			return False
		containment_ratio = intersection_area / child_area
		return containment_ratio >= threshold

	def _count_excluded_nodes(self: Any, node: SimplifiedNode, count: int = 0) -> int:
		if hasattr(node, 'excluded_by_parent') and node.excluded_by_parent:
			count += 1
		for child in node.children:
			count = self._count_excluded_nodes(child, count)
		return count

	def _is_propagating_element(self: Any, attributes: dict[str, str | None]) -> bool:
		keys_to_check = ['tag', 'role']
		for pattern in self.PROPAGATING_ELEMENTS:
			check = [pattern.get(key) is None or pattern.get(key) == attributes.get(key) for key in keys_to_check]
			if all(check):
				return True
		return False
