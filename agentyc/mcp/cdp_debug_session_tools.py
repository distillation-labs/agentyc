"""CDP debug, tracing, and tab/session helpers for MCP tools."""

from __future__ import annotations

import asyncio
import base64
import json
import time
from pathlib import Path
from typing import Any

from agentyc.mcp.debug_tools import _serialize_network_entry

_MAX_CAPTURED_BODY_BYTES = 64 * 1024


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
		target_id = None
		if session_id and self.browser_session and self.browser_session.session_manager:
			target_id = self.browser_session.session_manager.get_target_id_from_session_id(session_id)
		post_data = req.get('postData')
		entry: dict[str, Any] = {
			'request_id': req_id,
			'target_id': target_id,
			'url': req.get('url', ''),
			'method': req.get('method', 'GET'),
			'type': event.get('type', 'Other'),
			'status': None,
			'status_text': None,
			'error': None,
			'start_time': event.get('timestamp', time.time()),
			'observed_at': time.time(),
			'duration_ms': None,
			'req_headers': req.get('headers') or {},
			'post_data': post_data,
			'post_data_base64': base64.b64encode(str(post_data).encode('utf-8')).decode('ascii') if post_data else None,
			'response_body_text': None,
			'response_body_base64': None,
			'response_body_truncated': None,
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
		entry['response_at'] = time.time()

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
		entry['finished_at'] = time.time()

		async def _fetch_response_body() -> None:
			if session_id is None or self.browser_session is None:
				return
			try:
				cdp_session_for_body = (
					self.browser_session.session_manager.get_session(session_id) if self.browser_session.session_manager else None
				)
				if cdp_session_for_body is None:
					return
				result = await cdp_session_for_body.cdp_client.send.Network.getResponseBody(
					params={'requestId': req_id},
					session_id=cdp_session_for_body.session_id,
				)
				body = result.get('body')
				if body is None:
					return
				if result.get('base64Encoded'):
					decoded = base64.b64decode(body)
					entry['response_body_truncated'] = len(decoded) > _MAX_CAPTURED_BODY_BYTES
					entry['response_body_base64'] = base64.b64encode(decoded[:_MAX_CAPTURED_BODY_BYTES]).decode('ascii')
				else:
					body_text = str(body)
					body_bytes = body_text.encode('utf-8')
					entry['response_body_truncated'] = len(body_bytes) > _MAX_CAPTURED_BODY_BYTES
					entry['response_body_text'] = body_bytes[:_MAX_CAPTURED_BODY_BYTES].decode('utf-8', errors='replace')
			except Exception:
				return

		if session_id is not None:
			asyncio.create_task(_fetch_response_body())
		self._network_log_buffer.append(entry)
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
		entry['finished_at'] = time.time()
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
	display = [_serialize_network_entry(e, include_headers=include_headers) for e in entries]
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
	self._mark_browser_state_cache_dirty()
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
