"""Internal data models and utility helpers for HAR recording."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field


@dataclass
class _HarEntryBuilder:
	request_id: str = ''
	frame_id: str | None = None
	document_url: str | None = None
	url: str | None = None
	method: str | None = None
	request_headers: dict = field(default_factory=dict)
	request_body: bytes | None = None
	post_data: str | None = None
	status: int | None = None
	status_text: str | None = None
	response_headers: dict = field(default_factory=dict)
	mime_type: str | None = None
	encoded_data: bytearray = field(default_factory=bytearray)
	failed: bool = False
	ts_request: float | None = None
	wall_time_request: float | None = None
	ts_response: float | None = None
	ts_finished: float | None = None
	encoded_data_length: int | None = None
	response_body: bytes | None = None
	content_length: int | None = None
	protocol: str | None = None
	server_ip_address: str | None = None
	server_port: int | None = None
	security_details: dict | None = None
	transfer_size: int | None = None


def _is_https(url: str | None) -> bool:
	return bool(url and url.lower().startswith('https://'))


def _origin(url: str) -> str:
	if not url:
		return ''
	try:
		without_scheme = url.split('://', 1)[1]
		host_port = without_scheme.split('/', 1)[0]
		return f'https://{host_port}'
	except Exception:
		return ''


def _mime_to_extension(mime_type: str | None) -> str:
	if not mime_type:
		return 'bin'

	mime_lower = mime_type.lower().split(';')[0].strip()
	mime_map = {
		'text/html': 'html',
		'text/css': 'css',
		'text/javascript': 'js',
		'application/javascript': 'js',
		'application/x-javascript': 'js',
		'application/json': 'json',
		'application/xml': 'xml',
		'text/xml': 'xml',
		'text/plain': 'txt',
		'image/png': 'png',
		'image/jpeg': 'jpg',
		'image/jpg': 'jpg',
		'image/gif': 'gif',
		'image/webp': 'webp',
		'image/svg+xml': 'svg',
		'image/x-icon': 'ico',
		'font/woff': 'woff',
		'font/woff2': 'woff2',
		'application/font-woff': 'woff',
		'application/font-woff2': 'woff2',
		'application/x-font-woff': 'woff',
		'application/x-font-woff2': 'woff2',
		'font/ttf': 'ttf',
		'application/x-font-ttf': 'ttf',
		'font/otf': 'otf',
		'application/x-font-opentype': 'otf',
		'application/pdf': 'pdf',
		'application/zip': 'zip',
		'application/x-zip-compressed': 'zip',
		'video/mp4': 'mp4',
		'video/webm': 'webm',
		'audio/mpeg': 'mp3',
		'audio/mp3': 'mp3',
		'audio/wav': 'wav',
		'audio/ogg': 'ogg',
	}
	return mime_map.get(mime_lower, 'bin')


def _generate_har_filename(content: bytes, mime_type: str | None) -> str:
	content_hash = hashlib.sha1(content).hexdigest()
	extension = _mime_to_extension(mime_type)
	return f'{content_hash}.{extension}'
