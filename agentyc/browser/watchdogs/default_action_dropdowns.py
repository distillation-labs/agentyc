"""Dropdown helpers for the default action watchdog."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agentyc.browser.events import GetDropdownOptionsEvent, SelectDropdownOptionEvent
from agentyc.browser.watchdogs.default_action_dropdown_options import (
	get_dropdown_options,
	handle_aria_combobox_options,
)
from agentyc.browser.watchdogs.default_action_dropdown_selection import select_dropdown_option


class DefaultActionDropdownMixin:
	"""Dropdown inspection and selection helpers."""

	if TYPE_CHECKING:
		logger: Any
		browser_session: Any

	async def on_GetDropdownOptionsEvent(self, event: GetDropdownOptionsEvent) -> dict[str, str]:
		return await get_dropdown_options(self, event)

	async def _handle_aria_combobox_options(
		self,
		cdp_session,
		object_id: str,
		combobox_info: dict,
		index_for_logging: int | str,
	) -> dict[str, str]:
		return await handle_aria_combobox_options(self, cdp_session, object_id, combobox_info, index_for_logging)

	async def on_SelectDropdownOptionEvent(self, event: SelectDropdownOptionEvent) -> dict[str, str]:
		return await select_dropdown_option(self, event)
