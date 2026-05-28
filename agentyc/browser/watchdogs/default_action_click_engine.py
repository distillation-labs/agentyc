"""Low-level click execution helpers for the default action watchdog."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agentyc.browser.watchdogs.default_action_click_core import (
	check_element_occlusion,
	click_element_node_impl,
	click_on_coordinate,
	move_mouse_before_click,
)
from agentyc.browser.watchdogs.default_action_click_special_cases import (
	execute_click_with_download_detection,
	handle_print_button_click,
	is_print_related_element,
)
from agentyc.dom.service import EnhancedDOMTreeNode


class DefaultActionClickEngineMixin:
	"""Protocol-level click helpers shared by element and coordinate handlers."""

	if TYPE_CHECKING:
		logger: Any
		browser_session: Any

	async def _execute_click_with_download_detection(
		self,
		click_coro,
		download_complete_timeout: float = 30.0,
	) -> dict | None:
		return await execute_click_with_download_detection(
			self,
			click_coro,
			download_complete_timeout=download_complete_timeout,
		)

	def _is_print_related_element(self, element_node: EnhancedDOMTreeNode) -> bool:
		return is_print_related_element(element_node)

	async def _handle_print_button_click(self, element_node: EnhancedDOMTreeNode) -> dict | None:
		return await handle_print_button_click(self, element_node)

	async def _check_element_occlusion(self, backend_node_id: int, x: float, y: float, cdp_session) -> bool:
		return await check_element_occlusion(self, backend_node_id, x, y, cdp_session)

	async def _move_mouse_before_click(self, cdp_session, session_id: str, x: float, y: float) -> None:
		await move_mouse_before_click(self, cdp_session, session_id, x, y)

	async def _click_element_node_impl(self, element_node) -> dict | None:
		return await click_element_node_impl(self, element_node)

	async def _click_on_coordinate(self, coordinate_x: int, coordinate_y: int, force: bool = False) -> dict | None:
		return await click_on_coordinate(self, coordinate_x, coordinate_y, force=force)
