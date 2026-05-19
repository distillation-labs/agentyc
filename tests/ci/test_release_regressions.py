from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from agentyc.browser import session_runtime
from agentyc.mcp.shared_browser_registry import reuse_local_browser_enabled
from agentyc.tools.javascript import validate_and_fix_javascript


class _AwaitableEvent:
	def __init__(self, on_await=None):
		self._on_await = on_await

	def __await__(self):
		async def _runner():
			if self._on_await is not None:
				self._on_await()
			return None

		return _runner().__await__()


def test_validate_and_fix_javascript_preserves_escaped_selector_quotes():
	code = (
		'(function(){ var el=document.querySelector("button[aria-label=\\"Publish comment\\"]"); '
		'if(!el) return null; return el.getAttribute("aria-label"); })()'
	)

	result = validate_and_fix_javascript(code)

	assert 'querySelector("button[aria-label=\\"Publish comment\\"]")' in result


def test_reuse_local_browser_disabled_by_default(monkeypatch):
	monkeypatch.delenv('AGENTYC_REUSE_LOCAL_BROWSER', raising=False)

	assert reuse_local_browser_enabled() is False


async def test_session_kill_preserves_local_watchdog_cleanup_after_stop_reset():
	local_watchdog = SimpleNamespace(
		_subprocess=object(),
		_owns_browser_resources=True,
		on_BrowserKillEvent=AsyncMock(),
	)
	session = SimpleNamespace(
		_intentional_stop=False,
		logger=SimpleNamespace(debug=lambda *_args, **_kwargs: None),
		_local_browser_watchdog=local_watchdog,
	)

	def dispatch(event):
		if type(event).__name__ == 'BrowserStopEvent':
			return _AwaitableEvent(lambda: setattr(session, '_local_browser_watchdog', None))
		return _AwaitableEvent()

	stop_mock = AsyncMock()
	session.event_bus = SimpleNamespace(dispatch=dispatch, stop=stop_mock)

	with patch('agentyc.browser.session_runtime.reset', new=AsyncMock()):
		with patch('agentyc.browser.session_runtime.EventBus', return_value=SimpleNamespace()):
			await session_runtime.kill(session)

	local_watchdog.on_BrowserKillEvent.assert_awaited_once()
	stop_mock.assert_awaited_once_with(clear=True, timeout=5)
