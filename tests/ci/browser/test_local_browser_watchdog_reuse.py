from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from agentyc.browser.watchdogs.local_browser_watchdog import LocalBrowserWatchdog


async def test_browser_stop_does_not_kill_shared_keep_alive_browser():
	watchdog = LocalBrowserWatchdog.model_construct(
		event_bus=SimpleNamespace(dispatch=lambda _event: None),
		browser_session=SimpleNamespace(
			is_local=True,
			browser_profile=SimpleNamespace(keep_alive=True, cdp_url='http://127.0.0.1:9222/'),
			logger=SimpleNamespace(debug=lambda *_args, **_kwargs: None),
		),
	)
	watchdog._subprocess = SimpleNamespace(pid=12345)
	watchdog._owns_browser_resources = False

	with patch.object(watchdog.event_bus, 'dispatch') as dispatch:
		await watchdog.on_BrowserStopEvent(SimpleNamespace())

	dispatch.assert_not_called()


async def test_browser_kill_skips_process_cleanup_when_shared_browser_is_external():
	watchdog = LocalBrowserWatchdog.model_construct(
		event_bus=SimpleNamespace(dispatch=lambda _event: None),
		browser_session=SimpleNamespace(
			is_local=True,
			browser_profile=SimpleNamespace(keep_alive=True, cdp_url='http://127.0.0.1:9222/'),
			logger=SimpleNamespace(debug=lambda *_args, **_kwargs: None),
		),
	)
	watchdog._subprocess = SimpleNamespace(pid=12345)
	watchdog._owns_browser_resources = False
	watchdog._cleanup_process = AsyncMock()

	with patch('agentyc.browser.watchdogs.local_browser_watchdog.clear_registered_local_shared_browser') as clear_registry:
		await watchdog.on_BrowserKillEvent(SimpleNamespace())

	watchdog._cleanup_process.assert_not_called()
	clear_registry.assert_not_called()
	assert watchdog._subprocess is None
