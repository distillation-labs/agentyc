import asyncio
from types import SimpleNamespace

from agentyc.browser.watchdogs.default_action_click_engine import DefaultActionClickEngineMixin


class _Logger:
	def debug(self, *_args, **_kwargs):
		pass

	def info(self, *_args, **_kwargs):
		pass

	def warning(self, *_args, **_kwargs):
		pass


class _DownloadsWatchdog:
	def __init__(self):
		self._on_start = None
		self._on_progress = None
		self._on_complete = None

	def register_download_callbacks(self, on_start=None, on_progress=None, on_complete=None):
		self._on_start = on_start
		self._on_progress = on_progress
		self._on_complete = on_complete

	def unregister_download_callbacks(self, on_start=None, on_progress=None, on_complete=None):
		if self._on_start == on_start:
			self._on_start = None
		if self._on_progress == on_progress:
			self._on_progress = None
		if self._on_complete == on_complete:
			self._on_complete = None

	def fire_start(self, info):
		assert self._on_start is not None
		self._on_start(info)

	def fire_complete(self, info):
		assert self._on_complete is not None
		self._on_complete(info)


class _ClickHarness(DefaultActionClickEngineMixin):
	def __init__(self, downloads_watchdog):
		self.logger = _Logger()
		self.browser_session = SimpleNamespace(_downloads_watchdog=downloads_watchdog)


async def test_execute_click_with_download_detection_keeps_click_started_downloads():
	downloads_watchdog = _DownloadsWatchdog()
	harness = _ClickHarness(downloads_watchdog)

	async def click_coro():
		downloads_watchdog.fire_start(
			{
				'guid': 'download-1',
				'url': 'https://example.test/report.csv',
				'suggested_filename': 'report.csv',
				'auto_download': False,
			}
		)
		await asyncio.sleep(0)
		downloads_watchdog.fire_complete(
			{
				'guid': 'download-1',
				'path': '/tmp/report.csv',
				'file_name': 'report.csv',
				'file_size': 128,
				'file_type': 'csv',
				'mime_type': 'text/csv',
				'auto_download': False,
			}
		)
		return {'click_x': 10, 'click_y': 20}

	result = await harness._execute_click_with_download_detection(click_coro())

	assert result == {
		'click_x': 10,
		'click_y': 20,
		'download': {
			'path': '/tmp/report.csv',
			'file_name': 'report.csv',
			'file_size': 128,
			'file_type': 'csv',
			'mime_type': 'text/csv',
		},
	}
	assert downloads_watchdog._on_start is None
	assert downloads_watchdog._on_complete is None
