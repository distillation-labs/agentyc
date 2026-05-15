"""Helpers for browser window bounds and target window context."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agentyc.browser.session_models import BrowserWindowBounds, BrowserWindowContext


def build_create_target_params(
	*,
	url: str,
	background: bool = False,
	new_window: bool = False,
	window_bounds: BrowserWindowBounds | None = None,
	browser_context_id: str | None = None,
) -> dict[str, Any]:
	params: dict[str, Any] = {'url': url, 'background': background}
	if new_window:
		params['newWindow'] = True
	if browser_context_id:
		params['browserContextId'] = browser_context_id
	if window_bounds and window_bounds.left is not None:
		params['left'] = window_bounds.left
	if window_bounds and window_bounds.top is not None:
		params['top'] = window_bounds.top
	if window_bounds and window_bounds.width is not None:
		params['width'] = window_bounds.width
	if window_bounds and window_bounds.height is not None:
		params['height'] = window_bounds.height
	return params


def normalize_window_bounds(bounds: dict[str, Any] | BrowserWindowBounds | None) -> BrowserWindowBounds | None:
	if bounds is None:
		return None
	if isinstance(bounds, BrowserWindowBounds):
		return bounds
	return BrowserWindowBounds.model_validate(bounds)


def window_context_from_cdp(payload: Mapping[str, Any]) -> BrowserWindowContext | None:
	window_id = payload.get('windowId')
	if window_id is None:
		return None
	bounds_payload = payload.get('bounds')
	return BrowserWindowContext(
		window_id=window_id,
		bounds=normalize_window_bounds(bounds_payload),
	)
