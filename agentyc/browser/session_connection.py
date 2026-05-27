"""Connection, bootstrap, and reconnection helpers for BrowserSession."""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any, TypeVar, cast
from urllib.parse import urlparse, urlunparse

from cdp_use import CDPClient

from agentyc.browser.events import (
	AgentFocusChangedEvent,
	BrowserConnectedEvent,
	BrowserErrorEvent,
	BrowserLaunchEvent,
	BrowserLaunchResult,
	BrowserReconnectedEvent,
	BrowserReconnectingEvent,
	BrowserStartEvent,
	BrowserStopEvent,
	BrowserStoppedEvent,
	TabCreatedEvent,
)
from agentyc.browser.session_network import configure_fetch_interception
from agentyc.utils import get_agentyc_version, is_new_tab_page

if TYPE_CHECKING:
	from agentyc.browser.session import BrowserSession


SessionT = TypeVar('SessionT', bound='BrowserSession')


async def attach_all_watchdogs(session: BrowserSession) -> None:
	"""Initialize and attach all watchdogs with explicit handler registration."""
	if session._watchdogs_attached:
		session.logger.debug('Watchdogs already attached, skipping duplicate attachment')
		return

	from agentyc.browser.watchdogs.aboutblank_watchdog import AboutBlankWatchdog
	from agentyc.browser.watchdogs.captcha_watchdog import CaptchaWatchdog
	from agentyc.browser.watchdogs.crash_watchdog import CrashWatchdog
	from agentyc.browser.watchdogs.default_action_watchdog import DefaultActionWatchdog
	from agentyc.browser.watchdogs.dom_watchdog import DOMWatchdog
	from agentyc.browser.watchdogs.downloads_watchdog import DownloadsWatchdog
	from agentyc.browser.watchdogs.har_recording_watchdog import HarRecordingWatchdog
	from agentyc.browser.watchdogs.local_browser_watchdog import LocalBrowserWatchdog
	from agentyc.browser.watchdogs.permissions_watchdog import PermissionsWatchdog
	from agentyc.browser.watchdogs.popups_watchdog import PopupsWatchdog
	from agentyc.browser.watchdogs.recording_watchdog import RecordingWatchdog
	from agentyc.browser.watchdogs.screenshot_watchdog import ScreenshotWatchdog
	from agentyc.browser.watchdogs.security_watchdog import SecurityWatchdog
	from agentyc.browser.watchdogs.storage_state_watchdog import StorageStateWatchdog

	CrashWatchdog.model_rebuild()
	session._crash_watchdog = CrashWatchdog(event_bus=session.event_bus, browser_session=session)
	session._crash_watchdog.attach_to_session()

	DownloadsWatchdog.model_rebuild()
	session._downloads_watchdog = DownloadsWatchdog(event_bus=session.event_bus, browser_session=session)
	session._downloads_watchdog.attach_to_session()
	if session.browser_profile.auto_download_pdfs:
		session.logger.debug('📄 PDF auto-download enabled for this session')

	should_enable_storage_state = (
		session.browser_profile.storage_state is not None or session.browser_profile.user_data_dir is not None
	)
	if should_enable_storage_state:
		StorageStateWatchdog.model_rebuild()
		session._storage_state_watchdog = StorageStateWatchdog(
			event_bus=session.event_bus,
			browser_session=session,
			auto_save_interval=60.0,
			save_on_change=False,
		)
		session._storage_state_watchdog.attach_to_session()
		session.logger.debug(
			f'🍪 StorageStateWatchdog enabled (storage_state: {bool(session.browser_profile.storage_state)}, user_data_dir: {bool(session.browser_profile.user_data_dir)})'
		)
	else:
		session.logger.debug('🍪 StorageStateWatchdog disabled (no storage_state or user_data_dir configured)')

	LocalBrowserWatchdog.model_rebuild()
	session._local_browser_watchdog = LocalBrowserWatchdog(event_bus=session.event_bus, browser_session=session)
	session._local_browser_watchdog.attach_to_session()

	SecurityWatchdog.model_rebuild()
	session._security_watchdog = SecurityWatchdog(event_bus=session.event_bus, browser_session=session)
	session._security_watchdog.attach_to_session()

	AboutBlankWatchdog.model_rebuild()
	session._aboutblank_watchdog = AboutBlankWatchdog(event_bus=session.event_bus, browser_session=session)
	session._aboutblank_watchdog.attach_to_session()

	PopupsWatchdog.model_rebuild()
	session._popups_watchdog = PopupsWatchdog(event_bus=session.event_bus, browser_session=session)
	session._popups_watchdog.attach_to_session()

	PermissionsWatchdog.model_rebuild()
	session._permissions_watchdog = PermissionsWatchdog(event_bus=session.event_bus, browser_session=session)
	session._permissions_watchdog.attach_to_session()

	DefaultActionWatchdog.model_rebuild()
	session._default_action_watchdog = DefaultActionWatchdog(event_bus=session.event_bus, browser_session=session)
	session._default_action_watchdog.attach_to_session()

	ScreenshotWatchdog.model_rebuild()
	session._screenshot_watchdog = ScreenshotWatchdog(event_bus=session.event_bus, browser_session=session)
	session._screenshot_watchdog.attach_to_session()

	DOMWatchdog.model_rebuild()
	session._dom_watchdog = DOMWatchdog(event_bus=session.event_bus, browser_session=session)
	session._dom_watchdog.attach_to_session()

	RecordingWatchdog.model_rebuild()
	session._recording_watchdog = RecordingWatchdog(event_bus=session.event_bus, browser_session=session)
	session._recording_watchdog.attach_to_session()

	if session.browser_profile.record_har_path:
		HarRecordingWatchdog.model_rebuild()
		session._har_recording_watchdog = HarRecordingWatchdog(event_bus=session.event_bus, browser_session=session)
		session._har_recording_watchdog.attach_to_session()

	if session.browser_profile.captcha_solver:
		CaptchaWatchdog.model_rebuild()
		session._captcha_watchdog = CaptchaWatchdog(event_bus=session.event_bus, browser_session=session)
		session._captcha_watchdog.attach_to_session()

	session._watchdogs_attached = True


async def on_BrowserStartEvent(session: BrowserSession, event: BrowserStartEvent) -> dict[str, str]:
	"""Handle browser start request."""
	await attach_all_watchdogs(session)

	try:
		if not session.cdp_url:
			if session.is_local:
				launch_event = session.event_bus.dispatch(BrowserLaunchEvent())
				await launch_event
				launch_result: BrowserLaunchResult = cast(
					BrowserLaunchResult, await launch_event.event_result(raise_if_none=True, raise_if_any=True)
				)
				session.browser_profile.cdp_url = launch_result.cdp_url
			else:
				raise ValueError('Got BrowserSession(is_local=False) but no cdp_url was provided to connect to!')

		assert session.cdp_url and '://' in session.cdp_url

		async with session._connection_lock:
			if session._cdp_client_root is None:
				try:
					await asyncio.wait_for(connect(session, cdp_url=session.cdp_url), timeout=15.0)
				except TimeoutError:
					cdp_client = cast(CDPClient | None, session._cdp_client_root)
					if cdp_client is not None:
						try:
							await cdp_client.stop()
						except Exception:
							pass
						session._cdp_client_root = None
					manager = session.session_manager
					if manager is not None:
						try:
							await manager.clear()
						except Exception:
							pass
						session.session_manager = None
					session.agent_focus_target_id = None
					raise RuntimeError(
						f'connect() timed out after 15s — CDP connection to {session.cdp_url} is too slow or unresponsive'
					)
				assert session.cdp_client is not None
				await session.event_bus.dispatch(BrowserConnectedEvent(cdp_url=session.cdp_url))
				if session.browser_profile.demo_mode:
					try:
						demo = session.demo_mode
						if demo:
							await demo.ensure_ready()
					except Exception as exc:
						session.logger.warning(f'[DemoMode] Failed to inject demo overlay: {exc}')
			else:
				session.logger.debug('Already connected to CDP, skipping reconnection')
				if session.browser_profile.demo_mode:
					try:
						demo = session.demo_mode
						if demo:
							await demo.ensure_ready()
					except Exception as exc:
						session.logger.warning(f'[DemoMode] Failed to inject demo overlay: {exc}')

		return {'cdp_url': session.cdp_url}
	except Exception as e:
		session.event_bus.dispatch(
			BrowserErrorEvent(
				error_type='BrowserStartEventError',
				message=f'Failed to start browser: {type(e).__name__} {e}',
				details={'cdp_url': session.cdp_url, 'is_local': session.is_local},
			)
		)
		raise


async def on_BrowserStopEvent(session: BrowserSession, event: BrowserStopEvent) -> None:
	"""Handle browser stop request."""
	try:
		if session.browser_profile.keep_alive and not event.force:
			session.event_bus.dispatch(BrowserStoppedEvent(reason='Kept alive due to keep_alive=True'))
			return

		session.logger.info(
			f'📢 on_BrowserStopEvent - Calling reset() (force={event.force}, keep_alive={session.browser_profile.keep_alive})'
		)
		await session.reset()

		if session.is_local:
			session.browser_profile.cdp_url = None

		stop_event = session.event_bus.dispatch(BrowserStoppedEvent(reason='Stopped by request'))
		await stop_event
	except Exception as e:
		session.event_bus.dispatch(
			BrowserErrorEvent(
				error_type='BrowserStopEventError',
				message=f'Failed to stop browser: {type(e).__name__} {e}',
				details={'cdp_url': session.cdp_url, 'is_local': session.is_local},
			)
		)


def _build_cdp_headers(session: BrowserSession) -> dict[str, str]:
	headers = dict(getattr(session.browser_profile, 'headers', None) or {})
	if not session.is_local:
		headers.setdefault('User-Agent', f'agentyc/{get_agentyc_version()}')
	return headers


def _get_timeout_wrapped_cdp_client_class() -> type[CDPClient]:
	from agentyc.browser import session as session_module

	return cast(type[CDPClient], getattr(session_module, 'TimeoutWrappedCDPClient'))


def _get_httpx_module() -> Any:
	from agentyc.browser import session as session_module

	return getattr(session_module, 'httpx')


async def _create_root_cdp_client(session: BrowserSession) -> CDPClient:
	assert session.cdp_url is not None, 'CDP URL is None.'
	cdp_client = _get_timeout_wrapped_cdp_client_class()(
		session.cdp_url,
		additional_headers=_build_cdp_headers(session) or None,
		max_ws_frame_size=200 * 1024 * 1024,
	)
	await cdp_client.start()
	return cdp_client


async def _initialize_session_manager(session: BrowserSession) -> None:
	from agentyc.browser.session_manager import SessionManager

	assert session._cdp_client_root is not None
	await session._cdp_client_root.send.Target.setAutoAttach(
		params={'autoAttach': True, 'waitForDebuggerOnStart': False, 'flatten': True}
	)
	session.logger.debug('CDP client connected with auto-attach enabled')

	session.session_manager = SessionManager(session)
	await session.session_manager.start_monitoring()
	session.logger.debug('Event-driven session manager started')


async def _redirect_new_tab_pages(session: BrowserSession, page_targets: list[Any]) -> None:
	async def _redirect_newtab(target: Any) -> None:
		target_url = target.url
		target_id = target.target_id
		session.logger.debug(f'🔄 Redirecting {target_url} to about:blank for target {target_id}')
		try:
			cdp_session = await session.get_or_create_cdp_session(target_id, focus=False)
			await cdp_session.cdp_client.send.Page.navigate(params={'url': 'about:blank'}, session_id=cdp_session.session_id)
			target.url = 'about:blank'
		except Exception as e:
			session.logger.warning(f'Failed to redirect {target_url}: {e}')

	redirect_tasks = [
		_redirect_newtab(target) for target in page_targets if is_new_tab_page(target.url) and target.url != 'about:blank'
	]
	if redirect_tasks:
		await asyncio.gather(*redirect_tasks, return_exceptions=True)


async def _bootstrap_page_targets(session: BrowserSession) -> list[Any]:
	page_targets = session.session_manager.get_all_page_targets()
	if session.is_shared_browser_runtime:
		session._assign_shared_browser_ownership(page_targets)
	else:
		for target in page_targets:
			session.session_manager.set_target_ownership(target.target_id, session.runtime_metadata, source='current_runtime')

	await _redirect_new_tab_pages(session, page_targets)
	owned_page_targets = session.get_owned_page_targets() if session.is_shared_browser_runtime else page_targets
	initial_target_id: str | None = None

	if not owned_page_targets:
		if session.is_shared_browser_runtime and getattr(session, '_browser_context_id', None) is None:
			session.logger.debug(
				'📄 Shared runtime has no browser context yet; skipping provisional page creation during bootstrap'
			)
		else:
			assert session._cdp_client_root is not None
			initial_target_id = await session._cdp_create_new_page(
				'about:blank',
				background=session.browser_profile.shared_browser_focus_policy == 'preserve',
			)
			session.logger.debug(f'📄 Created new blank page: {initial_target_id}')
			page_targets = session.session_manager.get_all_page_targets()
			owned_page_targets = session.get_owned_page_targets() if session.is_shared_browser_runtime else page_targets
	else:
		initial_target_id = owned_page_targets[0].target_id
		session.logger.debug(f'📄 Using existing page: {initial_target_id}')

	if initial_target_id is not None:
		try:
			await session.get_or_create_cdp_session(initial_target_id, focus=True)
			await session._apply_runtime_markers_to_target(initial_target_id)
			await session._cdp_get_window_context(initial_target_id)
			session.logger.debug(f'📄 Agent focus set to {initial_target_id[:8]}...')
		except ValueError as e:
			raise RuntimeError(f'Failed to get session for initial target {initial_target_id}: {e}') from e

	for idx, target in enumerate(page_targets):
		target_url = target.url
		await session._apply_runtime_markers_to_target(target.target_id)
		await session._cdp_get_window_context(target.target_id)
		session.logger.debug(f'Dispatching TabCreatedEvent for initial tab {idx}: {target_url}')
		session.event_bus.dispatch(TabCreatedEvent(url=target_url, target_id=target.target_id))

	if owned_page_targets:
		initial_url = owned_page_targets[0].url
		session.event_bus.dispatch(AgentFocusChangedEvent(target_id=owned_page_targets[0].target_id, url=initial_url))
		session.logger.debug(f'Initial agent focus set to tab 0: {initial_url}')

	return page_targets


async def connect(session: SessionT, cdp_url: str | None = None) -> SessionT:
	"""Connect to a remote chromium-based browser via CDP using cdp-use."""
	session.browser_profile.cdp_url = cdp_url or session.cdp_url
	if not session.cdp_url:
		raise RuntimeError('Cannot setup CDP connection without CDP URL')

	if session._cdp_client_root is not None:
		session.logger.warning(
			'⚠️ connect() called but CDP client already exists! Cleaning up old connection before creating new one.'
		)
		try:
			await session._cdp_client_root.stop()
		except Exception as e:
			session.logger.debug(f'Error stopping old CDP client: {e}')
		session._cdp_client_root = None

	if not session.cdp_url.startswith('ws'):
		httpx = _get_httpx_module()
		parsed_url = urlparse(session.cdp_url)
		path = parsed_url.path.rstrip('/')
		if not path.endswith('/json/version'):
			path = path + '/json/version'
		url = urlunparse((parsed_url.scheme, parsed_url.netloc, path, parsed_url.params, parsed_url.query, parsed_url.fragment))
		is_localhost = parsed_url.hostname in ('localhost', '127.0.0.1', '::1')
		async with httpx.AsyncClient(timeout=httpx.Timeout(30.0), trust_env=not is_localhost) as client:
			headers = dict(session.browser_profile.headers or {})
			headers.setdefault('User-Agent', f'agentyc/{get_agentyc_version()}')
			version_info = await client.get(url, headers=headers)
			session.logger.debug(f'Raw version info: {str(version_info)}')
			session.browser_profile.cdp_url = version_info.json()['webSocketDebuggerUrl']

	assert session.cdp_url is not None, 'CDP URL is None.'
	browser_location = 'local browser' if session.is_local else 'remote browser'
	session.logger.debug(f'🌎 Connecting to existing chromium-based browser via CDP: {session.cdp_url} -> ({browser_location})')

	try:
		session._cdp_client_root = await _create_root_cdp_client(session)
		await _initialize_session_manager(session)
		page_targets = await _bootstrap_page_targets(session)
		await _setup_proxy_auth(session)
		session._intentional_stop = False
		_attach_ws_drop_callback(session)

		if session.agent_focus_target_id:
			target = session.session_manager.get_target(session.agent_focus_target_id)
			if target.title == 'Unknown title':
				session.logger.warning('Target created but title is unknown (may be normal for about:blank)')
	except Exception as e:
		session.logger.error(f'❌ FATAL: Failed to setup CDP connection: {e}')
		session.logger.error('❌ Browser cannot continue without CDP connection')
		if session.session_manager:
			try:
				await session.session_manager.clear()
				session.logger.debug('Cleared SessionManager state after initialization failure')
			except Exception as cleanup_error:
				session.logger.debug(f'Error clearing SessionManager: {cleanup_error}')
		if session._cdp_client_root:
			try:
				await session._cdp_client_root.stop()
				session.logger.debug('Closed CDP client WebSocket after initialization failure')
			except Exception as cleanup_error:
				session.logger.debug(f'Error closing CDP client: {cleanup_error}')
		session.session_manager = None
		session._cdp_client_root = None
		session.agent_focus_target_id = None
		raise RuntimeError(f'Failed to establish CDP connection to browser: {e}') from e

	return session


async def _setup_proxy_auth(session: BrowserSession) -> None:
	"""Configure Fetch interception for proxy auth and active network mocks."""
	try:
		await configure_fetch_interception(session)
	except Exception as e:
		session.logger.debug(f'Skipping proxy auth setup: {type(e).__name__}: {e}')


async def reconnect(session: BrowserSession) -> None:
	"""Re-establish the CDP WebSocket connection to an already-running browser."""
	assert session.cdp_url, 'Cannot reconnect without a CDP URL'
	old_focus_target_id = session.agent_focus_target_id

	if session._cdp_client_root:
		try:
			await session._cdp_client_root.stop()
		except Exception as e:
			session.logger.debug(f'Error stopping old CDP client during reconnect: {e}')
		session._cdp_client_root = None

	if session.session_manager:
		try:
			await session.session_manager.clear()
		except Exception as e:
			session.logger.debug(f'Error clearing SessionManager during reconnect: {e}')
		session.session_manager = None

	session.agent_focus_target_id = None
	session._cdp_client_root = await _create_root_cdp_client(session)
	await _initialize_session_manager(session)

	page_targets = session.session_manager.get_all_page_targets()
	if session.is_shared_browser_runtime:
		session._assign_shared_browser_ownership(page_targets)
	else:
		for target in page_targets:
			session.session_manager.set_target_ownership(target.target_id, session.runtime_metadata, source='current_runtime')

	owned_page_targets = session.get_owned_page_targets() if session.is_shared_browser_runtime else page_targets

	restored = False
	if old_focus_target_id:
		for target in owned_page_targets:
			if target.target_id == old_focus_target_id:
				await session.get_or_create_cdp_session(old_focus_target_id, focus=True)
				await session._apply_runtime_markers_to_target(old_focus_target_id)
				await session._cdp_get_window_context(old_focus_target_id)
				restored = True
				session.logger.debug(f'🔄 Restored agent focus to previous target {old_focus_target_id[:8]}...')
				break

	if not restored:
		if owned_page_targets:
			fallback_id = owned_page_targets[0].target_id
			await session.get_or_create_cdp_session(fallback_id, focus=True)
			await session._apply_runtime_markers_to_target(fallback_id)
			await session._cdp_get_window_context(fallback_id)
			session.logger.debug(f'🔄 Agent focus set to fallback target {fallback_id[:8]}...')
		else:
			target_id = await session._cdp_create_new_page(
				'about:blank',
				background=session.browser_profile.shared_browser_focus_policy == 'preserve',
			)
			await session.get_or_create_cdp_session(target_id, focus=True)
			await session._apply_runtime_markers_to_target(target_id)
			await session._cdp_get_window_context(target_id)
			session.logger.debug(f'🔄 Created new blank page during reconnect: {target_id[:8]}...')

	await _setup_proxy_auth(session)
	_attach_ws_drop_callback(session)


async def _auto_reconnect(session: BrowserSession, max_attempts: int = 3) -> None:
	"""Attempt to reconnect with exponential backoff."""
	async with session._reconnect_lock:
		if session._reconnecting:
			return
		session._reconnecting = True
		session._reconnect_event.clear()

	start_time = time.time()
	delays = [1.0, 2.0, 4.0]
	try:
		for attempt in range(1, max_attempts + 1):
			session.event_bus.dispatch(
				BrowserReconnectingEvent(
					cdp_url=session.cdp_url or '',
					attempt=attempt,
					max_attempts=max_attempts,
				)
			)
			session.logger.warning(f'🔄 WebSocket reconnection attempt {attempt}/{max_attempts}...')
			try:
				await asyncio.wait_for(reconnect(session), timeout=15.0)
				downtime = time.time() - start_time
				session.event_bus.dispatch(
					BrowserReconnectedEvent(
						cdp_url=session.cdp_url or '',
						attempt=attempt,
						downtime_seconds=downtime,
					)
				)
				session.logger.info(f'🔄 WebSocket reconnected after {downtime:.1f}s (attempt {attempt})')
				return
			except Exception as e:
				session.logger.warning(f'🔄 Reconnection attempt {attempt} failed: {type(e).__name__}: {e}')
				if attempt < max_attempts:
					delay = delays[attempt - 1] if attempt - 1 < len(delays) else delays[-1]
					await asyncio.sleep(delay)

		session.logger.error(f'🔄 All {max_attempts} reconnection attempts failed')
		session.event_bus.dispatch(
			BrowserErrorEvent(
				error_type='ReconnectionFailed',
				message=f'Failed to reconnect after {max_attempts} attempts ({time.time() - start_time:.1f}s)',
				details={'cdp_url': session.cdp_url or '', 'max_attempts': max_attempts},
			)
		)
	finally:
		session._reconnecting = False
		session._reconnect_event.set()


def _attach_ws_drop_callback(session: BrowserSession) -> None:
	"""Attach a done callback to the CDPClient's message handler task to detect WS drops."""
	if not session._cdp_client_root or not hasattr(session._cdp_client_root, '_message_handler_task'):
		return

	task = session._cdp_client_root._message_handler_task
	if task is None or task.done():
		return

	def _on_message_handler_done(fut: asyncio.Future) -> None:
		if session._intentional_stop or session._reconnecting or not session.cdp_url:
			return

		exc = fut.exception() if not fut.cancelled() else None
		session.logger.warning(
			f'🔌 CDP WebSocket message handler exited unexpectedly'
			f'{f": {type(exc).__name__}: {exc}" if exc else " (connection closed)"}'
		)
		try:
			loop = asyncio.get_running_loop()
			session._reconnect_task = loop.create_task(_auto_reconnect(session))
		except RuntimeError:
			session.logger.error('🔌 No event loop available for auto-reconnect')

	task.add_done_callback(_on_message_handler_done)
