import asyncio
import os
import queue
import sys
from types import SimpleNamespace
from typing import Any, cast

import pytest
from pytest_httpserver import HTTPServer

from agentyc.browser import BrowserProfile, BrowserSession
from agentyc.browser.demo_mode import DemoMode
from agentyc.browser.feedback import BUG_REPORT_URL, FEATURE_REQUEST_URL, SECURITY_POLICY_URL, build_feedback_config
from agentyc.browser.hud_events import publish_intent
from agentyc.browser.hud_overlay import HudOverlay
from agentyc.browser.hud_stream import HudEvent, HudStream
from agentyc.mcp.intent_tools import _set_intent
from agentyc.mcp.server import AgentycServer
from agentyc.mcp.tool_dispatch import _execute_tool
from agentyc.mcp.tool_feedback import _should_publish_hud_event, _summarize_tool_arguments

_HUD_VISIBILITY_HTML = """
<!DOCTYPE html>
<html lang="en">
<head><title>HUD visibility</title></head>
<body>
	<main>
		<h1>HUD visibility test page</h1>
		<p>The in-browser HUD should stay visible in headed mode.</p>
	</main>
</body>
</html>
"""


def _headed_browser_supported() -> bool:
	return sys.platform == 'darwin' or bool(os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY'))


def test_hud_stream_replays_recent_events_by_session() -> None:
	stream = HudStream(max_events=4)
	received: list[HudEvent] = []
	subscriber = received.append
	stream.subscribe(subscriber)

	first = HudEvent(kind='tool_start', label='Navigating to page', session_id='session-a')
	second = HudEvent(kind='tool_done', label='Clicking page element', session_id='session-b')
	third = HudEvent(kind='tool_error', label='Typing failed', session_id='session-a', error='boom')

	stream.publish(first)
	stream.publish(second)
	stream.publish(third)

	assert received == [first, second, third]
	assert stream.recent_events(session_id='session-a') == [first, third]
	assert stream.recent_events(session_id='session-a', limit=1) == [third]


def test_hud_stream_unsubscribe_and_bound_buffer() -> None:
	stream = HudStream(max_events=2)
	received: list[HudEvent] = []
	subscriber = received.append
	stream.subscribe(subscriber)
	stream.publish(HudEvent(kind='tool_start', label='A', session_id='session-a'))
	stream.unsubscribe(subscriber)
	stream.publish(HudEvent(kind='tool_done', label='B', session_id='session-a'))
	stream.publish(HudEvent(kind='tool_done', label='C', session_id='session-a'))

	assert [event.label for event in received] == ['A']
	assert [event.label for event in stream.recent_events()] == ['B', 'C']


def test_demo_mode_script_injects_feedback_config() -> None:
	session = SimpleNamespace(id='session-123')
	demo_mode = DemoMode(cast(Any, session))

	try:
		script = demo_mode._load_script()
		assert '__AGENTYC_HUD_CONFIG_PLACEHOLDER__' not in script
		assert 'session-123' in script
		assert BUG_REPORT_URL in script
		assert FEATURE_REQUEST_URL in script
		assert SECURITY_POLICY_URL in script
		assert 'DETAILS' in script
		assert 'Tool details' in script
	finally:
		demo_mode.cleanup()


def test_build_feedback_config_uses_public_report_destinations() -> None:
	config = build_feedback_config('session-abc')

	assert config['sessionId'] == 'session-abc'
	assert config['feedbackUrls']['bug'] == BUG_REPORT_URL
	assert config['feedbackUrls']['feature'] == FEATURE_REQUEST_URL
	assert config['feedbackUrls']['security'] == SECURITY_POLICY_URL


def test_should_publish_hud_event_filters_fast_state_reads() -> None:
	assert not _should_publish_hud_event('tool_start', 'browser_get_state', None, None)
	assert not _should_publish_hud_event('tool_done', 'browser_get_state', 240.0, None)
	assert not _should_publish_hud_event('tool_done', 'browser_get_state', 900.0, None)
	assert _should_publish_hud_event('tool_done', 'browser_get_state', 2400.0, None)
	assert _should_publish_hud_event('tool_error', 'browser_get_state', 100.0, 'timed out')


def test_summarize_tool_arguments_redacts_sensitive_values() -> None:
	summary = _summarize_tool_arguments(
		{
			'url': 'https://example.com/dashboard',
			'text': 'super-secret-value',
			'headers': {'Authorization': 'Bearer secret'},
			'focus_ref': 'e17',
		}
	)

	assert 'url=https://example.com/dashboard' in summary
	assert 'text=<redacted>' in summary
	assert 'headers=<redacted>' in summary
	assert '+1 more' in summary


async def test_set_intent_publishes_hud_event() -> None:
	stream = HudStream.get()
	received: list[HudEvent] = []
	subscriber = received.append
	stream.subscribe(subscriber)

	try:
		server = SimpleNamespace(browser_session=SimpleNamespace(id='session-z'))
		result = await _set_intent(server, 'Reviewing checkout flow')
	finally:
		stream.unsubscribe(subscriber)

	assert result == 'Intent updated: Reviewing checkout flow'
	assert received[-1].kind == 'intent'
	assert received[-1].label == 'Reviewing checkout flow'
	assert received[-1].session_id == 'session-z'


async def test_browser_set_intent_dispatches_via_public_tool() -> None:
	stream = HudStream.get()
	received: list[HudEvent] = []
	subscriber = received.append

	server = SimpleNamespace(browser_session=SimpleNamespace(id='session-z'))

	async def dispatch_set_intent(intent: str) -> str:
		return await _set_intent(server, intent)

	server._set_intent = dispatch_set_intent

	stream.subscribe(subscriber)
	try:
		result = await _execute_tool(server, 'browser_set_intent', {'intent': 'Reviewing checkout flow'})
	finally:
		stream.unsubscribe(subscriber)

	assert result == 'Intent updated: Reviewing checkout flow'
	assert received[-1].kind == 'intent'
	assert received[-1].label == 'Reviewing checkout flow'
	assert received[-1].session_id == 'session-z'


async def test_headed_demo_mode_hud_is_visible_and_shows_details(httpserver: HTTPServer) -> None:
	if not _headed_browser_supported():
		pytest.skip('No headed browser display is available in this environment.')

	httpserver.expect_request('/hud').respond_with_data(_HUD_VISIBILITY_HTML, content_type='text/html')
	url = httpserver.url_for('/hud').replace('localhost', '127.0.0.1')
	session = BrowserSession(browser_profile=BrowserProfile(headless=False, demo_mode=True))

	try:
		await session.start()
		await session.navigate_to(url)
		await session.demo_mode.ensure_ready()

		server = AgentycServer()
		server.browser_session = SimpleNamespace(id=session.id)
		server._publish_hud_event(
			'tool_done',
			'browser_get_state',
			{'mode': 'focus', 'focus_ref': 'e17'},
			duration=2.4,
		)
		publish_intent(session_id=session.id, intent='Reviewing HUD visibility')

		cdp_session = await session.get_or_create_cdp_session(focus=False)
		for _ in range(20):
			result = await session.cdp_client.send.Runtime.evaluate(
				params={
					'expression': f"""
					(() => {{
						const panel = document.getElementById('agentyc-hud-panel-{session.id}');
						const toggle = panel?.querySelector('[data-role="details-toggle"]');
						if (toggle && toggle.dataset.active !== 'true') {{
							toggle.click();
						}}
						const advanced = panel?.querySelector('.agentyc-hud-advanced');
						const current = panel?.querySelector('.agentyc-hud-current-text');
						const status = panel?.querySelector('.agentyc-hud-status');
						const dot = panel?.querySelector('.agentyc-hud-status-dot');
						return {{
							exists: Boolean(panel),
							visible: panel ? getComputedStyle(panel).display !== 'none' && parseFloat(getComputedStyle(panel).opacity || '1') > 0 : false,
							width: panel ? parseFloat(getComputedStyle(panel).width) : 0,
							current: current?.textContent || '',
							status: status?.textContent || '',
							detailsVisible: advanced ? getComputedStyle(advanced).display !== 'none' : false,
							advancedText: advanced?.textContent || '',
							dotColor: dot ? getComputedStyle(dot).backgroundColor : '',
						}};
					}})()
					""",
					'returnByValue': True,
				},
				session_id=cdp_session.session_id,
			)
			value = result['result']['value'] if isinstance(result, dict) else result.result.value
			if value.get('exists') and 'Reviewing HUD visibility' in value.get('current', ''):
				break
			await asyncio.sleep(0.1)
		else:
			raise AssertionError('HUD panel never became visible in headed mode.')

		assert value['visible'] is True
		assert value['width'] >= 340
		assert value['status']
		assert value['detailsVisible'] is True
		assert 'browser_get_state' in value['advancedText']
		assert 'mode=focus' in value['advancedText']
		assert session.id in value['advancedText']
		assert value['dotColor']
	finally:
		await session.stop()


def test_hud_overlay_start_fails_open_when_child_never_reports_ready(monkeypatch) -> None:
	class FakeQueue:
		def __init__(self, items: list[dict[str, str]] | None = None) -> None:
			self._items = list(items or [])

		def put(self, item: dict[str, str]) -> None:
			self._items.append(item)

		def get(self, timeout: float | None = None) -> dict[str, str]:
			if self._items:
				return self._items.pop(0)
			raise queue.Empty

	class FakeProcess:
		def __init__(self) -> None:
			self.started = False

		def start(self) -> None:
			self.started = True

		def is_alive(self) -> bool:
			return False

		def join(self, timeout: float | None = None) -> None:
			return None

		def terminate(self) -> None:
			return None

	class FakeContext:
		def __init__(self) -> None:
			self._queues = [
				FakeQueue(),
				FakeQueue([{'type': 'unavailable'}]),
			]
			self.process = FakeProcess()

		def Queue(self) -> FakeQueue:
			return self._queues.pop(0)

		def Process(self, **_: Any) -> FakeProcess:
			return self.process

	monkeypatch.setattr('agentyc.browser.hud_overlay.get_context', lambda _: FakeContext())
	overlay = HudOverlay()

	assert overlay.start() is False
	assert overlay._process is None
