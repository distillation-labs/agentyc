"""Shared action-runtime helpers."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def _ensure_extract_runtime(self) -> None:
	if self.file_system is None:
		from agentyc.filesystem.file_system import FileSystem

		base_dir = self._file_system_base_dir or Path('~/.agentyc-mcp').expanduser()
		self.file_system = FileSystem(base_dir=base_dir)


def _resolve_element_index(self, index: int | None = None, ref: str | None = None) -> int:
	if ref is not None:
		from agentyc.mcp.state import parse_element_ref

		return parse_element_ref(ref)
	if index is None:
		raise ValueError('Provide either ref or index.')
	return index


def _resolve_upload_available_file_paths(self, path: str) -> list[str]:
	available_file_paths = [path]
	if os.path.exists(path):
		return available_file_paths
	if self.file_system is not None:
		file_obj = self.file_system.get_file(path)
		if file_obj is not None:
			return [str(self.file_system.get_dir() / file_obj.full_name)]
	return available_file_paths


def _validate_actionable_element(self, element: Any, *, action_name: str) -> tuple[str, str] | None:
	if not getattr(element, 'is_visible', True):
		return 'target_not_visible', f'Element <{element.tag_name}> is not visible enough to interact with.'
	attributes = getattr(element, 'attributes', {}) or {}
	if 'disabled' in attributes or str(attributes.get('aria-disabled', '')).strip().lower() == 'true':
		return 'target_disabled', f'Element <{element.tag_name}> is disabled and cannot be used yet.'
	if action_name == 'type':
		tag_name = str(getattr(element, 'tag_name', '') or '').lower()
		is_text_like = tag_name in {'input', 'textarea'} or 'contenteditable' in attributes
		if not is_text_like:
			return 'invalid_target', f'Element <{element.tag_name}> does not accept typed text.'
	return None


_ERROR_HINTS: dict[str, str] = {
	'stale_ref': 'Call browser_get_state() to get fresh refs before retrying.',
	'target_not_visible': 'Try browser_scroll() to bring the element into view, then retry.',
	'target_disabled': 'Wait for the element to become enabled or check for a prerequisite step.',
	'navigation_timeout': 'Try browser_wait_for_network_idle() or increase the timeout, then get state.',
	'target_blocked': 'A dialog or overlay may be covering the element. Check browser_get_state() for overlays.',
	'browser_connection': 'The browser may have crashed. Call browser_list_sessions() to check session health.',
}


def _classify_action_error(self, message: str, *, default_code: str) -> str:
	normalized = message.lower()
	if 'not found' in normalized or 'page may have changed' in normalized or 'stale' in normalized:
		return 'stale_ref'
	if 'disabled' in normalized:
		return 'target_disabled'
	if 'not visible' in normalized or 'interactable or visible' in normalized:
		return 'target_not_visible'
	if 'select' in normalized or 'file input' in normalized:
		return 'invalid_target'
	if 'timeout' in normalized or 'timed out' in normalized:
		return 'navigation_timeout'
	if 'blocked' in normalized or 'overlay' in normalized or 'dialog' in normalized:
		return 'target_blocked'
	if 'connection' in normalized or 'cdp' in normalized:
		return 'browser_connection'
	if 'site unavailable' in normalized or 'err_' in normalized or 'net::' in normalized:
		return 'site_unavailable'
	return default_code


def _format_action_error(self, message: str, *, default_code: str) -> str:
	error_code = self._classify_action_error(message, default_code=default_code)
	hint = _ERROR_HINTS.get(error_code, '')
	suffix = f' Hint: {hint}' if hint else ''
	return f'Error [{error_code}]: {message}{suffix}'


__all__ = [
	'_classify_action_error',
	'_ensure_extract_runtime',
	'_format_action_error',
	'_resolve_element_index',
	'_resolve_upload_available_file_paths',
	'_validate_actionable_element',
]
