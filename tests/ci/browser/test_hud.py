import queue
from types import SimpleNamespace
from typing import Any, cast

from agentyc.browser.demo_mode import DemoMode
from agentyc.browser.feedback import BUG_REPORT_URL, FEATURE_REQUEST_URL, SECURITY_POLICY_URL, build_feedback_config
from agentyc.browser.hud_overlay import HudOverlay
from agentyc.browser.hud_stream import HudEvent, HudStream
from agentyc.mcp.intent_tools import _set_intent
from agentyc.mcp.tool_feedback import _should_publish_hud_event


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
	assert _should_publish_hud_event('tool_done', 'browser_get_state', 900.0, None)
	assert _should_publish_hud_event('tool_error', 'browser_get_state', 100.0, 'timed out')


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
