"""DOM delegate methods for BrowserSession."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from agentyc.browser.session_dom import _get_element_bounds as session_dom_get_element_bounds
from agentyc.browser.session_dom import add_highlights as session_dom_add_highlights
from agentyc.browser.session_dom import find_file_input_near_element as session_dom_find_file_input_near_element
from agentyc.browser.session_dom import get_dom_element_at_coordinates as session_dom_get_dom_element_at_coordinates
from agentyc.browser.session_dom import get_dom_element_by_index as session_dom_get_dom_element_by_index
from agentyc.browser.session_dom import get_element_by_index as session_dom_get_element_by_index
from agentyc.browser.session_dom import get_element_coordinates as session_dom_get_element_coordinates
from agentyc.browser.session_dom import get_index_by_class as session_dom_get_index_by_class
from agentyc.browser.session_dom import get_index_by_id as session_dom_get_index_by_id
from agentyc.browser.session_dom import get_selector_map as session_dom_get_selector_map
from agentyc.browser.session_dom import highlight_coordinate_click as session_dom_highlight_coordinate_click
from agentyc.browser.session_dom import highlight_interaction_element as session_dom_highlight_interaction_element
from agentyc.browser.session_dom import is_file_input as session_dom_is_file_input
from agentyc.browser.session_dom import remove_highlights as session_dom_remove_highlights
from agentyc.browser.session_dom import screenshot_element as session_dom_screenshot_element
from agentyc.browser.session_dom import update_cached_selector_map as session_dom_update_cached_selector_map
from agentyc.browser.session_models import CDPSession
from agentyc.dom.views import DOMRect, EnhancedDOMTreeNode
from agentyc.observability import observe_debug

if TYPE_CHECKING:
	from agentyc.browser.session import BrowserSession


class SessionDOMMixin:
	def _session(self) -> BrowserSession:
		return cast('BrowserSession', self)

	async def get_dom_element_by_index(self, index: int) -> EnhancedDOMTreeNode | None:
		return await session_dom_get_dom_element_by_index(self._session(), index)

	def update_cached_selector_map(self, selector_map: dict[int, EnhancedDOMTreeNode]) -> None:
		session_dom_update_cached_selector_map(self._session(), selector_map)

	# Alias for backwards compatibility
	async def get_element_by_index(self, index: int) -> EnhancedDOMTreeNode | None:
		return await session_dom_get_element_by_index(self._session(), index)

	async def get_dom_element_at_coordinates(self, x: int, y: int) -> EnhancedDOMTreeNode | None:
		return await session_dom_get_dom_element_at_coordinates(self._session(), x, y)

	def is_file_input(self, element: Any) -> bool:
		return session_dom_is_file_input(self._session(), element)

	def find_file_input_near_element(
		self,
		node: 'EnhancedDOMTreeNode',
		max_height: int = 3,
		max_descendant_depth: int = 3,
	) -> 'EnhancedDOMTreeNode | None':
		return session_dom_find_file_input_near_element(self._session(), node, max_height, max_descendant_depth)

	async def get_selector_map(self) -> dict[int, EnhancedDOMTreeNode]:
		return await session_dom_get_selector_map(self._session())

	async def get_index_by_id(self, element_id: str) -> int | None:
		return await session_dom_get_index_by_id(self._session(), element_id)

	async def get_index_by_class(self, class_name: str) -> int | None:
		return await session_dom_get_index_by_class(self._session(), class_name)

	async def remove_highlights(self) -> None:
		await session_dom_remove_highlights(self._session())

	@observe_debug(ignore_input=True, ignore_output=True, name='get_element_coordinates')
	async def get_element_coordinates(self, backend_node_id: int, cdp_session: CDPSession) -> DOMRect | None:
		return await session_dom_get_element_coordinates(self._session(), backend_node_id, cdp_session)

	async def highlight_interaction_element(self, node: 'EnhancedDOMTreeNode') -> None:
		await session_dom_highlight_interaction_element(self._session(), node)

	async def highlight_coordinate_click(self, x: int, y: int) -> None:
		await session_dom_highlight_coordinate_click(self._session(), x, y)

	async def add_highlights(self, selector_map: dict[int, 'EnhancedDOMTreeNode']) -> None:
		await session_dom_add_highlights(self._session(), selector_map)

	async def screenshot_element(
		self,
		selector: str,
		path: str | None = None,
		format: str = 'png',
		quality: int | None = None,
	) -> bytes:
		return await session_dom_screenshot_element(self._session(), selector, path=path, format=format, quality=quality)

	async def _get_element_bounds(self, selector: str) -> dict | None:
		return await session_dom_get_element_bounds(self._session(), selector)
