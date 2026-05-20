"""CDP-native MCP helpers and tab utilities."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, cast


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

	data = await self.browser_session.take_screenshot(full_page=full_page)
	b64 = base64.b64encode(data).decode()

	state = await self.browser_session.get_browser_state_summary(include_screenshot=False)
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
	try:
		sx, sy = await self._resolve_element_coords(source_ref, None, source_x, source_y)
		tx, ty = await self._resolve_element_coords(target_ref, None, target_x, target_y)

		cdp_session = await self.browser_session.get_or_create_cdp_session(target_id=None, focus=False)
		sid = cdp_session.session_id

		await cdp_session.cdp_client.send.Input.dispatchMouseEvent(
			params={'type': 'mouseMoved', 'x': sx, 'y': sy}, session_id=sid
		)
		await asyncio.sleep(0.02)
		await cdp_session.cdp_client.send.Input.dispatchMouseEvent(
			params={'type': 'mousePressed', 'x': sx, 'y': sy, 'button': 'left', 'clickCount': 1}, session_id=sid
		)
		await asyncio.sleep(0.01)

		n = max(steps, 2)
		for i in range(1, n + 1):
			mx = sx + (tx - sx) * i / n
			my = sy + (ty - sy) * i / n
			await cdp_session.cdp_client.send.Input.dispatchMouseEvent(
				params={'type': 'mouseMoved', 'x': mx, 'y': my, 'button': 'left'}, session_id=sid
			)
			await asyncio.sleep(0.01)

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
				entry for entry in self._network_pending.values() if float(entry.get('start_time') or 0.0) >= baseline_started_at
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


async def _register_cdp_event_listeners(self) -> None:
	"""Register native CDP event listeners for console and network capture."""
	if self._cdp_events_registered or not self.browser_session:
		return
	cdp_session = await self.browser_session.get_or_create_cdp_session(target_id=None, focus=False)
	our_session_id = cdp_session.session_id

	sm = getattr(self.browser_session, 'session_manager', None)
	all_sids: list[str] = list(sm.get_all_sessions().keys()) if sm else [our_session_id]
	if not all_sids:
		all_sids = [our_session_id]
	for _sid in all_sids:
		try:
			await cdp_session.cdp_client.send.Runtime.enable(session_id=_sid)
		except Exception:
			pass
		try:
			# Network domain events require explicit enablement per CDP session.
			await cdp_session.cdp_client.send.Network.enable(session_id=_sid)
		except Exception:
			pass

	self._cdp_client_for_runtime = cdp_session.cdp_client

	def _arg_to_str(arg: Any) -> str:
		if isinstance(arg, dict):
			return str(arg.get('description') or arg.get('value') or arg.get('type') or '')
		return str(arg)

	def _ms_to_ts(ts_ms: float) -> str:
		import datetime as _dt

		try:
			dt = _dt.datetime.utcfromtimestamp(ts_ms / 1000.0)
			return dt.strftime('%H:%M:%S.') + f'{int(ts_ms % 1000):03d}'
		except (OSError, OverflowError, ValueError):
			return ''

	def _is_our_session(session_id: str | None) -> bool:
		if self.browser_session is None:
			return False
		sm = getattr(self.browser_session, 'session_manager', None)
		if sm is None:
			return True
		sessions: dict[str, Any] = getattr(sm, '_sessions', {}) or {}
		return session_id in sessions

	def on_console_api_called(event: Any, session_id: str | None = None) -> None:
		if not _is_our_session(session_id) or not isinstance(event, dict):
			return
		level = str(event.get('type', 'log'))
		if level == 'warning':
			level = 'warn'
		args = event.get('args') or []
		text = ' '.join(_arg_to_str(a) for a in args).strip()
		if not text:
			return
		self._console_log_buffer.append({'level': level, 'time': _ms_to_ts(event.get('timestamp') or 0), 'text': text})

	def on_exception_thrown(event: Any, session_id: str | None = None) -> None:
		if not _is_our_session(session_id) or not isinstance(event, dict):
			return
		details = event.get('exceptionDetails') or {}
		msg = details.get('text') or ''
		exc = details.get('exception') or {}
		desc = exc.get('description') or exc.get('value') or ''
		text = f'{msg} {desc}'.strip() or 'Unknown exception'
		url = details.get('url', '')
		line = details.get('lineNumber', '')
		if url:
			text = f'{text} at {url}:{line}'
		self._console_log_buffer.append({'level': 'error', 'time': _ms_to_ts(event.get('timestamp') or 0), 'text': text})

	cdp_session.cdp_client.register.Runtime.consoleAPICalled(on_console_api_called)
	cdp_session.cdp_client.register.Runtime.exceptionThrown(on_exception_thrown)

	def on_request_will_be_sent(event: Any, session_id: str | None = None) -> None:
		if not _is_our_session(session_id) or not isinstance(event, dict):
			return
		req_id = event.get('requestId', '')
		req = event.get('request') or {}
		entry: dict[str, Any] = {
			'request_id': req_id,
			'url': req.get('url', ''),
			'method': req.get('method', 'GET'),
			'type': event.get('type', 'Other'),
			'status': None,
			'status_text': None,
			'error': None,
			'start_time': event.get('timestamp', time.time()),
			'duration_ms': None,
			'req_headers': req.get('headers') or {},
		}
		self._network_pending[req_id] = entry

	def on_response_received(event: Any, session_id: str | None = None) -> None:
		if not _is_our_session(session_id) or not isinstance(event, dict):
			return
		req_id = event.get('requestId', '')
		entry = self._network_pending.get(req_id)
		if entry is None:
			return
		resp = event.get('response') or {}
		entry['status'] = resp.get('status')
		entry['status_text'] = resp.get('statusText')
		entry['resp_headers'] = resp.get('headers') or {}

	def on_loading_finished(event: Any, session_id: str | None = None) -> None:
		if not _is_our_session(session_id) or not isinstance(event, dict):
			return
		req_id = event.get('requestId', '')
		entry = self._network_pending.get(req_id)
		if entry is None:
			return
		now = event.get('timestamp', time.time())
		start = entry['start_time']
		entry['duration_ms'] = round((now - start) * 1000, 1)
		self._network_log_buffer.append(dict(entry))
		self._network_pending.pop(req_id, None)

	def on_loading_failed(event: Any, session_id: str | None = None) -> None:
		if not _is_our_session(session_id) or not isinstance(event, dict):
			return
		req_id = event.get('requestId', '')
		entry = self._network_pending.get(req_id)
		if entry is None:
			return
		entry['error'] = event.get('errorText', 'Failed')
		now = event.get('timestamp', time.time())
		entry['duration_ms'] = round((now - entry['start_time']) * 1000, 1)
		self._network_log_buffer.append(dict(entry))
		self._network_pending.pop(req_id, None)

	cdp_session.cdp_client.register.Network.requestWillBeSent(on_request_will_be_sent)
	cdp_session.cdp_client.register.Network.responseReceived(on_response_received)
	cdp_session.cdp_client.register.Network.loadingFinished(on_loading_finished)
	cdp_session.cdp_client.register.Network.loadingFailed(on_loading_failed)

	def on_trace_data_collected(event: Any, session_id: str | None = None) -> None:
		if isinstance(event, dict):
			value = event.get('value', [])
			if isinstance(value, list):
				self._trace_events.extend(value)

	if hasattr(cdp_session.cdp_client.register, 'Tracing'):
		cdp_session.cdp_client.register.Tracing.dataCollected(on_trace_data_collected)

	self._cdp_events_registered = True


async def _get_console_logs(self, level: str = 'all', max_entries: int = 50) -> str:
	"""Return recent browser console messages from the CDP-native capture buffer."""
	if not self.browser_session:
		return 'Error: No browser session active'
	self._update_session_activity(self.browser_session.id)
	entries = list(self._console_log_buffer)
	if level != 'all':
		entries = [e for e in entries if e.get('level') == level]
	entries = entries[-max_entries:]
	return json.dumps(entries)


async def _get_network_log(
	self, type_filter: str = 'all', status_filter: str = 'all', max_entries: int = 50, include_headers: bool = False
) -> str:
	"""Return recent network requests captured via CDP Network domain events."""
	if not self.browser_session:
		return 'Error: No browser session active'
	self._update_session_activity(self.browser_session.id)
	if not self._cdp_events_registered:
		try:
			await self._register_cdp_event_listeners()
		except Exception:
			pass
	entries = list(self._network_log_buffer)
	if type_filter != 'all':
		entries = [e for e in entries if e.get('type', '').lower() == type_filter.lower()]
	if status_filter == 'errors':
		entries = [e for e in entries if e.get('error') or (e.get('status') or 0) >= 400]
	elif status_filter == 'success':
		entries = [e for e in entries if not e.get('error') and 200 <= (e.get('status') or 0) < 400]
	entries = entries[-max_entries:]
	if not entries:
		return json.dumps([])
	_omit = {'request_id', 'start_time'} if include_headers else {'request_id', 'start_time', 'req_headers', 'resp_headers'}
	display = [{k: v for k, v in e.items() if k not in _omit and v is not None} for e in entries]
	return json.dumps(display)


async def _get_downloads(self) -> str:
	"""List files downloaded during the current session."""
	if not self.browser_session:
		return 'Error: No browser session active'
	files = self.browser_session.downloaded_files
	if not files:
		return 'No files downloaded in this session.'
	result = []
	for f in files:
		p = Path(f)
		result.append(
			{
				'path': str(p),
				'name': p.name,
				'size_bytes': p.stat().st_size if p.exists() else 0,
			}
		)
	return json.dumps(result)


async def _set_viewport(self, width: int, height: int, device_scale_factor: float = 1.0) -> str:
	"""Set the browser viewport dimensions for the current tab."""
	if not self.browser_session:
		return 'Error: No browser session active'
	try:
		cdp_session = await self.browser_session.get_or_create_cdp_session(target_id=None, focus=False)
		await cdp_session.cdp_client.send.Emulation.setDeviceMetricsOverride(
			params={
				'width': width,
				'height': height,
				'deviceScaleFactor': device_scale_factor,
				'mobile': False,
			},
			session_id=cdp_session.session_id,
		)
		return f'Viewport set to {width}x{height} (scale: {device_scale_factor})'
	except Exception as e:
		return self._format_action_error(str(e), default_code='action_failed')


async def _get_focused_element(self) -> str:
	"""Return information about the element that currently has keyboard focus."""
	if not self.browser_session:
		return 'Error: No browser session active'

	self._update_session_activity(self.browser_session.id)
	cdp_session = await self.browser_session.get_or_create_cdp_session(target_id=None, focus=False)
	if not cdp_session:
		return 'Error: No active CDP session'

	js = """(function() {
		const el = document.activeElement;
		if (!el || el.tagName === 'BODY' || el.tagName === 'HTML') return null;
		return {
			tag: el.tagName.toLowerCase(),
			id: el.id || null,
			name: el.getAttribute('name') || null,
			type: el.getAttribute('type') || null,
			placeholder: el.getAttribute('placeholder') || null,
			ariaLabel: el.getAttribute('aria-label') || null,
			value: el.value !== undefined ? String(el.value).substring(0, 100) : (el.textContent || '').trim().substring(0, 100),
			role: el.getAttribute('role') || null
		};
	})()"""

	result = await cdp_session.cdp_client.send.Runtime.evaluate(
		params={'expression': js, 'returnByValue': True},
		session_id=cdp_session.session_id,
	)
	value = result.get('result', {}).get('value')
	if value is None:
		return 'No element has focus (or focus is on document body)'
	return json.dumps(value)


async def _list_tabs(self) -> str:
	"""List all open tabs."""
	if not self.browser_session:
		return 'Error: No browser session active'

	from agentyc.mcp.state import build_tab_groups_payload, serialize_tab_info

	tabs_info = await self.browser_session.get_tabs()
	tabs = [serialize_tab_info(tab) for tab in tabs_info]
	current_target_id = getattr(self.browser_session, 'agent_focus_target_id', None)
	current_tab_id = str(current_target_id)[-4:] if current_target_id else None
	payload = {
		'tabs': tabs,
		'tab_groups': build_tab_groups_payload(tabs, current_tab_id=current_tab_id),
	}
	return json.dumps(payload)


async def _switch_tab(self, tab_id: str) -> str:
	"""Switch to a different tab."""
	if not self.browser_session:
		return 'Error: No browser session active'

	from agentyc.browser.events import SwitchTabEvent

	try:
		target_id = await self.browser_session.get_target_id_from_tab_id(tab_id)
		event = self.browser_session.event_bus.dispatch(SwitchTabEvent(target_id=target_id))
		await event
		await event.event_result(raise_if_any=True, raise_if_none=False)
	except Exception as e:
		return self._format_action_error(str(e), default_code='action_failed')
	if self.browser_session.agent_focus_target_id != target_id:
		return self._format_action_error(
			f'Switch tab to {tab_id} completed but the requested tab did not become active.',
			default_code='postcondition_failed',
		)
	if self._cdp_client_for_runtime:
		try:
			focused_session = await self.browser_session.get_or_create_cdp_session(target_id=None, focus=False)
			await self._cdp_client_for_runtime.send.Runtime.enable(session_id=focused_session.session_id)
			await self._cdp_client_for_runtime.send.Network.enable(session_id=focused_session.session_id)
		except Exception:
			pass
	current_url = await self.browser_session.get_current_page_url()
	return f'Switched to tab {tab_id}: {current_url}'


async def _new_tab(self, url: str = 'about:blank') -> str:
	"""Create a new browser tab, switch focus to it, and optionally navigate to a URL."""
	if not self.browser_session:
		return 'Error: No browser session active'

	self._update_session_activity(self.browser_session.id)

	try:
		from agentyc.browser.events import AgentFocusChangedEvent, TabCreatedEvent

		new_page = await self.browser_session.new_page(url if url not in ('about:blank', '') else None)
		new_target_id = new_page._target_id

		await self.browser_session.event_bus.dispatch(TabCreatedEvent(target_id=new_target_id, url=url))
		focus_event = self.browser_session.event_bus.dispatch(AgentFocusChangedEvent(target_id=new_target_id, url=url))
		await focus_event

		if self._cdp_client_for_runtime:
			try:
				focused_session = await self.browser_session.get_or_create_cdp_session(target_id=None, focus=False)
				await self._cdp_client_for_runtime.send.Runtime.enable(session_id=focused_session.session_id)
				await self._cdp_client_for_runtime.send.Network.enable(session_id=focused_session.session_id)
			except Exception:
				pass

		current_url = await self.browser_session.get_current_page_url()
		return f'Opened new tab: {current_url}'
	except Exception as e:
		return self._format_action_error(str(e), default_code='action_failed')


async def _close_tab(self, tab_id: str) -> str:
	"""Close a specific tab."""
	if not self.browser_session:
		return 'Error: No browser session active'

	from agentyc.browser.events import CloseTabEvent

	try:
		target_id = await self.browser_session.get_target_id_from_tab_id(tab_id)
		event = self.browser_session.event_bus.dispatch(CloseTabEvent(target_id=target_id))
		await event
		await event.event_result(raise_if_any=True, raise_if_none=False)
	except Exception as e:
		return self._format_action_error(str(e), default_code='action_failed')
	deadline = time.monotonic() + 0.3
	while True:
		if not any(tab.target_id == target_id for tab in await self.browser_session.get_tabs()):
			break
		if time.monotonic() >= deadline:
			return self._format_action_error(
				f'Close tab {tab_id} completed but the tab is still present.',
				default_code='postcondition_failed',
			)
		await asyncio.sleep(0.05)
	if self.browser_session.agent_focus_target_id is None:
		await self.browser_session.session_manager.ensure_valid_focus(timeout=3.0)
	current_url = await self.browser_session.get_current_page_url()
	return f'Closed tab # {tab_id}, now on {current_url}'


async def _wait_for_stable_dom(self, timeout_seconds: float = 10.0, quiet_ms: int = 500) -> str:
	"""Wait until DOM mutations settle for the quiet period."""
	if not self.browser_session:
		return 'Error: No browser session active'
	try:
		cdp_session = await self.browser_session.get_or_create_cdp_session(target_id=None, focus=False)
		if not cdp_session:
			return 'Error: No active CDP session'
		code = f"""
		(async () => {{
			const quietMs = {quiet_ms};
			const timeoutMs = {int(timeout_seconds * 1000)};
			return new Promise((resolve, reject) => {{
				const timer = setTimeout(() => resolve("timeout"), timeoutMs);
				let timeoutId;
				const observer = new MutationObserver(() => {{
					clearTimeout(timeoutId);
					timeoutId = setTimeout(() => {{
						observer.disconnect();
						clearTimeout(timer);
						resolve("stable");
					}}, quietMs);
				}});
				observer.observe(document.documentElement, {{
					childList: true, subtree: true, attributes: true, characterData: true,
				}});
				timeoutId = setTimeout(() => {{
					observer.disconnect();
					clearTimeout(timer);
					resolve("stable");
				}}, quietMs);
			}});
		}})()
		"""
		result = await cdp_session.cdp_client.send.Runtime.evaluate(
			params={'expression': code, 'awaitPromise': True, 'returnByValue': True},
			session_id=cdp_session.session_id,
		)
		status = (result.get('result') or {}).get('value', 'unknown')
		return f'DOM stable after quiet period ({status})'
	except Exception as e:
		return self._format_action_error(str(e), default_code='action_failed')


async def _handle_dialog(self, accept: bool = True, prompt_text: str | None = None) -> str:
	"""Accept or dismiss a JavaScript dialog."""
	if not self.browser_session:
		return 'Error: No browser session active'
	try:
		cdp_session = await self.browser_session.get_or_create_cdp_session(target_id=None, focus=False)
		if not cdp_session:
			return 'Error: No active CDP session'
		params: dict[str, Any] = {'accept': accept}
		if prompt_text is not None:
			params['promptText'] = prompt_text
		await cdp_session.cdp_client.send.Page.handleJavaScriptDialog(
			params=params,
			session_id=cdp_session.session_id,
		)
		return f'Dialog {"accepted" if accept else "dismissed"}'
	except Exception as e:
		return self._format_action_error(str(e), default_code='action_failed')


async def _get_attribute(self, name: str, ref: str | None = None, index: int | None = None) -> str:
	"""Get a specific attribute from a page element."""
	if not self.browser_session:
		return 'Error: No browser session active'
	try:
		self._update_session_activity(self.browser_session.id)

		from agentyc.mcp.state import parse_element_ref

		if ref:
			backend_node_id = parse_element_ref(ref)
		elif index is not None:
			backend_node_id = index
		else:
			return 'Error: Either ref or index is required'

		cdp_session = await self.browser_session.get_or_create_cdp_session(target_id=None, focus=False)
		if not cdp_session:
			return 'Error: No active CDP session'

		resolve_result = await cdp_session.cdp_client.send.DOM.resolveNode(
			params={'backendNodeId': backend_node_id},
			session_id=cdp_session.session_id,
		)
		remote_obj = resolve_result.get('object', {})
		object_id = remote_obj.get('objectId')
		if not object_id:
			return 'Error: Could not resolve element'

		call_result = await cdp_session.cdp_client.send.Runtime.callFunctionOn(
			params={
				'functionDeclaration': f'function() {{ return this.getAttribute({json.dumps(name)}); }}',
				'objectId': object_id,
				'returnByValue': True,
			},
			session_id=cdp_session.session_id,
		)
		attr_value = call_result.get('result', {}).get('value')
		if attr_value is None:
			return f'Attribute "{name}" not found on element'
		return str(attr_value)
	except Exception as e:
		return self._format_action_error(str(e), default_code='action_failed')


async def _clear_logs(self, console: bool = True, network: bool = True) -> str:
	"""Clear console and/or network log buffers."""
	if not hasattr(self, '_console_log_buffer') or not hasattr(self, '_network_log_buffer'):
		return 'Error: Log buffers not initialized'
	cleared = []
	if console:
		self._console_log_buffer.clear()
		cleared.append('console')
	if network:
		self._network_log_buffer.clear()
		self._network_pending.clear()
		cleared.append('network')
	return f'Cleared: {", ".join(cleared)} logs'


async def _start_trace(self, categories: str | None = None) -> str:
	"""Start a CDP performance trace."""
	if not self.browser_session:
		return 'Error: No browser session active'
	try:
		cdp_session = await self.browser_session.get_or_create_cdp_session(target_id=None, focus=False)
		if not cdp_session:
			return 'Error: No active CDP session'
		trace_categories = categories or '-*,disabled-by-default-devtools.timeline,devtools.timeline,loading,net,network'
		await cdp_session.cdp_client.send.Tracing.start(
			params={'categories': trace_categories, 'transferMode': 'ReportEvents'},
			session_id=cdp_session.session_id,
		)
		self._trace_active = True
		self._trace_events = []
		return 'Trace started'
	except Exception as e:
		return self._format_action_error(str(e), default_code='action_failed')


async def _stop_trace(self) -> str:
	"""Stop the active CDP trace and return events as JSON."""
	if not self.browser_session:
		return 'Error: No browser session active'
	try:
		if not getattr(self, '_trace_active', False):
			return 'Error: No active trace'
		cdp_session = await self.browser_session.get_or_create_cdp_session(target_id=None, focus=False)
		if not cdp_session:
			return 'Error: No active CDP session'
		await cdp_session.cdp_client.send.Tracing.end(
			session_id=cdp_session.session_id,
		)
		events = getattr(self, '_trace_events', [])
		self._trace_active = False
		self._trace_events = []
		return json.dumps(events, default=str)
	except Exception as e:
		return self._format_action_error(str(e), default_code='action_failed')
