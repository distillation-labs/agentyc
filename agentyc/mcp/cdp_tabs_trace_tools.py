"""CDP tab, dialog, and trace helpers for MCP tools."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any


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
	self._mark_browser_state_cache_dirty()

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
	self._mark_browser_state_cache_dirty()

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
	self._mark_browser_state_cache_dirty()

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
	self._mark_browser_state_cache_dirty()
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
				const root = document.documentElement || document.body || document;
				if (!root) {{
					clearTimeout(timer);
					resolve("stable");
					return;
				}}
				const observer = new MutationObserver(() => {{
					clearTimeout(timeoutId);
					timeoutId = setTimeout(() => {{
						observer.disconnect();
						clearTimeout(timer);
						resolve("stable");
					}}, quietMs);
				}});
				observer.observe(root, {{
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
		error_message = str(e)
		pending_dialogs = getattr(self.browser_session, '_pending_auto_handled_dialogs', [])
		if 'No dialog is showing' in error_message and pending_dialogs:
			dialog_summary = pending_dialogs.pop(0)
			return f'Dialog already auto-handled by runtime: {dialog_summary}'
		return self._format_action_error(error_message, default_code='action_failed')


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
		self._trace_categories = trace_categories
		self._trace_started_at = time.time()
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
		self._last_trace_summary = {
			'event_count': len(events),
			'categories': getattr(self, '_trace_categories', None),
			'started_at': getattr(self, '_trace_started_at', None),
			'stopped_at': time.time(),
		}
		self._trace_active = False
		self._trace_events = []
		self._trace_categories = None
		self._trace_started_at = None
		return json.dumps(events, default=str)
	except Exception as e:
		return self._format_action_error(str(e), default_code='action_failed')
