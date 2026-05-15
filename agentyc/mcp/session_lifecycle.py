"""Browser session lifecycle helpers for the agentyc MCP server."""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import suppress
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


async def _init_browser_session(self, allowed_domains: list[str] | None = None, **kwargs):
	"""Initialize browser session using config."""
	if self.browser_session:
		return

	from agentyc.mcp.server import _ensure_all_loggers_use_stderr

	_ensure_all_loggers_use_stderr()
	logger.debug('Initializing browser session...')

	from agentyc.browser import BrowserProfile, BrowserSession
	from agentyc.config import get_default_profile
	from agentyc.tools.service import Tools

	profile_config = get_default_profile(self.config)
	cdp_url = self._cdp_url or kwargs.pop('cdp_url', None)

	if cdp_url:
		profile_data: dict[str, Any] = {
			'cdp_url': cdp_url,
			'keep_alive': True,
			'runtime_label': self._runtime_label,
			'runtime_role': self._runtime_role,
			'parent_runtime_id': self._parent_runtime_id,
			'shared_browser_mode': self._shared_browser_mode,
			'shared_browser_window_bounds': self._shared_browser_window_bounds,
			'shared_browser_focus_policy': self._shared_browser_focus_policy,
			'downloads_path': str(Path.home() / 'Downloads' / 'agentyc-mcp'),
			'device_scale_factor': 1.0,
			'disable_security': False,
			**profile_config,
		}
		if allowed_domains is not None:
			profile_data['allowed_domains'] = allowed_domains
		for key, value in kwargs.items():
			profile_data[key] = value
		profile = BrowserProfile(**profile_data)
		self.browser_session = BrowserSession(browser_profile=profile)
		await self.browser_session.start()
		try:
			cdp_root = self.browser_session._cdp_client_root
			if cdp_root is not None:
				ctx_result = await cdp_root.send.Target.createBrowserContext()
				self.browser_session._browser_context_id = ctx_result['browserContextId']
		except Exception as _ctx_e:
			logger.debug(f'Browser context creation failed (non-critical): {_ctx_e}')
		new_page = await self.browser_session.create_collaborative_page('about:blank')
		new_target_id = new_page._target_id
		from agentyc.browser.events import AgentFocusChangedEvent, TabCreatedEvent

		await self.browser_session.event_bus.dispatch(TabCreatedEvent(target_id=new_target_id, url='about:blank'))
		await self.browser_session.event_bus.dispatch(AgentFocusChangedEvent(target_id=new_target_id, url='about:blank'))
	else:
		profile_data = {
			'downloads_path': str(Path.home() / 'Downloads' / 'agentyc-mcp'),
			'keep_alive': False,
			'runtime_label': self._runtime_label,
			'runtime_role': self._runtime_role,
			'parent_runtime_id': self._parent_runtime_id,
			'user_data_dir': '~/.config/agentyc/profiles/default',
			'device_scale_factor': 1.0,
			'disable_security': False,
			'headless': False,
			**profile_config,
		}
		if allowed_domains is not None:
			profile_data['allowed_domains'] = allowed_domains
		for key, value in kwargs.items():
			profile_data[key] = value
		profile = BrowserProfile(**profile_data)
		self.browser_session = BrowserSession(browser_profile=profile)
		await self.browser_session.start()

	self._track_session(self.browser_session)
	self.tools = Tools()
	self.tools.set_coordinate_clicking(True)
	self.file_system = None
	file_system_path = profile_config.get('file_system_path', '~/.agentyc-mcp')
	self._file_system_base_dir = Path(file_system_path).expanduser()

	try:
		await self._register_cdp_event_listeners()
	except Exception as _e:
		logger.debug(f'CDP event listener registration failed (non-critical): {_e}')

	logger.debug('Browser session initialized')


def _track_session(self, session) -> None:
	"""Track a browser session for management."""
	self.active_sessions[session.id] = {
		'session': session,
		'created_at': time.time(),
		'last_activity': time.time(),
		'url': getattr(session, 'current_url', None),
	}


def _update_session_activity(self, session_id: str) -> None:
	"""Update the last activity time for a session and schedule a URL refresh."""
	if session_id in self.active_sessions:
		self.active_sessions[session_id]['last_activity'] = time.time()
		asyncio.create_task(self._update_session_url(session_id))


async def _update_session_url(self, session_id: str) -> None:
	"""Refresh the stored URL for a tracked session."""
	if session_id not in self.active_sessions or not self.browser_session:
		return
	try:
		url = await self.browser_session.get_current_page_url()
		if session_id in self.active_sessions:
			self.active_sessions[session_id]['url'] = url
	except Exception:
		pass


async def _list_sessions(self) -> str:
	"""List all active browser sessions."""
	if not self.active_sessions:
		return 'No active browser sessions'

	import json

	sessions_info = []
	for session_id, session_data in self.active_sessions.items():
		session = session_data['session']
		created_at = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(session_data['created_at']))
		last_activity = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(session_data['last_activity']))
		is_active = hasattr(session, 'cdp_client') and session.cdp_client is not None
		sessions_info.append(
			{
				'session_id': session_id,
				'created_at': created_at,
				'last_activity': last_activity,
				'active': is_active,
				'current_url': session_data.get('url', 'Unknown'),
				'age_minutes': (time.time() - session_data['created_at']) / 60,
			}
		)

	return json.dumps(sessions_info, indent=2)


async def _close_session(self, session_id: str) -> str:
	"""Close a specific browser session."""
	if session_id not in self.active_sessions:
		return f'Session {session_id} not found'

	session_data = self.active_sessions[session_id]
	session = session_data['session']

	try:
		browser_context_id = getattr(session, '_browser_context_id', None)
		if browser_context_id:
			with suppress(Exception):
				cdp_root = getattr(session, '_cdp_client_root', None)
				if cdp_root:
					await cdp_root.send.Target.disposeBrowserContext(params={'browserContextId': browser_context_id})
			session._browser_context_id = None
		if hasattr(session, 'kill'):
			await session.kill()
		del self.active_sessions[session_id]
		if self.browser_session and self.browser_session.id == session_id:
			self.browser_session = None
			self.tools = None
		return f'Successfully closed session {session_id}'
	except Exception as e:
		return f'Error closing session {session_id}: {str(e)}'


async def _close_all_sessions(self) -> str:
	"""Close all active browser sessions."""
	if not self.active_sessions:
		return 'No active sessions to close'

	closed_count = 0
	errors = []
	for session_id in list(self.active_sessions.keys()):
		try:
			result = await self._close_session(session_id)
			if 'Successfully closed' in result:
				closed_count += 1
			else:
				errors.append(f'{session_id}: {result}')
		except Exception as e:
			errors.append(f'{session_id}: {str(e)}')

	self.browser_session = None
	self.tools = None

	result = f'Closed {closed_count} sessions'
	if errors:
		result += '. Errors: ' + '; '.join(errors)
	return result


async def _cleanup_expired_sessions(self) -> None:
	"""Background task to clean up expired sessions. Skipped when session_timeout_minutes <= 0."""
	if self.session_timeout_minutes <= 0:
		return

	current_time = time.time()
	timeout_seconds = self.session_timeout_minutes * 60

	expired_sessions = []
	for session_id, session_data in self.active_sessions.items():
		last_activity = session_data['last_activity']
		if current_time - last_activity > timeout_seconds:
			expired_sessions.append(session_id)

	for session_id in expired_sessions:
		try:
			await self._close_session(session_id)
			logger.info(f'Auto-closed expired session {session_id}')
		except Exception as e:
			logger.error(f'Error auto-closing session {session_id}: {e}')


async def _start_cleanup_task(self) -> None:
	"""Start the background cleanup task."""

	async def cleanup_loop():
		while True:
			try:
				await self._cleanup_expired_sessions()
				await asyncio.sleep(120)
			except Exception as e:
				logger.error(f'Error in cleanup task: {e}')
				await asyncio.sleep(120)

	from agentyc.utils import create_task_with_error_handling

	self._cleanup_task = create_task_with_error_handling(cleanup_loop(), name='mcp_cleanup_loop', suppress_exceptions=True)


async def _shutdown(self) -> None:
	"""Stop background work and force-close any tracked browser sessions."""
	if self._cleanup_task is not None:
		self._cleanup_task.cancel()
		with suppress(asyncio.CancelledError):
			await self._cleanup_task
		self._cleanup_task = None

	if self.active_sessions:
		await self._close_all_sessions()
