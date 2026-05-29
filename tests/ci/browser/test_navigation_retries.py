from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from agentyc.browser import BrowserProfile, BrowserSession, session_navigation


async def test_navigate_and_wait_retries_transient_transport_failures():
	session = BrowserSession(browser_profile=BrowserProfile(headless=True))
	session.session_manager = SimpleNamespace(get_target=lambda _target_id: SimpleNamespace(url='about:blank'))
	fake_send = SimpleNamespace(
		Page=SimpleNamespace(
			navigate=AsyncMock(
				side_effect=[
					{'errorText': 'net::ERR_HTTP2_PROTOCOL_ERROR'},
					{'loaderId': 'loader-2'},
				]
			)
		),
		Runtime=SimpleNamespace(
			evaluate=AsyncMock(
				return_value={'result': {'value': {'readyState': 'complete', 'url': 'https://example.test/recovered'}}}
			)
		),
	)
	fake_cdp_session = SimpleNamespace(
		session_id='session-1',
		target_id='target-1',
		cdp_client=SimpleNamespace(send=fake_send),
		_lifecycle_events=[],
	)

	with (
		patch.object(BrowserSession, 'get_or_create_cdp_session', new=AsyncMock(return_value=fake_cdp_session)),
		patch.object(session_navigation, '_TRANSIENT_NAVIGATION_RETRY_BASE_DELAY_S', 0.0),
	):
		await session._navigate_and_wait('https://example.test/recovered', 'target-1', timeout=8.0, wait_until='load')

	assert fake_send.Page.navigate.await_count == 2
	fake_send.Runtime.evaluate.assert_awaited()


async def test_navigate_and_wait_does_not_retry_non_transient_transport_failures():
	session = BrowserSession(browser_profile=BrowserProfile(headless=True))
	session.session_manager = SimpleNamespace(get_target=lambda _target_id: SimpleNamespace(url='about:blank'))
	fake_send = SimpleNamespace(
		Page=SimpleNamespace(navigate=AsyncMock(return_value={'errorText': 'net::ERR_NAME_NOT_RESOLVED'})),
		Runtime=SimpleNamespace(evaluate=AsyncMock()),
	)
	fake_cdp_session = SimpleNamespace(
		session_id='session-1',
		target_id='target-1',
		cdp_client=SimpleNamespace(send=fake_send),
		_lifecycle_events=[],
	)

	with (
		patch.object(BrowserSession, 'get_or_create_cdp_session', new=AsyncMock(return_value=fake_cdp_session)),
		patch.object(session_navigation, '_TRANSIENT_NAVIGATION_RETRY_BASE_DELAY_S', 0.0),
	):
		with pytest.raises(RuntimeError, match='ERR_NAME_NOT_RESOLVED'):
			await session._navigate_and_wait('https://example.test/missing', 'target-1', timeout=8.0, wait_until='load')

	assert fake_send.Page.navigate.await_count == 1
	fake_send.Runtime.evaluate.assert_not_awaited()


async def test_navigate_and_wait_stops_after_bounded_transient_retries():
	session = BrowserSession(browser_profile=BrowserProfile(headless=True))
	session.session_manager = SimpleNamespace(get_target=lambda _target_id: SimpleNamespace(url='about:blank'))
	fake_send = SimpleNamespace(
		Page=SimpleNamespace(navigate=AsyncMock(return_value={'errorText': 'net::ERR_HTTP2_PROTOCOL_ERROR'})),
		Runtime=SimpleNamespace(evaluate=AsyncMock()),
	)
	fake_cdp_session = SimpleNamespace(
		session_id='session-1',
		target_id='target-1',
		cdp_client=SimpleNamespace(send=fake_send),
		_lifecycle_events=[],
	)

	with (
		patch.object(BrowserSession, 'get_or_create_cdp_session', new=AsyncMock(return_value=fake_cdp_session)),
		patch.object(session_navigation, '_TRANSIENT_NAVIGATION_RETRY_BASE_DELAY_S', 0.0),
		patch.object(session_navigation, '_TRANSIENT_NAVIGATION_MAX_ATTEMPTS', 3),
	):
		with pytest.raises(RuntimeError, match='ERR_HTTP2_PROTOCOL_ERROR'):
			await session._navigate_and_wait('https://example.test/flaky', 'target-1', timeout=8.0, wait_until='load')

	assert fake_send.Page.navigate.await_count == 3
	fake_send.Runtime.evaluate.assert_not_awaited()


async def test_refresh_navigation_target_state_schedules_frame_snapshot_prewarm_for_focused_target():
	session = BrowserSession(browser_profile=BrowserProfile(headless=True))
	session.agent_focus_target_id = 'target-1'
	session._cached_browser_state_summary = object()
	session._cached_selector_map['stale'] = object()
	clear_cache = Mock()
	session._dom_watchdog = SimpleNamespace(clear_cache=clear_cache)
	scheduled_coroutines = []

	def fake_create_task(coro, *, name: str, logger_instance, suppress_exceptions: bool):
		scheduled_coroutines.append((coro, name, suppress_exceptions))
		return None

	with (
		patch.object(BrowserSession, '_apply_runtime_markers_to_target', new=AsyncMock()) as apply_runtime_markers,
		patch('agentyc.browser.session_navigation_refresh.get_all_frames', new=AsyncMock()) as get_all_frames,
		patch('agentyc.browser.session_navigation_refresh.create_task_with_error_handling', side_effect=fake_create_task),
	):
		await session_navigation._refresh_navigation_target_state(session, 'target-1')

	for coro, _, _ in scheduled_coroutines:
		coro.close()

	clear_cache.assert_called_once_with()
	assert session._cached_browser_state_summary is None
	assert session._cached_selector_map == {}
	assert [name for _, name, _ in scheduled_coroutines] == [
		'refresh_navigation_runtime_markers',
		'refresh_navigation_frame_snapshot',
	]
	apply_runtime_markers.assert_called_once_with('target-1')
	get_all_frames.assert_called_once_with(session, include_backend_node_ids=False)


async def test_refresh_navigation_target_state_skips_frame_snapshot_prewarm_for_non_focused_target():
	session = BrowserSession(browser_profile=BrowserProfile(headless=True))
	session.agent_focus_target_id = 'target-2'
	session._cached_browser_state_summary = object()
	session._cached_selector_map['stale'] = object()
	clear_cache = Mock()
	session._dom_watchdog = SimpleNamespace(clear_cache=clear_cache)
	scheduled_coroutines = []

	def fake_create_task(coro, *, name: str, logger_instance, suppress_exceptions: bool):
		scheduled_coroutines.append((coro, name, suppress_exceptions))
		return None

	with (
		patch.object(BrowserSession, '_apply_runtime_markers_to_target', new=AsyncMock()) as apply_runtime_markers,
		patch('agentyc.browser.session_navigation_refresh.get_all_frames', new=AsyncMock()) as get_all_frames,
		patch('agentyc.browser.session_navigation_refresh.create_task_with_error_handling', side_effect=fake_create_task),
	):
		await session_navigation._refresh_navigation_target_state(session, 'target-1')

	for coro, _, _ in scheduled_coroutines:
		coro.close()

	clear_cache.assert_called_once_with()
	assert [name for _, name, _ in scheduled_coroutines] == ['refresh_navigation_runtime_markers']
	apply_runtime_markers.assert_called_once_with('target-1')
	get_all_frames.assert_not_called()
