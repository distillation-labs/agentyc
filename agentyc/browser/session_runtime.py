"""Session runtime, state, and lightweight lifecycle helpers for BrowserSession."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, cast

from bubus import EventBus

from agentyc.browser.events import (
	AgentFocusChangedEvent,
	BrowserStartEvent,
	BrowserStateRequestEvent,
	BrowserStopEvent,
	CloseTabEvent,
	FileDownloadedEvent,
	NavigateToUrlEvent,
	SwitchTabEvent,
	TabClosedEvent,
	TabCreatedEvent,
)
from agentyc.browser.hud_events import publish_browser_event
from agentyc.browser.session_models import RuntimeOwnershipMetadata
from agentyc.browser.views import BrowserStateSummary

if TYPE_CHECKING:
	from agentyc.browser.session import BrowserSession
	from agentyc.browser.session_models import Target


async def reset(session: BrowserSession) -> None:
	session._intentional_stop = True
	if session._reconnect_task and not session._reconnect_task.done():
		session._reconnect_task.cancel()
		session._reconnect_task = None
	session._reconnecting = False
	session._reconnect_event.set()

	cdp_status = 'connected' if session._cdp_client_root else 'not connected'
	session_mgr_status = 'exists' if session.session_manager else 'None'
	session.logger.debug(
		f'🔄 Resetting browser session (CDP: {cdp_status}, SessionManager: {session_mgr_status}, '
		f'focus: {session.agent_focus_target_id[-4:] if session.agent_focus_target_id else "None"})'
	)

	if session.session_manager:
		await session.session_manager.clear()
		session.session_manager = None

	if session._cdp_client_root:
		try:
			await session._cdp_client_root.stop()
			session.logger.debug('Closed CDP client WebSocket during reset')
		except Exception as e:
			session.logger.debug(f'Error closing CDP client during reset: {e}')

	session._cdp_client_root = None
	session._cached_browser_state_summary = None
	session._cached_selector_map.clear()
	session._downloaded_files.clear()
	session._network_mock_rules.clear()
	session._network_conditions_by_target.clear()
	session._fetch_handlers_registered = False
	session.agent_focus_target_id = None
	if session.is_local:
		session.browser_profile.cdp_url = None

	session._crash_watchdog = None
	session._downloads_watchdog = None
	session._aboutblank_watchdog = None
	session._security_watchdog = None
	session._storage_state_watchdog = None
	session._local_browser_watchdog = None
	session._default_action_watchdog = None
	session._dom_watchdog = None
	session._screenshot_watchdog = None
	session._permissions_watchdog = None
	session._recording_watchdog = None
	session._captcha_watchdog = None
	session._watchdogs_attached = False
	if session._demo_mode:
		session._demo_mode.cleanup()
		session._demo_mode.reset()
		session._demo_mode = None

	session._intentional_stop = False
	session.logger.info('✅ Browser session reset complete')


def model_post_init(session: BrowserSession, context: Any) -> None:
	session._connection_lock = asyncio.Lock()
	session._runtime_metadata = RuntimeOwnershipMetadata.create(
		session_id=session.id,
		runtime_label=session.browser_profile.runtime_label,
		runtime_role=session.browser_profile.runtime_role,
		parent_runtime_id=session.browser_profile.parent_runtime_id,
	)
	session._reconnect_event = asyncio.Event()
	session._reconnect_event.set()

	from agentyc.browser.watchdog_base import BaseWatchdog

	start_handlers = session.event_bus.handlers.get('BrowserStartEvent', [])
	start_handler_names = [getattr(h, '__name__', str(h)) for h in start_handlers]
	if any('on_BrowserStartEvent' in name for name in start_handler_names):
		raise RuntimeError(
			'[BrowserSession] Duplicate handler registration attempted! '
			'on_BrowserStartEvent is already registered. '
			'This likely means BrowserSession was initialized multiple times with the same EventBus.'
		)

	BaseWatchdog.attach_handler_to_session(session, BrowserStartEvent, session.on_BrowserStartEvent)
	BaseWatchdog.attach_handler_to_session(session, BrowserStopEvent, session.on_BrowserStopEvent)
	BaseWatchdog.attach_handler_to_session(session, NavigateToUrlEvent, session.on_NavigateToUrlEvent)
	BaseWatchdog.attach_handler_to_session(session, SwitchTabEvent, session.on_SwitchTabEvent)
	BaseWatchdog.attach_handler_to_session(session, TabCreatedEvent, session.on_TabCreatedEvent)
	BaseWatchdog.attach_handler_to_session(session, TabClosedEvent, session.on_TabClosedEvent)
	BaseWatchdog.attach_handler_to_session(session, AgentFocusChangedEvent, session.on_AgentFocusChangedEvent)
	BaseWatchdog.attach_handler_to_session(session, FileDownloadedEvent, session.on_FileDownloadedEvent)
	BaseWatchdog.attach_handler_to_session(session, CloseTabEvent, session.on_CloseTabEvent)


async def get_browser_state_summary(
	session: BrowserSession,
	include_screenshot: bool = True,
	cached: bool = False,
	include_recent_events: bool = False,
) -> BrowserStateSummary:
	if cached and session._cached_browser_state_summary is not None and session._cached_browser_state_summary.dom_state:
		selector_map = session._cached_browser_state_summary.dom_state.selector_map
		if include_screenshot and not session._cached_browser_state_summary.screenshot:
			session.logger.debug('⚠️ Cached browser state has no screenshot, fetching fresh state with screenshot')
		elif selector_map and len(selector_map) > 0:
			session.logger.debug('🔄 Using pre-cached browser state summary for open tab')
			return session._cached_browser_state_summary
		else:
			session.logger.debug('⚠️ Cached browser state has 0 interactive elements, fetching fresh state')

	event: BrowserStateRequestEvent = cast(
		BrowserStateRequestEvent,
		session.event_bus.dispatch(
			BrowserStateRequestEvent(
				include_dom=True,
				include_screenshot=include_screenshot,
				include_recent_events=include_recent_events,
			)
		),
	)
	result = await event.event_result(raise_if_none=True, raise_if_any=True)
	assert result is not None and result.dom_state is not None
	return result


async def get_state_as_text(session: BrowserSession) -> str:
	state = await get_browser_state_summary(session)
	assert state.dom_state is not None
	return state.dom_state.llm_representation()


def get_focused_target(session: BrowserSession) -> Target | None:
	if not session.session_manager:
		return None
	return session.session_manager.get_focused_target()


def get_page_targets(session: BrowserSession) -> list[Target]:
	if not session.session_manager:
		return []
	return session.session_manager.get_all_page_targets()


async def on_FileDownloadedEvent(session: BrowserSession, event: FileDownloadedEvent) -> None:
	session.logger.debug(f'FileDownloadedEvent received: {event.file_name} at {event.path}')
	if event.path and event.path not in session._downloaded_files:
		session._downloaded_files.append(event.path)
		publish_browser_event(session_id=session.id, label=f'Downloaded {event.file_name}')
		session.logger.info(
			f'📁 Tracked download: {event.file_name} ({len(session._downloaded_files)} total downloads in session)'
		)
	else:
		if not event.path:
			session.logger.warning(f'FileDownloadedEvent has no path: {event}')
		else:
			session.logger.debug(f'File already tracked: {event.path}')


async def kill(session: BrowserSession) -> None:
	session._intentional_stop = True
	session.logger.debug('🛑 kill() called - stopping browser with force=True and resetting state')
	from agentyc.browser.events import BrowserKillEvent, SaveStorageStateEvent

	local_watchdog = session._local_browser_watchdog
	save_event = session.event_bus.dispatch(SaveStorageStateEvent())
	await save_event
	await session.event_bus.dispatch(BrowserStopEvent(force=True))
	if (
		local_watchdog
		and getattr(local_watchdog, '_subprocess', None) is not None
		and getattr(local_watchdog, '_owns_browser_resources', True)
	):
		await local_watchdog.on_BrowserKillEvent(BrowserKillEvent())
	await session.event_bus.stop(clear=True, timeout=5)
	await reset(session)
	session.event_bus = EventBus()


async def stop(session: BrowserSession) -> None:
	session._intentional_stop = True
	session.logger.debug('⏸️  stop() called - stopping browser gracefully (force=False) and resetting state')
	from agentyc.browser.events import BrowserKillEvent, SaveStorageStateEvent

	local_watchdog = session._local_browser_watchdog
	save_event = session.event_bus.dispatch(SaveStorageStateEvent())
	await save_event
	await session.event_bus.dispatch(BrowserStopEvent(force=False))
	if (
		local_watchdog
		and getattr(local_watchdog, '_subprocess', None) is not None
		and getattr(local_watchdog, '_owns_browser_resources', True)
	):
		await local_watchdog.on_BrowserKillEvent(BrowserKillEvent())
	await session.event_bus.stop(clear=True, timeout=5)
	await reset(session)
	session.event_bus = EventBus()


async def close(session: BrowserSession) -> None:
	await stop(session)


async def send_demo_mode_log(
	session: BrowserSession, message: str, level: str = 'info', metadata: dict[str, Any] | None = None
) -> None:
	if not session.browser_profile.demo_mode:
		return
	demo = session.demo_mode
	if not demo:
		return
	try:
		await demo.send_log(message=message, level=level, metadata=metadata or {})
	except Exception as exc:
		session.logger.debug(f'[DemoMode] Failed to send log: {exc}')


def downloaded_files(session: BrowserSession) -> list[str]:
	return session._downloaded_files.copy()
