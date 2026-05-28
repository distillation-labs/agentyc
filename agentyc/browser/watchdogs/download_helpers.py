"""Leaf helpers for download tracking."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agentyc.browser.events import FileDownloadedEvent


def _track_download(watchdog: Any, file_path: str, guid: str | None = None) -> None:
	try:
		path = Path(file_path)
		if path.exists():
			file_size = path.stat().st_size
			watchdog.logger.debug(f'[DownloadsWatchdog] Tracked download: {path.name} ({file_size} bytes)')
			file_ext = path.suffix.lower().lstrip('.')
			complete_info = {
				'guid': guid,
				'url': str(path),
				'path': str(path),
				'file_name': path.name,
				'file_size': file_size,
				'file_type': file_ext if file_ext else None,
				'auto_download': False,
			}
			for callback in watchdog._download_complete_callbacks:
				try:
					callback(complete_info)
				except Exception as error:
					watchdog.logger.debug(f'[DownloadsWatchdog] Error in download complete callback: {error}')

			watchdog.event_bus.dispatch(
				FileDownloadedEvent(
					guid=guid,
					url=str(path),
					path=str(path),
					file_name=path.name,
					file_size=file_size,
				)
			)
		else:
			watchdog.logger.warning(f'[DownloadsWatchdog] Downloaded file not found: {file_path}')
	except Exception as error:
		watchdog.logger.error(f'[DownloadsWatchdog] Error tracking download: {error}')
