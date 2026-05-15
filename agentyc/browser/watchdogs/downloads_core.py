"""Lifecycle and CDP download handling for the downloads watchdog."""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from cdp_use.cdp.browser import DownloadProgressEvent as CDPDownloadProgressEvent
from cdp_use.cdp.browser import DownloadWillBeginEvent
from cdp_use.cdp.target import SessionID, TargetID

from agentyc.browser.events import (
	BrowserLaunchEvent,
	BrowserStateRequestEvent,
	BrowserStoppedEvent,
	DownloadProgressEvent,
	DownloadStartedEvent,
	FileDownloadedEvent,
	NavigationCompleteEvent,
	TabClosedEvent,
	TabCreatedEvent,
)
from agentyc.utils import create_task_with_error_handling


class DownloadsCoreMixin:
	"""Core lifecycle methods and CDP download listener setup."""

	if TYPE_CHECKING:
		logger: Any
		event_bus: Any
		browser_session: Any
		_download_start_callbacks: list[Any]
		_download_progress_callbacks: list[Any]
		_download_complete_callbacks: list[Any]
		_initial_downloads_snapshot: set[str]
		_cdp_event_tasks: set[asyncio.Task]
		_download_cdp_session: Any
		_download_cdp_session_setup: bool
		_sessions_with_listeners: set[str]
		_active_downloads: dict[str, Any]
		_pdf_viewer_cache: dict[str, bool]
		_session_pdf_urls: dict[str, str]
		_network_monitored_targets: set[str]
		_detected_downloads: set[str]
		_network_callback_registered: bool
		_cdp_downloads_info: dict[str, dict[str, Any]]

		async def _setup_network_monitoring(self, target_id: TargetID) -> None: ...
		async def check_for_pdf_viewer(self, target_id: TargetID) -> bool: ...
		async def trigger_pdf_download(self, target_id: TargetID) -> str | None: ...
		async def _get_unique_filename(self, directory: str, filename: str) -> str: ...

	def register_download_callbacks(
		self,
		on_start: Any | None = None,
		on_progress: Any | None = None,
		on_complete: Any | None = None,
	) -> None:
		self.logger.debug(
			f'[DownloadsWatchdog] Registering callbacks: start={on_start is not None}, progress={on_progress is not None}, complete={on_complete is not None}'
		)
		if on_start:
			self._download_start_callbacks.append(on_start)
			self.logger.debug(
				f'[DownloadsWatchdog] Registered start callback, now have {len(self._download_start_callbacks)} start callbacks'
			)
		if on_progress:
			self._download_progress_callbacks.append(on_progress)
		if on_complete:
			self._download_complete_callbacks.append(on_complete)

	def unregister_download_callbacks(
		self,
		on_start: Any | None = None,
		on_progress: Any | None = None,
		on_complete: Any | None = None,
	) -> None:
		if on_start and on_start in self._download_start_callbacks:
			self._download_start_callbacks.remove(on_start)
		if on_progress and on_progress in self._download_progress_callbacks:
			self._download_progress_callbacks.remove(on_progress)
		if on_complete and on_complete in self._download_complete_callbacks:
			self._download_complete_callbacks.remove(on_complete)

	async def on_BrowserLaunchEvent(self, event: BrowserLaunchEvent) -> None:
		self.logger.debug(f'[DownloadsWatchdog] Received BrowserLaunchEvent, EventBus ID: {id(self.event_bus)}')
		downloads_path = self.browser_session.browser_profile.downloads_path
		if downloads_path:
			expanded_path = Path(downloads_path).expanduser().resolve()
			expanded_path.mkdir(parents=True, exist_ok=True)
			self.logger.debug(f'[DownloadsWatchdog] Ensured downloads directory exists: {expanded_path}')
			if expanded_path.exists():
				for file_path in expanded_path.iterdir():
					if file_path.is_file() and not file_path.name.startswith('.'):
						self._initial_downloads_snapshot.add(file_path.name)
				self.logger.debug(
					f'[DownloadsWatchdog] Captured initial downloads: {len(self._initial_downloads_snapshot)} files'
				)

	async def on_TabCreatedEvent(self, event: TabCreatedEvent) -> None:
		assert self.browser_session.browser_profile.downloads_path is not None, 'Downloads path must be configured'
		if event.target_id:
			await self.attach_to_target(event.target_id)
		else:
			self.logger.warning(f'[DownloadsWatchdog] No target found for tab {event.target_id}')

	async def on_TabClosedEvent(self, event: TabClosedEvent) -> None:
		pass

	async def on_BrowserStateRequestEvent(self, event: BrowserStateRequestEvent) -> None:
		self.logger.debug(f'[DownloadsWatchdog] on_BrowserStateRequestEvent started, event_id={event.event_id[-4:]}')
		try:
			cdp_session = await self.browser_session.get_or_create_cdp_session()
		except ValueError:
			self.logger.warning(f'[DownloadsWatchdog] No valid focus, skipping BrowserStateRequestEvent {event.event_id[-4:]}')
			return

		self.logger.debug(
			f'[DownloadsWatchdog] About to call get_current_page_url(), target_id={cdp_session.target_id[-4:] if cdp_session.target_id else "None"}'
		)
		url = await self.browser_session.get_current_page_url()
		self.logger.debug(f'[DownloadsWatchdog] Got URL: {url[:80] if url else "None"}')
		if not url:
			self.logger.warning(f'[DownloadsWatchdog] No URL found for BrowserStateRequestEvent {event.event_id[-4:]}')
			return

		target_id = cdp_session.target_id
		self.logger.debug(f'[DownloadsWatchdog] About to dispatch NavigationCompleteEvent for target {target_id[-4:]}')
		self.event_bus.dispatch(
			NavigationCompleteEvent(
				event_type='NavigationCompleteEvent',
				url=url,
				target_id=target_id,
				event_parent_id=event.event_id,
			)
		)
		self.logger.debug('[DownloadsWatchdog] Successfully completed BrowserStateRequestEvent')

	async def on_BrowserStoppedEvent(self, event: BrowserStoppedEvent) -> None:
		for task in list(self._cdp_event_tasks):
			if not task.done():
				task.cancel()
		if self._cdp_event_tasks:
			await asyncio.gather(*self._cdp_event_tasks, return_exceptions=True)
		self._cdp_event_tasks.clear()
		self._download_cdp_session = None
		self._download_cdp_session_setup = False
		self._sessions_with_listeners.clear()
		self._active_downloads.clear()
		self._pdf_viewer_cache.clear()
		self._session_pdf_urls.clear()
		self._network_monitored_targets.clear()
		self._detected_downloads.clear()
		self._initial_downloads_snapshot.clear()
		self._network_callback_registered = False

	async def on_NavigationCompleteEvent(self, event: NavigationCompleteEvent) -> None:
		self.logger.debug(f'[DownloadsWatchdog] NavigationCompleteEvent received for {event.url}, tab #{event.target_id[-4:]}')
		if event.url in self._pdf_viewer_cache:
			del self._pdf_viewer_cache[event.url]
		if not self._is_auto_download_enabled():
			return
		target_id = event.target_id
		self.logger.debug(f'[DownloadsWatchdog] Got target_id={target_id} for tab #{event.target_id[-4:]}')
		is_pdf = await self.check_for_pdf_viewer(target_id)
		if is_pdf:
			self.logger.debug(f'[DownloadsWatchdog] 📄 PDF detected at {event.url}, triggering auto-download...')
			download_path = await self.trigger_pdf_download(target_id)
			if not download_path:
				self.logger.warning(f'[DownloadsWatchdog] ⚠️ PDF download failed for {event.url}')

	def _is_auto_download_enabled(self) -> bool:
		return self.browser_session.browser_profile.auto_download_pdfs

	async def attach_to_target(self, target_id: TargetID) -> None:
		def download_will_begin_handler(event: DownloadWillBeginEvent, session_id: SessionID | None) -> None:
			self.logger.debug(f'[DownloadsWatchdog] Download will begin: {event}')
			guid = event.get('guid', '')
			url = event.get('url', '')
			suggested_filename = event.get('suggestedFilename', 'download')
			try:
				assert suggested_filename, 'CDP DownloadWillBegin missing suggestedFilename'
				self._cdp_downloads_info[guid] = {
					'url': url,
					'suggested_filename': suggested_filename,
					'handled': False,
				}
			except (AssertionError, KeyError):
				pass

			download_info = {
				'guid': guid,
				'url': url,
				'suggested_filename': suggested_filename,
				'auto_download': False,
			}
			self.logger.debug(f'[DownloadsWatchdog] Calling {len(self._download_start_callbacks)} start callbacks')
			for callback in self._download_start_callbacks:
				try:
					self.logger.debug(f'[DownloadsWatchdog] Calling start callback: {callback}')
					callback(download_info)
				except Exception as error:
					self.logger.debug(f'[DownloadsWatchdog] Error in download start callback: {error}')

			self.event_bus.dispatch(
				DownloadStartedEvent(
					guid=guid,
					url=url,
					suggested_filename=suggested_filename,
					auto_download=False,
				)
			)
			task = create_task_with_error_handling(
				self._handle_cdp_download(event, target_id, session_id),
				name='handle_cdp_download',
				logger_instance=self.logger,
				suppress_exceptions=True,
			)
			self._cdp_event_tasks.add(task)
			task.add_done_callback(lambda completed: self._cdp_event_tasks.discard(completed))

		def download_progress_handler(event: CDPDownloadProgressEvent, session_id: SessionID | None) -> None:
			guid = event.get('guid', '')
			state = event.get('state', '')
			received_bytes = int(event.get('receivedBytes', 0))
			total_bytes = int(event.get('totalBytes', 0))
			progress_info = {
				'guid': guid,
				'received_bytes': received_bytes,
				'total_bytes': total_bytes,
				'state': state,
			}
			for callback in self._download_progress_callbacks:
				try:
					callback(progress_info)
				except Exception as error:
					self.logger.debug(f'[DownloadsWatchdog] Error in download progress callback: {error}')

			self.event_bus.dispatch(
				DownloadProgressEvent(
					guid=guid,
					received_bytes=received_bytes,
					total_bytes=total_bytes,
					state=state,
				)
			)

			if state == 'completed':
				file_path = event.get('filePath')
				if self.browser_session.is_local:
					if file_path:
						self.logger.debug(f'[DownloadsWatchdog] Download completed: {file_path}')
						self._track_download(file_path, guid=guid)
						try:
							if guid in self._cdp_downloads_info:
								self._cdp_downloads_info[guid]['handled'] = True
						except (KeyError, AttributeError):
							pass
					else:
						self.logger.debug('[DownloadsWatchdog] No filePath in progress event; detecting via filesystem')
						downloads_path = self.browser_session.browser_profile.downloads_path
						if downloads_path:
							downloads_dir = Path(downloads_path).expanduser().resolve()
							if downloads_dir.exists():
								for file_path_obj in downloads_dir.iterdir():
									if (
										file_path_obj.is_file()
										and not file_path_obj.name.startswith('.')
										and file_path_obj.name not in self._initial_downloads_snapshot
									):
										if file_path_obj.stat().st_size > 4:
											self._initial_downloads_snapshot.add(file_path_obj.name)
											self.logger.debug(f'[DownloadsWatchdog] Detected new download: {file_path_obj.name}')
											self._track_download(str(file_path_obj))
											try:
												if guid in self._cdp_downloads_info:
													self._cdp_downloads_info[guid]['handled'] = True
											except (KeyError, AttributeError):
												pass
											break
				else:
					info = self._cdp_downloads_info.get(guid, {})
					try:
						suggested_filename = info.get('suggested_filename') or (Path(file_path).name if file_path else 'download')
						downloads_path = str(self.browser_session.browser_profile.downloads_path or '')
						effective_path = file_path or str(Path(downloads_path) / suggested_filename)
						file_name = Path(effective_path).name
						file_ext = Path(file_name).suffix.lower().lstrip('.')
						self.event_bus.dispatch(
							FileDownloadedEvent(
								guid=guid,
								url=info.get('url', ''),
								path=str(effective_path),
								file_name=file_name,
								file_size=0,
								file_type=file_ext if file_ext else None,
							)
						)
						self.logger.debug(f'[DownloadsWatchdog] ✅ (remote) Download completed: {effective_path}')
					finally:
						if guid in self._cdp_downloads_info:
							del self._cdp_downloads_info[guid]

		try:
			downloads_path_raw = self.browser_session.browser_profile.downloads_path
			if not downloads_path_raw:
				return
			if self._download_cdp_session_setup:
				self.logger.debug('[DownloadsWatchdog] Download listener already set up for browser session')
				return

			if not self._download_cdp_session_setup:
				cdp_client = self.browser_session.cdp_client
				downloads_path = self.browser_session.browser_profile.downloads_path
				if not downloads_path:
					self.logger.warning('[DownloadsWatchdog] No downloads path configured, skipping CDP download setup')
					return
				expanded_downloads_path = Path(downloads_path).expanduser().resolve()
				await cdp_client.send.Browser.setDownloadBehavior(
					params={
						'behavior': 'allow',
						'downloadPath': str(expanded_downloads_path),
						'eventsEnabled': True,
					}
				)
				cdp_client.register.Browser.downloadWillBegin(download_will_begin_handler)  # type: ignore[arg-type]
				cdp_client.register.Browser.downloadProgress(download_progress_handler)  # type: ignore[arg-type]
				self._download_cdp_session_setup = True
				self.logger.debug('[DownloadsWatchdog] Set up CDP download listeners')
		except Exception as error:
			self.logger.warning(f'[DownloadsWatchdog] Failed to set up CDP download listener for target {target_id}: {error}')

		await self._setup_network_monitoring(target_id)

	def _track_download(self, file_path: str, guid: str | None = None) -> None:
		try:
			path = Path(file_path)
			if path.exists():
				file_size = path.stat().st_size
				self.logger.debug(f'[DownloadsWatchdog] Tracked download: {path.name} ({file_size} bytes)')
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
				for callback in self._download_complete_callbacks:
					try:
						callback(complete_info)
					except Exception as error:
						self.logger.debug(f'[DownloadsWatchdog] Error in download complete callback: {error}')

				self.event_bus.dispatch(
					FileDownloadedEvent(
						guid=guid,
						url=str(path),
						path=str(path),
						file_name=path.name,
						file_size=file_size,
					)
				)
			else:
				self.logger.warning(f'[DownloadsWatchdog] Downloaded file not found: {file_path}')
		except Exception as error:
			self.logger.error(f'[DownloadsWatchdog] Error tracking download: {error}')

	async def _handle_cdp_download(
		self, event: DownloadWillBeginEvent, target_id: TargetID, session_id: SessionID | None
	) -> None:
		downloads_dir = (
			Path(
				self.browser_session.browser_profile.downloads_path
				or f'{tempfile.gettempdir()}/agentyc_downloads.{str(self.browser_session.id)[-4:]}'
			)
			.expanduser()
			.resolve()
		)
		file_size = 0
		download_url = event.get('url', '')
		suggested_filename = event.get('suggestedFilename', 'download')
		guid = event.get('guid', '')

		try:
			self.logger.debug(f'[DownloadsWatchdog] ⬇️ File download starting: {suggested_filename} from {download_url[:100]}...')
			self.logger.debug(f'[DownloadsWatchdog] Full CDP event: {event}')
			if not self.browser_session.is_local:
				return
		except Exception as error:
			self.logger.error(f'[DownloadsWatchdog] ❌ Error handling CDP download: {type(error).__name__} {error}')

		self.logger.debug(f'[DownloadsWatchdog] Checking if browser auto-download saved the file for us: {suggested_filename}')
		max_wait = 20
		start_time = asyncio.get_event_loop().time()
		while asyncio.get_event_loop().time() - start_time < max_wait:  # noqa: ASYNC110
			await asyncio.sleep(5.0)
			if Path(downloads_dir).exists():
				for file_path in Path(downloads_dir).iterdir():
					if (
						file_path.is_file()
						and not file_path.name.startswith('.')
						and file_path.name not in self._initial_downloads_snapshot
					):
						self._initial_downloads_snapshot.add(file_path.name)
						try:
							file_size = file_path.stat().st_size
							if file_size > 4:
								self.logger.debug(
									f'[DownloadsWatchdog] ✅ Found downloaded file: {file_path} ({file_size} bytes)'
								)
								file_ext = file_path.suffix.lower().lstrip('.')
								file_type = file_ext if file_ext else None
								info = self._cdp_downloads_info.get(guid, {})
								if info.get('handled'):
									return
								self.event_bus.dispatch(
									FileDownloadedEvent(
										guid=guid,
										url=download_url,
										path=str(file_path),
										file_name=file_path.name,
										file_size=file_size,
										file_type=file_type,
									)
								)
							try:
								if guid in self._cdp_downloads_info:
									self._cdp_downloads_info[guid]['handled'] = True
							except (KeyError, AttributeError):
								pass
							return
						except Exception as error:
							self.logger.debug(f'[DownloadsWatchdog] Error checking file {file_path}: {error}')

		self.logger.warning(f'[DownloadsWatchdog] Download did not complete within {max_wait} seconds')

	async def _handle_download(self, download: Any) -> None:
		download_id = f'{id(download)}'
		self._active_downloads[download_id] = download
		self.logger.debug(f'[DownloadsWatchdog] ⬇️ Handling download: {download.suggested_filename} from {download.url[:100]}...')
		failure = await download.failure()
		self.logger.warning(f'[DownloadsWatchdog] ❌ Download state - canceled: {failure}, url: {download.url}')

		try:
			current_step = 'getting_download_info'
			url = download.url
			suggested_filename = download.suggested_filename
			current_step = 'determining_download_directory'
			downloads_dir = self.browser_session.browser_profile.downloads_path
			if not downloads_dir:
				downloads_dir = str(Path.home() / 'Downloads')
			else:
				downloads_dir = str(downloads_dir)

			original_path = Path(downloads_dir) / suggested_filename
			if original_path.exists() and original_path.stat().st_size > 0:
				self.logger.debug(
					f'[DownloadsWatchdog] File already downloaded by Playwright: {original_path} ({original_path.stat().st_size} bytes)'
				)
				download_path = original_path
				file_size = original_path.stat().st_size
			else:
				current_step = 'generating_unique_filename'
				unique_filename = await self._get_unique_filename(downloads_dir, suggested_filename)
				download_path = Path(downloads_dir) / unique_filename
				self.logger.debug(f'[DownloadsWatchdog] Download started: {unique_filename} from {url[:100]}...')
				current_step = 'calling_save_as'
				self.logger.debug(f'[DownloadsWatchdog] Saving download to: {download_path}')
				self.logger.debug(f'[DownloadsWatchdog] Download path exists: {download_path.parent.exists()}')
				self.logger.debug(f'[DownloadsWatchdog] Download path writable: {os.access(download_path.parent, os.W_OK)}')
				try:
					self.logger.debug('[DownloadsWatchdog] About to call download.save_as()...')
					await download.save_as(str(download_path))
					self.logger.debug(f'[DownloadsWatchdog] Successfully saved download to: {download_path}')
					current_step = 'save_as_completed'
				except Exception as save_error:
					self.logger.error(f'[DownloadsWatchdog] save_as() failed with error: {save_error}')
					raise save_error
				file_size = download_path.stat().st_size if download_path.exists() else 0

			file_ext = download_path.suffix.lower().lstrip('.')
			file_type = file_ext if file_ext else None
			mime_type = None
			auto_download = file_type == 'pdf' and self._is_auto_download_enabled()
			self.event_bus.dispatch(
				FileDownloadedEvent(
					url=url,
					path=str(download_path),
					file_name=suggested_filename,
					file_size=file_size,
					file_type=file_type,
					mime_type=mime_type,
					from_cache=False,
					auto_download=auto_download,
				)
			)
			self.logger.debug(
				f'[DownloadsWatchdog] ✅ Download completed: {suggested_filename} ({file_size} bytes) saved to {download_path}'
			)
		except Exception as error:
			self.logger.error(
				f'[DownloadsWatchdog] Error handling download at step "{locals().get("current_step", "unknown")}", error: {error}'
			)
			self.logger.error(
				f'[DownloadsWatchdog] Download state - URL: {download.url}, filename: {download.suggested_filename}'
			)
		finally:
			if download_id in self._active_downloads:
				del self._active_downloads[download_id]
