from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from typing import Any

from cdp_use.cdp.dom.types import ShadowRootType
from cdp_use.cdp.target.types import SessionID, TargetID
from uuid_extensions import uuid7str

from agentyc.dom.constants import DYNAMIC_CLASS_PATTERNS, STATIC_ATTRIBUTES
from agentyc.dom.models import DOMRect, EnhancedAXNode, EnhancedSnapshotNode, NodeType
from agentyc.dom.utils import cap_text_length


def filter_dynamic_classes(class_str: str | None) -> str:
	"""Remove transient state classes and sort the remainder for deterministic hashing."""
	if not class_str:
		return ''
	classes = class_str.split()
	stable = [c for c in classes if not any(pattern in c.lower() for pattern in DYNAMIC_CLASS_PATTERNS)]
	return ' '.join(sorted(stable))


@dataclass(slots=True)
class EnhancedDOMTreeNode:
	node_id: int
	backend_node_id: int
	node_type: NodeType
	node_name: str
	node_value: str
	attributes: dict[str, str]
	is_scrollable: bool | None
	is_visible: bool | None
	absolute_position: DOMRect | None
	target_id: TargetID
	frame_id: str | None
	session_id: SessionID | None
	content_document: EnhancedDOMTreeNode | None
	shadow_root_type: ShadowRootType | None
	shadow_roots: list[EnhancedDOMTreeNode] | None
	parent_node: EnhancedDOMTreeNode | None
	children_nodes: list[EnhancedDOMTreeNode] | None
	ax_node: EnhancedAXNode | None
	snapshot_node: EnhancedSnapshotNode | None
	_compound_children: list[dict[str, Any]] = field(default_factory=list)
	has_js_click_listener: bool = False
	hidden_elements_info: list[dict[str, Any]] = field(default_factory=list)
	has_hidden_content: bool = False
	uuid: str = field(default_factory=uuid7str)

	@property
	def parent(self) -> EnhancedDOMTreeNode | None:
		return self.parent_node

	@property
	def children(self) -> list[EnhancedDOMTreeNode]:
		return self.children_nodes or []

	@property
	def children_and_shadow_roots(self) -> list[EnhancedDOMTreeNode]:
		children = list(self.children_nodes) if self.children_nodes else []
		if self.shadow_roots:
			children.extend(self.shadow_roots)
		return children

	@property
	def tag_name(self) -> str:
		return self.node_name.lower()

	@property
	def xpath(self) -> str:
		segments = []
		current_element = self

		while current_element and (
			current_element.node_type == NodeType.ELEMENT_NODE
			or current_element.node_type == NodeType.DOCUMENT_FRAGMENT_NODE
		):
			if current_element.node_type == NodeType.DOCUMENT_FRAGMENT_NODE:
				current_element = current_element.parent_node
				continue

			if current_element.parent_node and current_element.parent_node.node_name.lower() == 'iframe':
				break

			position = self._get_element_position(current_element)
			tag_name = current_element.node_name.lower()
			xpath_index = f'[{position}]' if position > 0 else ''
			segments.insert(0, f'{tag_name}{xpath_index}')

			current_element = current_element.parent_node

		return '/'.join(segments)

	def _get_element_position(self, element: EnhancedDOMTreeNode) -> int:
		if not element.parent_node or not element.parent_node.children_nodes:
			return 0

		same_tag_siblings = [
			child
			for child in element.parent_node.children_nodes
			if child.node_type == NodeType.ELEMENT_NODE and child.node_name.lower() == element.node_name.lower()
		]

		if len(same_tag_siblings) <= 1:
			return 0

		try:
			return same_tag_siblings.index(element) + 1
		except ValueError:
			return 0

	def __json__(self) -> dict[str, Any]:
		return {
			'node_id': self.node_id,
			'backend_node_id': self.backend_node_id,
			'node_type': self.node_type.name,
			'node_name': self.node_name,
			'node_value': self.node_value,
			'is_visible': self.is_visible,
			'attributes': self.attributes,
			'is_scrollable': self.is_scrollable,
			'session_id': self.session_id,
			'target_id': self.target_id,
			'frame_id': self.frame_id,
			'content_document': self.content_document.__json__() if self.content_document else None,
			'shadow_root_type': self.shadow_root_type,
			'ax_node': asdict(self.ax_node) if self.ax_node else None,
			'snapshot_node': asdict(self.snapshot_node) if self.snapshot_node else None,
			'shadow_roots': [r.__json__() for r in self.shadow_roots] if self.shadow_roots else [],
			'children_nodes': [c.__json__() for c in self.children_nodes] if self.children_nodes else [],
		}

	def get_all_children_text(self, max_depth: int = -1) -> str:
		text_parts = []

		def collect_text(node: EnhancedDOMTreeNode, current_depth: int) -> None:
			if max_depth != -1 and current_depth > max_depth:
				return

			if node.node_type == NodeType.TEXT_NODE:
				text_parts.append(node.node_value)
			elif node.node_type == NodeType.ELEMENT_NODE:
				for child in node.children:
					collect_text(child, current_depth + 1)

		collect_text(self, 0)
		return '\n'.join(text_parts).strip()

	def __repr__(self) -> str:
		attributes = ', '.join([f'{k}={v}' for k, v in self.attributes.items()])
		is_scrollable = getattr(self, 'is_scrollable', False)
		num_children = len(self.children_nodes or [])
		return (
			f'<{self.tag_name} {attributes} is_scrollable={is_scrollable} '
			f'num_children={num_children} >{self.node_value}</{self.tag_name}>'
		)

	def llm_representation(self, max_text_length: int = 100) -> str:
		return f'<{self.tag_name}>{cap_text_length(self.get_all_children_text(), max_text_length) or ""}'

	def get_meaningful_text_for_llm(self) -> str:
		meaningful_text = ''
		if hasattr(self, 'attributes') and self.attributes:
			for attr in ['value', 'aria-label', 'title', 'placeholder', 'alt']:
				if attr in self.attributes and self.attributes[attr]:
					meaningful_text = self.attributes[attr]
					break

		if not meaningful_text:
			meaningful_text = self.get_all_children_text()

		return meaningful_text.strip()

	@property
	def is_actually_scrollable(self) -> bool:
		if self.is_scrollable:
			return True

		if not self.snapshot_node:
			return False

		scroll_rects = self.snapshot_node.scrollRects
		client_rects = self.snapshot_node.clientRects

		if scroll_rects and client_rects:
			has_vertical_scroll = scroll_rects.height > client_rects.height + 1
			has_horizontal_scroll = scroll_rects.width > client_rects.width + 1

			if has_vertical_scroll or has_horizontal_scroll:
				if self.snapshot_node.computed_styles:
					styles = self.snapshot_node.computed_styles
					overflow = styles.get('overflow', 'visible').lower()
					overflow_x = styles.get('overflow-x', overflow).lower()
					overflow_y = styles.get('overflow-y', overflow).lower()
					return (
						overflow in ['auto', 'scroll', 'overlay']
						or overflow_x in ['auto', 'scroll', 'overlay']
						or overflow_y in ['auto', 'scroll', 'overlay']
					)

				scrollable_tags = {'div', 'main', 'section', 'article', 'aside', 'body', 'html'}
				return self.tag_name.lower() in scrollable_tags

		return False

	@property
	def should_show_scroll_info(self) -> bool:
		if self.tag_name.lower() == 'iframe':
			return True

		if not (self.is_scrollable or self.is_actually_scrollable):
			return False

		if self.tag_name.lower() in {'body', 'html'}:
			return True

		if self.parent_node and (self.parent_node.is_scrollable or self.parent_node.is_actually_scrollable):
			return False

		return True

	def _find_html_in_content_document(self) -> EnhancedDOMTreeNode | None:
		if not self.content_document:
			return None

		if self.content_document.tag_name.lower() == 'html':
			return self.content_document

		if self.content_document.children_nodes:
			for child in self.content_document.children_nodes:
				if child.tag_name.lower() == 'html':
					return child

		return None

	@property
	def scroll_info(self) -> dict[str, Any] | None:
		if not self.is_actually_scrollable or not self.snapshot_node:
			return None

		scroll_rects = self.snapshot_node.scrollRects
		client_rects = self.snapshot_node.clientRects

		if not scroll_rects or not client_rects:
			return None

		scroll_top = scroll_rects.y
		scroll_left = scroll_rects.x
		scrollable_height = scroll_rects.height
		scrollable_width = scroll_rects.width
		visible_height = client_rects.height
		visible_width = client_rects.width
		content_above = max(0, scroll_top)
		content_below = max(0, scrollable_height - visible_height - scroll_top)
		content_left = max(0, scroll_left)
		content_right = max(0, scrollable_width - visible_width - scroll_left)

		vertical_scroll_percentage = 0
		horizontal_scroll_percentage = 0

		if scrollable_height > visible_height:
			max_scroll_top = scrollable_height - visible_height
			vertical_scroll_percentage = (scroll_top / max_scroll_top) * 100 if max_scroll_top > 0 else 0

		if scrollable_width > visible_width:
			max_scroll_left = scrollable_width - visible_width
			horizontal_scroll_percentage = (scroll_left / max_scroll_left) * 100 if max_scroll_left > 0 else 0

		pages_above = content_above / visible_height if visible_height > 0 else 0
		pages_below = content_below / visible_height if visible_height > 0 else 0
		total_pages = scrollable_height / visible_height if visible_height > 0 else 1

		return {
			'scroll_top': scroll_top,
			'scroll_left': scroll_left,
			'scrollable_height': scrollable_height,
			'scrollable_width': scrollable_width,
			'visible_height': visible_height,
			'visible_width': visible_width,
			'content_above': content_above,
			'content_below': content_below,
			'content_left': content_left,
			'content_right': content_right,
			'vertical_scroll_percentage': round(vertical_scroll_percentage, 1),
			'horizontal_scroll_percentage': round(horizontal_scroll_percentage, 1),
			'pages_above': round(pages_above, 1),
			'pages_below': round(pages_below, 1),
			'total_pages': round(total_pages, 1),
			'can_scroll_up': content_above > 0,
			'can_scroll_down': content_below > 0,
			'can_scroll_left': content_left > 0,
			'can_scroll_right': content_right > 0,
		}

	def get_scroll_info_text(self) -> str:
		if self.tag_name.lower() == 'iframe':
			if self.content_document:
				html_element = self._find_html_in_content_document()
				if html_element and html_element.scroll_info:
					info = html_element.scroll_info
					pages_below = info.get('pages_below', 0)
					pages_above = info.get('pages_above', 0)
					v_pct = int(info.get('vertical_scroll_percentage', 0))

					if pages_below > 0 or pages_above > 0:
						return f'scroll: {pages_above:.1f}↑ {pages_below:.1f}↓ {v_pct}%'

			return 'scroll'

		scroll_info = self.scroll_info
		if not scroll_info:
			return ''

		parts = []
		if scroll_info['scrollable_height'] > scroll_info['visible_height']:
			parts.append(f'{scroll_info["pages_above"]:.1f} pages above, {scroll_info["pages_below"]:.1f} pages below')

		if scroll_info['scrollable_width'] > scroll_info['visible_width']:
			parts.append(f'horizontal {scroll_info["horizontal_scroll_percentage"]:.0f}%')

		return ' '.join(parts)

	@property
	def element_hash(self) -> int:
		return hash(self)

	def compute_stable_hash(self) -> int:
		parent_branch_path = self._get_parent_branch_path()
		parent_branch_path_string = '/'.join(parent_branch_path)

		filtered_attrs: dict[str, str] = {}
		for k, v in self.attributes.items():
			if k not in STATIC_ATTRIBUTES:
				continue
			if k == 'class':
				v = filter_dynamic_classes(v)
				if not v:
					continue
			filtered_attrs[k] = v

		attributes_string = ''.join(f'{k}={v}' for k, v in sorted(filtered_attrs.items()))

		ax_name = ''
		if self.ax_node and self.ax_node.name:
			ax_name = f'|ax_name={self.ax_node.name}'

		combined_string = f'{parent_branch_path_string}|{attributes_string}{ax_name}'
		hash_hex = hashlib.sha256(combined_string.encode()).hexdigest()
		return int(hash_hex[:16], 16)

	def __str__(self) -> str:
		return f'[<{self.tag_name}>#{self.frame_id[-4:] if self.frame_id else "?"}:{self.backend_node_id}]'

	def __hash__(self) -> int:
		parent_branch_path = self._get_parent_branch_path()
		parent_branch_path_string = '/'.join(parent_branch_path)
		attributes_string = ''.join(
			f'{k}={v}' for k, v in sorted((k, v) for k, v in self.attributes.items() if k in STATIC_ATTRIBUTES)
		)

		ax_name = ''
		if self.ax_node and self.ax_node.name:
			ax_name = f'|ax_name={self.ax_node.name}'

		combined_string = f'{parent_branch_path_string}|{attributes_string}{ax_name}'
		element_hash = hashlib.sha256(combined_string.encode()).hexdigest()
		return int(element_hash[:16], 16)

	def parent_branch_hash(self) -> int:
		parent_branch_path = self._get_parent_branch_path()
		parent_branch_path_string = '/'.join(parent_branch_path)
		element_hash = hashlib.sha256(parent_branch_path_string.encode()).hexdigest()
		return int(element_hash[:16], 16)

	def _get_parent_branch_path(self) -> list[str]:
		parents: list[EnhancedDOMTreeNode] = []
		current_element: EnhancedDOMTreeNode | None = self

		while current_element is not None:
			if current_element.node_type == NodeType.ELEMENT_NODE:
				parents.append(current_element)
			current_element = current_element.parent_node

		parents.reverse()
		return [parent.tag_name for parent in parents]
