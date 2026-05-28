"""Special-case click helpers for the default action watchdog."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from agentyc.dom.service import EnhancedDOMTreeNode

if TYPE_CHECKING:
	from agentyc.browser.watchdogs.default_action_click_engine import DefaultActionClickEngineMixin


async def execute_click_with_download_detection(
	watchdog: DefaultActionClickEngineMixin,
	click_coro,
	download_complete_timeout: float = 30.0,
) -> dict | None:
	"""Execute a click operation and wait only for downloads already triggered by the click."""
	import time

	download_started = asyncio.Event()
	download_completed = asyncio.Event()
	download_info: dict = {}
	progress_info: dict = {'last_update': 0.0, 'received_bytes': 0, 'total_bytes': 0, 'state': ''}

	def on_download_start(info: dict) -> None:
		if info.get('auto_download'):
			return
		download_info['guid'] = info.get('guid', '')
		download_info['url'] = info.get('url', '')
		download_info['suggested_filename'] = info.get('suggested_filename', 'download')
		download_started.set()
		watchdog.logger.debug(f'[ClickWithDownload] Download started: {download_info["suggested_filename"]}')

	def on_download_progress(info: dict) -> None:
		if download_info.get('guid') and info.get('guid') != download_info['guid']:
			return
		progress_info['last_update'] = time.time()
		progress_info['received_bytes'] = info.get('received_bytes', 0)
		progress_info['total_bytes'] = info.get('total_bytes', 0)
		progress_info['state'] = info.get('state', '')
		watchdog.logger.debug(
			f'[ClickWithDownload] Progress: {progress_info["received_bytes"]}/{progress_info["total_bytes"]} bytes ({progress_info["state"]})'
		)

	def on_download_complete(info: dict) -> None:
		if info.get('auto_download'):
			return
		if download_info.get('guid') and info.get('guid') and info.get('guid') != download_info['guid']:
			return
		download_info['path'] = info.get('path', '')
		download_info['file_name'] = info.get('file_name', '')
		download_info['file_size'] = info.get('file_size', 0)
		download_info['file_type'] = info.get('file_type')
		download_info['mime_type'] = info.get('mime_type')
		download_completed.set()
		watchdog.logger.debug(f'[ClickWithDownload] Download completed: {download_info["file_name"]}')

	downloads_watchdog = watchdog.browser_session._downloads_watchdog
	watchdog.logger.debug(f'[ClickWithDownload] downloads_watchdog={downloads_watchdog is not None}')
	if downloads_watchdog:
		watchdog.logger.debug('[ClickWithDownload] Registering download callbacks...')
		downloads_watchdog.register_download_callbacks(
			on_start=on_download_start,
			on_progress=on_download_progress,
			on_complete=on_download_complete,
		)
	else:
		watchdog.logger.warning('[ClickWithDownload] No downloads_watchdog available!')

	try:
		click_metadata = await click_coro
		if isinstance(click_metadata, dict) and 'validation_error' in click_metadata:
			return click_metadata

		if download_started.is_set():
			watchdog.logger.info(f'📥 Download started: {download_info.get("suggested_filename", "unknown")}')

			try:
				await asyncio.wait_for(download_completed.wait(), timeout=download_complete_timeout)
				msg = (
					f'Downloaded file: {download_info["file_name"]} ({download_info["file_size"]} bytes) '
					f'saved to {download_info["path"]}'
				)
				watchdog.logger.info(f'💾 {msg}')

				if click_metadata is None:
					click_metadata = {}
				click_metadata['download'] = {
					'path': download_info['path'],
					'file_name': download_info['file_name'],
					'file_size': download_info['file_size'],
					'file_type': download_info.get('file_type'),
					'mime_type': download_info.get('mime_type'),
				}
			except TimeoutError:
				if click_metadata is None:
					click_metadata = {}

				filename = download_info.get('suggested_filename', 'unknown')
				received = progress_info.get('received_bytes', 0)
				total = progress_info.get('total_bytes', 0)
				state = progress_info.get('state', 'unknown')
				last_update = progress_info.get('last_update', 0.0)
				time_since_update = time.time() - last_update if last_update > 0 else float('inf')
				is_still_active = time_since_update < 5.0 and state == 'inProgress'

				if is_still_active:
					if total > 0:
						percent = (received / total) * 100
						progress_str = f'{percent:.1f}% ({received:,}/{total:,} bytes)'
					else:
						progress_str = f'{received:,} bytes downloaded (total size unknown)'

					msg = (
						f'Download timed out after {download_complete_timeout}s but is still in progress: '
						f'{filename} - {progress_str}. '
						f'The download appears to be progressing normally. Consider using the wait action '
						f'to allow more time for the download to complete.'
					)
					watchdog.logger.warning(f'⏱️ {msg}')
					click_metadata['download_in_progress'] = {
						'file_name': filename,
						'received_bytes': received,
						'total_bytes': total,
						'state': state,
						'message': msg,
					}
				else:
					if received > 0:
						msg = (
							f'Download timed out after {download_complete_timeout}s: {filename}. '
							f'Last progress: {received:,} bytes received. '
							f'The download may have stalled or completed - check the downloads folder.'
						)
					else:
						msg = (
							f'Download timed out after {download_complete_timeout}s: {filename}. '
							f'No progress data received - the download may have failed to start properly.'
						)
					watchdog.logger.warning(f'⏱️ {msg}')
					click_metadata['download_timeout'] = {
						'file_name': filename,
						'received_bytes': received,
						'total_bytes': total,
						'message': msg,
					}

		return click_metadata if isinstance(click_metadata, dict) else None
	finally:
		if downloads_watchdog:
			downloads_watchdog.unregister_download_callbacks(
				on_start=on_download_start,
				on_progress=on_download_progress,
				on_complete=on_download_complete,
			)


def is_print_related_element(element_node: EnhancedDOMTreeNode) -> bool:
	onclick = element_node.attributes.get('onclick', '').lower() if element_node.attributes else ''
	return bool(onclick and 'print' in onclick)


async def handle_print_button_click(watchdog: DefaultActionClickEngineMixin, element_node: EnhancedDOMTreeNode) -> dict | None:
	try:
		import base64
		import os
		import re
		from pathlib import Path

		import anyio

		from agentyc.browser.events import FileDownloadedEvent

		cdp_session = await watchdog.browser_session.get_or_create_cdp_session(focus=True)
		result = await asyncio.wait_for(
			cdp_session.cdp_client.send.Page.printToPDF(
				params={
					'printBackground': True,
					'preferCSSPageSize': True,
				},
				session_id=cdp_session.session_id,
			),
			timeout=15.0,
		)

		pdf_data = result.get('data')
		if not pdf_data:
			watchdog.logger.warning('⚠️ PDF generation returned no data')
			return None

		pdf_bytes = base64.b64decode(pdf_data)
		downloads_path = watchdog.browser_session.browser_profile.downloads_path
		if not downloads_path:
			watchdog.logger.warning('⚠️ No downloads path configured, cannot save PDF')
			return None

		try:
			page_title = await asyncio.wait_for(watchdog.browser_session.get_current_page_title(), timeout=2.0)
			safe_title = re.sub(r'[^\w\s-]', '', page_title)[:50]
			filename = f'{safe_title}.pdf' if safe_title else 'print.pdf'
		except Exception:
			filename = 'print.pdf'

		downloads_dir = Path(downloads_path).expanduser().resolve()
		downloads_dir.mkdir(parents=True, exist_ok=True)

		final_path = downloads_dir / filename
		if final_path.exists():
			base, ext = os.path.splitext(filename)
			counter = 1
			while (downloads_dir / f'{base} ({counter}){ext}').exists():
				counter += 1
			final_path = downloads_dir / f'{base} ({counter}){ext}'

		async with await anyio.open_file(final_path, 'wb') as file_handle:
			await file_handle.write(pdf_bytes)

		file_size = final_path.stat().st_size
		watchdog.logger.info(f'✅ Generated PDF via CDP: {final_path} ({file_size:,} bytes)')

		page_url = await watchdog.browser_session.get_current_page_url()
		watchdog.browser_session.event_bus.dispatch(
			FileDownloadedEvent(
				url=page_url,
				path=str(final_path),
				file_name=final_path.name,
				file_size=file_size,
				file_type='pdf',
				mime_type='application/pdf',
				auto_download=False,
			)
		)
		return {'pdf_generated': True, 'path': str(final_path)}
	except TimeoutError:
		watchdog.logger.warning('⏱️ PDF generation timed out')
		return None
	except Exception as error:
		watchdog.logger.warning(f'⚠️ Failed to generate PDF via CDP: {type(error).__name__}: {error}')
		return None
