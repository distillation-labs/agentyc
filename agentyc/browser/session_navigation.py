"""Navigation and tab workflow helpers for BrowserSession."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from cdp_use.cdp.target import TargetID

from agentyc.browser.events import (
	AgentFocusChangedEvent,
	CloseTabEvent,
	NavigateToUrlEvent,
	NavigationCompleteEvent,
	NavigationStartedEvent,
	SwitchTabEvent,
	TabClosedEvent,
	TabCreatedEvent,
)
from agentyc.utils import create_task_with_error_handling, is_new_tab_page

if TYPE_CHECKING:
	from agentyc.browser.session import BrowserSession

_TRANSIENT_NAVIGATION_ERROR_MARKERS = (
	'ERR_HTTP2_PROTOCOL_ERROR',
	'ERR_HTTP3_PROTOCOL_ERROR',
	'ERR_QUIC_PROTOCOL_ERROR',
	'ERR_NETWORK_CHANGED',
	'ERR_CONNECTION_RESET',
	'ERR_CONNECTION_CLOSED',
	'ERR_CONNECTION_ABORTED',
	'ERR_EMPTY_RESPONSE',
)
_TRANSIENT_NAVIGATION_MAX_ATTEMPTS = 5
_TRANSIENT_NAVIGATION_RETRY_BASE_DELAY_S = 0.75


def _is_transient_navigation_error(error: Exception) -> bool:
	message = str(error).upper()
	return any(marker in message for marker in _TRANSIENT_NAVIGATION_ERROR_MARKERS)


async def on_NavigateToUrlEvent(session: BrowserSession, event: NavigateToUrlEvent) -> None:
	"""Handle navigation requests - core browser functionality."""
	session.logger.debug(f'[on_NavigateToUrlEvent] Received NavigateToUrlEvent: url={event.url}, new_tab={event.new_tab}')
	if not session.agent_focus_target_id:
		focus_recovered = False
		if session.session_manager is not None:
			focus_recovered = await session.session_manager.ensure_valid_focus(timeout=3.0)
		if not focus_recovered or not session.agent_focus_target_id:
			raise RuntimeError('Cannot navigate - browser not connected')

	target_id = None
	current_target_id = session.agent_focus_target_id
	current_target = session.session_manager.get_target(current_target_id)
	if (
		event.new_tab
		and current_target is not None
		and is_new_tab_page(current_target.url)
		and (not session.is_shared_browser_runtime or session.is_target_owned_by_current_runtime(current_target_id))
	):
		session.logger.debug(f'[on_NavigateToUrlEvent] Already on blank tab ({current_target.url}), reusing')
		event.new_tab = False

	try:
		session.logger.debug(f'[on_NavigateToUrlEvent] Processing new_tab={event.new_tab}')

		if event.new_tab:
			page_targets = (
				session.get_owned_page_targets()
				if session.is_shared_browser_runtime
				else session.session_manager.get_all_page_targets()
			)
			session.logger.debug(f'[on_NavigateToUrlEvent] Found {len(page_targets)} existing tabs')

			for idx, target in enumerate(page_targets):
				session.logger.debug(f'[on_NavigateToUrlEvent] Tab {idx}: url={target.url}, targetId={target.target_id}')
				if target.url == 'about:blank' and target.target_id != current_target_id:
					target_id = target.target_id
					session.logger.debug(f'Reusing existing about:blank tab #{target_id[-4:]}')
					break

			if not target_id:
				session.logger.debug('[on_NavigateToUrlEvent] No reusable about:blank tab found, creating new tab...')
				try:
					target_id = await session._cdp_create_new_page(
						'about:blank',
						background=session.browser_profile.shared_browser_focus_policy == 'preserve',
					)
					session.logger.debug(f'Created new tab #{target_id[-4:]}')
					await session.event_bus.dispatch(TabCreatedEvent(target_id=target_id, url='about:blank'))
				except Exception as e:
					session.logger.error(f'[on_NavigateToUrlEvent] Failed to create new tab: {type(e).__name__}: {e}')
					target_id = current_target_id
					session.logger.warning(f'[on_NavigateToUrlEvent] Falling back to current tab #{target_id[-4:]}')
		else:
			target_id = target_id or current_target_id

		if session.agent_focus_target_id is None or session.agent_focus_target_id != target_id:
			session.logger.debug(
				f'[on_NavigateToUrlEvent] Switching to target tab {target_id[-4:]} '
				f'(current: {session.agent_focus_target_id[-4:] if session.agent_focus_target_id else "none"})'
			)
			await session.event_bus.dispatch(
				SwitchTabEvent(
					target_id=target_id,
					activate_target=session.browser_profile.shared_browser_focus_policy == 'activate',
				)
			)
		else:
			session.logger.debug(f'[on_NavigateToUrlEvent] Already on target tab {target_id[-4:]}, skipping SwitchTabEvent')

		assert session.agent_focus_target_id is not None and session.agent_focus_target_id == target_id, (
			'Agent focus not updated to new target_id after SwitchTabEvent should have switched to it'
		)

		await session.event_bus.dispatch(NavigationStartedEvent(target_id=target_id, url=event.url))
		await _navigate_and_wait(
			session,
			event.url,
			target_id,
			timeout=event.timeout_ms / 1000 if event.timeout_ms is not None else None,
			wait_until=event.wait_until,
		)
		await _close_extension_options_pages(session)

		session.logger.debug(f'Dispatching NavigationCompleteEvent for {event.url} (tab #{target_id[-4:]})')
		await session.event_bus.dispatch(
			NavigationCompleteEvent(
				target_id=target_id,
				url=event.url,
				status=None,
			)
		)
		await _refresh_navigation_target_state(session, target_id)

	except Exception as e:
		session.logger.error(f'Navigation failed: {type(e).__name__}: {e}')
		if 'target_id' in locals() and target_id:
			await session.event_bus.dispatch(
				NavigationCompleteEvent(
					target_id=target_id,
					url=event.url,
					error_message=f'{type(e).__name__}: {e}',
				)
			)
			await _refresh_navigation_target_state(session, target_id)
		raise


async def _refresh_navigation_target_state(session: BrowserSession, target_id: str) -> None:
	"""Refresh lightweight per-tab state after navigation without re-running full focus handlers."""
	if session._dom_watchdog:
		session._dom_watchdog.clear_cache()
	session._cached_browser_state_summary = None
	session._cached_selector_map.clear()
	create_task_with_error_handling(
		session._apply_runtime_markers_to_target(target_id),
		name='refresh_navigation_runtime_markers',
		logger_instance=session.logger,
		suppress_exceptions=True,
	)


async def _navigate_and_wait(
	session: BrowserSession,
	url: str,
	target_id: str,
	timeout: float | None = None,
	wait_until: str = 'load',
	nav_timeout: float | None = None,
) -> None:
	"""Navigate to URL and wait for page readiness using CDP lifecycle events."""
	for attempt in range(1, _TRANSIENT_NAVIGATION_MAX_ATTEMPTS + 1):
		try:
			await _navigate_and_wait_once(
				session,
				url,
				target_id,
				timeout=timeout,
				wait_until=wait_until,
				nav_timeout=nav_timeout,
			)
			return
		except Exception as e:
			if not _is_transient_navigation_error(e) or attempt >= _TRANSIENT_NAVIGATION_MAX_ATTEMPTS:
				raise
			retry_delay = _TRANSIENT_NAVIGATION_RETRY_BASE_DELAY_S * attempt
			session.logger.warning(
				f'⚠️ Transient navigation error for {url} on attempt {attempt}/{_TRANSIENT_NAVIGATION_MAX_ATTEMPTS}: '
				f'{e}. Retrying in {retry_delay:.2f}s...'
			)
			await asyncio.sleep(retry_delay)


async def _navigate_and_wait_once(
	session: BrowserSession,
	url: str,
	target_id: str,
	timeout: float | None = None,
	wait_until: str = 'load',
	nav_timeout: float | None = None,
) -> None:
	"""Perform one navigation attempt and wait for page readiness."""
	cdp_session = await session.get_or_create_cdp_session(target_id, focus=True)

	if timeout is None:
		target = session.session_manager.get_target(target_id)
		current_url = target.url
		same_domain = (
			url.split('/')[2] == current_url.split('/')[2] if url.startswith('http') and current_url.startswith('http') else False
		)
		timeout = 3.0 if same_domain else 8.0

	nav_start_time = asyncio.get_event_loop().time()
	if nav_timeout is None:
		nav_timeout = 20.0
	try:
		nav_result = await asyncio.wait_for(
			cdp_session.cdp_client.send.Page.navigate(
				params={'url': url, 'transitionType': 'address_bar'},
				session_id=cdp_session.session_id,
			),
			timeout=nav_timeout,
		)
	except TimeoutError:
		duration_ms = (asyncio.get_event_loop().time() - nav_start_time) * 1000
		raise RuntimeError(f'Page.navigate() timed out after {nav_timeout}s ({duration_ms:.0f}ms) for {url}')

	if nav_result.get('errorText'):
		raise RuntimeError(f'Navigation failed: {nav_result["errorText"]}')

	if wait_until == 'commit':
		duration_ms = (asyncio.get_event_loop().time() - nav_start_time) * 1000
		session.logger.debug(f'✅ Page ready for {url} (commit, {duration_ms:.0f}ms)')
		return

	navigation_id = nav_result.get('loaderId')
	start_time = asyncio.get_event_loop().time()
	seen_events = []
	next_dom_ready_probe_at = start_time

	if not hasattr(cdp_session, '_lifecycle_events'):
		raise RuntimeError(
			f'❌ Lifecycle monitoring not enabled for {cdp_session.target_id[:8]}! '
			f'This is a bug - SessionManager should have initialized it. '
			f'Session: {cdp_session}'
		)

	acceptable_events: set[str] = {'networkIdle'}
	if wait_until in ('load', 'domcontentloaded'):
		acceptable_events.add('load')
	if wait_until == 'domcontentloaded':
		acceptable_events.add('DOMContentLoaded')

	poll_interval = 0.05
	while (asyncio.get_event_loop().time() - start_time) < timeout:
		try:
			for event_data in list(cdp_session._lifecycle_events):
				event_timestamp = event_data.get('timestamp')
				if isinstance(event_timestamp, (int, float)) and event_timestamp < nav_start_time:
					continue
				event_name = event_data.get('name')
				event_loader_id = event_data.get('loaderId')

				event_str = f'{event_name}(loader={event_loader_id[:8] if event_loader_id else "none"})'
				if event_str not in seen_events:
					seen_events.append(event_str)

				if event_loader_id and navigation_id and event_loader_id != navigation_id:
					continue

				if event_name in acceptable_events:
					duration_ms = (asyncio.get_event_loop().time() - nav_start_time) * 1000
					session.logger.debug(f'✅ Page ready for {url} ({event_name}, {duration_ms:.0f}ms)')
					return

		except Exception as e:
			session.logger.debug(f'Error polling lifecycle events: {e}')

		now = asyncio.get_event_loop().time()
		if wait_until in {'load', 'domcontentloaded'} and now >= next_dom_ready_probe_at:
			if await _navigation_ready_via_dom(session, cdp_session=cdp_session, url=url, wait_until=wait_until):
				duration_ms = (asyncio.get_event_loop().time() - nav_start_time) * 1000
				session.logger.debug(f'✅ Page ready for {url} (document.readyState fallback, {duration_ms:.0f}ms)')
				return
			next_dom_ready_probe_at = now + 0.25

		await asyncio.sleep(poll_interval)

	duration_ms = (asyncio.get_event_loop().time() - nav_start_time) * 1000
	if not seen_events:
		session.logger.error(
			f'❌ No lifecycle events received for {url} after {duration_ms:.0f}ms! '
			f'Monitoring may have failed. Target: {cdp_session.target_id[:8]}'
		)
	else:
		session.logger.warning(f'⚠️ Page readiness timeout ({timeout}s, {duration_ms:.0f}ms) for {url}')
	if wait_until in {'load', 'domcontentloaded'} and await _navigation_ready_via_dom(
		session, cdp_session=cdp_session, url=url, wait_until=wait_until
	):
		session.logger.debug(f'✅ Page ready for {url} (final document.readyState fallback, {duration_ms:.0f}ms)')
		return
	probe = await _get_navigation_dom_probe(session, cdp_session=cdp_session)
	if probe is not None:
		current_url, ready_state = probe
		if current_url and not _urls_match_for_navigation_ready(current_url, url):
			raise RuntimeError(
				f'Navigation timed out after {timeout}s before reaching {url}; '
				f'current page remained {current_url} (readyState={ready_state or "unknown"})'
			)
		if current_url:
			raise RuntimeError(
				f'Navigation timed out after {timeout}s waiting for {wait_until} on {url} '
				f'(current_url={current_url}, readyState={ready_state or "unknown"})'
			)
	raise RuntimeError(f'Navigation timed out after {timeout}s waiting for {wait_until} on {url}')


def _urls_match_for_navigation_ready(current_url: str, target_url: str) -> bool:
	current = urlsplit(current_url)
	target = urlsplit(target_url)
	return (
		current.scheme,
		current.netloc,
		current.path.rstrip('/'),
		current.query,
	) == (
		target.scheme,
		target.netloc,
		target.path.rstrip('/'),
		target.query,
	)


async def _navigation_ready_via_dom(session: BrowserSession, *, cdp_session: Any, url: str, wait_until: str) -> bool:
	probe = await _get_navigation_dom_probe(session, cdp_session=cdp_session)
	if probe is None:
		return False
	current_url, ready_state = probe
	if not _urls_match_for_navigation_ready(current_url, url):
		return False
	if wait_until == 'domcontentloaded':
		return ready_state in {'interactive', 'complete'}
	return ready_state == 'complete'


async def _get_navigation_dom_probe(session: BrowserSession, *, cdp_session: Any) -> tuple[str, str] | None:
	try:
		result = await cdp_session.cdp_client.send.Runtime.evaluate(
			params={
				'expression': '({readyState: document.readyState, url: window.location.href})',
				'returnByValue': True,
			},
			session_id=cdp_session.session_id,
		)
	except Exception as e:
		session.logger.debug(f'document.readyState fallback failed: {e}')
		return None

	payload = result.get('result', {}).get('value')
	if not isinstance(payload, dict):
		return None
	return (str(payload.get('url') or ''), str(payload.get('readyState') or ''))


async def on_SwitchTabEvent(session: BrowserSession, event: SwitchTabEvent) -> TargetID:
	"""Handle tab switching - core browser functionality."""
	if not session.agent_focus_target_id:
		focus_recovered = await session.session_manager.ensure_valid_focus(timeout=3.0)
		if not focus_recovered or not session.agent_focus_target_id:
			raise RuntimeError('Cannot switch tabs - browser not connected')

	page_targets = session.session_manager.get_all_page_targets()
	if event.target_id is None:
		if page_targets:
			candidate_targets = session.get_owned_page_targets() if session.is_shared_browser_runtime else page_targets
			if candidate_targets:
				event.target_id = candidate_targets[-1].target_id
			else:
				raise RuntimeError('Cannot switch tabs: no tabs owned by this runtime are available')
		else:
			assert session._cdp_client_root is not None, 'CDP client root not initialized - browser may not be connected yet'
			target_id = await session._cdp_create_new_page(
				'about:blank',
				background=session.browser_profile.shared_browser_focus_policy == 'preserve',
			)
			session.event_bus.dispatch(TabCreatedEvent(url='about:blank', target_id=target_id))
			session.event_bus.dispatch(AgentFocusChangedEvent(target_id=target_id, url='about:blank'))
			return target_id

	assert event.target_id is not None, 'target_id must be set at this point'
	session.require_owned_target(event.target_id, action='switch to')
	cdp_session = await session.get_or_create_cdp_session(target_id=event.target_id, focus=True)

	if event.activate_target:
		await cdp_session.cdp_client.send.Target.activateTarget(params={'targetId': event.target_id})

	target = session.session_manager.get_target(event.target_id)
	await session.event_bus.dispatch(
		AgentFocusChangedEvent(
			target_id=target.target_id,
			url=target.url,
		)
	)
	return target.target_id


async def on_CloseTabEvent(session: BrowserSession, event: CloseTabEvent) -> None:
	"""Handle tab closure - update focus if needed."""
	session.require_owned_target(event.target_id, action='close')
	try:
		await session.event_bus.dispatch(TabClosedEvent(target_id=event.target_id))

		try:
			cdp_session = await session.get_or_create_cdp_session(target_id=None, focus=False)
			await cdp_session.cdp_client.send.Target.closeTarget(params={'targetId': event.target_id})
		except Exception as e:
			session.logger.debug(f'Target may already be closed: {e}')
	except Exception as e:
		session.logger.warning(f'Error during tab close cleanup: {e}')


async def on_TabClosedEvent(session: BrowserSession, event: TabClosedEvent) -> None:
	"""Handle tab closure - update focus if needed."""
	if not session.agent_focus_target_id:
		return

	current_target_id = session.agent_focus_target_id
	if current_target_id == event.target_id:
		await session.event_bus.dispatch(SwitchTabEvent(target_id=None))


async def on_TabCreatedEvent(session: BrowserSession, event: TabCreatedEvent) -> None:
	"""Handle tab creation - apply viewport settings to new tab."""
	await session._apply_runtime_markers_to_target(event.target_id)
	await session._cdp_get_window_context(event.target_id)

	if session.browser_profile.viewport and not session.browser_profile.no_viewport:
		try:
			viewport_width = session.browser_profile.viewport.width
			viewport_height = session.browser_profile.viewport.height
			device_scale_factor = session.browser_profile.device_scale_factor or 1.0

			session.logger.info(
				f'Setting viewport to {viewport_width}x{viewport_height} with device scale factor {device_scale_factor} whereas original device scale factor was {session.browser_profile.device_scale_factor}'
			)
			await session._cdp_set_viewport(viewport_width, viewport_height, device_scale_factor, target_id=event.target_id)
			session.logger.debug(f'Applied viewport {viewport_width}x{viewport_height} to tab {event.target_id[-8:]}')
		except Exception as e:
			session.logger.warning(f'Failed to set viewport for new tab {event.target_id[-8:]}: {e}')


async def on_AgentFocusChangedEvent(session: BrowserSession, event: AgentFocusChangedEvent) -> None:
	"""Handle agent focus change - update focus and clear cache."""
	session.logger.debug(f'🔄 AgentFocusChangedEvent received: target_id=...{event.target_id[-4:]} url={event.url}')

	if session._dom_watchdog:
		session._dom_watchdog.clear_cache()

	session._cached_browser_state_summary = None
	session._cached_selector_map.clear()
	session.logger.debug('🔄 Cached browser state cleared')

	if event.target_id:
		await session.get_or_create_cdp_session(target_id=event.target_id, focus=True)
		await session._apply_runtime_markers_to_target(event.target_id)

		if session.browser_profile.viewport and not session.browser_profile.no_viewport:
			try:
				viewport_width = session.browser_profile.viewport.width
				viewport_height = session.browser_profile.viewport.height
				device_scale_factor = session.browser_profile.device_scale_factor or 1.0
				await session._cdp_set_viewport(viewport_width, viewport_height, device_scale_factor, target_id=event.target_id)
				session.logger.debug(f'Applied viewport {viewport_width}x{viewport_height} to tab {event.target_id[-8:]}')
			except Exception as e:
				session.logger.warning(f'Failed to set viewport for tab {event.target_id[-8:]}: {e}')
	else:
		raise RuntimeError('AgentFocusChangedEvent received with no target_id for newly focused tab')


async def navigate_to(session: BrowserSession, url: str, new_tab: bool = False) -> None:
	"""Navigate to a URL using the standard event system."""
	event = session.event_bus.dispatch(NavigateToUrlEvent(url=url, new_tab=new_tab))
	await event
	await event.event_result(raise_if_any=True, raise_if_none=False)


async def _close_extension_options_pages(session: BrowserSession) -> None:
	"""Close any extension options/welcome pages that have opened."""
	try:
		page_targets = session.session_manager.get_all_page_targets()

		for target in page_targets:
			target_url = target.url
			target_id = target.target_id

			if 'chrome-extension://' in target_url and (
				'options.html' in target_url or 'welcome.html' in target_url or 'onboarding.html' in target_url
			):
				session.logger.info(f'[BrowserSession] 🚫 Closing extension options page: {target_url}')
				try:
					await session._cdp_close_page(target_id)
				except Exception as e:
					session.logger.debug(f'[BrowserSession] Could not close extension page {target_id}: {e}')

	except Exception as e:
		session.logger.debug(f'[BrowserSession] Error closing extension options pages: {e}')
