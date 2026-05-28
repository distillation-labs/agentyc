"""CDP interaction and session-state helpers for MCP tools."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, cast

from agentyc.browser.session import BrowserSession
from agentyc.mcp.debug_tools import _network_entry_started_since

_DRAG_MOUSE_MOVE_SETTLE_S = 0.01
_DRAG_MOUSE_PRESS_SETTLE_S = 0.005
_DRAG_STEP_SETTLE_S = 0.005


async def _get_html(self, selector: str | None = None) -> str:
	"""Get raw HTML of the page or a specific element."""
	if not self.browser_session:
		return 'Error: No browser session active'

	self._update_session_activity(self.browser_session.id)

	cdp_session = await self.browser_session.get_or_create_cdp_session(target_id=None, focus=False)
	if not cdp_session:
		return 'Error: No active CDP session'

	if selector:
		js = f'(function(){{ const el = document.querySelector({json.dumps(selector)}); return el ? el.outerHTML : null; }})()'
	else:
		js = 'document.documentElement.outerHTML'

	result = await cdp_session.cdp_client.send.Runtime.evaluate(
		params={'expression': js, 'returnByValue': True},
		session_id=cdp_session.session_id,
	)
	html = result.get('result', {}).get('value')
	if html is None:
		return f'No element found for selector: {selector}' if selector else 'Error: Could not get page HTML'
	return html


async def _screenshot(self, full_page: bool = False) -> tuple[str, str | None]:
	"""Take a screenshot. Returns (metadata_json, screenshot_b64 | None)."""
	if not self.browser_session:
		return 'Error: No browser session active', None

	import base64

	self._update_session_activity(self.browser_session.id)

	session = self.browser_session
	llm_processing_enabled = (
		session.llm_screenshot_size is not None or session.llm_screenshot_format != 'png' or session.llm_screenshot_grayscale
	)
	data = await self.browser_session.take_screenshot(full_page=full_page)

	if llm_processing_enabled:
		data = BrowserSession.resize_screenshot_for_llm(
			data,
			target_size=session.llm_screenshot_size,
			target_format=session.llm_screenshot_format,
			quality=session.llm_screenshot_quality,
			grayscale=session.llm_screenshot_grayscale,
		)

	b64 = base64.b64encode(data).decode()

	state = await session.get_browser_state_summary(include_screenshot=False)
	result: dict[str, Any] = {'size_bytes': len(data)}
	if state.page_info:
		result['viewport'] = {
			'width': state.page_info.viewport_width,
			'height': state.page_info.viewport_height,
		}
	return json.dumps(result), b64


async def _get_viewport_coords(self, backend_node_id: int) -> tuple[float, float] | None:
	"""Get viewport-relative center coordinates for an element using DOM.getContentQuads."""
	try:
		cdp_session = await self.browser_session.get_or_create_cdp_session(target_id=None, focus=False)  # type: ignore[union-attr]
		result = await cdp_session.cdp_client.send.DOM.getContentQuads(
			params={'backendNodeId': backend_node_id},
			session_id=cdp_session.session_id,
		)
		quads = result.get('quads', [])
		if not quads:
			return None
		quad = quads[0]
		if len(quad) < 8:
			return None
		cx = sum(quad[i] for i in range(0, 8, 2)) / 4
		cy = sum(quad[i] for i in range(1, 8, 2)) / 4
		return cx, cy
	except Exception:
		return None


async def _resolve_element_coords(
	self, ref: str | None, index: int | None, fallback_x: int | None, fallback_y: int | None
) -> tuple[float, float]:
	"""Resolve element ref/index to viewport coordinates using live CDP quads."""
	if fallback_x is not None and fallback_y is not None:
		return float(fallback_x), float(fallback_y)
	if ref is None and index is None:
		raise ValueError('Provide ref/index or explicit coordinates')
	resolved_index = self._resolve_element_index(index=index, ref=ref)
	coords = await self._get_viewport_coords(resolved_index)
	if coords:
		return coords
	element = await self.browser_session.get_dom_element_by_index(resolved_index)  # type: ignore[union-attr]
	if element is None:
		await self._refresh_selector_map()
		element = await self.browser_session.get_dom_element_by_index(resolved_index)  # type: ignore[union-attr]
	if element is None:
		raise ValueError(f'Element {ref or resolved_index} not found. Refresh state first.')
	if element.absolute_position:
		p = element.absolute_position
		return p.x + p.width / 2, p.y + p.height / 2
	raise ValueError(f'Could not determine coordinates for element {ref or resolved_index}')


async def _hover(
	self, ref: str | None = None, index: int | None = None, coordinate_x: int | None = None, coordinate_y: int | None = None
) -> str:
	"""Hover over element to trigger CSS :hover and JS mouseover/mouseenter."""
	if not self.browser_session:
		return 'Error: No browser session active'
	self._update_session_activity(self.browser_session.id)
	self._mark_browser_state_cache_dirty()
	try:
		x, y = await self._resolve_element_coords(ref, index, coordinate_x, coordinate_y)
		cdp_session = await self.browser_session.get_or_create_cdp_session(target_id=None, focus=False)
		await cdp_session.cdp_client.send.Input.dispatchMouseEvent(
			params={'type': 'mouseMoved', 'x': x, 'y': y, 'button': 'none'},
			session_id=cdp_session.session_id,
		)
		await asyncio.sleep(0.1)
		label = ref or (f'index {index}' if index else f'({coordinate_x},{coordinate_y})')
		return f'Hovered over {label}. Use browser_get_state to see hover-triggered elements (dropdowns, tooltips, etc.).'
	except ValueError as e:
		return self._format_action_error(str(e), default_code='stale_ref')
	except Exception as e:
		return self._format_action_error(str(e), default_code='action_failed')


async def _double_click(
	self, ref: str | None = None, index: int | None = None, coordinate_x: int | None = None, coordinate_y: int | None = None
) -> str:
	"""Double-click an element."""
	if not self.browser_session:
		return 'Error: No browser session active'
	self._update_session_activity(self.browser_session.id)
	self._mark_browser_state_cache_dirty()
	try:
		x, y = await self._resolve_element_coords(ref, index, coordinate_x, coordinate_y)
		cdp_session = await self.browser_session.get_or_create_cdp_session(target_id=None, focus=False)
		sid = cdp_session.session_id
		for _ in range(2):
			await cdp_session.cdp_client.send.Input.dispatchMouseEvent(
				params={'type': 'mousePressed', 'x': x, 'y': y, 'button': 'left', 'clickCount': 2},
				session_id=sid,
			)
			await cdp_session.cdp_client.send.Input.dispatchMouseEvent(
				params={'type': 'mouseReleased', 'x': x, 'y': y, 'button': 'left', 'clickCount': 2},
				session_id=sid,
			)
		label = ref or (f'index {index}' if index else f'({coordinate_x},{coordinate_y})')
		return f'Double-clicked {label}'
	except ValueError as e:
		return self._format_action_error(str(e), default_code='stale_ref')
	except Exception as e:
		return self._format_action_error(str(e), default_code='action_failed')


async def _drag_to(
	self,
	source_ref: str | None = None,
	target_ref: str | None = None,
	source_x: int | None = None,
	source_y: int | None = None,
	target_x: int | None = None,
	target_y: int | None = None,
	steps: int = 10,
) -> str:
	"""Drag from one element or coordinate to another."""
	if not self.browser_session:
		return 'Error: No browser session active'
	self._update_session_activity(self.browser_session.id)
	self._mark_browser_state_cache_dirty()
	try:
		sx, sy = await self._resolve_element_coords(source_ref, None, source_x, source_y)
		tx, ty = await self._resolve_element_coords(target_ref, None, target_x, target_y)

		cdp_session = await self.browser_session.get_or_create_cdp_session(target_id=None, focus=False)
		sid = cdp_session.session_id

		await cdp_session.cdp_client.send.Input.dispatchMouseEvent(
			params={'type': 'mouseMoved', 'x': sx, 'y': sy}, session_id=sid
		)
		await asyncio.sleep(_DRAG_MOUSE_MOVE_SETTLE_S)
		await cdp_session.cdp_client.send.Input.dispatchMouseEvent(
			params={'type': 'mousePressed', 'x': sx, 'y': sy, 'button': 'left', 'clickCount': 1}, session_id=sid
		)
		await asyncio.sleep(_DRAG_MOUSE_PRESS_SETTLE_S)

		n = max(steps, 2)
		for i in range(1, n + 1):
			mx = sx + (tx - sx) * i / n
			my = sy + (ty - sy) * i / n
			await cdp_session.cdp_client.send.Input.dispatchMouseEvent(
				params={'type': 'mouseMoved', 'x': mx, 'y': my, 'button': 'left'}, session_id=sid
			)
			await asyncio.sleep(_DRAG_STEP_SETTLE_S)

		await cdp_session.cdp_client.send.Input.dispatchMouseEvent(
			params={'type': 'mouseReleased', 'x': tx, 'y': ty, 'button': 'left', 'clickCount': 1}, session_id=sid
		)

		return f'Dragged from ({sx:.0f},{sy:.0f}) to ({tx:.0f},{ty:.0f})'
	except ValueError as e:
		return self._format_action_error(str(e), default_code='invalid_argument')
	except Exception as e:
		return self._format_action_error(str(e), default_code='action_failed')


async def _scroll_to_text(self, text: str) -> str:
	"""Scroll page until given text is visible in the viewport."""
	if not self.browser_session:
		return 'Error: No browser session active'
	self._update_session_activity(self.browser_session.id)
	self._mark_browser_state_cache_dirty()
	from agentyc.browser.events import ScrollToTextEvent

	event = self.browser_session.event_bus.dispatch(ScrollToTextEvent(text=text))
	await event
	try:
		await event.event_result(raise_if_any=True, raise_if_none=False)
	except Exception as e:
		return self._format_action_error(str(e), default_code='not_found')
	return f'Scrolled to text: {text!r}'


async def _save_state(self, path: str | None = None) -> str:
	"""Save browser session state (cookies, localStorage) to a file."""
	if not self.browser_session:
		return 'Error: No browser session active'
	self._update_session_activity(self.browser_session.id)
	from agentyc.browser.events import SaveStorageStateEvent

	save_path = path or str(Path.home() / '.agentyc-mcp' / 'browser-state.json')
	event = self.browser_session.event_bus.dispatch(SaveStorageStateEvent(path=save_path))
	await event
	try:
		await event.event_result(raise_if_any=True, raise_if_none=False)
	except Exception as e:
		return self._format_action_error(str(e), default_code='action_failed')
	return f'Browser state saved to: {save_path}'


async def _load_state(self, path: str) -> str:
	"""Restore browser session state from a file."""
	if not self.browser_session:
		return 'Error: No browser session active'
	self._update_session_activity(self.browser_session.id)
	from agentyc.browser.events import LoadStorageStateEvent

	event = self.browser_session.event_bus.dispatch(LoadStorageStateEvent(path=path))
	await event
	try:
		await event.event_result(raise_if_any=True, raise_if_none=False)
	except Exception as e:
		return self._format_action_error(str(e), default_code='action_failed')
	return f'Browser state loaded from: {path}'


async def _wait_for_network_idle(self, timeout_seconds: float = 10.0, idle_duration_ms: int = 500) -> str:
	"""Wait until no pending network requests for idle_duration_ms."""
	if not self.browser_session:
		return 'Error: No browser session active'
	timeout = min(timeout_seconds, 30.0)
	idle_needed = idle_duration_ms / 1000.0
	import time as _time

	start = _time.monotonic()
	try:
		await self.browser_session.get_or_create_cdp_session(target_id=None, focus=False)
		if not self._cdp_events_registered:
			try:
				await self._register_cdp_event_listeners()
			except Exception:
				pass
		baseline_started_at = time.time()
		idle_start: float | None = None
		deadline = start + timeout
		while _time.monotonic() < deadline:
			active_pending = [
				entry for entry in self._network_pending.values() if _network_entry_started_since(entry, baseline_started_at)
			]
			if not active_pending:
				now = _time.monotonic()
				if idle_start is None:
					idle_start = now
				elif now - idle_start >= idle_needed:
					elapsed = now - start
					return f'Network idle after {elapsed:.1f}s'
			else:
				idle_start = None
			await asyncio.sleep(min(idle_needed / 2 if idle_needed > 0 else 0.05, 0.05))

		elapsed = _time.monotonic() - start
		return f'Network idle wait timed out after {elapsed:.1f}s — proceeding'
	except Exception as e:
		await asyncio.sleep(min(idle_needed, 1.0))
		return f'Network idle wait completed (fallback mode): {e}'


async def _right_click(
	self, ref: str | None = None, index: int | None = None, coordinate_x: int | None = None, coordinate_y: int | None = None
) -> str:
	"""Right-click an element to open its context menu."""
	if not self.browser_session:
		return 'Error: No browser session active'
	self._update_session_activity(self.browser_session.id)
	try:
		cx, cy = await self._resolve_element_coords(ref=ref, index=index, fallback_x=coordinate_x, fallback_y=coordinate_y)
	except ValueError as e:
		return self._format_action_error(str(e), default_code='element_not_found')
	cdp_session = await self.browser_session.get_or_create_cdp_session(target_id=None, focus=False)
	for event_type in ('mousePressed', 'mouseReleased'):
		await cdp_session.cdp_client.send.Input.dispatchMouseEvent(
			params={'type': event_type, 'x': cx, 'y': cy, 'button': 'right', 'clickCount': 1, 'buttons': 2},
			session_id=cdp_session.session_id,
		)
	return f'Right-clicked at ({cx:.0f},{cy:.0f})'


async def _get_cookies(self) -> str:
	"""Return all cookies for the current page URL."""
	if not self.browser_session:
		return 'Error: No browser session active'
	self._update_session_activity(self.browser_session.id)
	cdp_session = await self.browser_session.get_or_create_cdp_session(target_id=None, focus=False)
	try:
		url = await self.browser_session.get_current_page_url()
		result = await cdp_session.cdp_client.send.Network.getCookies(
			params={'urls': [url]},
			session_id=cdp_session.session_id,
		)
		cookies = result.get('cookies', [])
		if not cookies:
			return 'No cookies found for the current page'
		simplified = [
			{k: v for k, v in c.items() if k in ('name', 'value', 'domain', 'path', 'secure', 'httpOnly', 'expires')}
			for c in cookies
		]
		return json.dumps(simplified)
	except Exception as e:
		return self._format_action_error(str(e), default_code='action_failed')


async def _set_cookies(self, cookies: list[dict[str, Any]]) -> str:
	"""Set one or more cookies."""
	if not self.browser_session:
		return 'Error: No browser session active'
	self._update_session_activity(self.browser_session.id)
	cdp_session = await self.browser_session.get_or_create_cdp_session(target_id=None, focus=False)
	try:
		await cdp_session.cdp_client.send.Network.setCookies(
			params=cast(Any, {'cookies': cookies}),
			session_id=cdp_session.session_id,
		)
		names = [c.get('name', '?') for c in cookies]
		return f'Set {len(cookies)} cookie(s): {", ".join(names)}'
	except Exception as e:
		return self._format_action_error(str(e), default_code='action_failed')


async def _clear_cookies(self, name: str | None = None) -> str:
	"""Clear cookies — all for the current domain, or a specific cookie by name."""
	if not self.browser_session:
		return 'Error: No browser session active'
	self._update_session_activity(self.browser_session.id)
	cdp_session = await self.browser_session.get_or_create_cdp_session(target_id=None, focus=False)
	try:
		if name:
			url = await self.browser_session.get_current_page_url()
			await cdp_session.cdp_client.send.Network.deleteCookies(
				params={'name': name, 'url': url},
				session_id=cdp_session.session_id,
			)
			return f'Deleted cookie: {name}'
		await cdp_session.cdp_client.send.Network.clearBrowserCookies(session_id=cdp_session.session_id)
		return 'Cleared all browser cookies'
	except Exception as e:
		return self._format_action_error(str(e), default_code='action_failed')
