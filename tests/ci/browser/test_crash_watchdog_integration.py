from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from agentyc.browser.events import BrowserConnectedEvent, BrowserReconnectedEvent, TabCreatedEvent
from agentyc.browser.session import BrowserSession
from agentyc.browser.watchdogs.crash_watchdog import CrashWatchdog


@pytest.mark.asyncio
async def test_attach_all_watchdogs_registers_crash_watchdog_once():
	session = BrowserSession(headless=True, user_data_dir=None)

	await session.attach_all_watchdogs()
	connected_handlers = [getattr(handler, '__name__', '') for handler in session.event_bus.handlers['BrowserConnectedEvent']]
	created_handlers = [getattr(handler, '__name__', '') for handler in session.event_bus.handlers['TabCreatedEvent']]

	assert session._crash_watchdog is not None
	assert connected_handlers.count('CrashWatchdog.on_BrowserConnectedEvent') == 1
	assert created_handlers.count('CrashWatchdog.on_TabCreatedEvent') == 1

	await session.attach_all_watchdogs()

	connected_handlers_after = [
		getattr(handler, '__name__', '') for handler in session.event_bus.handlers['BrowserConnectedEvent']
	]
	created_handlers_after = [getattr(handler, '__name__', '') for handler in session.event_bus.handlers['TabCreatedEvent']]
	assert connected_handlers_after.count('CrashWatchdog.on_BrowserConnectedEvent') == 1
	assert created_handlers_after.count('CrashWatchdog.on_TabCreatedEvent') == 1


@pytest.mark.asyncio
async def test_crash_watchdog_attaches_existing_page_targets_on_connect(monkeypatch):
	session = BrowserSession(headless=True, user_data_dir=None)
	page_targets = [
		SimpleNamespace(target_id='page-1', url='https://example.com'),
		SimpleNamespace(target_id='page-2', url='https://example.org'),
	]
	session.session_manager = SimpleNamespace(get_all_page_targets=lambda: page_targets)
	watchdog = CrashWatchdog(event_bus=session.event_bus, browser_session=session)
	attach_to_target = AsyncMock()
	start_monitoring = AsyncMock()

	with (
		patch.object(CrashWatchdog, 'attach_to_target', attach_to_target),
		patch.object(CrashWatchdog, '_start_monitoring', start_monitoring),
	):
		await watchdog.on_BrowserConnectedEvent(BrowserConnectedEvent(cdp_url='ws://example.test/devtools/browser/123'))

	assert [call.args[0] for call in attach_to_target.await_args_list] == ['page-1', 'page-2']
	start_monitoring.assert_awaited_once()


@pytest.mark.asyncio
async def test_crash_watchdog_uses_created_target_id_not_agent_focus(monkeypatch):
	session = BrowserSession(headless=True, user_data_dir=None)
	session.agent_focus_target_id = 'focused-tab'
	watchdog = CrashWatchdog(event_bus=session.event_bus, browser_session=session)
	attach_to_target = AsyncMock()

	with patch.object(CrashWatchdog, 'attach_to_target', attach_to_target):
		await watchdog.on_TabCreatedEvent(TabCreatedEvent(target_id='new-tab', url='about:blank'))

	attach_to_target.assert_awaited_once_with('new-tab')


@pytest.mark.asyncio
async def test_crash_watchdog_reconnect_clears_listener_state_before_reattach(monkeypatch):
	session = BrowserSession(headless=True, user_data_dir=None)
	page_target = SimpleNamespace(target_id='page-1', url='https://example.com')
	registered_handlers = []
	fake_register = SimpleNamespace(Target=SimpleNamespace(targetCrashed=lambda handler: registered_handlers.append(handler)))
	fake_cdp_session = SimpleNamespace(session_id='session-1', cdp_client=SimpleNamespace(register=fake_register))
	session.session_manager = SimpleNamespace(
		get_all_page_targets=lambda: [page_target],
		get_target=lambda target_id: page_target if target_id == 'page-1' else None,
	)
	get_or_create_cdp_session = AsyncMock(return_value=fake_cdp_session)
	start_monitoring = AsyncMock()

	watchdog = CrashWatchdog(event_bus=session.event_bus, browser_session=session)
	watchdog._targets_with_listeners.add('page-1')

	with (
		patch.object(BrowserSession, 'get_or_create_cdp_session', get_or_create_cdp_session),
		patch.object(CrashWatchdog, '_start_monitoring', start_monitoring),
	):
		await watchdog.on_BrowserReconnectedEvent(
			BrowserReconnectedEvent(cdp_url='ws://example.test/devtools/browser/123', attempt=1, downtime_seconds=0.25)
		)

	get_or_create_cdp_session.assert_awaited_once_with('page-1', focus=False)
	start_monitoring.assert_awaited_once()
	assert len(registered_handlers) == 1
	assert watchdog._targets_with_listeners == {'page-1'}
