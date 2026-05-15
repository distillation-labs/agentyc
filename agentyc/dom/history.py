from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agentyc.dom.models import DOMRect, NodeType
from agentyc.dom.node import EnhancedDOMTreeNode


@dataclass
class DOMInteractedElement:
	node_id: int
	backend_node_id: int
	frame_id: str | None
	node_type: NodeType
	node_value: str
	node_name: str
	attributes: dict[str, str] | None
	bounds: DOMRect | None
	x_path: str
	element_hash: int
	stable_hash: int | None = None
	ax_name: str | None = None

	def to_dict(self) -> dict[str, Any]:
		return {
			'node_id': self.node_id,
			'backend_node_id': self.backend_node_id,
			'frame_id': self.frame_id,
			'node_type': self.node_type.value,
			'node_value': self.node_value,
			'node_name': self.node_name,
			'attributes': self.attributes,
			'x_path': self.x_path,
			'element_hash': self.element_hash,
			'stable_hash': self.stable_hash,
			'bounds': self.bounds.to_dict() if self.bounds else None,
			'ax_name': self.ax_name,
		}

	@classmethod
	def load_from_enhanced_dom_tree(cls, enhanced_dom_tree: EnhancedDOMTreeNode) -> DOMInteractedElement:
		ax_name = None
		if enhanced_dom_tree.ax_node and enhanced_dom_tree.ax_node.name:
			ax_name = enhanced_dom_tree.ax_node.name

		return cls(
			node_id=enhanced_dom_tree.node_id,
			backend_node_id=enhanced_dom_tree.backend_node_id,
			frame_id=enhanced_dom_tree.frame_id,
			node_type=enhanced_dom_tree.node_type,
			node_value=enhanced_dom_tree.node_value,
			node_name=enhanced_dom_tree.node_name,
			attributes=enhanced_dom_tree.attributes,
			bounds=enhanced_dom_tree.snapshot_node.bounds if enhanced_dom_tree.snapshot_node else None,
			x_path=enhanced_dom_tree.xpath,
			element_hash=hash(enhanced_dom_tree),
			stable_hash=enhanced_dom_tree.compute_stable_hash(),
			ax_name=ax_name,
		)
