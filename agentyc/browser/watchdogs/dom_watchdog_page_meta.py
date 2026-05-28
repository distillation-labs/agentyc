"""Page metadata helpers for the DOM watchdog."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from agentyc.dom.service import DomService

if TYPE_CHECKING:
	from agentyc.browser.views import PageInfo, PaginationButton
	from agentyc.dom.views import EnhancedDOMTreeNode


def detect_pagination_buttons(watchdog, selector_map: dict[int, EnhancedDOMTreeNode]) -> list[PaginationButton]:
	"""Detect pagination buttons from the DOM selector map."""
	from agentyc.browser.views import PaginationButton

	pagination_buttons_data = []
	try:
		watchdog.logger.debug('🔍 DOMWatchdog._detect_pagination_buttons: Detecting pagination buttons...')
		pagination_buttons_raw = DomService.detect_pagination_buttons(selector_map)
		pagination_buttons_data = [
			PaginationButton(
				button_type=button['button_type'],  # type: ignore
				backend_node_id=button['backend_node_id'],  # type: ignore
				text=button['text'],  # type: ignore
				selector=button['selector'],  # type: ignore
				is_disabled=button['is_disabled'],  # type: ignore
			)
			for button in pagination_buttons_raw
		]
		if pagination_buttons_data:
			watchdog.logger.debug(
				f'🔍 DOMWatchdog._detect_pagination_buttons: Found {len(pagination_buttons_data)} pagination buttons'
			)
	except Exception as error:
		watchdog.logger.warning(f'🔍 DOMWatchdog._detect_pagination_buttons: Pagination detection failed: {error}')

	return pagination_buttons_data


async def get_page_info(watchdog) -> PageInfo:
	"""Get comprehensive page information using a single CDP call."""
	from agentyc.browser.views import PageInfo

	cdp_session = await watchdog.browser_session.get_or_create_cdp_session(
		target_id=watchdog.browser_session.agent_focus_target_id,
		focus=False,
	)

	metrics_timeout = 0.25 if watchdog.browser_session.is_shared_browser_runtime else 10.0
	metrics = await asyncio.wait_for(
		cdp_session.cdp_client.send.Page.getLayoutMetrics(session_id=cdp_session.session_id),
		timeout=metrics_timeout,
	)

	layout_viewport = metrics.get('layoutViewport', {})
	visual_viewport = metrics.get('visualViewport', {})
	css_visual_viewport = metrics.get('cssVisualViewport', {})
	css_layout_viewport = metrics.get('cssLayoutViewport', {})
	content_size = metrics.get('contentSize', {})

	css_width = css_visual_viewport.get('clientWidth', css_layout_viewport.get('clientWidth', 1280.0))
	device_width = visual_viewport.get('clientWidth', css_width)
	device_pixel_ratio = device_width / css_width if css_width > 0 else 1.0

	viewport_width = int(css_layout_viewport.get('clientWidth') or layout_viewport.get('clientWidth', 1280))
	viewport_height = int(css_layout_viewport.get('clientHeight') or layout_viewport.get('clientHeight', 720))

	raw_page_width = content_size.get('width', viewport_width * device_pixel_ratio)
	raw_page_height = content_size.get('height', viewport_height * device_pixel_ratio)
	page_width = int(raw_page_width / device_pixel_ratio)
	page_height = int(raw_page_height / device_pixel_ratio)

	scroll_x = int(css_visual_viewport.get('pageX') or css_layout_viewport.get('pageX', 0))
	scroll_y = int(css_visual_viewport.get('pageY') or css_layout_viewport.get('pageY', 0))

	pixels_above = scroll_y
	pixels_below = max(0, page_height - viewport_height - scroll_y)
	pixels_left = scroll_x
	pixels_right = max(0, page_width - viewport_width - scroll_x)

	return PageInfo(
		viewport_width=viewport_width,
		viewport_height=viewport_height,
		page_width=page_width,
		page_height=page_height,
		scroll_x=scroll_x,
		scroll_y=scroll_y,
		pixels_above=pixels_above,
		pixels_below=pixels_below,
		pixels_left=pixels_left,
		pixels_right=pixels_right,
	)
