from __future__ import annotations

import logging
import time
from typing import Any

from agentyc.dom.serializer.clickable_elements import ClickableElementDetector
from agentyc.dom.views import EnhancedDOMTreeNode, NodeType, SimplifiedNode


class SerializerIndexingMixin:
	def _is_interactive_cached(self: Any, node: EnhancedDOMTreeNode) -> bool:
		if node.node_id not in self._clickable_cache:
			start_time = time.time()
			result = ClickableElementDetector.is_interactive(node)
			end_time = time.time()
			if 'clickable_detection_time' not in self.timing_info:
				self.timing_info['clickable_detection_time'] = 0
			self.timing_info['clickable_detection_time'] += end_time - start_time
			self._clickable_cache[node.node_id] = result

		return self._clickable_cache[node.node_id]

	def _is_explicitly_hidden(self: Any, node: EnhancedDOMTreeNode) -> bool:
		attributes = node.attributes or {}
		hidden_attr = attributes.get('hidden')
		if hidden_attr is not None and str(hidden_attr).lower() != 'false':
			return True

		aria_hidden = attributes.get('aria-hidden')
		if aria_hidden is not None and str(aria_hidden).lower() == 'true':
			return True

		inline_style = attributes.get('style', '').replace(' ', '').lower()
		if 'display:none' in inline_style or 'visibility:hidden' in inline_style or 'opacity:0' in inline_style:
			return True

		if node.snapshot_node and node.snapshot_node.computed_styles:
			computed_styles = node.snapshot_node.computed_styles
			display = computed_styles.get('display', '').lower()
			visibility = computed_styles.get('visibility', '').lower()
			opacity = computed_styles.get('opacity', '1')
			if display == 'none' or visibility == 'hidden':
				return True
			try:
				if float(opacity) <= 0:
					return True
			except (ValueError, TypeError):
				pass

		return False

	def _assign_relaxed_interactive_indices_and_mark_new_nodes(self: Any, node: EnhancedDOMTreeNode | None) -> None:
		if not node:
			return

		if node.node_type == NodeType.ELEMENT_NODE and self._is_interactive_cached(node) and not self._is_explicitly_hidden(node):
			if node.backend_node_id not in self._selector_map:
				self._selector_map[node.backend_node_id] = node
				self._interactive_counter += 1

		for child in node.children_and_shadow_roots:
			self._assign_relaxed_interactive_indices_and_mark_new_nodes(child)

		if node.content_document:
			self._assign_relaxed_interactive_indices_and_mark_new_nodes(node.content_document)

	def _collect_interactive_elements(self: Any, node: SimplifiedNode, elements: list[SimplifiedNode]) -> None:
		is_interactive = self._is_interactive_cached(node.original_node)
		is_visible = node.original_node.snapshot_node and node.original_node.is_visible
		is_file_input = (
			node.original_node.tag_name
			and node.original_node.tag_name.lower() == 'input'
			and node.original_node.attributes
			and node.original_node.attributes.get('type') == 'file'
		)
		if is_interactive and (is_visible or is_file_input):
			elements.append(node)

		for child in node.children:
			self._collect_interactive_elements(child, elements)

	def _has_interactive_descendants(self: Any, node: SimplifiedNode) -> bool:
		for child in node.children:
			if self._is_interactive_cached(child.original_node):
				return True
			if self._has_interactive_descendants(child):
				return True
		return False

	def _is_inside_shadow_dom(self: Any, node: SimplifiedNode) -> bool:
		current = node.original_node.parent_node
		while current is not None:
			if current.node_type == NodeType.DOCUMENT_FRAGMENT_NODE and current.shadow_root_type is not None:
				return True
			current = current.parent_node
		return False

	def _is_text_entry_control(self: Any, node: SimplifiedNode) -> bool:
		tag_name = (node.original_node.tag_name or '').lower()
		if tag_name in {'textarea', 'select'}:
			return True
		if tag_name != 'input':
			return False
		input_type = (node.original_node.attributes or {}).get('type', 'text').lower()
		return input_type not in {'button', 'checkbox', 'color', 'file', 'hidden', 'image', 'radio', 'range', 'reset', 'submit'}

	def _has_visible_text_entry_descendant(self: Any, node: SimplifiedNode, max_depth: int = 3) -> bool:
		if max_depth <= 0:
			return False
		for child in node.children:
			if child.original_node.node_type != NodeType.ELEMENT_NODE:
				continue
			if (
				self._is_text_entry_control(child)
				and self._is_interactive_cached(child.original_node)
				and child.original_node.snapshot_node
				and child.original_node.is_visible
			):
				return True
			if self._has_visible_text_entry_descendant(child, max_depth=max_depth - 1):
				return True
		return False

	def _assign_interactive_indices_and_mark_new_nodes(self: Any, node: SimplifiedNode | None) -> None:
		if not node:
			return

		if not node.excluded_by_parent and not node.ignored_by_paint_order:
			is_interactive_assign = self._is_interactive_cached(node.original_node)
			is_visible = node.original_node.snapshot_node and node.original_node.is_visible
			is_scrollable = node.original_node.is_actually_scrollable

			if is_interactive_assign and not node.original_node.snapshot_node:
				logger = logging.getLogger('agentyc.dom.serializer')
				attrs = node.original_node.attributes or {}
				attr_str = f'name={attrs.get("name", "")} id={attrs.get("id", "")} type={attrs.get("type", "")}'
				in_shadow = self._is_inside_shadow_dom(node)
				if (
					in_shadow
					and node.original_node.tag_name
					and node.original_node.tag_name.lower() in ['input', 'button', 'select', 'textarea', 'a']
				):
					logger.debug(
						f'🔍 INCLUDING shadow DOM <{node.original_node.tag_name}> (no snapshot_node but in shadow DOM): '
						f'backendNodeId={node.original_node.backend_node_id} {attr_str}'
					)
				else:
					logger.debug(
						f'🔍 SKIPPING interactive <{node.original_node.tag_name}> (no snapshot_node, not in shadow DOM): '
						f'backendNodeId={node.original_node.backend_node_id} {attr_str}'
					)

			is_file_input = (
				node.original_node.tag_name
				and node.original_node.tag_name.lower() == 'input'
				and node.original_node.attributes
				and node.original_node.attributes.get('type') == 'file'
			)

			is_shadow_dom_element = (
				is_interactive_assign
				and not node.original_node.snapshot_node
				and node.original_node.tag_name
				and node.original_node.tag_name.lower() in ['input', 'button', 'select', 'textarea', 'a']
				and self._is_inside_shadow_dom(node)
			)

			should_make_interactive = False
			if is_scrollable:
				attrs = node.original_node.attributes or {}
				role = attrs.get('role', '').lower()
				tag_name = (node.original_node.tag_name or '').lower()
				class_attr = attrs.get('class', '').lower()
				class_list = class_attr.split() if class_attr else []
				is_dropdown_by_role = role in ('listbox', 'menu', 'combobox', 'menubar', 'tree', 'grid')
				is_dropdown_by_tag = tag_name == 'select'
				is_dropdown_by_class = (
					'dropdown' in class_list
					or 'dropdown-menu' in class_list
					or 'select-menu' in class_list
					or ('ui' in class_list and 'dropdown' in class_attr)
				)
				is_dropdown_container = is_dropdown_by_role or is_dropdown_by_tag or is_dropdown_by_class
				if is_dropdown_container:
					should_make_interactive = True
				else:
					has_interactive_desc = self._has_interactive_descendants(node)
					if not has_interactive_desc:
						should_make_interactive = True
			elif is_interactive_assign and (is_visible or is_file_input or is_shadow_dom_element):
				should_make_interactive = True

			if (
				should_make_interactive
				and node.original_node.tag_name
				and node.original_node.tag_name.lower() == 'label'
				and self._has_visible_text_entry_descendant(node)
			):
				should_make_interactive = False

			if should_make_interactive:
				node.is_interactive = True
				self._selector_map[node.original_node.backend_node_id] = node.original_node
				self._interactive_counter += 1
				if node.is_compound_component:
					node.is_new = True
				elif self._previous_cached_selector_map:
					previous_backend_node_ids = {node.backend_node_id for node in self._previous_cached_selector_map.values()}
					if node.original_node.backend_node_id not in previous_backend_node_ids:
						node.is_new = True

		for child in node.children:
			self._assign_interactive_indices_and_mark_new_nodes(child)
