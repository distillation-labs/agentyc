"""Post-click wait helpers for downloads, tabs, URLs, and network events."""

from __future__ import annotations

import json

from agentyc.mcp.cdp_tabs_trace_tools import _wait_for_tab_since


async def _with_click_download_result(
	self,
	*,
	base_msg: str,
	expected_download_name: str | None,
	download_timeout_seconds: float,
) -> str:
	wait_result = await self._wait_for_download(
		expected_name=expected_download_name,
		timeout_seconds=download_timeout_seconds,
	)
	if wait_result.startswith('Error'):
		return wait_result
	payload = json.loads(wait_result)
	file_name = payload.get('name') or 'download'
	size_bytes = payload.get('size_bytes')
	if isinstance(size_bytes, int):
		return f'{base_msg} → downloaded {file_name} ({size_bytes} bytes)'
	return f'{base_msg} → downloaded {file_name}'


async def _with_click_tab_result(
	self,
	*,
	base_msg: str,
	before_target_ids: set[str],
	expected_tab_url_substring: str | None,
	tab_timeout_seconds: float,
) -> str:
	wait_result = await _wait_for_tab_since(
		self,
		before_target_ids=before_target_ids,
		url_substring=expected_tab_url_substring,
		timeout_seconds=tab_timeout_seconds,
		switch_focus=True,
	)
	if wait_result.startswith('Error'):
		return wait_result
	payload = json.loads(wait_result)
	tab_id = payload.get('tab_id') or 'new tab'
	url = payload.get('url') or 'unknown URL'
	return f'{base_msg} → switched to tab {tab_id}: {url}'


async def _with_click_url_result(
	self,
	*,
	base_msg: str,
	wait_for_url_substring: str | None,
	wait_for_url_regex: str | None,
	url_timeout_seconds: float,
) -> str:
	wait_result = await self._wait_for_url(
		url_substring=wait_for_url_substring,
		url_regex=wait_for_url_regex,
		timeout_seconds=url_timeout_seconds,
	)
	if wait_result.startswith('Error'):
		return wait_result
	current_url = await self.browser_session.get_current_page_url()
	if current_url and current_url in base_msg:
		return base_msg
	return f'{base_msg} → {current_url}'


async def _with_click_response_result(
	self,
	*,
	wait_for_response: dict[str, object],
	baseline_started_at: float,
) -> str:
	timeout_value = wait_for_response.get('timeout_seconds', 10.0)
	timeout_seconds = float(timeout_value) if isinstance(timeout_value, int | float) else 10.0
	return await self._wait_for_response(
		url_substring=wait_for_response.get('url_substring') if isinstance(wait_for_response.get('url_substring'), str) else None,
		url_regex=wait_for_response.get('url_regex') if isinstance(wait_for_response.get('url_regex'), str) else None,
		method=wait_for_response.get('method') if isinstance(wait_for_response.get('method'), str) else None,
		resource_type=(
			wait_for_response.get('resource_type') if isinstance(wait_for_response.get('resource_type'), str) else None
		),
		status=wait_for_response.get('status') if isinstance(wait_for_response.get('status'), int) else None,
		timeout_seconds=timeout_seconds,
		include_headers=bool(wait_for_response.get('include_headers', False)),
		_baseline_started_at=baseline_started_at,
	)


async def _with_click_request_result(
	self,
	*,
	wait_for_request: dict[str, object],
	baseline_started_at: float,
) -> str:
	timeout_value = wait_for_request.get('timeout_seconds', 10.0)
	timeout_seconds = float(timeout_value) if isinstance(timeout_value, int | float) else 10.0
	return await self._wait_for_request(
		url_substring=wait_for_request.get('url_substring') if isinstance(wait_for_request.get('url_substring'), str) else None,
		url_regex=wait_for_request.get('url_regex') if isinstance(wait_for_request.get('url_regex'), str) else None,
		method=wait_for_request.get('method') if isinstance(wait_for_request.get('method'), str) else None,
		resource_type=(wait_for_request.get('resource_type') if isinstance(wait_for_request.get('resource_type'), str) else None),
		timeout_seconds=timeout_seconds,
		include_headers=bool(wait_for_request.get('include_headers', False)),
		_baseline_started_at=baseline_started_at,
	)


async def _finalize_click_wait_result(
	self,
	*,
	base_msg: str,
	wait_for_download: bool,
	expected_download_name: str | None,
	download_timeout_seconds: float,
	wait_for_tab: bool,
	before_target_ids: set[str],
	expected_tab_url_substring: str | None,
	tab_timeout_seconds: float,
	wait_for_url_substring: str | None,
	wait_for_url_regex: str | None,
	url_timeout_seconds: float,
	wait_for_request: dict[str, object] | None,
	wait_for_response: dict[str, object] | None,
	network_wait_started_at: float | None,
) -> str:
	if wait_for_download:
		return await _with_click_download_result(
			self,
			base_msg=base_msg,
			expected_download_name=expected_download_name,
			download_timeout_seconds=download_timeout_seconds,
		)
	if wait_for_tab:
		return await _with_click_tab_result(
			self,
			base_msg=base_msg,
			before_target_ids=before_target_ids,
			expected_tab_url_substring=expected_tab_url_substring,
			tab_timeout_seconds=tab_timeout_seconds,
		)
	if wait_for_url_substring or wait_for_url_regex:
		return await _with_click_url_result(
			self,
			base_msg=base_msg,
			wait_for_url_substring=wait_for_url_substring,
			wait_for_url_regex=wait_for_url_regex,
			url_timeout_seconds=url_timeout_seconds,
		)
	if wait_for_request is not None and network_wait_started_at is not None:
		return await _with_click_request_result(
			self,
			wait_for_request=wait_for_request,
			baseline_started_at=network_wait_started_at,
		)
	if wait_for_response is not None and network_wait_started_at is not None:
		return await _with_click_response_result(
			self,
			wait_for_response=wait_for_response,
			baseline_started_at=network_wait_started_at,
		)
	return base_msg


__all__ = [
	'_with_click_download_result',
	'_finalize_click_wait_result',
	'_with_click_tab_result',
	'_with_click_url_result',
	'_with_click_request_result',
	'_with_click_response_result',
]
