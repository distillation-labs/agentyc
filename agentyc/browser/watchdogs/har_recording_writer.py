"""HAR serialization and writing helpers."""

from __future__ import annotations

import base64
import json
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import TYPE_CHECKING

from agentyc.browser.watchdogs.har_recording_models import (
	_generate_har_filename,
	_HarEntryBuilder,
	_is_https,
	_origin,
)

if TYPE_CHECKING:
	from agentyc.browser.watchdogs.har_recording_watchdog import HarRecordingWatchdog


async def write_har(watchdog: HarRecordingWatchdog) -> None:
	entries = [entry for entry in watchdog._entries.values() if include_entry(watchdog, entry)]

	har_entries = []
	sidecar_dir: Path | None = None
	if watchdog._content_mode == 'attach':
		sidecar_dir = watchdog._har_dir / f'{watchdog._har_path.stem}_har_parts'
		sidecar_dir.mkdir(parents=True, exist_ok=True)

	for entry in entries:
		content_obj: dict = {'mimeType': entry.mime_type or ''}
		body_data = entry.response_body if entry.response_body is not None else entry.encoded_data
		if isinstance(body_data, str):
			body_bytes = body_data.encode('utf-8', errors='replace')
		elif isinstance(body_data, bytearray):
			body_bytes = bytes(body_data)
		elif isinstance(body_data, bytes):
			body_bytes = body_data
		else:
			try:
				body_bytes = bytes(body_data) if body_data else b''
			except (TypeError, ValueError):
				body_bytes = b''

		content_size = len(body_bytes)
		compression = 0
		if entry.content_length is not None and entry.encoded_data_length is not None:
			compression = max(0, entry.content_length - entry.encoded_data_length)

		if watchdog._content_mode == 'embed' and content_size > 0:
			try:
				text_decoded = body_bytes.decode('utf-8')
				content_obj['text'] = text_decoded
				content_obj['size'] = content_size
				content_obj['compression'] = compression
			except UnicodeDecodeError:
				content_obj['text'] = base64.b64encode(body_bytes).decode('ascii')
				content_obj['encoding'] = 'base64'
				content_obj['size'] = content_size
				content_obj['compression'] = compression
		elif watchdog._content_mode == 'attach' and content_size > 0 and sidecar_dir is not None:
			filename = _generate_har_filename(body_bytes, entry.mime_type)
			(sidecar_dir / filename).write_bytes(body_bytes)
			content_obj['_file'] = filename
			content_obj['size'] = content_size
			content_obj['compression'] = compression
		else:
			content_obj['size'] = content_size
			if content_size > 0:
				content_obj['compression'] = compression

		started_date_time, total_time_ms, timings = compute_timings(entry)
		req_headers_list = [{'name': key, 'value': str(value)} for key, value in (entry.request_headers or {}).items()]
		resp_headers_list = [{'name': key, 'value': str(value)} for key, value in (entry.response_headers or {}).items()]
		request_headers_size = calc_headers_size(entry.method or 'GET', entry.url or '', req_headers_list)
		response_headers_size = calc_headers_size(None, None, resp_headers_list)
		request_body_size = calc_request_body_size(entry)
		request_post_data = None
		if entry.post_data and watchdog._content_mode != 'omit':
			if watchdog._content_mode == 'embed':
				request_post_data = {'mimeType': entry.request_headers.get('content-type', ''), 'text': entry.post_data}
			elif watchdog._content_mode == 'attach' and sidecar_dir is not None:
				post_data_bytes = entry.post_data.encode('utf-8')
				req_mime_type = entry.request_headers.get('content-type', 'text/plain')
				req_filename = _generate_har_filename(post_data_bytes, req_mime_type)
				(sidecar_dir / req_filename).write_bytes(post_data_bytes)
				request_post_data = {'mimeType': req_mime_type, '_file': req_filename}

		http_version = entry.protocol if entry.protocol else 'HTTP/1.1'
		response_body_size = entry.transfer_size
		if response_body_size is None:
			response_body_size = entry.encoded_data_length
		if response_body_size is None:
			response_body_size = content_size if content_size > 0 else -1

		entry_dict = {
			'startedDateTime': started_date_time,
			'time': total_time_ms,
			'request': {
				'method': entry.method or 'GET',
				'url': entry.url or '',
				'httpVersion': http_version,
				'headers': req_headers_list,
				'queryString': [],
				'cookies': [],
				'headersSize': request_headers_size,
				'bodySize': request_body_size,
				'postData': request_post_data,
			},
			'response': {
				'status': entry.status or 0,
				'statusText': entry.status_text or '',
				'httpVersion': http_version,
				'headers': resp_headers_list,
				'cookies': [],
				'content': content_obj,
				'redirectURL': '',
				'headersSize': response_headers_size,
				'bodySize': response_body_size,
			},
			'cache': {},
			'timings': timings,
			'pageref': page_ref_for_entry(watchdog, entry),
		}

		if entry.server_ip_address:
			entry_dict['serverIPAddress'] = entry.server_ip_address
		if entry.server_port is not None:
			entry_dict['_serverPort'] = entry.server_port
		if entry.security_details:
			security_filtered = {}
			if 'protocol' in entry.security_details:
				security_filtered['protocol'] = entry.security_details['protocol']
			if 'subjectName' in entry.security_details:
				security_filtered['subjectName'] = entry.security_details['subjectName']
			if 'issuer' in entry.security_details:
				security_filtered['issuer'] = entry.security_details['issuer']
			if 'validFrom' in entry.security_details:
				security_filtered['validFrom'] = entry.security_details['validFrom']
			if 'validTo' in entry.security_details:
				security_filtered['validTo'] = entry.security_details['validTo']
			if security_filtered:
				entry_dict['_securityDetails'] = security_filtered
		if entry.transfer_size is not None:
			entry_dict['response']['_transferSize'] = entry.transfer_size

		har_entries.append(entry_dict)

	try:
		bu_version = importlib_metadata.version('agentyc')
	except Exception:
		bu_version = 'dev'

	har_obj = {
		'log': {
			'version': '1.2',
			'creator': {'name': 'agentyc', 'version': bu_version},
			'browser': {'name': watchdog._browser_name, 'version': watchdog._browser_version},
			'pages': [
				{
					'id': f'page@{page_id}',
					'title': page_info.get('title', page_info.get('url', '')),
					'startedDateTime': format_page_started_datetime(page_info.get('startedDateTime')),
					'pageTimings': (
						lambda _ocl, _ol: {
							key: value for key, value in (('onContentLoad', _ocl), ('onLoad', _ol)) if value is not None
						}
					)(
						(page_info.get('onContentLoad') if page_info.get('onContentLoad', -1) >= 0 else None),
						(page_info.get('onLoad') if page_info.get('onLoad', -1) >= 0 else None),
					),
				}
				for page_id, page_info in watchdog._top_level_pages.items()
			],
			'entries': har_entries,
		}
	}

	tmp_path = watchdog._har_path.with_suffix(watchdog._har_path.suffix + '.tmp')
	tmp_path.write_bytes(json.dumps(har_obj, indent=2, ensure_ascii=False).encode('utf-8'))
	tmp_path.replace(watchdog._har_path)


def format_page_started_datetime(timestamp: float | None) -> str:
	if timestamp is None:
		return ''
	try:
		from datetime import datetime, timezone

		return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat().replace('+00:00', 'Z')
	except Exception:
		return ''


def page_ref_for_entry(watchdog: HarRecordingWatchdog, entry: _HarEntryBuilder) -> str | None:
	if entry.frame_id and entry.frame_id in watchdog._top_level_pages:
		return f'page@{entry.frame_id}'
	return None


def include_entry(watchdog: HarRecordingWatchdog, entry: _HarEntryBuilder) -> bool:
	if not _is_https(entry.url):
		return False
	if entry.url and '/favicon.ico' in entry.url.lower():
		return False
	if getattr(watchdog, '_mode', 'full') == 'full':
		return True
	if entry.frame_id and entry.frame_id in watchdog._top_level_pages:
		page_info = watchdog._top_level_pages[entry.frame_id]
		page_url = page_info.get('url') if isinstance(page_info, dict) else page_info
		return _origin(entry.url or '') == _origin(page_url or '')
	return False


def compute_timings(entry: _HarEntryBuilder) -> tuple[str, int, dict]:
	started = ''
	try:
		if entry.wall_time_request is not None:
			from datetime import datetime, timezone

			started = datetime.fromtimestamp(entry.wall_time_request, tz=timezone.utc).isoformat().replace('+00:00', 'Z')
	except Exception:
		started = ''

	dns_ms = 0
	connect_ms = 0
	ssl_ms = 0
	send_ms = 0
	wait_ms = 0
	receive_ms = 0
	if entry.ts_request is not None and entry.ts_response is not None:
		wait_ms = max(0, int(round((entry.ts_response - entry.ts_request) * 1000)))
	if entry.ts_response is not None and entry.ts_finished is not None:
		receive_ms = max(0, int(round((entry.ts_finished - entry.ts_response) * 1000)))

	total = dns_ms + connect_ms + ssl_ms + send_ms + wait_ms + receive_ms
	return (
		started,
		total,
		{
			'dns': dns_ms,
			'connect': connect_ms,
			'ssl': ssl_ms,
			'send': send_ms,
			'wait': wait_ms,
			'receive': receive_ms,
		},
	)


def calc_headers_size(method: str | None, url: str | None, headers_list: list[dict]) -> int:
	try:
		size = 0
		if method and url:
			size += len(f'{method} {url} HTTP/1.1\r\n'.encode('latin1'))
		for header in headers_list:
			size += len(f'{header.get("name", "")}: {header.get("value", "")}\r\n'.encode('latin1'))
		size += len(b'\r\n')
		return size
	except Exception:
		return -1


def calc_request_body_size(entry: _HarEntryBuilder) -> int:
	try:
		cl = None
		if entry.request_headers:
			cl = entry.request_headers.get('content-length') or entry.request_headers.get('Content-Length')
		if cl is not None:
			return int(cl)
		if entry.post_data:
			return len(entry.post_data.encode('utf-8'))
		if entry.request_body is not None:
			return len(entry.request_body)
		if entry.method and entry.method.upper() in ('GET', 'HEAD'):
			return 0
	except Exception:
		pass
	return -1
