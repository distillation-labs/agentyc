"""Browser session lifecycle helpers for the agentyc MCP server."""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import suppress
from pathlib import Path
from typing import Any

from agentyc.utils import is_new_tab_page

logger = logging.getLogger(__name__)


def _configured_headless_value(profile_config: dict[str, Any], kwargs: dict[str, Any]) -> bool | None:
	if 'headless' in kwargs:
		return kwargs['headless']
	if 'headless' in profile_config:
		return profile_config['headless']
	return None


def _select_reusable_attach_target(browser_session) -> Any | None:
	"""Pick an existing blank tab that can be claimed on first attach.

	Prefer a current-runtime tab when one already exists. Otherwise only reuse
	placeholder surfaces that are still unowned/human-owned and blank.
	"""
	session_manager = getattr(browser_session, 'session_manager', None)
	if session_manager is None or not hasattr(session_manager, 'get_all_page_targets'):
		return None

	runtime_metadata = getattr(browser_session, 'runtime_metadata', None)
	current_runtime_id = getattr(runtime_metadata, 'runtime_id', None)
	reusable_target = None

	for target in session_manager.get_all_page_targets():
		ownership = getattr(target, 'ownership', None)
		runtime = getattr(ownership, 'runtime', None) if ownership is not None else None
		if runtime is not None and getattr(runtime, 'runtime_id', None) == current_runtime_id:
			return target

		target_url = str(getattr(target, 'url', '') or '')
		if target_url != 'about:blank' and not is_new_tab_page(target_url):
			continue
		if ownership is None or getattr(ownership, 'owner_kind', None) == 'human':
			reusable_target = target
			break

	return reusable_target


async def _focus_or_create_attach_target(self) -> None:
	assert self.browser_session is not None
	from agentyc.browser.events import AgentFocusChangedEvent, TabCreatedEvent

	reusable_target = _select_reusable_attach_target(self.browser_session)
	if reusable_target is not None:
		session_manager = getattr(self.browser_session, 'session_manager', None)
		if session_manager is not None and hasattr(session_manager, 'set_target_ownership'):
			session_manager.set_target_ownership(
				reusable_target.target_id,
				self.browser_session.runtime_metadata,
				source='current_runtime',
			)
		await self.browser_session.event_bus.dispatch(
			AgentFocusChangedEvent(target_id=reusable_target.target_id, url=reusable_target.url)
		)
		return

	new_page = await self.browser_session.create_collaborative_page('about:blank')
	new_target_id = new_page._target_id
	await self.browser_session.event_bus.dispatch(TabCreatedEvent(target_id=new_target_id, url='about:blank'))
	await self.browser_session.event_bus.dispatch(AgentFocusChangedEvent(target_id=new_target_id, url='about:blank'))


def _runtime_profile_defaults(self) -> dict[str, Any]:
	return {
		'runtime_label': self._runtime_label,
		'runtime_role': self._runtime_role,
		'parent_runtime_id': self._parent_runtime_id,
		'downloads_path': str(Path.home() / 'Downloads' / 'agentyc-mcp'),
		'device_scale_factor': 1.0,
		'disable_security': False,
	}


def _apply_profile_overrides(
	base_profile_data: dict[str, Any],
	*,
	profile_config: dict[str, Any],
	allowed_domains: list[str] | None,
	kwargs: dict[str, Any],
) -> dict[str, Any]:
	profile_data = {**base_profile_data, **profile_config}
	if allowed_domains is not None:
		profile_data['allowed_domains'] = allowed_domains
	profile_data.update(kwargs)
	return profile_data


def _build_attached_profile_data(
	self,
	*,
	cdp_url: str,
	profile_config: dict[str, Any],
	allowed_domains: list[str] | None,
	kwargs: dict[str, Any],
) -> dict[str, Any]:
	base_profile_data = {
		'cdp_url': cdp_url,
		'keep_alive': True,
		'shared_browser_mode': self._shared_browser_mode,
		'shared_browser_window_bounds': self._shared_browser_window_bounds,
		'shared_browser_focus_policy': self._shared_browser_focus_policy,
		**_runtime_profile_defaults(self),
	}
	return _apply_profile_overrides(
		base_profile_data,
		profile_config=profile_config,
		allowed_domains=allowed_domains,
		kwargs=kwargs,
	)


def _build_local_profile_data(
	self,
	*,
	profile_config: dict[str, Any],
	allowed_domains: list[str] | None,
	kwargs: dict[str, Any],
) -> dict[str, Any]:
	from agentyc.config import CONFIG

	base_profile_data = {
		'keep_alive': False,
		'user_data_dir': str(CONFIG.AGENTYC_DEFAULT_USER_DATA_DIR),
		'headless': False,
		**_runtime_profile_defaults(self),
	}
	return _apply_profile_overrides(
		base_profile_data,
		profile_config=profile_config,
		allowed_domains=allowed_domains,
		kwargs=kwargs,
	)


async def _start_browser_session_with_profile_data(self, profile_data: dict[str, Any]) -> None:
	from agentyc.browser import BrowserProfile, BrowserSession

	profile = BrowserProfile(**profile_data)
	self.browser_session = BrowserSession(browser_profile=profile)
	await self.browser_session.start()


def _register_reusable_local_browser(self, register_local_shared_browser) -> None:
	assert self.browser_session is not None
	cdp_url = self.browser_session.browser_profile.cdp_url
	if not cdp_url:
		return
	local_watchdog = getattr(self.browser_session, '_local_browser_watchdog', None)
	if local_watchdog is not None:
		local_watchdog._owns_browser_resources = False
	self._cdp_url = cdp_url
	browser_pid = getattr(local_watchdog, 'browser_pid', None)
	register_local_shared_browser(
		cdp_url=cdp_url,
		browser_pid=browser_pid,
		headless=self.browser_session.browser_profile.headless,
		user_data_dir=str(self.browser_session.browser_profile.user_data_dir)
		if self.browser_session.browser_profile.user_data_dir
		else None,
	)


async def _initialize_browser_runtime_dependencies(self, *, profile_config: dict[str, Any]) -> None:
	assert self.browser_session is not None
	from agentyc.tools.service import Tools

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


async def _init_browser_session(self, allowed_domains: list[str] | None = None, **kwargs):
	"""Initialize browser session using config."""
	if self.browser_session:
		return

	from agentyc.mcp.server_bootstrap import _ensure_all_loggers_use_stderr

	_ensure_all_loggers_use_stderr()
	logger.debug('Initializing browser session...')

	from agentyc.config import get_default_profile
	from agentyc.mcp.shared_browser_registry import (
		get_reusable_local_browser_cdp_url,
		register_local_shared_browser,
		reuse_local_browser_enabled,
	)

	profile_config = get_default_profile(self.config)
	provided_cdp_url = kwargs.pop('cdp_url', None)
	cdp_url = self._cdp_url or provided_cdp_url
	headless = _configured_headless_value(profile_config, kwargs)
	is_attaching_to_existing_browser = cdp_url is not None
	reuse_local_browser = getattr(self, '_reuse_local_browser', None)
	should_reuse_local_browser = bool(
		(reuse_local_browser if reuse_local_browser is not None else reuse_local_browser_enabled())
		and not getattr(self, '_explicit_cdp_url', False)
	)
	if cdp_url is None and should_reuse_local_browser:
		cdp_url = await get_reusable_local_browser_cdp_url(headless=headless)
		if cdp_url:
			self._cdp_url = cdp_url

	try:
		if cdp_url:
			profile_data = _build_attached_profile_data(
				self,
				cdp_url=cdp_url,
				profile_config=profile_config,
				allowed_domains=allowed_domains,
				kwargs=kwargs,
			)
			await _start_browser_session_with_profile_data(self, profile_data)
			assert self.browser_session is not None
			self._cdp_url = self.browser_session.browser_profile.cdp_url
			# Shared-browser runtimes intentionally stay in the existing browser profile so
			# subagents can share auth/cookies/storage. On first attach, claim an existing
			# blank tab when possible instead of leaving it idle and opening a second tab.
			await _focus_or_create_attach_target(self)
		else:
			profile_data = _build_local_profile_data(
				self,
				profile_config=profile_config,
				allowed_domains=allowed_domains,
				kwargs=kwargs,
			)
			await _start_browser_session_with_profile_data(self, profile_data)
			if should_reuse_local_browser and not is_attaching_to_existing_browser:
				_register_reusable_local_browser(self, register_local_shared_browser)

		await _initialize_browser_runtime_dependencies(self, profile_config=profile_config)
	except Exception:
		await self._reset_broken_browser_runtime()
		raise

	logger.debug('Browser session initialized')


def _browser_runtime_is_ready(self) -> bool:
	"""Return True when the current browser runtime is safe to reuse."""
	if self.browser_session is None or self.tools is None:
		return False
	return self.browser_session.is_cdp_connected


async def _reset_broken_browser_runtime(self) -> None:
	"""Drop any partially initialized runtime so the next tool call can recreate it cleanly."""
	broken_session = self.browser_session
	self.browser_session = None
	self.tools = None
	self.file_system = None
	self._file_system_base_dir = None
	self._browser_state_cache_clean = False
	self._browser_state_cache_timestamp = 0.0

	if broken_session is None:
		return

	self.active_sessions.pop(getattr(broken_session, 'id', None), None)
	with suppress(Exception):
		await broken_session.kill()


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
		is_active = bool(getattr(session, 'is_cdp_connected', False))
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
		if getattr(session.browser_profile, 'keep_alive', False):
			if hasattr(session, 'stop'):
				await session.stop()
		elif hasattr(session, 'kill'):
			await session.kill()
		del self.active_sessions[session_id]
		if self.browser_session and self.browser_session.id == session_id:
			self.browser_session = None
			self.tools = None
			self._cdp_events_registered = False
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
