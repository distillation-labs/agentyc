"""Network and fetch-based helpers for the downloads watchdog."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import anyio
from cdp_use.cdp.network import ResponseReceivedEvent
from cdp_use.cdp.target import TargetID

from agentyc.browser.events import FileDownloadedEvent
from agentyc.browser.watchdogs.downloads_helpers import check_url_for_pdf, get_unique_filename, is_chrome_pdf_viewer_url
from agentyc.utils import create_task_with_error_handling


class DownloadsNetworkMixin:
	"""Network-driven download detection and fetch helpers."""

	if TYPE_CHECKING:
		logger: Any
		event_bus: Any
		browser_session: Any
		_network_monitored_targets: set[str]
		_network_callback_registered: bool
		_session_pdf_urls: dict[str, str]
		_detected_downloads: set[str]
		_cdp_event_tasks: set[asyncio.Task]
		_pdf_viewer_cache: dict[str, bool]

		def _is_auto_download_enabled(self) -> bool: ...

	async def _setup_network_monitoring(self, target_id: TargetID) -> None:
		if target_id in self._network_monitored_targets:
			self.logger.debug(f'[DownloadsWatchdog] Network monitoring already enabled for target {target_id[-4:]}')
			return
		if not self._is_auto_download_enabled():
			self.logger.debug('[DownloadsWatchdog] Auto-download disabled, skipping network monitoring')
			return

		try:
			cdp_client = self.browser_session.cdp_client
			if not self._network_callback_registered:

				def on_response_received(event: ResponseReceivedEvent, session_id: str | None) -> None:
					try:
						if not self.browser_session.session_manager:
							self.logger.warning('[DownloadsWatchdog] Session manager not found, skipping network monitoring')
							return
						event_target_id = self.browser_session.session_manager.get_target_id_from_session_id(session_id)
						if not event_target_id:
							return
						if event_target_id not in self._network_monitored_targets:
							return

						response = event.get('response', {})
						url = response.get('url', '')
						content_type = response.get('mimeType', '').lower()
						headers = {key.lower(): value for key, value in response.get('headers', {}).items()}
						request_type = event.get('type', '')
						if not url.startswith('http'):
							return
						if request_type in ('Fetch', 'XHR'):
							return

						is_pdf = 'application/pdf' in content_type
						content_disposition = str(headers.get('content-disposition', '')).lower()
						is_download_attachment = 'attachment' in content_disposition
						unwanted_content_types = [
							'image/',
							'video/',
							'audio/',
							'text/css',
							'text/javascript',
							'application/javascript',
							'application/x-javascript',
							'text/html',
							'application/json',
							'font/',
							'application/font',
							'application/x-font',
						]
						if any(content_type.startswith(prefix) for prefix in unwanted_content_types):
							return

						url_lower = url.lower().split('?')[0]
						unwanted_extensions = [
							'.jpg',
							'.jpeg',
							'.png',
							'.gif',
							'.webp',
							'.svg',
							'.ico',
							'.css',
							'.js',
							'.woff',
							'.woff2',
							'.ttf',
							'.eot',
							'.mp4',
							'.webm',
							'.mp3',
							'.wav',
							'.ogg',
						]
						if any(url_lower.endswith(ext) for ext in unwanted_extensions):
							return
						if not (is_pdf or is_download_attachment):
							return

						existing_path = self._session_pdf_urls.get(url)
						if existing_path:
							if os.path.exists(existing_path):
								return
							del self._session_pdf_urls[url]
						if url in self._detected_downloads:
							self.logger.debug(f'[DownloadsWatchdog] Already detected download: {url[:80]}...')
							return
						self._detected_downloads.add(url)

						suggested_filename = None
						if 'filename=' in content_disposition:
							import re

							filename_match = re.search(r'filename[^;=\n]*=(([\'"]).*?\2|[^;\n]*)', content_disposition)
							if filename_match:
								suggested_filename = filename_match.group(1).strip('\'"')

						self.logger.info(f'[DownloadsWatchdog] 🔍 Detected downloadable content via network: {url[:80]}...')
						self.logger.debug(
							f'[DownloadsWatchdog]   Content-Type: {content_type}, Is PDF: {is_pdf}, Is Attachment: {is_download_attachment}'
						)

						async def download_in_background():
							try:
								download_path = await self.download_file_from_url(
									url=url,
									target_id=event_target_id,
									content_type=content_type,
									suggested_filename=suggested_filename,
								)
								if download_path:
									self.logger.info(f'[DownloadsWatchdog] ✅ Successfully downloaded: {download_path}')
								else:
									self.logger.warning(f'[DownloadsWatchdog] ⚠️  Failed to download: {url[:80]}...')
							except Exception as error:
								self.logger.error(
									f'[DownloadsWatchdog] Error downloading in background: {type(error).__name__}: {error}'
								)
							finally:
								self._detected_downloads.discard(url)

						task = create_task_with_error_handling(
							download_in_background(),
							name='download_in_background',
							logger_instance=self.logger,
							suppress_exceptions=True,
						)
						self._cdp_event_tasks.add(task)
						task.add_done_callback(lambda completed: self._cdp_event_tasks.discard(completed))
					except Exception as error:
						self.logger.error(
							f'[DownloadsWatchdog] Error in network response handler: {type(error).__name__}: {error}'
						)

				cdp_client.register.Network.responseReceived(on_response_received)
				self._network_callback_registered = True
				self.logger.debug('[DownloadsWatchdog] ✅ Registered global network response callback')

			cdp_session = await self.browser_session.get_or_create_cdp_session(target_id, focus=False)
			await cdp_client.send.Network.enable(session_id=cdp_session.session_id)
			self.logger.debug(f'[DownloadsWatchdog] Enabled Network domain for target {target_id[-4:]}')
			self._network_monitored_targets.add(target_id)
			self.logger.debug(f'[DownloadsWatchdog] ✅ Network monitoring enabled for target {target_id[-4:]}')
		except Exception as error:
			self.logger.warning(f'[DownloadsWatchdog] Failed to set up network monitoring for target {target_id}: {error}')

	async def download_file_from_url(
		self,
		url: str,
		target_id: TargetID,
		content_type: str | None = None,
		suggested_filename: str | None = None,
	) -> str | None:
		if not self.browser_session.browser_profile.downloads_path:
			self.logger.warning('[DownloadsWatchdog] No downloads path configured')
			return None
		if url in self._session_pdf_urls:
			existing_path = self._session_pdf_urls[url]
			if os.path.exists(existing_path):
				self.logger.debug(f'[DownloadsWatchdog] File already downloaded in session: {existing_path}')
				return existing_path
			self.logger.debug(f'[DownloadsWatchdog] Cached download path no longer exists, re-downloading: {existing_path}')
			del self._session_pdf_urls[url]

		try:
			temp_session = await self.browser_session.get_or_create_cdp_session(target_id, focus=False)
			if suggested_filename:
				filename = suggested_filename
			else:
				filename = os.path.basename(url.split('?')[0])
				if not filename or '.' not in filename:
					filename = 'document.pdf' if content_type and 'pdf' in content_type else 'download'

			downloads_dir = str(self.browser_session.browser_profile.downloads_path)
			os.makedirs(downloads_dir, exist_ok=True)
			final_filename = filename
			existing_files = os.listdir(downloads_dir)
			if filename in existing_files:
				base, ext = os.path.splitext(filename)
				counter = 1
				while f'{base} ({counter}){ext}' in existing_files:
					counter += 1
				final_filename = f'{base} ({counter}){ext}'
				self.logger.debug(f'[DownloadsWatchdog] File exists, using: {final_filename}')

			self.logger.debug(f'[DownloadsWatchdog] Downloading from: {url[:100]}...')
			escaped_url = json.dumps(url)
			result = await asyncio.wait_for(
				temp_session.cdp_client.send.Runtime.evaluate(
					params={
						'expression': f"""
                (async () => {{
                    try {{
                        const response = await fetch({escaped_url}, {{
                            cache: 'force-cache'
                        }});
                        if (!response.ok) {{
                            throw new Error(`HTTP error! status: ${{response.status}}`);
                        }}
                        const blob = await response.blob();
                        const arrayBuffer = await blob.arrayBuffer();
                        const uint8Array = new Uint8Array(arrayBuffer);
                        return {{
                            data: Array.from(uint8Array),
                            responseSize: uint8Array.length
                        }};
                    }} catch (error) {{
                        throw new Error(`Fetch failed: ${{error.message}}`);
                    }}
                }})()
                """,
						'awaitPromise': True,
						'returnByValue': True,
					},
					session_id=temp_session.session_id,
				),
				timeout=15.0,
			)
			download_result = result.get('result', {}).get('value', {})
			if download_result and download_result.get('data') and len(download_result['data']) > 0:
				download_path = os.path.join(downloads_dir, final_filename)
				async with await anyio.open_file(download_path, 'wb') as file_handle:
					await file_handle.write(bytes(download_result['data']))
				if os.path.exists(download_path):
					actual_size = os.path.getsize(download_path)
					self.logger.debug(f'[DownloadsWatchdog] File written: {download_path} ({actual_size} bytes)')
					file_ext = Path(final_filename).suffix.lower().lstrip('.')
					mime_type = content_type or f'application/{file_ext}'
					self._session_pdf_urls[url] = download_path
					self.logger.debug(f'[DownloadsWatchdog] Dispatching FileDownloadedEvent for {final_filename}')
					self.event_bus.dispatch(
						FileDownloadedEvent(
							url=url,
							path=download_path,
							file_name=final_filename,
							file_size=actual_size,
							file_type=file_ext if file_ext else None,
							mime_type=mime_type,
							auto_download=True,
						)
					)
					return download_path
				self.logger.error(f'[DownloadsWatchdog] Failed to write file: {download_path}')
				return None
			self.logger.warning(f'[DownloadsWatchdog] No data received when downloading from {url}')
			return None
		except TimeoutError:
			self.logger.warning(f'[DownloadsWatchdog] Download timed out: {url[:80]}...')
			return None
		except Exception as error:
			self.logger.warning(f'[DownloadsWatchdog] Download failed: {type(error).__name__}: {error}')
			return None

	async def check_for_pdf_viewer(self, target_id: TargetID) -> bool:
		self.logger.debug(f'[DownloadsWatchdog] Checking if target {target_id} is PDF viewer...')
		try:
			await self.browser_session.get_or_create_cdp_session(target_id, focus=False)
		except ValueError as error:
			self.logger.warning(f'[DownloadsWatchdog] No session found for {target_id}: {error}')
			return False

		target = self.browser_session.session_manager.get_target(target_id)
		if not target:
			self.logger.warning(f'[DownloadsWatchdog] No target found for {target_id}')
			return False
		page_url = target.url
		if page_url in self._pdf_viewer_cache:
			cached_result = self._pdf_viewer_cache[page_url]
			self.logger.debug(f'[DownloadsWatchdog] Using cached PDF check result for {page_url}: {cached_result}')
			return cached_result

		try:
			url_is_pdf = self._check_url_for_pdf(page_url)
			if url_is_pdf:
				self.logger.debug(f'[DownloadsWatchdog] PDF detected via URL pattern: {page_url}')
				self._pdf_viewer_cache[page_url] = True
				return True
			chrome_pdf_viewer = self._is_chrome_pdf_viewer_url(page_url)
			if chrome_pdf_viewer:
				self.logger.debug(f'[DownloadsWatchdog] Chrome PDF viewer detected: {page_url}')
				self._pdf_viewer_cache[page_url] = True
				return True
			self._pdf_viewer_cache[page_url] = False
			return False
		except Exception as error:
			self.logger.warning(f'[DownloadsWatchdog] ❌ Error checking for PDF viewer: {error}')
			self._pdf_viewer_cache[page_url] = False
			return False

	def _check_url_for_pdf(self, url: str) -> bool:
		return check_url_for_pdf(url)

	def _is_chrome_pdf_viewer_url(self, url: str) -> bool:
		return is_chrome_pdf_viewer_url(url)

	async def _check_network_headers_for_pdf(self, target_id: TargetID) -> bool:
		try:
			temp_session = await self.browser_session.get_or_create_cdp_session(target_id, focus=False)
			history = await asyncio.wait_for(
				temp_session.cdp_client.send.Page.getNavigationHistory(session_id=temp_session.session_id), timeout=3.0
			)
			current_entry = history.get('entries', [])
			if current_entry:
				current_index = history.get('currentIndex', 0)
				if 0 <= current_index < len(current_entry):
					current_url = current_entry[current_index].get('url', '')
					if self._check_url_for_pdf(current_url):
						return True
			return False
		except Exception as error:
			self.logger.debug(f'[DownloadsWatchdog] Network headers check failed (non-critical): {error}')
			return False

	async def trigger_pdf_download(self, target_id: TargetID) -> str | None:
		self.logger.debug(f'[DownloadsWatchdog] trigger_pdf_download called for target_id={target_id}')
		if not self.browser_session.browser_profile.downloads_path:
			self.logger.warning('[DownloadsWatchdog] ❌ No downloads path configured, cannot save PDF download')
			return None
		downloads_path = self.browser_session.browser_profile.downloads_path
		self.logger.debug(f'[DownloadsWatchdog] Downloads path: {downloads_path}')

		try:
			self.logger.debug(f'[DownloadsWatchdog] Creating CDP session for PDF download from target {target_id}')
			temp_session = await self.browser_session.get_or_create_cdp_session(target_id, focus=False)
			result = await asyncio.wait_for(
				temp_session.cdp_client.send.Runtime.evaluate(
					params={
						'expression': """
                (() => {
                    const embedElement = document.querySelector('embed[type="application/x-google-chrome-pdf"]') ||
                                        document.querySelector('embed[type="application/pdf"]');
                    if (embedElement) {
                        return { url: window.location.href };
                    }
                    return { url: window.location.href };
                })()
                """,
						'returnByValue': True,
					},
					session_id=temp_session.session_id,
				),
				timeout=5.0,
			)
			pdf_info = result.get('result', {}).get('value', {})
			pdf_url = pdf_info.get('url', '')
			if not pdf_url:
				self.logger.warning(f'[DownloadsWatchdog] ❌ Could not determine PDF URL for download {pdf_info}')
				return None

			pdf_filename = os.path.basename(pdf_url.split('?')[0])
			if not pdf_filename or not pdf_filename.endswith('.pdf'):
				parsed = urlparse(pdf_url)
				pdf_filename = os.path.basename(parsed.path) or 'document.pdf'
				if not pdf_filename.endswith('.pdf'):
					pdf_filename += '.pdf'

			self.logger.debug(f'[DownloadsWatchdog] Generated filename: {pdf_filename}')
			self.logger.debug(f'[DownloadsWatchdog] PDF_URL: {pdf_url}, session_pdf_urls: {self._session_pdf_urls}')
			if pdf_url in self._session_pdf_urls:
				existing_path = self._session_pdf_urls[pdf_url]
				self.logger.debug(f'[DownloadsWatchdog] PDF already downloaded in session: {existing_path}')
				return existing_path

			downloads_dir = str(self.browser_session.browser_profile.downloads_path)
			os.makedirs(downloads_dir, exist_ok=True)
			final_filename = pdf_filename
			existing_files = os.listdir(downloads_dir)
			if pdf_filename in existing_files:
				base, ext = os.path.splitext(pdf_filename)
				counter = 1
				while f'{base} ({counter}){ext}' in existing_files:
					counter += 1
				final_filename = f'{base} ({counter}){ext}'
				self.logger.debug(f'[DownloadsWatchdog] File exists, using: {final_filename}')

			self.logger.debug(f'[DownloadsWatchdog] Starting PDF download from: {pdf_url[:100]}...')
			try:
				escaped_pdf_url = json.dumps(pdf_url)
				result = await asyncio.wait_for(
					temp_session.cdp_client.send.Runtime.evaluate(
						params={
							'expression': f"""
                    (async () => {{
                        try {{
                            const response = await fetch({escaped_pdf_url}, {{
                                cache: 'force-cache'
                            }});
                            if (!response.ok) {{
                                throw new Error(`HTTP error! status: ${{response.status}}`);
                            }}
                            const blob = await response.blob();
                            const arrayBuffer = await blob.arrayBuffer();
                            const uint8Array = new Uint8Array(arrayBuffer);
                            const fromCache = response.headers.has('age') || !response.headers.has('date');
                            return {{
                                data: Array.from(uint8Array),
                                fromCache: fromCache,
                                responseSize: uint8Array.length,
                                transferSize: response.headers.get('content-length') || 'unknown'
                            }};
                        }} catch (error) {{
                            throw new Error(`Fetch failed: ${{error.message}}`);
                        }}
                    }})()
                    """,
							'awaitPromise': True,
							'returnByValue': True,
						},
						session_id=temp_session.session_id,
					),
					timeout=10.0,
				)
				download_result = result.get('result', {}).get('value', {})
				if download_result and download_result.get('data') and len(download_result['data']) > 0:
					downloads_dir = str(self.browser_session.browser_profile.downloads_path)
					os.makedirs(downloads_dir, exist_ok=True)
					download_path = os.path.join(downloads_dir, final_filename)
					async with await anyio.open_file(download_path, 'wb') as file_handle:
						await file_handle.write(bytes(download_result['data']))
					if not os.path.exists(download_path):
						self.logger.error(f'[DownloadsWatchdog] ❌ Failed to write PDF file to: {download_path}')
						return None
					actual_size = os.path.getsize(download_path)
					self.logger.debug(f'[DownloadsWatchdog] PDF file written successfully: {download_path} ({actual_size} bytes)')
					cache_status = 'from cache' if download_result.get('fromCache') else 'from network'
					response_size = download_result.get('responseSize', 0)
					self.logger.debug(
						f'[DownloadsWatchdog] ✅ Auto-downloaded PDF ({cache_status}, {response_size:,} bytes): {download_path}'
					)
					self._session_pdf_urls[pdf_url] = download_path
					self.logger.debug(f'[DownloadsWatchdog] Dispatching FileDownloadedEvent for {final_filename}')
					self.event_bus.dispatch(
						FileDownloadedEvent(
							url=pdf_url,
							path=download_path,
							file_name=final_filename,
							file_size=response_size,
							file_type='pdf',
							mime_type='application/pdf',
							from_cache=download_result.get('fromCache', False),
							auto_download=True,
						)
					)
					return download_path
				self.logger.warning(f'[DownloadsWatchdog] No data received when downloading PDF from {pdf_url}')
				return None
			except Exception as error:
				self.logger.warning(
					f'[DownloadsWatchdog] Failed to auto-download PDF from {pdf_url}: {type(error).__name__}: {error}'
				)
				return None
		except TimeoutError:
			self.logger.debug('[DownloadsWatchdog] PDF download operation timed out')
			return None
		except Exception as error:
			self.logger.error(f'[DownloadsWatchdog] Error in PDF download: {type(error).__name__}: {error}')
			return None

	async def _get_unique_filename(self, directory: str, filename: str) -> str:
		return await get_unique_filename(directory, filename)
