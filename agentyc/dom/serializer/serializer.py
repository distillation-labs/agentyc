# @file purpose: Serializes enhanced DOM trees to string format for LLM consumption

import time

from agentyc.dom.serializer.bbox import SerializerBoundingBoxMixin
from agentyc.dom.serializer.compound import SerializerCompoundMixin
from agentyc.dom.serializer.constants import DEFAULT_CONTAINMENT_THRESHOLD, PROPAGATING_ELEMENTS
from agentyc.dom.serializer.indexing import SerializerIndexingMixin
from agentyc.dom.serializer.paint_order import PaintOrderRemover
from agentyc.dom.serializer.rendering import build_attributes_string, serialize_tree
from agentyc.dom.serializer.tree import SerializerTreeMixin
from agentyc.dom.views import DOMSelectorMap, EnhancedDOMTreeNode, SerializedDOMState


class DOMTreeSerializer(
	SerializerCompoundMixin,
	SerializerTreeMixin,
	SerializerIndexingMixin,
	SerializerBoundingBoxMixin,
):
	"""Serializes enhanced DOM trees to string format."""

	PROPAGATING_ELEMENTS = PROPAGATING_ELEMENTS
	DEFAULT_CONTAINMENT_THRESHOLD = DEFAULT_CONTAINMENT_THRESHOLD
	serialize_tree = staticmethod(serialize_tree)
	_build_attributes_string = staticmethod(build_attributes_string)

	def __init__(
		self,
		root_node: EnhancedDOMTreeNode,
		previous_cached_state: SerializedDOMState | None = None,
		enable_bbox_filtering: bool = True,
		containment_threshold: float | None = None,
		paint_order_filtering: bool = True,
		session_id: str | None = None,
	):
		self.root_node = root_node
		self._interactive_counter = 1
		self._selector_map: DOMSelectorMap = {}
		self._previous_cached_selector_map = previous_cached_state.selector_map if previous_cached_state else None
		self.timing_info: dict[str, float] = {}
		self._clickable_cache: dict[int, bool] = {}
		self.enable_bbox_filtering = enable_bbox_filtering
		self.containment_threshold = containment_threshold or self.DEFAULT_CONTAINMENT_THRESHOLD
		self.paint_order_filtering = paint_order_filtering
		self.session_id = session_id

	def serialize_accessible_elements(self) -> tuple[SerializedDOMState, dict[str, float]]:
		start_total = time.time()
		self._interactive_counter = 1
		self._selector_map = {}
		self._semantic_groups = []
		self._clickable_cache = {}

		start_step1 = time.time()
		simplified_tree = self._create_simplified_tree(self.root_node)
		self.timing_info['create_simplified_tree'] = time.time() - start_step1

		start_step2 = time.time()
		if self.paint_order_filtering and simplified_tree:
			PaintOrderRemover(simplified_tree).calculate_paint_order()
		self.timing_info['calculate_paint_order'] = time.time() - start_step2

		start_step3 = time.time()
		optimized_tree = self._optimize_tree(simplified_tree)
		self.timing_info['optimize_tree'] = time.time() - start_step3

		if self.enable_bbox_filtering and optimized_tree:
			start_step4 = time.time()
			filtered_tree = self._apply_bounding_box_filtering(optimized_tree)
			self.timing_info['bbox_filtering'] = time.time() - start_step4
		else:
			filtered_tree = optimized_tree

		start_step5 = time.time()
		self._assign_interactive_indices_and_mark_new_nodes(filtered_tree)
		if not self._selector_map:
			start_relaxed_fallback = time.time()
			self._assign_relaxed_interactive_indices_and_mark_new_nodes(self.root_node)
			if self._selector_map:
				self.timing_info['relaxed_interactive_fallback'] = time.time() - start_relaxed_fallback
		self.timing_info['assign_interactive_indices'] = time.time() - start_step5

		self.timing_info['serialize_accessible_elements_total'] = time.time() - start_total
		return SerializedDOMState(_root=filtered_tree, selector_map=self._selector_map), self.timing_info
