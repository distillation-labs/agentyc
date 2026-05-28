"""HAR Recording Watchdog for Agentyc browser sessions."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from bubus import BaseEvent
from cdp_use.cdp.network.events import (
	DataReceivedEvent,
	LoadingFailedEvent,
	LoadingFinishedEvent,
	RequestWillBeSentEvent,
	ResponseReceivedEvent,
)
from cdp_use.cdp.page.events import FrameNavigatedEvent, LifecycleEventEvent

from agentyc.browser.events import BrowserConnectedEvent, BrowserStopEvent
from agentyc.browser.watchdog_base import BaseWatchdog
from agentyc.browser.watchdogs.har_recording_models import _HarEntryBuilder, _is_https
from agentyc.browser.watchdogs.har_recording_writer import (
	calc_headers_size,
	calc_request_body_size,
	compute_timings,
	format_page_started_datetime,
	include_entry,
	page_ref_for_entry,
	write_har,
)


class HarRecordingWatchdog(BaseWatchdog):
	"""Collects HTTPS requests/responses and writes a HAR 1.2 file on stop."""

	LISTENS_TO: ClassVar[list[type[BaseEvent]]] = [BrowserConnectedEvent, BrowserStopEvent]
	EMITS: ClassVar[list[type[BaseEvent]]] = []

	def __init__(self, *args, **kwargs) -> None:
		super().__init__(*args, **kwargs)
		self._enabled: bool = False
		self._entries: dict[str, _HarEntryBuilder] = {}
		self._top_level_pages: dict[
			str, dict
		] = {}  # frameId -> {url, title, startedDateTime, monotonic_start, onContentLoad, onLoad}

	async def on_BrowserConnectedEvent(self, event: BrowserConnectedEvent) -> None:
		profile = self.browser_session.browser_profile
		if not profile.record_har_path:
			return

		# Normalize config
		self._content_mode = (profile.record_har_content or 'embed').lower()
		self._mode = (profile.record_har_mode or 'full').lower()
		self._har_path = Path(str(profile.record_har_path)).expanduser().resolve()
		self._har_dir = self._har_path.parent
		self._har_dir.mkdir(parents=True, exist_ok=True)

		try:
			# Enable Network and Page domains for events
			cdp_session = await self.browser_session.get_or_create_cdp_session()
			await cdp_session.cdp_client.send.Network.enable(session_id=cdp_session.session_id)
			await cdp_session.cdp_client.send.Page.enable(session_id=cdp_session.session_id)

			# Query browser version for HAR log.browser
			try:
				version_info = await self.browser_session.cdp_client.send.Browser.getVersion()
				self._browser_name = version_info.get('product') or 'Chromium'
				self._browser_version = version_info.get('jsVersion') or ''
			except Exception:
				self._browser_name = 'Chromium'
				self._browser_version = ''

			cdp = self.browser_session.cdp_client.register
			cdp.Network.requestWillBeSent(self._on_request_will_be_sent)
			cdp.Network.responseReceived(self._on_response_received)
			cdp.Network.dataReceived(self._on_data_received)
			cdp.Network.loadingFinished(self._on_loading_finished)
			cdp.Network.loadingFailed(self._on_loading_failed)
			cdp.Page.lifecycleEvent(self._on_lifecycle_event)
			cdp.Page.frameNavigated(self._on_frame_navigated)

			self._enabled = True
			self.logger.info(f'📊 Starting HAR recording to {self._har_path}')
		except Exception as e:
			self.logger.warning(f'Failed to enable HAR recording: {e}')
			self._enabled = False

	async def on_BrowserStopEvent(self, event: BrowserStopEvent) -> None:
		if not self._enabled:
			return
		try:
			await self._write_har()
			self.logger.info(f'📊 HAR file saved: {self._har_path}')
		except Exception as e:
			self.logger.warning(f'Failed to write HAR: {e}')

	# =============== CDP Event Handlers (sync) ==================
	def _on_request_will_be_sent(self, params: RequestWillBeSentEvent, session_id: str | None) -> None:
		try:
			req = params.get('request', {}) if hasattr(params, 'get') else getattr(params, 'request', {})
			url = req.get('url') if isinstance(req, dict) else getattr(req, 'url', None)
			if not _is_https(url):
				return  # HTTPS-only requirement (only HTTPS requests are recorded for now)

			request_id = params.get('requestId') if hasattr(params, 'get') else getattr(params, 'requestId', None)
			if not request_id:
				return

			entry = self._entries.setdefault(request_id, _HarEntryBuilder(request_id=request_id))
			entry.url = url
			entry.method = req.get('method') if isinstance(req, dict) else getattr(req, 'method', None)
			entry.post_data = req.get('postData') if isinstance(req, dict) else getattr(req, 'postData', None)

			# Convert headers to plain dict, handling various formats
			headers_raw = req.get('headers') if isinstance(req, dict) else getattr(req, 'headers', None)
			if headers_raw is None:
				entry.request_headers = {}
			elif isinstance(headers_raw, dict):
				entry.request_headers = {k.lower(): str(v) for k, v in headers_raw.items()}
			elif isinstance(headers_raw, list):
				entry.request_headers = {
					h.get('name', '').lower(): str(h.get('value') or '') for h in headers_raw if isinstance(h, dict)
				}
			else:
				# Handle Headers type or other formats - convert to dict
				try:
					headers_dict = dict(headers_raw) if hasattr(headers_raw, '__iter__') else {}
					entry.request_headers = {k.lower(): str(v) for k, v in headers_dict.items()}
				except Exception:
					entry.request_headers = {}

			entry.frame_id = params.get('frameId') if hasattr(params, 'get') else getattr(params, 'frameId', None)
			entry.document_url = (
				params.get('documentURL')
				if hasattr(params, 'get')
				else getattr(params, 'documentURL', None) or entry.document_url
			)

			# Timing anchors
			entry.ts_request = params.get('timestamp') if hasattr(params, 'get') else getattr(params, 'timestamp', None)
			entry.wall_time_request = params.get('wallTime') if hasattr(params, 'get') else getattr(params, 'wallTime', None)

			# Track top-level navigations for page context
			req_type = params.get('type') if hasattr(params, 'get') else getattr(params, 'type', None)
			is_same_doc = (
				params.get('isSameDocument', False) if hasattr(params, 'get') else getattr(params, 'isSameDocument', False)
			)
			if req_type == 'Document' and not is_same_doc:
				# best-effort: consider as navigation
				if entry.frame_id and url:
					if entry.frame_id not in self._top_level_pages:
						self._top_level_pages[entry.frame_id] = {
							'url': str(url),
							'title': str(url),  # Default to URL, will be updated from DOM
							'startedDateTime': entry.wall_time_request,
							'monotonic_start': entry.ts_request,  # Track monotonic start time for timing calculations
							'onContentLoad': -1,
							'onLoad': -1,
						}
					else:
						# Update startedDateTime and monotonic_start if this is earlier
						page_info = self._top_level_pages[entry.frame_id]
						if entry.wall_time_request and (
							page_info['startedDateTime'] is None or entry.wall_time_request < page_info['startedDateTime']
						):
							page_info['startedDateTime'] = entry.wall_time_request
							page_info['monotonic_start'] = entry.ts_request
		except Exception as e:
			self.logger.debug(f'requestWillBeSent handling error: {e}')

	def _on_response_received(self, params: ResponseReceivedEvent, session_id: str | None) -> None:
		try:
			request_id = params.get('requestId') if hasattr(params, 'get') else getattr(params, 'requestId', None)
			if not request_id or request_id not in self._entries:
				return
			response = params.get('response', {}) if hasattr(params, 'get') else getattr(params, 'response', {})
			entry = self._entries[request_id]
			entry.status = response.get('status') if isinstance(response, dict) else getattr(response, 'status', None)
			entry.status_text = (
				response.get('statusText') if isinstance(response, dict) else getattr(response, 'statusText', None)
			)

			# Extract Content-Length for compression calculation (before converting headers)
			headers_raw = response.get('headers') if isinstance(response, dict) else getattr(response, 'headers', None)
			if headers_raw:
				if isinstance(headers_raw, dict):
					cl_str = headers_raw.get('content-length') or headers_raw.get('Content-Length')
				elif isinstance(headers_raw, list):
					cl_header = next(
						(h for h in headers_raw if isinstance(h, dict) and h.get('name', '').lower() == 'content-length'), None
					)
					cl_str = cl_header.get('value') if cl_header else None
				else:
					cl_str = None
				if cl_str:
					try:
						entry.content_length = int(cl_str)
					except Exception:
						pass

			# Convert headers to plain dict, handling various formats
			if headers_raw is None:
				entry.response_headers = {}
			elif isinstance(headers_raw, dict):
				entry.response_headers = {k.lower(): str(v) for k, v in headers_raw.items()}
			elif isinstance(headers_raw, list):
				entry.response_headers = {
					h.get('name', '').lower(): str(h.get('value') or '') for h in headers_raw if isinstance(h, dict)
				}
			else:
				# Handle Headers type or other formats - convert to dict
				try:
					headers_dict = dict(headers_raw) if hasattr(headers_raw, '__iter__') else {}
					entry.response_headers = {k.lower(): str(v) for k, v in headers_dict.items()}
				except Exception:
					entry.response_headers = {}

			entry.mime_type = response.get('mimeType') if isinstance(response, dict) else getattr(response, 'mimeType', None)
			entry.ts_response = params.get('timestamp') if hasattr(params, 'get') else getattr(params, 'timestamp', None)

			protocol_raw = response.get('protocol') if isinstance(response, dict) else getattr(response, 'protocol', None)
			if protocol_raw:
				protocol_lower = str(protocol_raw).lower()
				if protocol_lower == 'h2' or protocol_lower.startswith('http/2'):
					entry.protocol = 'HTTP/2.0'
				elif protocol_lower.startswith('http/1.1'):
					entry.protocol = 'HTTP/1.1'
				elif protocol_lower.startswith('http/1.0'):
					entry.protocol = 'HTTP/1.0'
				else:
					entry.protocol = str(protocol_raw).upper()

			entry.server_ip_address = (
				response.get('remoteIPAddress') if isinstance(response, dict) else getattr(response, 'remoteIPAddress', None)
			)
			server_port_raw = response.get('remotePort') if isinstance(response, dict) else getattr(response, 'remotePort', None)
			if server_port_raw is not None:
				try:
					entry.server_port = int(server_port_raw)
				except (ValueError, TypeError):
					pass

			# Extract security details (TLS info)
			security_details_raw = (
				response.get('securityDetails') if isinstance(response, dict) else getattr(response, 'securityDetails', None)
			)
			if security_details_raw:
				try:
					entry.security_details = dict(security_details_raw)
				except Exception:
					pass
		except Exception as e:
			self.logger.debug(f'responseReceived handling error: {e}')

	def _on_data_received(self, params: DataReceivedEvent, session_id: str | None) -> None:
		try:
			request_id = params.get('requestId') if hasattr(params, 'get') else getattr(params, 'requestId', None)
			if not request_id or request_id not in self._entries:
				return
			data = params.get('data') if hasattr(params, 'get') else getattr(params, 'data', None)
			if isinstance(data, str):
				try:
					self._entries[request_id].encoded_data.extend(data.encode('latin1'))
				except Exception:
					pass
		except Exception as e:
			self.logger.debug(f'dataReceived handling error: {e}')

	def _on_loading_finished(self, params: LoadingFinishedEvent, session_id: str | None) -> None:
		try:
			request_id = params.get('requestId') if hasattr(params, 'get') else getattr(params, 'requestId', None)
			if not request_id or request_id not in self._entries:
				return
			entry = self._entries[request_id]
			entry.ts_finished = params.get('timestamp')
			# Fetch response body via CDP as dataReceived may be incomplete
			import asyncio as _asyncio

			async def _fetch_body(self_ref, req_id, sess_id):
				try:
					resp = await self_ref.browser_session.cdp_client.send.Network.getResponseBody(
						params={'requestId': req_id}, session_id=sess_id
					)
					data = resp.get('body', b'')
					if resp.get('base64Encoded'):
						import base64 as _b64

						data = _b64.b64decode(data)
					else:
						# Ensure data is bytes even if CDP returns a string
						if isinstance(data, str):
							data = data.encode('utf-8', errors='replace')
					# Ensure we always have bytes
					if not isinstance(data, bytes):
						data = bytes(data) if data else b''
					entry.response_body = data
				except Exception:
					pass

			# Always schedule the response body fetch task
			_asyncio.create_task(_fetch_body(self, request_id, session_id))

			encoded_length = (
				params.get('encodedDataLength') if hasattr(params, 'get') else getattr(params, 'encodedDataLength', None)
			)
			if encoded_length is not None:
				try:
					entry.encoded_data_length = int(encoded_length)
					entry.transfer_size = entry.encoded_data_length
				except Exception:
					entry.encoded_data_length = None
		except Exception as e:
			self.logger.debug(f'loadingFinished handling error: {e}')

	def _on_loading_failed(self, params: LoadingFailedEvent, session_id: str | None) -> None:
		try:
			request_id = params.get('requestId') if hasattr(params, 'get') else getattr(params, 'requestId', None)
			if request_id and request_id in self._entries:
				self._entries[request_id].failed = True
		except Exception as e:
			self.logger.debug(f'loadingFailed handling error: {e}')

	# ===================== HAR Writing ==========================
	def _on_lifecycle_event(self, params: LifecycleEventEvent, session_id: str | None) -> None:
		"""Handle Page.lifecycleEvent for tracking page load timings."""
		try:
			frame_id = params.get('frameId') if hasattr(params, 'get') else getattr(params, 'frameId', None)
			name = params.get('name') if hasattr(params, 'get') else getattr(params, 'name', None)
			timestamp = params.get('timestamp') if hasattr(params, 'get') else getattr(params, 'timestamp', None)

			if not frame_id or not name or frame_id not in self._top_level_pages:
				return

			page_info = self._top_level_pages[frame_id]
			# Use monotonic_start instead of startedDateTime (wall-clock) for timing calculations
			monotonic_start = page_info.get('monotonic_start')

			if name == 'DOMContentLoaded' and monotonic_start is not None:
				# Calculate milliseconds since page start using monotonic timestamps
				try:
					elapsed_ms = int(round((timestamp - monotonic_start) * 1000))
					page_info['onContentLoad'] = max(0, elapsed_ms)
				except Exception:
					pass
			elif name == 'load' and monotonic_start is not None:
				try:
					elapsed_ms = int(round((timestamp - monotonic_start) * 1000))
					page_info['onLoad'] = max(0, elapsed_ms)
				except Exception:
					pass
		except Exception as e:
			self.logger.debug(f'lifecycleEvent handling error: {e}')

	def _on_frame_navigated(self, params: FrameNavigatedEvent, session_id: str | None) -> None:
		"""Handle Page.frameNavigated to update page title from DOM."""
		try:
			frame = params.get('frame') if hasattr(params, 'get') else getattr(params, 'frame', None)
			if not frame:
				return

			frame_id = frame.get('id') if isinstance(frame, dict) else getattr(frame, 'id', None)
			title = (
				frame.get('name') or frame.get('url')
				if isinstance(frame, dict)
				else getattr(frame, 'name', None) or getattr(frame, 'url', None)
			)

			if frame_id and frame_id in self._top_level_pages:
				# Try to get actual page title via Runtime.evaluate if possible
				# For now, use frame name or URL as fallback
				if title:
					self._top_level_pages[frame_id]['title'] = str(title)
		except Exception as e:
			self.logger.debug(f'frameNavigated handling error: {e}')

	async def _write_har(self) -> None:
		await write_har(self)

	def _format_page_started_datetime(self, timestamp: float | None) -> str:
		return format_page_started_datetime(timestamp)

	def _page_ref_for_entry(self, e: _HarEntryBuilder) -> str | None:
		return page_ref_for_entry(self, e)

	def _include_entry(self, e: _HarEntryBuilder) -> bool:
		return include_entry(self, e)

	def _compute_timings(self, e: _HarEntryBuilder) -> tuple[str, int, dict]:
		return compute_timings(e)

	def _calc_headers_size(self, method: str | None, url: str | None, headers_list: list[dict]) -> int:
		return calc_headers_size(method, url, headers_list)

	def _calc_request_body_size(self, e: _HarEntryBuilder) -> int:
		return calc_request_body_size(e)
