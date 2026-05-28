"""Async/CDP helpers for Python-based screenshot highlighting."""

from __future__ import annotations

import asyncio
import base64
import logging
import os

from agentyc.dom.views import DOMSelectorMap
from agentyc.utils import time_execution_async

from .python_highlights import create_highlighted_screenshot

logger = logging.getLogger(__name__)


async def get_viewport_info_from_cdp(cdp_session) -> tuple[float, int, int]:
	"""Get viewport information from a CDP session."""
	try:
		metrics = await cdp_session.cdp_client.send.Page.getLayoutMetrics(session_id=cdp_session.session_id)

		visual_viewport = metrics.get('visualViewport', {})
		css_visual_viewport = metrics.get('cssVisualViewport', {})
		css_layout_viewport = metrics.get('cssLayoutViewport', {})

		css_width = css_visual_viewport.get('clientWidth', css_layout_viewport.get('clientWidth', 1280.0))
		device_width = visual_viewport.get('clientWidth', css_width)
		device_pixel_ratio = device_width / css_width if css_width > 0 else 1.0

		scroll_x = int(css_visual_viewport.get('pageX', 0))
		scroll_y = int(css_visual_viewport.get('pageY', 0))

		return float(device_pixel_ratio), scroll_x, scroll_y
	except Exception as e:
		logger.debug(f'Failed to get viewport info from CDP: {e}')
		return 1.0, 0, 0


@time_execution_async('create_highlighted_screenshot_async')
async def create_highlighted_screenshot_async(
	screenshot_b64: str, selector_map: DOMSelectorMap, cdp_session=None, filter_highlight_ids: bool = True
) -> str:
	"""Async wrapper for creating highlighted screenshots."""
	device_pixel_ratio = 1.0
	viewport_offset_x = 0
	viewport_offset_y = 0

	if cdp_session:
		try:
			device_pixel_ratio, viewport_offset_x, viewport_offset_y = await get_viewport_info_from_cdp(cdp_session)
		except Exception as e:
			logger.debug(f'Failed to get viewport info from CDP: {e}')

	final_screenshot = await create_highlighted_screenshot(
		screenshot_b64, selector_map, device_pixel_ratio, viewport_offset_x, viewport_offset_y, filter_highlight_ids
	)

	filename = os.getenv('AGENTYC_SCREENSHOT_FILE')
	if filename:

		def _write_screenshot() -> None:
			try:
				with open(filename, 'wb') as f:
					f.write(base64.b64decode(final_screenshot))
				logger.debug('Saved screenshot to ' + str(filename))
			except Exception as e:
				logger.warning(f'Failed to save screenshot to {filename}: {e}')

		await asyncio.to_thread(_write_screenshot)
	return final_screenshot


__all__ = ['create_highlighted_screenshot_async', 'get_viewport_info_from_cdp']
