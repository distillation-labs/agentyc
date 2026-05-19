"""Cross-process registry for Agentyc-managed reusable local browsers."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx

from agentyc.config import CONFIG

_REGISTRY_FILENAME = 'mcp-shared-browser.json'
_REUSE_ENV_VAR = 'AGENTYC_REUSE_LOCAL_BROWSER'


def reuse_local_browser_enabled() -> bool:
	value = os.getenv(_REUSE_ENV_VAR)
	if value is None:
		return False
	return value.strip().lower() in {'1', 'true', 'yes', 'on'}


def _registry_path() -> Path:
	return CONFIG.AGENTYC_CONFIG_DIR / _REGISTRY_FILENAME


def _normalize_cdp_base_url(cdp_url: str) -> str:
	trimmed = cdp_url.strip()
	parsed = urlparse(trimmed)
	if parsed.scheme in {'ws', 'wss'}:
		http_scheme = 'https' if parsed.scheme == 'wss' else 'http'
		return urlunparse((http_scheme, parsed.netloc, '', '', '', '')).rstrip('/') + '/'
	return trimmed.rstrip('/') + '/'


def _read_registry() -> dict[str, Any] | None:
	path = _registry_path()
	if not path.exists():
		return None
	try:
		return json.loads(path.read_text())
	except Exception:
		return None


def register_local_shared_browser(
	*,
	cdp_url: str,
	browser_pid: int | None,
	headless: bool | None,
	user_data_dir: str | None,
) -> None:
	path = _registry_path()
	path.parent.mkdir(parents=True, exist_ok=True)
	payload = {
		'cdp_url': _normalize_cdp_base_url(cdp_url),
		'browser_pid': browser_pid,
		'headless': headless,
		'user_data_dir': user_data_dir,
		'updated_at': time.time(),
	}
	temp_path = path.with_suffix('.tmp')
	temp_path.write_text(json.dumps(payload, indent=2, sort_keys=True))
	temp_path.replace(path)


def clear_registered_local_shared_browser(*, cdp_url: str | None = None) -> None:
	path = _registry_path()
	if not path.exists():
		return
	if cdp_url is not None:
		payload = _read_registry()
		if not payload:
			try:
				path.unlink()
			except FileNotFoundError:
				pass
			return
		registered_url = payload.get('cdp_url')
		if not isinstance(registered_url, str):
			return
		if _normalize_cdp_base_url(registered_url) != _normalize_cdp_base_url(cdp_url):
			return
	try:
		path.unlink()
	except FileNotFoundError:
		pass


async def get_reusable_local_browser_cdp_url(*, headless: bool | None) -> str | None:
	payload = _read_registry()
	if not payload:
		return None
	cdp_url = payload.get('cdp_url')
	if not isinstance(cdp_url, str) or not cdp_url.strip():
		clear_registered_local_shared_browser()
		return None
	registered_headless = payload.get('headless')
	if headless is not None and registered_headless is not None and bool(registered_headless) != bool(headless):
		return None
	normalized_cdp_url = _normalize_cdp_base_url(cdp_url)
	if await _browser_endpoint_is_live(normalized_cdp_url):
		return normalized_cdp_url
	clear_registered_local_shared_browser(cdp_url=normalized_cdp_url)
	return None


async def _browser_endpoint_is_live(cdp_url: str) -> bool:
	version_url = cdp_url.rstrip('/') + '/json/version'
	try:
		async with httpx.AsyncClient(timeout=httpx.Timeout(1.5), trust_env=False) as client:
			response = await client.get(version_url)
		if response.status_code != 200:
			return False
		payload = response.json()
		return isinstance(payload.get('webSocketDebuggerUrl'), str) and bool(payload['webSocketDebuggerUrl'])
	except Exception:
		return False
