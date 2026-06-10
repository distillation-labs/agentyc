"""Leaf helpers shared by BrowserSession helper modules."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

if TYPE_CHECKING:
	from agentyc.browser.session import BrowserSession
	from agentyc.browser.session_models import Target

_FORBIDDEN_FETCH_HEADERS = {
	'accept-charset',
	'accept-encoding',
	'access-control-request-headers',
	'access-control-request-method',
	'connection',
	'content-length',
	'cookie',
	'cookie2',
	'date',
	'dnt',
	'expect',
	'host',
	'keep-alive',
	'origin',
	'referer',
	'te',
	'trailer',
	'transfer-encoding',
	'upgrade',
	'via',
}


def _urls_match_for_navigation_ready(current_url: str, target_url: str) -> bool:
	current = urlsplit(current_url)
	target = urlsplit(target_url)
	return (
		current.scheme,
		current.netloc,
		current.path.rstrip('/'),
		current.query,
	) == (
		target.scheme,
		target.netloc,
		target.path.rstrip('/'),
		target.query,
	)


def _tab_display_title(session: BrowserSession, target: Target) -> str:
	return target.title


def _sanitize_replay_headers(headers: dict[str, Any] | None) -> dict[str, str]:
	if not headers:
		return {}
	sanitized: dict[str, str] = {}
	for key, value in headers.items():
		key_text = str(key).strip()
		if not key_text:
			continue
		key_lower = key_text.lower()
		if key_lower in _FORBIDDEN_FETCH_HEADERS or key_lower.startswith('sec-'):
			continue
		sanitized[key_text] = str(value)
	return sanitized
