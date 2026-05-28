"""Selector-map and lookup helpers for BrowserSession DOM access."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agentyc.dom.views import EnhancedDOMTreeNode

if TYPE_CHECKING:
	from agentyc.browser.session import BrowserSession


async def get_dom_element_by_index(session: BrowserSession, index: int) -> EnhancedDOMTreeNode | None:
	"""Get DOM element by index from cached selector map."""
	if session._cached_selector_map and index in session._cached_selector_map:
		return session._cached_selector_map[index]
	return None


def update_cached_selector_map(session: BrowserSession, selector_map: dict[int, EnhancedDOMTreeNode]) -> None:
	"""Update the cached selector map with new DOM state."""
	session._cached_selector_map = selector_map


async def get_element_by_index(session: BrowserSession, index: int) -> EnhancedDOMTreeNode | None:
	"""Alias for get_dom_element_by_index for backwards compatibility."""
	return await get_dom_element_by_index(session, index)


def is_file_input(session: BrowserSession, element: Any) -> bool:
	"""Check if element is a file input."""
	if session._dom_watchdog:
		return session._dom_watchdog.is_file_input(element)
	return (
		hasattr(element, 'node_name')
		and element.node_name.upper() == 'INPUT'
		and hasattr(element, 'attributes')
		and element.attributes.get('type', '').lower() == 'file'
	)


def find_file_input_near_element(
	session: BrowserSession,
	node: EnhancedDOMTreeNode,
	max_height: int = 3,
	max_descendant_depth: int = 3,
) -> EnhancedDOMTreeNode | None:
	"""Find the closest file input to the given element."""

	def _find_in_descendants(n: EnhancedDOMTreeNode, depth: int) -> EnhancedDOMTreeNode | None:
		if depth < 0:
			return None
		if is_file_input(session, n):
			return n
		for child in n.children_nodes or []:
			result = _find_in_descendants(child, depth - 1)
			if result:
				return result
		return None

	current: EnhancedDOMTreeNode | None = node
	for _ in range(max_height + 1):
		if current is None:
			break
		if is_file_input(session, current):
			return current
		result = _find_in_descendants(current, max_descendant_depth)
		if result:
			return result
		if current.parent_node:
			for sibling in current.parent_node.children_nodes or []:
				if sibling is current:
					continue
				if is_file_input(session, sibling):
					return sibling
				result = _find_in_descendants(sibling, max_descendant_depth)
				if result:
					return result
		current = current.parent_node
	return None


async def get_selector_map(session: BrowserSession) -> dict[int, EnhancedDOMTreeNode]:
	"""Get the current selector map from cached state or DOM watchdog."""
	if session._cached_selector_map:
		return session._cached_selector_map
	if session._dom_watchdog and hasattr(session._dom_watchdog, 'selector_map'):
		return session._dom_watchdog.selector_map or {}
	return {}


async def get_index_by_id(session: BrowserSession, element_id: str) -> int | None:
	"""Find element index by its id attribute."""
	selector_map = await get_selector_map(session)
	for idx, element in selector_map.items():
		if element.attributes and element.attributes.get('id') == element_id:
			return idx
	return None


async def get_index_by_class(session: BrowserSession, class_name: str) -> int | None:
	"""Find element index by its class attribute."""
	selector_map = await get_selector_map(session)
	for idx, element in selector_map.items():
		if element.attributes:
			element_class = element.attributes.get('class', '')
			if class_name in element_class.split():
				return idx
	return None
