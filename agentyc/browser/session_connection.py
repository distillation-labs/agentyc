"""Connection, bootstrap, and reconnection helpers for BrowserSession."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, TypeVar, cast
from urllib.parse import urlparse, urlunparse

from cdp_use import CDPClient

from agentyc.browser.events import (
	AgentFocusChangedEvent,
	BrowserConnectedEvent,
	BrowserErrorEvent,
	BrowserLaunchEvent,
	BrowserLaunchResult,
	BrowserStartEvent,
	BrowserStopEvent,
	BrowserStoppedEvent,
	TabCreatedEvent,
)

from agentyc.browser.session_network import configure_fetch_interception
from agentyc.browser.session_watchdogs import attach_all_watchdogs
from agentyc.utils import get_agentyc_version, is_new_tab_page

if TYPE_CHECKING:
	from agentyc.browser.session import BrowserSession


SessionT = TypeVar('SessionT', bound='BrowserSession')


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
			else:
				session.logger.debug('Already connected to CDP, skipping reconnection')

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
	await _redirect_new_tab_pages(session, page_targets)
	initial_target_id: str | None = None

	if not page_targets:
		assert session._cdp_client_root is not None
		initial_target_id = await session._cdp_create_new_page('about:blank')
		session.logger.debug(f'📄 Created new blank page: {initial_target_id}')
		page_targets = session.session_manager.get_all_page_targets()
	else:
		initial_target_id = page_targets[0].target_id
		session.logger.debug(f'📄 Using existing page: {initial_target_id}')

	if initial_target_id is not None:
		try:
			await session.get_or_create_cdp_session(initial_target_id, focus=True)
			await session._cdp_get_window_context(initial_target_id)
			session.logger.debug(f'📄 Agent focus set to {initial_target_id[:8]}...')
		except ValueError as e:
			raise RuntimeError(f'Failed to get session for initial target {initial_target_id}: {e}') from e

	for idx, target in enumerate(page_targets):
		await session._cdp_get_window_context(target.target_id)
		session.event_bus.dispatch(TabCreatedEvent(url=target.url, target_id=target.target_id))

	if page_targets:
		session.event_bus.dispatch(AgentFocusChangedEvent(target_id=page_targets[0].target_id, url=page_targets[0].url))

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
		from agentyc.browser.session_reconnect import _attach_ws_drop_callback

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
