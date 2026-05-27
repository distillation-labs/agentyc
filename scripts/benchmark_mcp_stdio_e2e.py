from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import importlib.util
import json
import math
import os
import sys
import tempfile
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from agentyc.mcp.tool_schemas import get_tool_schemas

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_PYTHON = REPO_ROOT / '.venv' / 'bin' / 'python'
DEFAULT_PYPI_PYTHON = Path('/tmp/agentyc-pypi-smoke-311-20260515/bin/python')
DEFAULT_OUTPUT = Path(tempfile.gettempdir()) / 'agentyc-mcp-stdio-e2e-baseline.json'
UPLOAD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head><title>Upload docs</title></head>
<body>
	<main>
		<h1>Upload docs</h1>
		<label>Upload document <input id="upload" aria-label="Upload document" type="file"></label>
		<p id="status">No file selected</p>
	</main>
	<script>
		const input = document.getElementById('upload');
		input.addEventListener('change', () => {
			const file = input.files && input.files[0];
			document.getElementById('status').textContent = file ? file.name : 'No file selected';
		});
	</script>
</body>
</html>
"""
FRAME_STORAGE_HTML = """
<!DOCTYPE html>
<html lang="en">
<head><title>Frame Storage Parent</title></head>
<body>
	<main>
		<h1>Frame Storage Parent</h1>
		<iframe name="child-frame" src="/iframe-child.html" style="width: 480px; height: 240px;"></iframe>
	</main>
</body>
</html>
"""
FRAME_STORAGE_CHILD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head><title>Frame Child Title</title></head>
<body>
	<main>
		<h2>Iframe child payload</h2>
		<p id="child-copy">Frame child body content</p>
	</main>
</body>
</html>
"""


def _load_runtime_benchmark_module() -> Any:
	module_path = REPO_ROOT / 'scripts' / 'benchmark_mcp_runtime.py'
	spec = importlib.util.spec_from_file_location('benchmark_mcp_runtime_shared', module_path)
	if spec is None or spec.loader is None:
		raise RuntimeError(f'Could not load benchmark module from {module_path}')
	module = importlib.util.module_from_spec(spec)
	sys.modules[spec.name] = module
	spec.loader.exec_module(module)
	return module


BENCH = _load_runtime_benchmark_module()
PUBLIC_TOOL_NAMES = [tool.name for tool in get_tool_schemas()]


@dataclass(slots=True)
class TargetConfig:
	name: str
	python_path: Path
	cwd: Path

	@property
	def command(self) -> str:
		return str(self.python_path)

	@property
	def args(self) -> list[str]:
		return ['-m', 'agentyc.mcp', '--session-timeout-minutes', '0']


@dataclass(slots=True)
class ToolCallRecord:
	tool_name: str
	latency_ms: float
	success: bool
	scenario: str
	error_text: str | None
	result_preview: str


@dataclass(slots=True)
class ToolCallResult:
	text: str
	image_data: list[str]
	success: bool
	error_text: str | None
	latency_ms: float


class MCPStdioAdapter:
	def __init__(self, target: TargetConfig):
		self.target = target
		self.session: ClientSession | None = None
		self._stdio_cm: Any = None
		self._session_cm: Any = None
		self.call_records: list[ToolCallRecord] = []
		self.current_url: str | None = None
		self.available_tools: list[str] = []
		self._agentyc_config_dir: tempfile.TemporaryDirectory[str] | None = None

	async def __aenter__(self) -> MCPStdioAdapter:
		# Use a fresh AGENTYC_CONFIG_DIR so the MCP subprocess gets a unique browser
		# profile that doesn't conflict with any existing Chrome instances.
		self._agentyc_config_dir = tempfile.TemporaryDirectory(prefix='agentyc-bench-cfg-')
		server = StdioServerParameters(
			command=self.target.command,
			args=self.target.args,
			cwd=self.target.cwd,
			env={
				**os.environ,
				'PYTHONUNBUFFERED': '1',
				'AGENTYC_CONFIG_DIR': self._agentyc_config_dir.name,
				# Keep stdio benchmark runs consistent with the direct runtime benchmark and CI.
				'AGENTYC_HEADLESS': 'true',
			},
		)
		self._stdio_cm = stdio_client(server)
		read_stream, write_stream = await self._stdio_cm.__aenter__()
		self._session_cm = ClientSession(read_stream, write_stream)
		session = await self._session_cm.__aenter__()
		self.session = session
		await session.initialize()
		tool_result = await session.list_tools()
		self.available_tools = [tool.name for tool in tool_result.tools]
		return self

	async def __aexit__(self, exc_type, exc, tb) -> None:
		if self._session_cm is not None:
			await self._session_cm.__aexit__(exc_type, exc, tb)
		if self._stdio_cm is not None:
			await self._stdio_cm.__aexit__(exc_type, exc, tb)
		if self._agentyc_config_dir is not None:
			try:
				self._agentyc_config_dir.cleanup()
			except Exception:
				pass
			self._agentyc_config_dir = None

	async def call_tool(
		self,
		tool_name: str,
		arguments: dict[str, Any] | None = None,
		*,
		scenario: str,
		expected_error_substring: str | None = None,
	) -> ToolCallResult:
		if self.session is None:
			raise RuntimeError('MCP session is not initialized')
		start = time.perf_counter()
		result = await self.session.call_tool(tool_name, arguments or {})
		latency_ms = round((time.perf_counter() - start) * 1000, 1)
		texts: list[str] = []
		images: list[str] = []
		for item in result.content:
			item_type = getattr(item, 'type', None)
			if item_type == 'text':
				texts.append(getattr(item, 'text', ''))
			elif item_type == 'image':
				images.append(getattr(item, 'data', ''))
		text = '\n'.join(part for part in texts if part)
		error_text = None
		success = not result.isError
		for line in texts:
			if line.startswith('Error'):
				error_text = line
				break
		if error_text is not None:
			if expected_error_substring and expected_error_substring in error_text:
				success = True
				error_text = None
			else:
				success = False
		preview = text.replace('\n', ' ')[:220]
		self.call_records.append(
			ToolCallRecord(
				tool_name=tool_name,
				latency_ms=latency_ms,
				success=success,
				scenario=scenario,
				error_text=error_text,
				result_preview=preview,
			)
		)
		return ToolCallResult(
			text=text,
			image_data=images,
			success=success,
			error_text=error_text,
			latency_ms=latency_ms,
		)

	async def _navigate(self, url: str, new_tab: bool = False, *, scenario: str = 'fixture') -> str:
		result = await self.call_tool('browser_navigate', {'url': url, 'new_tab': new_tab}, scenario=scenario)
		if result.success and not new_tab:
			self.current_url = url
		return result.text

	async def _click(
		self,
		ref: str | None = None,
		index: int | None = None,
		coordinate_x: int | None = None,
		coordinate_y: int | None = None,
		new_tab: bool = False,
		*,
		scenario: str = 'fixture',
	) -> str:
		arguments = {
			'ref': ref,
			'index': index,
			'coordinate_x': coordinate_x,
			'coordinate_y': coordinate_y,
			'new_tab': new_tab,
		}
		return (await self.call_tool('browser_click', _drop_nones(arguments), scenario=scenario)).text

	async def _type_text(self, text: str, index: int | None = None, ref: str | None = None, *, scenario: str = 'fixture') -> str:
		arguments = {'text': text, 'index': index, 'ref': ref}
		return (await self.call_tool('browser_type', _drop_nones(arguments), scenario=scenario)).text

	async def _upload_file(
		self, path: str, index: int | None = None, ref: str | None = None, *, scenario: str = 'fixture'
	) -> str:
		arguments = {'path': path, 'index': index, 'ref': ref}
		return (await self.call_tool('browser_upload_file', _drop_nones(arguments), scenario=scenario)).text

	async def _get_browser_state(
		self,
		include_screenshot: bool = False,
		mode: str = 'auto',
		focus_ref: str | None = None,
		since_hash: str | None = None,
		*,
		scenario: str = 'fixture',
	) -> tuple[str, str | None]:
		arguments = {
			'include_screenshot': include_screenshot,
			'mode': mode,
			'focus_ref': focus_ref,
			'since_hash': since_hash,
		}
		result = await self.call_tool('browser_get_state', _drop_nones(arguments), scenario=scenario)
		return result.text, result.image_data[0] if result.image_data else None

	async def get_browser_state_json(
		self,
		include_screenshot: bool = False,
		mode: str = 'auto',
		focus_ref: str | None = None,
		since_hash: str | None = None,
		*,
		scenario: str = 'fixture',
	) -> tuple[dict[str, Any], str | None]:
		state_text, screenshot = await self._get_browser_state(
			include_screenshot=include_screenshot,
			mode=mode,
			focus_ref=focus_ref,
			since_hash=since_hash,
			scenario=scenario,
		)
		parsed = _safe_json_loads(state_text)
		if not isinstance(parsed, dict):
			raise RuntimeError(f'browser_get_state returned non-JSON payload for {scenario}: {state_text[:300]}')
		return parsed, screenshot

	async def _extract_content(
		self,
		query: str,
		extract_links: bool = False,
		output_schema: dict[str, Any] | None = None,
		*,
		scenario: str = 'fixture',
	) -> str:
		arguments = {'query': query, 'extract_links': extract_links, 'output_schema': output_schema}
		return (await self.call_tool('browser_extract_content', _drop_nones(arguments), scenario=scenario)).text

	async def _get_html(self, selector: str | None = None, *, scenario: str = 'fixture') -> str:
		return (await self.call_tool('browser_get_html', _drop_nones({'selector': selector}), scenario=scenario)).text

	async def _screenshot(self, full_page: bool = False, *, scenario: str = 'coverage') -> tuple[str, str | None]:
		result = await self.call_tool('browser_screenshot', {'full_page': full_page}, scenario=scenario)
		return result.text, result.image_data[0] if result.image_data else None

	async def _list_tabs(self, *, scenario: str = 'coverage') -> str:
		return (await self.call_tool('browser_list_tabs', {}, scenario=scenario)).text

	async def _switch_tab(self, tab_id: str, *, scenario: str = 'coverage') -> str:
		return (await self.call_tool('browser_switch_tab', {'tab_id': tab_id}, scenario=scenario)).text

	async def _close_tab(self, tab_id: str, *, scenario: str = 'coverage') -> str:
		return (await self.call_tool('browser_close_tab', {'tab_id': tab_id}, scenario=scenario)).text

	async def _list_sessions(self, *, scenario: str = 'coverage') -> str:
		return (await self.call_tool('browser_list_sessions', {}, scenario=scenario)).text

	async def _close_session(self, session_id: str, *, scenario: str = 'coverage') -> str:
		return (await self.call_tool('browser_close_session', {'session_id': session_id}, scenario=scenario)).text

	async def _close_all_sessions(self, *, scenario: str = 'coverage') -> str:
		return (await self.call_tool('browser_close_all', {}, scenario=scenario)).text

	async def _new_tab(self, url: str = 'about:blank', *, scenario: str = 'coverage') -> str:
		return (await self.call_tool('browser_new_tab', {'url': url}, scenario=scenario)).text

	async def _wait_for_stable_dom(self, timeout_seconds: float = 10, quiet_ms: int = 500, *, scenario: str = 'coverage') -> str:
		arguments = {'timeout_seconds': timeout_seconds, 'quiet_ms': quiet_ms}
		return (await self.call_tool('browser_wait_for_stable_dom', arguments, scenario=scenario)).text

	async def _handle_dialog(self, accept: bool = True, prompt_text: str | None = None, *, scenario: str = 'coverage') -> str:
		arguments = {'accept': accept, 'prompt_text': prompt_text}
		return (await self.call_tool('browser_handle_dialog', _drop_nones(arguments), scenario=scenario)).text

	async def _get_attribute(
		self,
		name: str,
		ref: str | None = None,
		index: int | None = None,
		*,
		scenario: str = 'coverage',
	) -> str:
		arguments = {'name': name, 'ref': ref, 'index': index}
		return (await self.call_tool('browser_get_attribute', _drop_nones(arguments), scenario=scenario)).text

	async def _get_downloads(self, *, scenario: str = 'coverage') -> str:
		return (await self.call_tool('browser_get_downloads', {}, scenario=scenario)).text

	async def _set_viewport(
		self,
		width: int,
		height: int,
		device_scale_factor: float = 1.0,
		*,
		scenario: str = 'coverage',
	) -> str:
		arguments = {'width': width, 'height': height, 'device_scale_factor': device_scale_factor}
		return (await self.call_tool('browser_set_viewport', arguments, scenario=scenario)).text

	async def _clear_logs(self, console: bool = True, network: bool = True, *, scenario: str = 'coverage') -> str:
		arguments = {'console': console, 'network': network}
		return (await self.call_tool('browser_clear_logs', arguments, scenario=scenario)).text

	async def _start_trace(self, categories: str | None = None, *, scenario: str = 'coverage') -> str:
		return (await self.call_tool('browser_start_trace', _drop_nones({'categories': categories}), scenario=scenario)).text

	async def _stop_trace(self, *, scenario: str = 'coverage') -> str:
		return (await self.call_tool('browser_stop_trace', {}, scenario=scenario)).text

	async def _save_as_pdf(
		self,
		file_name: str | None = None,
		print_background: bool = True,
		landscape: bool = False,
		scale: float = 1.0,
		paper_format: str = 'Letter',
		*,
		scenario: str = 'coverage',
	) -> str:
		arguments = {
			'file_name': file_name,
			'print_background': print_background,
			'landscape': landscape,
			'scale': scale,
			'paper_format': paper_format,
		}
		return (await self.call_tool('browser_save_as_pdf', _drop_nones(arguments), scenario=scenario)).text

	async def _scroll(
		self,
		direction: str = 'down',
		pages: float = 1,
		ref: str | None = None,
		index: int | None = None,
		*,
		scenario: str = 'coverage',
	) -> str:
		arguments = {'direction': direction, 'pages': pages, 'ref': ref, 'index': index}
		return (await self.call_tool('browser_scroll', _drop_nones(arguments), scenario=scenario)).text

	async def _go_back(self, *, scenario: str = 'coverage') -> str:
		return (await self.call_tool('browser_go_back', {}, scenario=scenario)).text

	async def _go_forward(self, *, scenario: str = 'coverage') -> str:
		return (await self.call_tool('browser_go_forward', {}, scenario=scenario)).text

	async def _refresh(self, *, scenario: str = 'coverage') -> str:
		return (await self.call_tool('browser_refresh', {}, scenario=scenario)).text

	async def _press_key(self, key: str, *, scenario: str = 'coverage') -> str:
		return (await self.call_tool('browser_press_key', {'key': key}, scenario=scenario)).text

	async def _wait(self, seconds: float = 2, *, scenario: str = 'coverage') -> str:
		return (await self.call_tool('browser_wait', {'seconds': seconds}, scenario=scenario)).text

	async def _evaluate(self, code: str, *, scenario: str = 'coverage') -> str:
		return (await self.call_tool('browser_evaluate', {'code': code}, scenario=scenario)).text

	async def _select_option(
		self, text: str, ref: str | None = None, index: int | None = None, *, scenario: str = 'coverage'
	) -> str:
		arguments = {'text': text, 'ref': ref, 'index': index}
		return (await self.call_tool('browser_select_option', _drop_nones(arguments), scenario=scenario)).text

	async def _get_dropdown_options(self, ref: str | None = None, index: int | None = None, *, scenario: str = 'coverage') -> str:
		arguments = {'ref': ref, 'index': index}
		return (await self.call_tool('browser_get_dropdown_options', _drop_nones(arguments), scenario=scenario)).text

	async def _find_elements(
		self, selector: str, attributes: list[str] | None = None, max_results: int = 50, *, scenario: str = 'coverage'
	) -> str:
		arguments = {'selector': selector, 'attributes': attributes, 'max_results': max_results}
		return (await self.call_tool('browser_find_elements', _drop_nones(arguments), scenario=scenario)).text

	async def _wait_for_element(
		self,
		text: str | None = None,
		ref: str | None = None,
		appear: bool = True,
		timeout_seconds: float = 10,
		*,
		scenario: str = 'coverage',
	) -> str:
		arguments = {'text': text, 'ref': ref, 'appear': appear, 'timeout_seconds': timeout_seconds}
		return (await self.call_tool('browser_wait_for_element', _drop_nones(arguments), scenario=scenario)).text

	async def _search_page(self, pattern: str, regex: bool = False, max_results: int = 25, *, scenario: str = 'coverage') -> str:
		arguments = {'pattern': pattern, 'regex': regex, 'max_results': max_results}
		return (await self.call_tool('browser_search_page', arguments, scenario=scenario)).text

	async def _get_focused_element(self, *, scenario: str = 'coverage') -> str:
		return (await self.call_tool('browser_get_focused_element', {}, scenario=scenario)).text

	async def _hover(
		self,
		ref: str | None = None,
		index: int | None = None,
		coordinate_x: int | None = None,
		coordinate_y: int | None = None,
		*,
		scenario: str = 'fixture',
	) -> str:
		arguments = {'ref': ref, 'index': index, 'coordinate_x': coordinate_x, 'coordinate_y': coordinate_y}
		return (await self.call_tool('browser_hover', _drop_nones(arguments), scenario=scenario)).text

	async def _double_click(
		self,
		ref: str | None = None,
		index: int | None = None,
		coordinate_x: int | None = None,
		coordinate_y: int | None = None,
		*,
		scenario: str = 'coverage',
	) -> str:
		arguments = {'ref': ref, 'index': index, 'coordinate_x': coordinate_x, 'coordinate_y': coordinate_y}
		return (await self.call_tool('browser_double_click', _drop_nones(arguments), scenario=scenario)).text

	async def _drag_to(
		self,
		source_ref: str | None = None,
		target_ref: str | None = None,
		source_x: int | None = None,
		source_y: int | None = None,
		target_x: int | None = None,
		target_y: int | None = None,
		steps: int = 10,
		*,
		scenario: str = 'fixture',
	) -> str:
		arguments = {
			'source_ref': source_ref,
			'target_ref': target_ref,
			'source_x': source_x,
			'source_y': source_y,
			'target_x': target_x,
			'target_y': target_y,
			'steps': steps,
		}
		return (await self.call_tool('browser_drag_to', _drop_nones(arguments), scenario=scenario)).text

	async def _scroll_to_text(self, text: str, *, scenario: str = 'coverage') -> str:
		return (await self.call_tool('browser_scroll_to_text', {'text': text}, scenario=scenario)).text

	async def _save_state(self, path: str | None = None, *, scenario: str = 'coverage') -> str:
		return (await self.call_tool('browser_save_state', _drop_nones({'path': path}), scenario=scenario)).text

	async def _load_state(self, path: str, *, scenario: str = 'coverage') -> str:
		return (await self.call_tool('browser_load_state', {'path': path}, scenario=scenario)).text

	async def _wait_for_network_idle(
		self, timeout_seconds: float = 10, idle_duration_ms: int = 500, *, scenario: str = 'coverage'
	) -> str:
		arguments = {'timeout_seconds': timeout_seconds, 'idle_duration_ms': idle_duration_ms}
		return (await self.call_tool('browser_wait_for_network_idle', arguments, scenario=scenario)).text

	async def _wait_for_request(
		self,
		url_substring: str | None = None,
		url_regex: str | None = None,
		method: str | None = None,
		resource_type: str | None = None,
		timeout_seconds: float = 10,
		include_headers: bool = False,
		*,
		scenario: str = 'coverage',
	) -> str:
		arguments = {
			'url_substring': url_substring,
			'url_regex': url_regex,
			'method': method,
			'resource_type': resource_type,
			'timeout_seconds': timeout_seconds,
			'include_headers': include_headers,
		}
		return (await self.call_tool('browser_wait_for_request', _drop_nones(arguments), scenario=scenario)).text

	async def _wait_for_response(
		self,
		url_substring: str | None = None,
		url_regex: str | None = None,
		method: str | None = None,
		resource_type: str | None = None,
		status: int | None = None,
		timeout_seconds: float = 10,
		include_headers: bool = False,
		*,
		scenario: str = 'coverage',
	) -> str:
		arguments = {
			'url_substring': url_substring,
			'url_regex': url_regex,
			'method': method,
			'resource_type': resource_type,
			'status': status,
			'timeout_seconds': timeout_seconds,
			'include_headers': include_headers,
		}
		return (await self.call_tool('browser_wait_for_response', _drop_nones(arguments), scenario=scenario)).text

	async def _right_click(
		self,
		ref: str | None = None,
		index: int | None = None,
		coordinate_x: float | None = None,
		coordinate_y: float | None = None,
		*,
		scenario: str = 'fixture',
	) -> str:
		arguments = {'ref': ref, 'index': index, 'coordinate_x': coordinate_x, 'coordinate_y': coordinate_y}
		return (await self.call_tool('browser_right_click', _drop_nones(arguments), scenario=scenario)).text

	async def _get_cookies(self, *, scenario: str = 'coverage') -> str:
		return (await self.call_tool('browser_get_cookies', {}, scenario=scenario)).text

	async def _set_cookies(self, cookies: list[dict[str, Any]], *, scenario: str = 'coverage') -> str:
		return (await self.call_tool('browser_set_cookies', {'cookies': cookies}, scenario=scenario)).text

	async def _clear_cookies(self, name: str | None = None, *, scenario: str = 'coverage') -> str:
		return (await self.call_tool('browser_clear_cookies', _drop_nones({'name': name}), scenario=scenario)).text

	async def _get_console_logs(self, level: str = 'all', max_entries: int = 50, *, scenario: str = 'fixture') -> str:
		arguments = {'level': level, 'max_entries': max_entries}
		return (await self.call_tool('browser_get_console_logs', arguments, scenario=scenario)).text

	async def _get_network_log(
		self, type_filter: str = 'all', status_filter: str = 'all', max_entries: int = 50, *, scenario: str = 'fixture'
	) -> str:
		arguments = {'type_filter': type_filter, 'status_filter': status_filter, 'max_entries': max_entries}
		return (await self.call_tool('browser_get_network_log', arguments, scenario=scenario)).text

	async def _export_debug_bundle(
		self,
		*,
		state_mode: str = 'min',
		focus_ref: str | None = None,
		since_hash: str | None = None,
		include_screenshot: bool = True,
		include_headers: bool = False,
		include_html: bool = False,
		html_selector: str | None = None,
		console_max_entries: int = 20,
		network_max_entries: int = 20,
		network_status_filter: str = 'all',
		scenario: str = 'fixture',
	) -> tuple[str, str | None]:
		arguments = {
			'state_mode': state_mode,
			'focus_ref': focus_ref,
			'since_hash': since_hash,
			'include_screenshot': include_screenshot,
			'include_headers': include_headers,
			'include_html': include_html,
			'html_selector': html_selector,
			'console_max_entries': console_max_entries,
			'network_max_entries': network_max_entries,
			'network_status_filter': network_status_filter,
		}
		result = await self.call_tool('browser_export_debug_bundle', _drop_nones(arguments), scenario=scenario)
		return result.text, result.image_data[0] if result.image_data else None

	async def _list_frames(self, *, scenario: str = 'coverage') -> str:
		return (await self.call_tool('browser_list_frames', {}, scenario=scenario)).text

	async def _get_frame_html(
		self,
		frame_id: str,
		*,
		scenario: str = 'coverage',
		expected_error_substring: str | None = None,
	) -> str:
		arguments = {'frame_id': frame_id}
		return (
			await self.call_tool(
				'browser_get_frame_html',
				arguments,
				scenario=scenario,
				expected_error_substring=expected_error_substring,
			)
		).text

	async def _get_storage(
		self,
		origin: str | None = None,
		storage_type: str | None = None,
		key: str | None = None,
		*,
		scenario: str = 'coverage',
	) -> str:
		arguments = {'origin': origin, 'storage_type': storage_type, 'key': key}
		return (await self.call_tool('browser_get_storage', _drop_nones(arguments), scenario=scenario)).text

	async def _set_storage(
		self,
		origin: str,
		storage_type: str,
		key: str,
		value: str,
		*,
		scenario: str = 'coverage',
	) -> str:
		arguments = {'origin': origin, 'storage_type': storage_type, 'key': key, 'value': value}
		return (await self.call_tool('browser_set_storage', arguments, scenario=scenario)).text

	async def _clear_storage(
		self,
		origin: str,
		storage_type: str | None = None,
		key: str | None = None,
		*,
		scenario: str = 'coverage',
	) -> str:
		arguments = {'origin': origin, 'storage_type': storage_type, 'key': key}
		return (await self.call_tool('browser_clear_storage', _drop_nones(arguments), scenario=scenario)).text

	async def _inspect_network_entry(
		self,
		request_id: str | None = None,
		url_substring: str | None = None,
		url_regex: str | None = None,
		method: str | None = None,
		resource_type: str | None = None,
		status: int | None = None,
		include_headers: bool = False,
		include_request_body: bool = True,
		include_response_body: bool = True,
		max_body_bytes: int | None = None,
		decode_json: bool = True,
		*,
		scenario: str = 'coverage',
	) -> str:
		arguments = {
			'request_id': request_id,
			'url_substring': url_substring,
			'url_regex': url_regex,
			'method': method,
			'resource_type': resource_type,
			'status': status,
			'include_headers': include_headers,
			'include_request_body': include_request_body,
			'include_response_body': include_response_body,
			'max_body_bytes': max_body_bytes,
			'decode_json': decode_json,
		}
		return (await self.call_tool('browser_inspect_network_entry', _drop_nones(arguments), scenario=scenario)).text

	async def _add_network_mock(
		self,
		url_substring: str | None = None,
		url_regex: str | None = None,
		method: str | None = None,
		resource_type: str | None = None,
		action: str = 'fulfill',
		status: int = 200,
		headers: dict[str, Any] | None = None,
		body: str = '',
		error_reason: str = 'Failed',
		*,
		scenario: str = 'coverage',
	) -> str:
		arguments = {
			'url_substring': url_substring,
			'url_regex': url_regex,
			'method': method,
			'resource_type': resource_type,
			'action': action,
			'status': status,
			'headers': headers,
			'body': body,
			'error_reason': error_reason,
		}
		return (await self.call_tool('browser_add_network_mock', _drop_nones(arguments), scenario=scenario)).text

	async def _remove_network_mock(self, mock_id: str | None = None, *, scenario: str = 'coverage') -> str:
		return (await self.call_tool('browser_remove_network_mock', _drop_nones({'mock_id': mock_id}), scenario=scenario)).text

	async def _list_network_mocks(self, *, scenario: str = 'coverage') -> str:
		return (await self.call_tool('browser_list_network_mocks', {}, scenario=scenario)).text

	async def _set_network_conditions(
		self,
		offline: bool = False,
		latency_ms: float = 0.0,
		download_kbps: float | None = None,
		upload_kbps: float | None = None,
		connection_type: str | None = None,
		reset: bool = False,
		*,
		scenario: str = 'coverage',
	) -> str:
		arguments = {
			'offline': offline,
			'latency_ms': latency_ms,
			'download_kbps': download_kbps,
			'upload_kbps': upload_kbps,
			'connection_type': connection_type,
			'reset': reset,
		}
		return (await self.call_tool('browser_set_network_conditions', _drop_nones(arguments), scenario=scenario)).text

	async def _get_network_conditions(self, *, scenario: str = 'coverage') -> str:
		return (await self.call_tool('browser_get_network_conditions', {}, scenario=scenario)).text

	async def _replay_request(
		self,
		request_id: str | None = None,
		url_substring: str | None = None,
		url_regex: str | None = None,
		method: str | None = None,
		body: str | None = None,
		headers: dict[str, Any] | None = None,
		*,
		scenario: str = 'coverage',
	) -> str:
		arguments = {
			'request_id': request_id,
			'url_substring': url_substring,
			'url_regex': url_regex,
			'method': method,
			'body': body,
			'headers': headers,
		}
		return (await self.call_tool('browser_replay_request', _drop_nones(arguments), scenario=scenario)).text


def _drop_nones(payload: dict[str, Any]) -> dict[str, Any]:
	return {key: value for key, value in payload.items() if value is not None}


def _percentile(values: list[float], percentile: float) -> float:
	if not values:
		return 0.0
	ordered = sorted(values)
	if len(ordered) == 1:
		return round(ordered[0], 1)
	position = (len(ordered) - 1) * percentile
	lower = math.floor(position)
	upper = math.ceil(position)
	if lower == upper:
		return round(ordered[lower], 1)
	weight = position - lower
	return round((ordered[lower] * (1 - weight)) + (ordered[upper] * weight), 1)


def _latency_summary(values: list[float]) -> dict[str, float]:
	if not values:
		return {'count': 0, 'min_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p95_ms': 0.0, 'max_ms': 0.0, 'avg_ms': 0.0}
	return {
		'count': len(values),
		'min_ms': round(min(values), 1),
		'p50_ms': _percentile(values, 0.50),
		'p90_ms': _percentile(values, 0.90),
		'p95_ms': _percentile(values, 0.95),
		'max_ms': round(max(values), 1),
		'avg_ms': round(sum(values) / len(values), 1),
	}


def _safe_json_loads(text: str) -> Any:
	try:
		return json.loads(text)
	except Exception:
		return None


def _estimate_tokens_from_text(text: str) -> int:
	if not text:
		return 0
	return max(1, round(len(text) / 4))


def _context_utilization_from_text(text: str, *, context_budget_tokens: int = 128_000) -> dict[str, Any]:
	estimated_tokens = _estimate_tokens_from_text(text)
	return {
		'estimated_tokens': estimated_tokens,
		'context_budget_tokens': context_budget_tokens,
		'context_utilization_pct': round((estimated_tokens / context_budget_tokens) * 100, 3) if context_budget_tokens else 0.0,
	}


def _parse_find_elements_output(text: str) -> list[dict[str, Any]]:
	results: list[dict[str, Any]] = []
	for raw_line in text.splitlines():
		line = raw_line.strip()
		if not line.startswith('['):
			continue
		entry: dict[str, Any] = {'raw': line}
		if '<' in line and '>' in line:
			entry['tag'] = line.split('<', 1)[1].split('>', 1)[0]
		if '"' in line:
			parts = line.split('"')
			if len(parts) >= 3:
				entry['text'] = parts[1]
		if '{' in line and '}' in line:
			attrs_segment = line.split('{', 1)[1].rsplit('}', 1)[0]
			attrs: dict[str, str] = {}
			for attr in attrs_segment.split(', '):
				if '=' not in attr:
					continue
				key, value = attr.split('=', 1)
				attrs[key] = value.strip('"')
			if attrs:
				entry['attrs'] = attrs
		results.append(entry)
	return results


async def _wait_for_frame_url_suffix(
	adapter: MCPStdioAdapter,
	*,
	url_suffix: str,
	scenario: str,
	attempts: int = 30,
	delay_seconds: float = 0.1,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
	frames: list[dict[str, Any]] = []
	for _ in range(attempts):
		parsed = _safe_json_loads(await adapter._list_frames(scenario=scenario))
		frames = parsed if isinstance(parsed, list) else []
		match = next((frame for frame in frames if str(frame.get('url') or '').endswith(url_suffix)), None)
		if match is not None:
			return frames, match
		await asyncio.sleep(delay_seconds)
	return frames, None


def _accuracy_from_outcomes(outcomes: list[dict[str, Any]]) -> float:
	if not outcomes:
		return 0.0
	return round(sum(1 for outcome in outcomes if outcome.get('passed')) / len(outcomes), 4)


def _scenario_precision(outcomes: list[dict[str, Any]]) -> float:
	if not outcomes:
		return 0.0
	passed = sum(1 for outcome in outcomes if outcome.get('passed'))
	failed = len(outcomes) - passed
	if passed + failed == 0:
		return 0.0
	return round(passed / (passed + failed), 4)


def _efficiency_summary(records: list[ToolCallRecord], outcomes: list[dict[str, Any]]) -> dict[str, Any]:
	latency_total = sum(record.latency_ms for record in records)
	passed = sum(1 for outcome in outcomes if outcome.get('passed'))
	failed = len(outcomes) - passed
	return {
		'calls_per_second': round((len(records) / (latency_total / 1000)), 3) if latency_total > 0 else 0.0,
		'ms_per_tool_call': round(latency_total / len(records), 3) if records else 0.0,
		'failed_calls_per_100': round((sum(1 for record in records if not record.success) / len(records)) * 100, 3)
		if records
		else 0.0,
		'avg_tool_calls_per_scenario': round(len(records) / len(outcomes), 3) if outcomes else 0.0,
		'passed_scenarios_per_100_calls': round((passed / len(records)) * 100, 3) if records else 0.0,
		'failed_scenarios': failed,
	}


def _scenario_result(name: str, passed: bool, **details: Any) -> dict[str, Any]:
	return {'scenario': name, 'passed': passed, **details}


def _workflow_metrics(records: list[ToolCallRecord]) -> dict[str, Any]:
	latencies = [record.latency_ms for record in records]
	combined_text = '\n'.join(record.result_preview for record in records)
	return {
		'tool_calls': len(records),
		'tools_used': sorted({record.tool_name for record in records}),
		'success_rate': round(sum(1 for record in records if record.success) / len(records), 4) if records else 0.0,
		'latency_ms': _latency_summary(latencies),
		'token_metrics': _context_utilization_from_text(combined_text),
	}


def _summarize_workflows(outcomes: list[dict[str, Any]]) -> dict[str, Any]:
	durations = [float(outcome.get('duration_ms', 0.0)) for outcome in outcomes]
	tool_calls = [int(outcome.get('tool_calls', 0)) for outcome in outcomes]
	passed = [outcome for outcome in outcomes if outcome.get('passed')]
	failed = [outcome for outcome in outcomes if not outcome.get('passed')]
	return {
		'total_workflows': len(outcomes),
		'passed_workflows': len(passed),
		'failed_workflows': len(failed),
		'accuracy': _accuracy_from_outcomes(outcomes),
		'precision': _scenario_precision(outcomes),
		'duration_ms': _latency_summary(durations),
		'tool_calls': {
			'total': sum(tool_calls),
			'avg_per_workflow': round(sum(tool_calls) / len(tool_calls), 1) if tool_calls else 0.0,
		},
		'failed_scenarios': [outcome.get('scenario') for outcome in failed],
	}


async def _run_workflow_scenario(adapter: MCPStdioAdapter, name: str, runner) -> dict[str, Any]:
	start_index = len(adapter.call_records)
	started_at = time.perf_counter()
	result = await runner()
	records = adapter.call_records[start_index:]
	passed = bool(result.pop('passed', False))
	return _scenario_result(
		name,
		passed=passed,
		duration_ms=round((time.perf_counter() - started_at) * 1000, 1),
		**_workflow_metrics(records),
		**result,
	)


async def _run_remaining_tool_coverage(adapter: MCPStdioAdapter, base_url: str, scratch_dir: Path) -> list[dict[str, Any]]:
	outcomes: list[dict[str, Any]] = []
	host = base_url.replace('http://', '').split(':', 1)[0]
	state_file = scratch_dir / 'saved-browser-state.json'
	upload_file = scratch_dir / 'upload-fixture.txt'
	upload_file.write_text('agentyc stdio upload fixture', encoding='utf-8')

	list_before = await adapter._list_sessions(scenario='coverage-session-lifecycle')
	await adapter._navigate(f'{base_url}/cookie-auth.html', scenario='coverage-session-lifecycle')
	sessions_after = _safe_json_loads(await adapter._list_sessions(scenario='coverage-session-lifecycle')) or []
	session_id = sessions_after[0]['session_id'] if sessions_after else None
	set_cookie = await adapter._set_cookies(
		[{'name': 'session', 'value': 'abc123', 'domain': host}], scenario='coverage-session-lifecycle'
	)
	save_result = await adapter._save_state(str(state_file), scenario='coverage-session-lifecycle')
	clear_result = await adapter._clear_cookies('session', scenario='coverage-session-lifecycle')
	await adapter._refresh(scenario='coverage-session-lifecycle')
	logged_out_html = await adapter._get_html('#auth', scenario='coverage-session-lifecycle')
	load_result = await adapter._load_state(str(state_file), scenario='coverage-session-lifecycle')
	await adapter._refresh(scenario='coverage-session-lifecycle')
	logged_in_html = await adapter._get_html('#auth', scenario='coverage-session-lifecycle')
	cookies_after = _safe_json_loads(await adapter._get_cookies(scenario='coverage-session-lifecycle')) or []
	close_session_result = (
		await adapter._close_session(session_id, scenario='coverage-session-lifecycle') if session_id else 'missing-session-id'
	)
	list_after_close = await adapter._list_sessions(scenario='coverage-session-lifecycle')
	outcomes.append(
		_scenario_result(
			'coverage-session-lifecycle',
			passed=(
				session_id is not None
				and 'logged-out' in logged_out_html
				and 'logged-in' in logged_in_html
				and 'Browser state saved to:' in save_result
				and 'Browser state loaded from:' in load_result
				and any(cookie.get('name') == 'session' for cookie in cookies_after)
				and 'closed session' in close_session_result.lower()
				and 'No active browser sessions' in list_after_close
			),
			session_id=session_id,
			set_cookie=set_cookie,
			clear_cookie=clear_result,
			close_session=close_session_result,
		)
	)

	await adapter._navigate(f'{base_url}/small-form.html', scenario='coverage-navigation-history')
	await adapter._navigate(f'{base_url}/long-docs.html', scenario='coverage-navigation-history')
	back_result = await adapter._go_back(scenario='coverage-navigation-history')
	state, state_screenshot = await adapter.get_browser_state_json(
		include_screenshot=True,
		mode='auto',
		scenario='coverage-navigation-history',
	)
	forward_result = await adapter._go_forward(scenario='coverage-navigation-history')
	search_result = await adapter._search_page('Authentication', scenario='coverage-navigation-history')
	scroll_result = await adapter._scroll(direction='down', pages=1, scenario='coverage-navigation-history')
	scroll_to_result = await adapter._scroll_to_text('Documentation topic 30', scenario='coverage-navigation-history')
	press_result = await adapter._press_key('Tab', scenario='coverage-navigation-history')
	focused = await adapter._get_focused_element(scenario='coverage-navigation-history')
	focused_payload = _safe_json_loads(focused) if not focused.startswith('Error') else None
	evaluate_result = await adapter._evaluate('(function(){ return document.title; })()', scenario='coverage-navigation-history')
	await adapter._wait(seconds=0.2, scenario='coverage-navigation-history')
	await adapter._navigate(f'{base_url}/delayed-release.html', scenario='coverage-navigation-history')
	release_state, _ = await adapter.get_browser_state_json(mode='auto', scenario='coverage-navigation-history')
	release_ref = BENCH.find_element_ref(release_state, 'Release name', tag='input')
	publish_ref = BENCH.find_element_ref(release_state, 'Publish release')
	await adapter._type_text(ref=release_ref, text='v2.0.0', scenario='coverage-navigation-history')
	wait_element = await adapter._wait_for_element(ref=publish_ref, timeout_seconds=3, scenario='coverage-navigation-history')
	await adapter._wait(seconds=0.4, scenario='coverage-navigation-history')
	publish_html = await adapter._get_html('#publish', scenario='coverage-navigation-history')
	publish_enabled = await adapter._evaluate(
		"""(function(){ var el = document.getElementById('publish'); return el ? !el.disabled : null; })()""",
		scenario='coverage-navigation-history',
	)
	status_html = await adapter._get_html('#status', scenario='coverage-navigation-history')
	meta_json, screenshot_b64 = await adapter._screenshot(full_page=False, scenario='coverage-navigation-history')
	outcomes.append(
		_scenario_result(
			'coverage-navigation-history',
			passed=(
				'Navigated back to:' in back_result
				and state.get('url', '').endswith('/small-form.html')
				and state_screenshot is not None
				and 'Navigated forward to:' in forward_result
				and 'Authentication' in search_result
				and 'Scrolled' in scroll_result
				and 'Scrolled to text' in scroll_to_result
				and 'Pressed key:' in press_result
				and isinstance(focused_payload, dict)
				and focused_payload.get('ariaLabel') == 'Search documentation'
				and 'Long docs' in evaluate_result
				and 'Element ' in wait_element
				and 'appeared' in wait_element
				and 'true' in publish_enabled.lower()
				and 'Ready to publish.' in status_html
				and 'disabled' not in publish_html
				and screenshot_b64 is not None
			),
			focused=focused,
			wait_element=wait_element,
			publish_enabled=publish_enabled,
			screenshot_meta=meta_json,
		)
	)

	await adapter._navigate(f'{base_url}/workflow-form.html', scenario='coverage-dom-and-form')
	workflow_state, _ = await adapter.get_browser_state_json(mode='auto', scenario='coverage-dom-and-form')
	env_ref = BENCH.find_element_ref(workflow_state, 'Environment')
	dropdown_options = _safe_json_loads(await adapter._get_dropdown_options(ref=env_ref, scenario='coverage-dom-and-form')) or []
	select_result = await adapter._select_option(text='Production', ref=env_ref, scenario='coverage-dom-and-form')
	find_result = _parse_find_elements_output(
		await adapter._find_elements(
			'button, a', attributes=['href', 'aria-label'], max_results=10, scenario='coverage-dom-and-form'
		)
	)
	deterministic_result = await adapter._extract_content(
		'list all form fields on the page',
		scenario='coverage-dom-and-form',
	)
	await adapter._navigate(f'{base_url}/upload.html', scenario='coverage-dom-and-form')
	upload_state, _ = await adapter.get_browser_state_json(mode='auto', scenario='coverage-dom-and-form')
	upload_ref = BENCH.find_element_ref(upload_state, 'Upload document')
	upload_result = await adapter._upload_file(str(upload_file), ref=upload_ref, scenario='coverage-dom-and-form')
	upload_status = await adapter._get_html('#status', scenario='coverage-dom-and-form')
	outcomes.append(
		_scenario_result(
			'coverage-dom-and-form',
			passed=(
				('Preview' in json.dumps(dropdown_options) or 'Production' in select_result)
				and 'Selected option' in select_result
				and isinstance(find_result, list)
				and 'Project name' in deterministic_result
				and 'uploaded file' in upload_result.lower()
				and upload_file.name in upload_status
			),
			dropdown_options=dropdown_options,
			find_count=len(find_result),
		)
	)

	await adapter._navigate(f'{base_url}/frame-storage.html', scenario='coverage-frame-and-storage')
	frames, child_frame = await _wait_for_frame_url_suffix(
		adapter,
		url_suffix='/iframe-child.html',
		scenario='coverage-frame-and-storage',
	)
	root_frame = next((frame for frame in frames if str(frame.get('url') or '').endswith('/frame-storage.html')), None)
	child_html = (
		await adapter._get_frame_html(str(child_frame['frame_id']), scenario='coverage-frame-and-storage')
		if child_frame is not None
		else 'missing-frame'
	)
	missing_frame_result = await adapter._get_frame_html(
		'missing-frame-id',
		scenario='coverage-frame-and-storage',
		expected_error_substring='Unknown frame_id: missing-frame-id',
	)
	origin = base_url
	set_local_payload = (
		_safe_json_loads(
			await adapter._set_storage(
				origin=origin,
				storage_type='localStorage',
				key='release',
				value='train',
				scenario='coverage-frame-and-storage',
			)
		)
		or {}
	)
	set_session_payload = (
		_safe_json_loads(
			await adapter._set_storage(
				origin=origin,
				storage_type='sessionStorage',
				key='panel',
				value='network',
				scenario='coverage-frame-and-storage',
			)
		)
		or {}
	)
	storage_payload = _safe_json_loads(await adapter._get_storage(origin=origin, scenario='coverage-frame-and-storage')) or []
	filtered_payload = (
		_safe_json_loads(
			await adapter._get_storage(
				origin=origin,
				storage_type='localStorage',
				key='release',
				scenario='coverage-frame-and-storage',
			)
		)
		or []
	)
	clear_key_payload = (
		_safe_json_loads(
			await adapter._clear_storage(
				origin=origin,
				storage_type='localStorage',
				key='release',
				scenario='coverage-frame-and-storage',
			)
		)
		or {}
	)
	storage_after_key_clear = (
		_safe_json_loads(await adapter._get_storage(origin=origin, key='release', scenario='coverage-frame-and-storage')) or []
	)
	clear_all_payload = _safe_json_loads(await adapter._clear_storage(origin=origin, scenario='coverage-frame-and-storage')) or {}
	storage_after_clear_all = (
		_safe_json_loads(await adapter._get_storage(origin=origin, scenario='coverage-frame-and-storage')) or []
	)
	outcomes.append(
		_scenario_result(
			'coverage-frame-and-storage',
			passed=(
				root_frame is not None
				and child_frame is not None
				and child_frame.get('parent_frame_id') == root_frame.get('frame_id')
				and child_frame.get('name') == 'child-frame'
				and child_frame.get('is_cross_origin') is False
				and 'Frame Child Title' in child_html
				and 'Frame child body content' in child_html
				and missing_frame_result.startswith('Error [not_found]:')
				and set_local_payload.get('ok') is True
				and set_session_payload.get('ok') is True
				and len(storage_payload) == 1
				and any(item == {'name': 'release', 'value': 'train'} for item in storage_payload[0].get('localStorage', []))
				and any(item == {'name': 'panel', 'value': 'network'} for item in storage_payload[0].get('sessionStorage', []))
				and filtered_payload == [{'origin': origin, 'localStorage': [{'name': 'release', 'value': 'train'}]}]
				and clear_key_payload.get('ok') is True
				and storage_after_key_clear == []
				and clear_all_payload.get('ok') is True
				and clear_all_payload.get('storage_type') == 'all'
				and storage_after_clear_all == []
			),
			frame_count=len(frames),
			child_frame=child_frame,
		)
	)

	await adapter._navigate(f'{base_url}/tab-workspace.html', scenario='coverage-tabs-and-pointer')
	tab_state, _ = await adapter.get_browser_state_json(mode='auto', scenario='coverage-tabs-and-pointer')
	workspace_tab_id = tab_state.get('current_tab_id') or (tab_state.get('current_tab') or {}).get('tab_id')
	release_notes_ref = BENCH.find_element_ref(tab_state, 'Open release notes')
	open_tab = (
		await adapter._click(ref=release_notes_ref, new_tab=True, scenario='coverage-tabs-and-pointer')
		if release_notes_ref is not None
		else 'missing-ref: Open release notes'
	)
	tabs = (_safe_json_loads(await adapter._list_tabs(scenario='coverage-tabs-and-pointer')) or {}).get('tabs', [])
	release_notes_tab = next((tab for tab in tabs if tab.get('url', '').endswith('/release-notes.html')), None)
	close_tab_result = (
		await adapter._close_tab(release_notes_tab['tab_id'], scenario='coverage-tabs-and-pointer')
		if release_notes_tab
		else 'missing-tab'
	)
	await adapter._wait(seconds=0.2, scenario='coverage-tabs-and-pointer')
	await adapter._navigate(f'{base_url}/small-form.html', scenario='coverage-tabs-and-pointer')
	small_form_state, _ = await adapter.get_browser_state_json(mode='auto', scenario='coverage-tabs-and-pointer')
	doc_ref = BENCH.find_element_ref(small_form_state, 'Documentation')
	if doc_ref is None:
		small_form_state, _ = await adapter.get_browser_state_json(mode='full', scenario='coverage-tabs-and-pointer')
		doc_ref = BENCH.find_element_ref(small_form_state, 'Documentation')
	open_tab_two = (
		await adapter._click(ref=doc_ref, new_tab=True, scenario='coverage-tabs-and-pointer')
		if doc_ref is not None
		else 'missing-ref: Documentation'
	)
	tabs_two = (_safe_json_loads(await adapter._list_tabs(scenario='coverage-tabs-and-pointer')) or {}).get('tabs', [])
	documentation_tab = next((tab for tab in tabs_two if '/docs/getting-started' in tab.get('url', '')), None)
	switch_result = (
		await adapter._switch_tab(documentation_tab['tab_id'], scenario='coverage-tabs-and-pointer')
		if documentation_tab
		else 'missing-tab'
	)
	await adapter._navigate(f'{base_url}/contenteditable-compose.html', scenario='coverage-tabs-and-pointer')
	editor_state, _ = await adapter.get_browser_state_json(mode='auto', scenario='coverage-tabs-and-pointer')
	publish_ref = BENCH.find_element_ref(editor_state, 'Publish comment')
	coords_json = await adapter._evaluate(
		'(function(){ var el=document.querySelector("button[aria-label=\\"Publish comment\\"]"); if(!el) return null; var r=el.getBoundingClientRect(); return {x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2)}; })()',
		scenario='coverage-tabs-and-pointer',
	)
	coords = _safe_json_loads(coords_json) or {}
	double_click_result = await adapter._double_click(ref=publish_ref, scenario='coverage-tabs-and-pointer')
	if double_click_result.startswith('Error') and coords.get('x') is not None and coords.get('y') is not None:
		double_click_result = await adapter._double_click(
			coordinate_x=int(coords['x']),
			coordinate_y=int(coords['y']),
			scenario='coverage-tabs-and-pointer',
		)
	outcomes.append(
		_scenario_result(
			'coverage-tabs-and-pointer',
			passed=(
				'Clicked element' in open_tab
				and release_notes_tab is not None
				and 'Closed tab' in close_tab_result
				and 'Clicked element' in open_tab_two
				and documentation_tab is not None
				and 'Switched to tab' in switch_result
				and 'Double-clicked' in double_click_result
			),
			tab_count=len(tabs_two),
			release_notes_ref_found=release_notes_ref is not None,
			documentation_ref_found=doc_ref is not None,
			publish_ref_found=publish_ref is not None,
			open_tab=open_tab,
			open_tab_two=open_tab_two,
			switch_result=switch_result,
			double_click_result=double_click_result,
		)
	)

	await adapter._navigate(f'{base_url}/network-log.html', scenario='coverage-network-debug')
	network_state, _ = await adapter.get_browser_state_json(mode='auto', scenario='coverage-network-debug')
	post_ref = BENCH.find_element_ref(network_state, 'Post data')
	request_match: dict[str, Any] = {}
	response_match: dict[str, Any] = {}
	post_click = 'missing-ref'
	if post_ref is not None:
		request_task = asyncio.create_task(
			adapter._wait_for_request(
				url_substring='/api/submit',
				method='POST',
				include_headers=True,
				timeout_seconds=5,
				scenario='coverage-network-debug',
			)
		)
		response_task = asyncio.create_task(
			adapter._wait_for_response(
				url_substring='/api/submit',
				method='POST',
				include_headers=True,
				timeout_seconds=5,
				scenario='coverage-network-debug',
			)
		)
		post_click = await adapter._click(ref=post_ref, scenario='coverage-network-debug')
		request_match = _safe_json_loads(await request_task) or {}
		response_match = _safe_json_loads(await response_task) or {}
	debug_bundle_text, debug_bundle_image = await adapter._export_debug_bundle(
		include_screenshot=True,
		include_headers=True,
		include_html=True,
		html_selector='#status',
		console_max_entries=20,
		network_max_entries=20,
		scenario='coverage-network-debug',
	)
	debug_bundle = _safe_json_loads(debug_bundle_text) or {}
	network_entries = debug_bundle.get('network_log', []) if isinstance(debug_bundle, dict) else []
	outcomes.append(
		_scenario_result(
			'coverage-network-debug',
			passed=(
				post_ref is not None
				and 'Clicked element' in post_click
				and isinstance(request_match, dict)
				and request_match.get('method') == 'POST'
				and '/api/submit' in str(request_match.get('url', ''))
				and isinstance(response_match, dict)
				and '/api/submit' in str(response_match.get('url', ''))
				and (bool(response_match.get('error')) or int(response_match.get('status') or 0) >= 400)
				and isinstance(debug_bundle, dict)
				and debug_bundle_image is not None
				and any('/api/submit' in str(entry.get('url', '')) for entry in network_entries if isinstance(entry, dict))
				and (
					'post failed:' in str(debug_bundle.get('html', '')).lower()
					or 'post 501' in str(debug_bundle.get('html', '')).lower()
				)
			),
			post_ref_found=post_ref is not None,
			post_click=post_click,
			request_match=request_match,
			response_match=response_match,
		)
	)

	await adapter._navigate(f'{base_url}/network-log.html', scenario='coverage-network-controls')
	network_control_state, _ = await adapter.get_browser_state_json(mode='auto', scenario='coverage-network-controls')
	post_control_ref = BENCH.find_element_ref(network_control_state, 'Post data')
	post_control_click = 'missing-ref'
	captured_request: dict[str, Any] = {}
	captured_response: dict[str, Any] = {}
	inspected_entry: dict[str, Any] = {}
	replay_payload: dict[str, Any] = {}
	if post_control_ref is not None:
		request_task = asyncio.create_task(
			adapter._wait_for_request(
				url_substring='/api/submit',
				method='POST',
				include_headers=True,
				timeout_seconds=5,
				scenario='coverage-network-controls',
			)
		)
		response_task = asyncio.create_task(
			adapter._wait_for_response(
				url_substring='/api/submit',
				method='POST',
				include_headers=True,
				timeout_seconds=5,
				scenario='coverage-network-controls',
			)
		)
		post_control_click = await adapter._click(ref=post_control_ref, scenario='coverage-network-controls')
		captured_request = _safe_json_loads(await request_task) or {}
		captured_response = _safe_json_loads(await response_task) or {}
		inspected_entry = (
			_safe_json_loads(
				await adapter._inspect_network_entry(
					url_substring='/api/submit',
					method='POST',
					include_headers=True,
					scenario='coverage-network-controls',
				)
			)
			or {}
		)
		request_id = inspected_entry.get('request_id')
		if request_id:
			replay_payload = (
				_safe_json_loads(
					await adapter._replay_request(
						request_id=str(request_id),
						body=json.dumps({'x': 2}),
						headers={'Content-Type': 'application/json', 'X-Replay': 'yes'},
						scenario='coverage-network-controls',
					)
				)
				or {}
			)
	add_mock_payload = (
		_safe_json_loads(
			await adapter._add_network_mock(
				url_substring='/api/data',
				action='fulfill',
				status=200,
				headers={'Content-Type': 'text/plain'},
				body='mocked data',
				scenario='coverage-network-controls',
			)
		)
		or {}
	)
	mock_id = add_mock_payload.get('mock_id')
	list_before_mock = _safe_json_loads(await adapter._list_network_mocks(scenario='coverage-network-controls')) or []
	mocked_result = await adapter._evaluate(
		'(async function(){ const response = await fetch("/api/data"); return await response.text(); })()',
		scenario='coverage-network-controls',
	)
	list_after_mock = list_before_mock
	for _ in range(20):
		parsed_mocks = _safe_json_loads(await adapter._list_network_mocks(scenario='coverage-network-controls'))
		if isinstance(parsed_mocks, list):
			list_after_mock = parsed_mocks
		if list_after_mock and list_after_mock[0].get('match_count', 0) >= 1:
			break
		await asyncio.sleep(0.1)
	remove_mock_payload = (
		_safe_json_loads(await adapter._remove_network_mock(mock_id=str(mock_id), scenario='coverage-network-controls'))
		if mock_id
		else {}
	)
	list_after_remove = _safe_json_loads(await adapter._list_network_mocks(scenario='coverage-network-controls')) or []
	conditions_payload = (
		_safe_json_loads(
			await adapter._set_network_conditions(offline=True, latency_ms=5.0, scenario='coverage-network-controls')
		)
		or {}
	)
	condition_list = _safe_json_loads(await adapter._get_network_conditions(scenario='coverage-network-controls')) or []
	offline_result = await adapter._evaluate(
		"(async function(){ try { await fetch('/release-status.json'); return 'unexpected-success'; } catch (error) { return 'offline:' + (error && error.name ? error.name : String(error)); } })()",
		scenario='coverage-network-controls',
	)
	reset_conditions_payload = (
		_safe_json_loads(await adapter._set_network_conditions(reset=True, scenario='coverage-network-controls')) or {}
	)
	conditions_after_reset = _safe_json_loads(await adapter._get_network_conditions(scenario='coverage-network-controls')) or []
	await adapter._wait(seconds=0.2, scenario='coverage-network-controls')
	online_result = await adapter._evaluate(
		'(async function(){ const response = await fetch("/release-status.json"); return response.status + ":" + await response.text(); })()',
		scenario='coverage-network-controls',
	)
	outcomes.append(
		_scenario_result(
			'coverage-network-controls',
			passed=(
				post_control_ref is not None
				and 'Clicked element' in post_control_click
				and captured_request.get('method') == 'POST'
				and '/api/submit' in str(captured_request.get('url', ''))
				and '/api/submit' in str(captured_response.get('url', ''))
				and bool(inspected_entry.get('request_id'))
				and inspected_entry.get('method') == 'POST'
				and '/api/submit' in str(inspected_entry.get('url', ''))
				and int(replay_payload.get('status') or 0) >= 400
				and mock_id is not None
				and len(list_before_mock) == 1
				and list_before_mock[0].get('mock_id') == mock_id
				and 'mocked data' in mocked_result
				and bool(list_after_mock)
				and list_after_mock[0].get('match_count', 0) >= 1
				and remove_mock_payload.get('removed') == 1
				and list_after_remove == []
				and conditions_payload.get('offline') is True
				and conditions_payload.get('reset') is False
				and len(condition_list) == 1
				and condition_list[0].get('offline') is True
				and 'offline:' in offline_result
				and 'unexpected-success' not in offline_result
				and reset_conditions_payload.get('reset') is True
				and reset_conditions_payload.get('offline') is False
				and conditions_after_reset == []
				and '200:' in online_result
				and 'Release summary ready for export.' in online_result
			),
			mock_id=mock_id,
			request_id=inspected_entry.get('request_id'),
		)
	)

	new_tab_result = await adapter._new_tab(f'{base_url}/release-readiness.html', scenario='coverage-release-readiness')
	tabs_after_new = (_safe_json_loads(await adapter._list_tabs(scenario='coverage-release-readiness')) or {}).get('tabs', [])
	set_viewport_result = await adapter._set_viewport(
		width=1024,
		height=720,
		device_scale_factor=1.0,
		scenario='coverage-release-readiness',
	)
	stable_result = await adapter._wait_for_stable_dom(
		timeout_seconds=5.0,
		quiet_ms=250,
		scenario='coverage-release-readiness',
	)
	status_html = await adapter._get_html('#status', scenario='coverage-release-readiness')
	viewport_width = await adapter._evaluate(
		'(function(){ return window.innerWidth; })()',
		scenario='coverage-release-readiness',
	)
	probe_fetch = await adapter._evaluate(
		'(async function(){ const response = await fetch("/release-status.json"); return response.status; })()',
		scenario='coverage-release-readiness',
	)
	await adapter._wait(seconds=0.1, scenario='coverage-release-readiness')
	release_state, _ = await adapter.get_browser_state_json(mode='auto', scenario='coverage-release-readiness')
	download_ref = BENCH.find_element_ref(release_state, 'Download release summary')
	runbook_ref = BENCH.find_element_ref(release_state, 'Open release runbook')
	download_href = (
		await adapter._get_attribute('href', ref=download_ref, scenario='coverage-release-readiness')
		if download_ref is not None
		else 'missing-ref'
	)
	runbook_href = (
		await adapter._get_attribute('href', ref=runbook_ref, scenario='coverage-release-readiness')
		if runbook_ref is not None
		else 'missing-ref'
	)
	trace_start = await adapter._start_trace(scenario='coverage-release-readiness')
	download_click = (
		await adapter._click(ref=download_ref, scenario='coverage-release-readiness')
		if download_ref is not None
		else 'missing-ref'
	)
	downloads_payload: list[dict[str, Any]] = []
	for _ in range(20):
		raw_downloads = await adapter._get_downloads(scenario='coverage-release-readiness')
		parsed_downloads = _safe_json_loads(raw_downloads)
		if isinstance(parsed_downloads, list) and parsed_downloads:
			downloads_payload = parsed_downloads
			break
		await asyncio.sleep(0.1)
	trace_nav = await adapter._navigate(f'{base_url}/docs/runbook.html', scenario='coverage-release-readiness')
	await adapter._wait(seconds=0.2, scenario='coverage-release-readiness')
	trace_stop = await adapter._stop_trace(scenario='coverage-release-readiness')
	trace_events = _safe_json_loads(trace_stop)
	console_before = (
		_safe_json_loads(await adapter._get_console_logs(max_entries=20, scenario='coverage-release-readiness')) or []
	)
	network_before = _safe_json_loads(await adapter._get_network_log(max_entries=20, scenario='coverage-release-readiness')) or []
	clear_logs = await adapter._clear_logs(console=True, network=True, scenario='coverage-release-readiness')
	console_after = _safe_json_loads(await adapter._get_console_logs(max_entries=20, scenario='coverage-release-readiness')) or []
	network_after = _safe_json_loads(await adapter._get_network_log(max_entries=20, scenario='coverage-release-readiness')) or []
	pdf_result = await adapter._save_as_pdf(file_name='release-readiness.pdf', scenario='coverage-release-readiness')
	confirm_nav = await adapter._navigate(f'{base_url}/confirm-dialog.html', scenario='coverage-release-readiness')
	confirm_state, _ = await adapter.get_browser_state_json(mode='auto', scenario='coverage-release-readiness')
	confirm_delete_ref = BENCH.find_element_ref(confirm_state, 'Delete branch')
	confirm_click = (
		await adapter._click(ref=confirm_delete_ref, scenario='coverage-release-readiness')
		if confirm_delete_ref is not None
		else 'missing-ref'
	)
	confirm_status_html = await adapter._get_html('#status', scenario='coverage-release-readiness')
	handle_dialog_result = await adapter._handle_dialog(accept=True, scenario='coverage-release-readiness')
	outcomes.append(
		_scenario_result(
			'coverage-release-readiness',
			passed=(
				'Opened new tab:' in new_tab_result
				and len(tabs_after_new) >= 2
				and 'Viewport set to 1024x720' in set_viewport_result
				and 'DOM stable after quiet period' in stable_result
				and 'Release summary ready' in status_html
				and '1024' in str(viewport_width)
				and '200' in str(probe_fetch)
				and '/downloads/release-summary.txt' in download_href
				and '/docs/runbook.html' in runbook_href
				and 'Trace started' in trace_start
				and 'Clicked element' in download_click
				and any(entry.get('name') == 'release-summary.txt' for entry in downloads_payload if isinstance(entry, dict))
				and 'Navigated to:' in trace_nav
				and isinstance(trace_events, list)
				and any(
					'release readiness page loaded' in str(entry.get('text', ''))
					for entry in console_before
					if isinstance(entry, dict)
				)
				and any(
					'/release-status.json' in str(entry.get('url', '')) for entry in network_before if isinstance(entry, dict)
				)
				and 'Cleared:' in clear_logs
				and console_after == []
				and network_after == []
				and 'release-readiness.pdf' in pdf_result
				and 'Navigated to:' in confirm_nav
				and confirm_delete_ref is not None
				and 'Clicked element' in confirm_click
				and 'Deleted' in confirm_status_html
				and 'Dialog already auto-handled by runtime:' in handle_dialog_result
				and 'Delete this branch?' in handle_dialog_result
			),
			tab_count=len(tabs_after_new),
			download_ref_found=download_ref is not None,
			runbook_ref_found=runbook_ref is not None,
			download_count=len(downloads_payload),
			pdf_result=pdf_result,
			confirm_delete_ref_found=confirm_delete_ref is not None,
			confirm_click=confirm_click,
			handle_dialog_result=handle_dialog_result,
		)
	)

	close_all_result = await adapter._close_all_sessions(scenario='coverage-close-all')
	post_close_all = await adapter._list_sessions(scenario='coverage-close-all')
	outcomes.append(
		_scenario_result(
			'coverage-close-all',
			passed='No active browser sessions' in post_close_all,
			close_all=close_all_result,
		)
	)
	return outcomes


async def _run_fixture_suite(
	adapter: MCPStdioAdapter, fixtures: list[Any], base_url: str
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
	fixture_results: dict[str, Any] = {}
	outcomes: list[dict[str, Any]] = []
	for fixture in fixtures:
		result = await BENCH.run_fixture(adapter, fixture, base_url)
		fixture_results[fixture.slug] = result
		extract_ok = True
		if result.get('extract_content', {}).get('deterministic'):
			extract_ok = result['extract_content']['deterministic']['match']['recall'] >= 1.0
		action_ok = result.get('action_reliability', {}).get('passed', True)
		state_ok = (not fixture.expect_unchanged_delta) or result['state']['unchanged_delta']['changed'] is False
		outcomes.append(
			_scenario_result(
				f'fixture:{fixture.slug}',
				passed=state_ok and extract_ok and action_ok,
				latency_ms=result.get('action_reliability', {}).get('latency_ms'),
				action_passed=result.get('action_reliability', {}).get('passed'),
			)
		)
	return fixture_results, outcomes


async def _dense_catalog_profile(adapter: MCPStdioAdapter, base_url: str, iteration: int) -> dict[str, Any]:
	scenario = f'heavy:dense-catalog:{iteration}'
	await adapter._navigate(f'{base_url}/dense-catalog.html', scenario=scenario)
	min_state, _ = await adapter.get_browser_state_json(mode='min', scenario=scenario)
	search = await adapter._search_page('Featured Product 48', scenario=scenario)
	find = _parse_find_elements_output(await adapter._find_elements('article.card a', max_results=8, scenario=scenario))
	await adapter._scroll(direction='down', pages=1.5, scenario=scenario)
	await adapter._scroll(direction='up', pages=0.5, scenario=scenario)
	html = await adapter._get_html('section.catalog', scenario=scenario)
	eval_result = await adapter._evaluate(
		'(function(){ return document.querySelectorAll("article.card").length; })()', scenario=scenario
	)
	# The scroll actions above intentionally change the page state, so the unchanged delta
	# check must start from a fresh post-action hash rather than the pre-scroll baseline.
	post_action_state, _ = await adapter.get_browser_state_json(mode='auto', scenario=scenario)
	delta_state, _ = await adapter.get_browser_state_json(
		mode='auto',
		since_hash=post_action_state['state_hash'],
		scenario=scenario,
	)
	return _scenario_result(
		scenario,
		passed=(
			min_state.get('interactive_element_count', 0) > 0
			and 'Featured Product 48' in search
			and len(find) > 0
			and 'Featured Product 1' in html
			and '72' in str(eval_result)
			and delta_state.get('changed') is False
		),
		call_count=9,
	)


async def _workflow_form_profile(adapter: MCPStdioAdapter, base_url: str, iteration: int) -> dict[str, Any]:
	scenario = f'heavy:workflow-form:{iteration}'
	await adapter._navigate(f'{base_url}/workflow-form.html', scenario=scenario)
	state, _ = await adapter.get_browser_state_json(mode='auto', scenario=scenario)
	project_ref = BENCH.find_element_ref(state, 'Project name')
	repo_ref = BENCH.find_element_ref(state, 'Repository URL')
	env_ref = BENCH.find_element_ref(state, 'Environment')
	deploy_ref = BENCH.find_element_ref(state, 'Deploy preview')
	options = await adapter._get_dropdown_options(ref=env_ref, scenario=scenario)
	select = await adapter._select_option(text='Production', ref=env_ref, scenario=scenario)
	type_project = await adapter._type_text(ref=project_ref, text=f'mcp-bench-{iteration}', scenario=scenario)
	type_repo = await adapter._type_text(ref=repo_ref, text='https://github.com/agentyc/agentyc', scenario=scenario)
	click = await adapter._click(ref=deploy_ref, scenario=scenario)
	find = await adapter._find_elements('input, select, button', max_results=10, scenario=scenario)
	extract = await adapter._extract_content('list all form fields on the page', scenario=scenario)
	wait = await adapter._wait_for_element(text='Open validation help', timeout_seconds=2, scenario=scenario)
	return _scenario_result(
		scenario,
		passed=(
			'Preview' in options
			and 'Selected option' in select
			and not type_project.startswith('Error')
			and not type_repo.startswith('Error')
			and not click.startswith('Error')
			and 'Repository URL' in find
			and 'Project name' in extract
			and 'Element ' in wait
			and 'appeared' in wait
		),
		call_count=8,
	)


async def _docs_profile(adapter: MCPStdioAdapter, base_url: str, iteration: int) -> dict[str, Any]:
	scenario = f'heavy:long-docs:{iteration}'
	await adapter._navigate(f'{base_url}/long-docs.html', scenario=scenario)
	state, _ = await adapter.get_browser_state_json(mode='auto', scenario=scenario)
	search_ref = BENCH.find_element_ref(state, 'Search documentation')
	await adapter._type_text(ref=search_ref, text='sdk', scenario=scenario)
	await adapter._press_key('Tab', scenario=scenario)
	focused = await adapter._get_focused_element(scenario=scenario)
	search_result = await adapter._search_page('SDK quickstart 1', scenario=scenario)
	await adapter._scroll_to_text('Documentation topic 34', scenario=scenario)
	extract = await adapter._extract_content('list all links on the page', extract_links=True, scenario=scenario)
	html = await adapter._get_html('nav[aria-label="Primary docs navigation"]', scenario=scenario)
	eval_result = await adapter._evaluate('(function(){ return location.href; })()', scenario=scenario)
	return _scenario_result(
		scenario,
		passed=(
			not focused.startswith('Error')
			and 'SDK quickstart 1' in search_result
			and 'Authentication' in extract
			and 'API Reference' in html
			and '/long-docs.html' in eval_result
		),
		call_count=8,
	)


async def _interaction_profile(adapter: MCPStdioAdapter, base_url: str, iteration: int) -> dict[str, Any]:
	scenario = f'heavy:interaction:{iteration}'
	await adapter._navigate(f'{base_url}/contenteditable-compose.html', scenario=scenario)
	state, _ = await adapter.get_browser_state_json(mode='auto', scenario=scenario)
	editor_ref = BENCH.find_element_ref(state, 'Issue body editor')
	publish_ref = BENCH.find_element_ref(state, 'Publish comment')
	await adapter._type_text(ref=editor_ref, text=f'iteration {iteration}', scenario=scenario)
	double = await adapter._double_click(ref=publish_ref, scenario=scenario)
	click = await adapter._click(ref=publish_ref, scenario=scenario)
	status_html = await adapter._get_html('#status', scenario=scenario)
	await adapter._navigate(f'{base_url}/right-click-menu.html', scenario=scenario)
	menu_state, _ = await adapter.get_browser_state_json(mode='auto', scenario=scenario)
	button_ref = BENCH.find_element_ref(menu_state, 'Options')
	right_click = await adapter._right_click(ref=button_ref, scenario=scenario)
	menu_exists = await adapter._evaluate("(function(){ return document.getElementById('menu') !== null; })()", scenario=scenario)
	await adapter._navigate(f'{base_url}/drag-sortable.html', scenario=scenario)
	drag_state, _ = await adapter.get_browser_state_json(mode='auto', scenario=scenario)
	alpha_ref = BENCH.find_element_ref(drag_state, 'Task Alpha')
	gamma_ref = BENCH.find_element_ref(drag_state, 'Task Gamma')
	drag = await adapter._drag_to(source_ref=alpha_ref, target_ref=gamma_ref, scenario=scenario)
	return _scenario_result(
		scenario,
		passed=(
			'Double-clicked' in double
			and 'Clicked element' in click
			and f'iteration {iteration}' in status_html
			and 'Right-clicked' in right_click
			and 'true' in menu_exists.lower()
			and 'Dragged' in drag
		),
		call_count=10,
	)


async def _network_profile(adapter: MCPStdioAdapter, base_url: str, iteration: int) -> dict[str, Any]:
	scenario = f'heavy:network:{iteration}'
	await adapter._navigate(f'{base_url}/network-log.html', scenario=scenario)
	state, _ = await adapter.get_browser_state_json(mode='auto', scenario=scenario)
	fetch_ref = BENCH.find_element_ref(state, 'Fetch data')
	post_ref = BENCH.find_element_ref(state, 'Post data')
	await adapter._click(ref=fetch_ref, scenario=scenario)
	await adapter._click(ref=post_ref, scenario=scenario)
	wait_idle = await adapter._wait_for_network_idle(timeout_seconds=3, idle_duration_ms=200, scenario=scenario)
	network_log = _safe_json_loads(await adapter._get_network_log(max_entries=50, scenario=scenario)) or []
	await adapter._navigate(f'{base_url}/console-log-capture.html', scenario=scenario)
	await adapter._wait(seconds=0.1, scenario=scenario)
	console_before = _safe_json_loads(await adapter._get_console_logs(level='all', max_entries=20, scenario=scenario)) or []
	console_state, _ = await adapter.get_browser_state_json(mode='auto', scenario=scenario)
	trigger_ref = BENCH.find_element_ref(console_state, 'Trigger log')
	await adapter._click(ref=trigger_ref, scenario=scenario)
	console_errors = _safe_json_loads(await adapter._get_console_logs(level='error', max_entries=20, scenario=scenario)) or []
	return _scenario_result(
		scenario,
		passed=(
			('network idle' in wait_idle.lower() or 'timed out' in wait_idle.lower())
			and any((entry.get('url') or '').endswith('/api/data') for entry in network_log if isinstance(entry, dict))
			and any((entry.get('url') or '').endswith('/api/submit') for entry in network_log if isinstance(entry, dict))
			and any('page loaded' in str(entry.get('text', '')) for entry in console_before if isinstance(entry, dict))
			and any('test error' in str(entry.get('text', '')) for entry in console_errors if isinstance(entry, dict))
		),
		call_count=8,
	)


HEAVY_PROFILES = [
	_dense_catalog_profile,
	_workflow_form_profile,
	_docs_profile,
	_interaction_profile,
	_network_profile,
]


async def _issue_triage_workflow(adapter: MCPStdioAdapter, base_url: str) -> dict[str, Any]:
	scenario = 'workflow:issue-triage'
	await adapter._navigate(f'{base_url}/issue-queue.html', scenario=scenario)
	origin_tabs = (_safe_json_loads(await adapter._list_tabs(scenario=scenario)) or {}).get('tabs', [])
	state, _ = await adapter.get_browser_state_json(mode='auto', scenario=scenario)
	queue_tab_id = state.get('current_tab_id') or (state.get('current_tab') or {}).get('tab_id')
	if queue_tab_id is None and origin_tabs:
		queue_tab_id = origin_tabs[0]['tab_id']
	search_ref = BENCH.find_element_ref(state, 'Search issues')
	severity_ref = BENCH.find_element_ref(state, 'Filter severity')
	issue_ref = BENCH.find_element_ref(state, 'Build #482 failing on main')
	if search_ref is None or severity_ref is None or issue_ref is None:
		return {
			'passed': False,
			'error': f'Could not resolve issue queue controls: search={search_ref}, severity={severity_ref}, issue={issue_ref}',
		}
	type_result = await adapter._type_text(ref=search_ref, text='build failing', scenario=scenario)
	select_result = await adapter._select_option(text='Critical', ref=severity_ref, scenario=scenario)
	queue_extract = await adapter._extract_content('extract table rows from the issue queue table', scenario=scenario)
	queue_find = _parse_find_elements_output(
		await adapter._find_elements('table a', attributes=['href'], max_results=10, scenario=scenario)
	)
	issue_link = next(
		(
			(item.get('attrs', {}) or {}).get('href')
			for item in queue_find
			if isinstance(item, dict) and ((item.get('attrs', {}) or {}).get('href') or '').endswith('/issues/build-482.html')
		),
		None,
	)
	open_issue = await adapter._navigate(f'{base_url}/issues/build-482.html', new_tab=True, scenario=scenario)
	tabs = (_safe_json_loads(await adapter._list_tabs(scenario=scenario)) or {}).get('tabs', [])
	issue_tab = next((tab for tab in tabs if tab.get('url', '').endswith('/issues/build-482.html')), None)
	if issue_tab is None:
		return {
			'passed': False,
			'error': 'Issue detail tab was not created',
			'open_issue': open_issue,
			'tab_count': len(tabs),
		}
	switch_issue = await adapter._switch_tab(issue_tab['tab_id'], scenario=scenario)
	issue_html = await adapter._get_html('main', scenario=scenario)
	issue_state, _ = await adapter.get_browser_state_json(mode='auto', scenario=scenario)
	checklist_ref = BENCH.find_element_ref(issue_state, 'Open incident checklist')
	if checklist_ref is None:
		return {
			'passed': False,
			'error': 'Could not resolve incident checklist link from issue detail',
			'switch_issue': switch_issue,
		}
	open_checklist = await adapter._navigate(f'{base_url}/triage-checklist.html', new_tab=True, scenario=scenario)
	tabs_after = (_safe_json_loads(await adapter._list_tabs(scenario=scenario)) or {}).get('tabs', [])
	checklist_tab = next((tab for tab in tabs_after if tab.get('url', '').endswith('/triage-checklist.html')), None)
	if checklist_tab is None:
		return {
			'passed': False,
			'error': 'Checklist tab was not created',
			'open_checklist': open_checklist,
			'tab_count': len(tabs_after),
		}
	switch_checklist = await adapter._switch_tab(checklist_tab['tab_id'], scenario=scenario)
	checklist_extract = await adapter._extract_content('list all checklist items on the page', scenario=scenario)
	screenshot_meta, screenshot_b64 = await adapter._screenshot(full_page=False, scenario=scenario)
	if queue_tab_id is not None:
		await adapter._switch_tab(queue_tab_id, scenario=scenario)
	close_checklist = await adapter._close_tab(checklist_tab['tab_id'], scenario=scenario)
	close_issue = await adapter._close_tab(issue_tab['tab_id'], scenario=scenario)
	passed = (
		not type_result.startswith('Error')
		and 'Selected option' in select_result
		and 'Build #482 failing on main' in queue_extract
		and any(
			((item.get('attrs', {}) or {}).get('href') or '').endswith('/issues/build-482.html')
			for item in queue_find
			if isinstance(item, dict)
		)
		and 'Opened new tab' in open_issue
		and 'Switched to tab' in switch_issue
		and 'Severity: Critical' in issue_html
		and 'Opened new tab' in open_checklist
		and 'Switched to tab' in switch_checklist
		and 'Download the failed job logs' in checklist_extract
		and screenshot_b64 is not None
		and 'Closed tab' in close_checklist
		and 'Closed tab' in close_issue
	)
	return {
		'passed': passed,
		'queue_issue_count': len(queue_find),
		'issue_link_found': issue_link is not None,
		'issue_tab_url': issue_tab.get('url'),
		'checklist_tab_url': checklist_tab.get('url'),
		'screenshot_meta': screenshot_meta,
	}


async def _docs_tabs_workflow(adapter: MCPStdioAdapter, base_url: str) -> dict[str, Any]:
	scenario = 'workflow:docs-tabs'
	await adapter._navigate(f'{base_url}/search-results.html', scenario=scenario)
	origin_tabs = (_safe_json_loads(await adapter._list_tabs(scenario=scenario)) or {}).get('tabs', [])
	state, _ = await adapter.get_browser_state_json(mode='auto', scenario=scenario)
	search_tab_id = state.get('current_tab_id') or (state.get('current_tab') or {}).get('tab_id')
	if search_tab_id is None and origin_tabs:
		search_tab_id = origin_tabs[0]['tab_id']
	search_ref = BENCH.find_element_ref(state, 'Search documentation')
	auth_ref = BENCH.find_element_ref(state, 'Auth quickstart')
	webhooks_ref = BENCH.find_element_ref(state, 'Webhook retries')
	if search_ref is None or auth_ref is None or webhooks_ref is None:
		return {
			'passed': False,
			'error': f'Could not resolve docs search controls: search={search_ref}, auth={auth_ref}, webhooks={webhooks_ref}',
		}
	type_result = await adapter._type_text(ref=search_ref, text='auth', scenario=scenario)
	search_result = await adapter._search_page('Auth quickstart', scenario=scenario)
	results_extract = await adapter._extract_content('extract the search results on the page', scenario=scenario)
	result_links = _parse_find_elements_output(
		await adapter._find_elements('ol[aria-label="Search results"] a', attributes=['href'], max_results=10, scenario=scenario)
	)
	auth_link = next(
		(
			(item.get('attrs', {}) or {}).get('href')
			for item in result_links
			if isinstance(item, dict) and ((item.get('attrs', {}) or {}).get('href') or '').endswith('/docs/authentication.html')
		),
		None,
	)
	webhooks_link = next(
		(
			(item.get('attrs', {}) or {}).get('href')
			for item in result_links
			if isinstance(item, dict) and ((item.get('attrs', {}) or {}).get('href') or '').endswith('/docs/webhooks.html')
		),
		None,
	)
	open_auth = await adapter._navigate(f'{base_url}/docs/authentication.html', new_tab=True, scenario=scenario)
	open_webhooks = await adapter._navigate(f'{base_url}/docs/webhooks.html', new_tab=True, scenario=scenario)
	tabs = (_safe_json_loads(await adapter._list_tabs(scenario=scenario)) or {}).get('tabs', [])
	auth_tab = next((tab for tab in tabs if tab.get('url', '').endswith('/docs/authentication.html')), None)
	webhooks_tab = next((tab for tab in tabs if tab.get('url', '').endswith('/docs/webhooks.html')), None)
	if auth_tab is None or webhooks_tab is None:
		return {
			'passed': False,
			'error': f'Documentation tabs missing: auth={auth_tab is not None}, webhooks={webhooks_tab is not None}',
			'open_auth': open_auth,
			'open_webhooks': open_webhooks,
			'tab_count': len(tabs),
		}
	switch_auth = await adapter._switch_tab(auth_tab['tab_id'], scenario=scenario)
	auth_html = await adapter._get_html('main', scenario=scenario)
	auth_eval = await adapter._evaluate('(function(){ return location.pathname; })()', scenario=scenario)
	switch_webhooks = await adapter._switch_tab(webhooks_tab['tab_id'], scenario=scenario)
	webhooks_html = await adapter._get_html('main', scenario=scenario)
	webhooks_links = _parse_find_elements_output(
		await adapter._find_elements('a', attributes=['href'], max_results=10, scenario=scenario)
	)
	if search_tab_id is not None:
		await adapter._switch_tab(search_tab_id, scenario=scenario)
	close_auth = await adapter._close_tab(auth_tab['tab_id'], scenario=scenario)
	close_webhooks = await adapter._close_tab(webhooks_tab['tab_id'], scenario=scenario)
	passed = (
		not type_result.startswith('Error')
		and 'Auth quickstart' in search_result
		and '/docs/authentication.html' in results_extract
		and '/docs/webhooks.html' in results_extract
		and 'Opened new tab' in open_auth
		and 'Opened new tab' in open_webhooks
		and 'Switched to tab' in switch_auth
		and 'session cookies' in auth_html
		and '/docs/authentication.html' in auth_eval
		and 'Switched to tab' in switch_webhooks
		and 'network logs' in webhooks_html
		and any(
			((item.get('attrs', {}) or {}).get('href') or '').endswith('/triage-checklist.html')
			for item in webhooks_links
			if isinstance(item, dict)
		)
		and 'Closed tab' in close_auth
		and 'Closed tab' in close_webhooks
	)
	return {
		'passed': passed,
		'tab_count': len(tabs),
		'auth_link_found': auth_link is not None,
		'webhooks_link_found': webhooks_link is not None,
		'auth_tab_url': auth_tab.get('url'),
		'webhooks_tab_url': webhooks_tab.get('url'),
	}


async def _release_ops_workflow(adapter: MCPStdioAdapter, base_url: str, scratch_dir: Path) -> dict[str, Any]:
	scenario = 'workflow:release-ops'
	host = base_url.replace('http://', '').split(':', 1)[0]
	state_file = scratch_dir / 'release-workflow-state.json'
	upload_file = scratch_dir / 'release-notes.txt'
	upload_file.write_text('release notes artifact for stdio workflow benchmark', encoding='utf-8')

	await adapter._navigate(f'{base_url}/cookie-auth.html', scenario=scenario)
	set_cookie = await adapter._set_cookies([{'name': 'session', 'value': 'abc123', 'domain': host}], scenario=scenario)
	await adapter._refresh(scenario=scenario)
	logged_in_html = await adapter._get_html('#auth', scenario=scenario)
	save_state = await adapter._save_state(str(state_file), scenario=scenario)
	clear_cookie = await adapter._clear_cookies('session', scenario=scenario)
	await adapter._refresh(scenario=scenario)
	logged_out_html = await adapter._get_html('#auth', scenario=scenario)
	load_state = await adapter._load_state(str(state_file), scenario=scenario)
	await adapter._refresh(scenario=scenario)
	restored_html = await adapter._get_html('#auth', scenario=scenario)

	await adapter._navigate(f'{base_url}/modal-wizard.html', scenario=scenario)
	modal_state, _ = await adapter.get_browser_state_json(mode='auto', scenario=scenario)
	dismiss_ref = BENCH.find_element_ref(modal_state, 'Dismiss blocking modal')
	if dismiss_ref is None:
		return {'passed': False, 'error': 'Could not resolve blocking modal dismiss button'}
	dismiss_result = await adapter._click(ref=dismiss_ref, scenario=scenario)
	modal_state, _ = await adapter.get_browser_state_json(mode='auto', scenario=scenario)
	deploy_ref = BENCH.find_element_ref(modal_state, 'Run deployment')
	if deploy_ref is None:
		return {'passed': False, 'error': 'Could not resolve run deployment button after dismissing modal'}
	deploy_result = await adapter._click(ref=deploy_ref, scenario=scenario)
	modal_status = await adapter._get_html('#status', scenario=scenario)

	await adapter._navigate(f'{base_url}/delayed-release.html', scenario=scenario)
	release_state, _ = await adapter.get_browser_state_json(mode='auto', scenario=scenario)
	release_name_ref = BENCH.find_element_ref(release_state, 'Release name', tag='input')
	publish_ref = BENCH.find_element_ref(release_state, 'Publish release')
	if release_name_ref is None or publish_ref is None:
		return {
			'passed': False,
			'error': f'Could not resolve release controls: name={release_name_ref}, publish={publish_ref}',
		}
	type_release = await adapter._type_text(ref=release_name_ref, text='v2.4.0', scenario=scenario)
	await adapter._wait(seconds=0.4, scenario=scenario)
	publish_html_before = await adapter._get_html('#publish', scenario=scenario)
	status_html_before = await adapter._get_html('#status', scenario=scenario)
	publish_result = await adapter._click(ref=publish_ref, scenario=scenario)
	publish_status = await adapter._get_html('#status', scenario=scenario)

	await adapter._navigate(f'{base_url}/upload.html', scenario=scenario)
	upload_state, _ = await adapter.get_browser_state_json(mode='auto', scenario=scenario)
	upload_ref = BENCH.find_element_ref(upload_state, 'Upload document')
	if upload_ref is None:
		return {'passed': False, 'error': 'Could not resolve upload control'}
	upload_result = await adapter._upload_file(str(upload_file), ref=upload_ref, scenario=scenario)
	upload_status = await adapter._get_html('#status', scenario=scenario)

	await adapter._navigate(f'{base_url}/hover-dropdown.html', scenario=scenario)
	dropdown_state, _ = await adapter.get_browser_state_json(mode='auto', scenario=scenario)
	menu_ref = BENCH.find_element_ref(dropdown_state, 'Deploy menu', tag='div') or BENCH.find_element_ref(
		dropdown_state, 'Deploy'
	)
	if menu_ref is None:
		return {'passed': False, 'error': 'Could not resolve deploy menu'}
	hover_result = await adapter._hover(ref=menu_ref, scenario=scenario)
	dropdown_state, _ = await adapter.get_browser_state_json(mode='auto', scenario=scenario)
	deploy_prod_ref = BENCH.find_element_ref(dropdown_state, 'Deploy to Production')
	if deploy_prod_ref is None:
		await adapter._click(ref=menu_ref, scenario=scenario)
		dropdown_state, _ = await adapter.get_browser_state_json(mode='auto', scenario=scenario)
		deploy_prod_ref = BENCH.find_element_ref(dropdown_state, 'Deploy to Production')
	if deploy_prod_ref is None:
		return {'passed': False, 'error': 'Could not reveal deploy-to-production action', 'hover_result': hover_result}
	deploy_prod = await adapter._click(ref=deploy_prod_ref, scenario=scenario)
	dropdown_status = await adapter._get_html('#status', scenario=scenario)

	await adapter._navigate(f'{base_url}/network-log.html', scenario=scenario)
	network_state, _ = await adapter.get_browser_state_json(mode='auto', scenario=scenario)
	fetch_ref = BENCH.find_element_ref(network_state, 'Fetch data')
	post_ref = BENCH.find_element_ref(network_state, 'Post data')
	if fetch_ref is None or post_ref is None:
		return {'passed': False, 'error': f'Could not resolve network controls: fetch={fetch_ref}, post={post_ref}'}
	await adapter._click(ref=fetch_ref, scenario=scenario)
	await adapter._click(ref=post_ref, scenario=scenario)
	wait_idle = await adapter._wait_for_network_idle(timeout_seconds=3, idle_duration_ms=200, scenario=scenario)
	network_log = _safe_json_loads(await adapter._get_network_log(max_entries=50, scenario=scenario)) or []

	await adapter._navigate(f'{base_url}/console-log-capture.html', scenario=scenario)
	await adapter._wait(seconds=0.1, scenario=scenario)
	console_before = _safe_json_loads(await adapter._get_console_logs(level='all', max_entries=20, scenario=scenario)) or []
	console_state, _ = await adapter.get_browser_state_json(mode='auto', scenario=scenario)
	trigger_ref = BENCH.find_element_ref(console_state, 'Trigger log')
	if trigger_ref is None:
		return {'passed': False, 'error': 'Could not resolve Trigger log button'}
	trigger_log = await adapter._click(ref=trigger_ref, scenario=scenario)
	await adapter._wait(seconds=0.1, scenario=scenario)
	console_errors = _safe_json_loads(await adapter._get_console_logs(level='error', max_entries=20, scenario=scenario)) or []

	passed = (
		'Browser state saved to:' in save_state
		and 'Browser state loaded from:' in load_state
		and 'logged-in' in logged_in_html
		and 'logged-out' in logged_out_html
		and 'logged-in' in restored_html
		and not set_cookie.startswith('Error')
		and not clear_cookie.startswith('Error')
		and 'Deployment started.' in modal_status
		and not dismiss_result.startswith('Error')
		and not deploy_result.startswith('Error')
		and not type_release.startswith('Error')
		and 'disabled' not in publish_html_before
		and 'Ready to publish.' in status_html_before
		and not publish_result.startswith('Error')
		and 'Release published.' in publish_status
		and 'uploaded file' in upload_result.lower()
		and upload_file.name in upload_status
		and not hover_result.startswith('Error')
		and 'Deploying to production' in dropdown_status
		and 'Clicked element' in deploy_prod
		and ('network idle' in wait_idle.lower() or 'timed out' in wait_idle.lower())
		and any((entry.get('url') or '').endswith('/api/data') for entry in network_log if isinstance(entry, dict))
		and any((entry.get('url') or '').endswith('/api/submit') for entry in network_log if isinstance(entry, dict))
		and any('page loaded' in str(entry.get('text', '')) for entry in console_before if isinstance(entry, dict))
		and any('test error' in str(entry.get('text', '')) for entry in console_errors if isinstance(entry, dict))
		and 'Clicked element' in trigger_log
	)
	return {
		'passed': passed,
		'state_file': str(state_file),
		'network_request_count': len(network_log),
		'console_error_count': len(console_errors),
	}


async def _run_end_to_end_workflows(adapter: MCPStdioAdapter, base_url: str, scratch_dir: Path) -> list[dict[str, Any]]:
	return [
		await _run_workflow_scenario(adapter, 'workflow:issue-triage', lambda: _issue_triage_workflow(adapter, base_url)),
		await _run_workflow_scenario(adapter, 'workflow:docs-tabs', lambda: _docs_tabs_workflow(adapter, base_url)),
		await _run_workflow_scenario(
			adapter, 'workflow:release-ops', lambda: _release_ops_workflow(adapter, base_url, scratch_dir)
		),
	]


async def _run_heavy_workload(adapter: MCPStdioAdapter, base_url: str, minimum_total_calls: int) -> list[dict[str, Any]]:
	outcomes: list[dict[str, Any]] = []
	iteration = 0
	while len(adapter.call_records) < minimum_total_calls:
		profile = HEAVY_PROFILES[iteration % len(HEAVY_PROFILES)]
		outcomes.append(await profile(adapter, base_url, iteration + 1))
		iteration += 1
	return outcomes


def _summarize_tool_metrics(records: list[ToolCallRecord]) -> dict[str, Any]:
	per_tool_latencies: dict[str, list[float]] = defaultdict(list)
	per_tool_counts: dict[str, Counter[str]] = defaultdict(Counter)
	per_tool_tokens: dict[str, list[int]] = defaultdict(list)
	for record in records:
		per_tool_latencies[record.tool_name].append(record.latency_ms)
		per_tool_counts[record.tool_name]['total'] += 1
		per_tool_counts[record.tool_name]['success'] += int(record.success)
		per_tool_counts[record.tool_name]['failure'] += int(not record.success)
		per_tool_tokens[record.tool_name].append(_estimate_tokens_from_text(record.result_preview))
	return {
		tool_name: {
			'total_calls': per_tool_counts[tool_name]['total'],
			'success_count': per_tool_counts[tool_name]['success'],
			'failure_count': per_tool_counts[tool_name]['failure'],
			'latency_ms': _latency_summary(per_tool_latencies[tool_name]),
			'estimated_tokens': {
				'avg': round(sum(per_tool_tokens[tool_name]) / len(per_tool_tokens[tool_name]), 1)
				if per_tool_tokens[tool_name]
				else 0.0,
				'max': max(per_tool_tokens[tool_name]) if per_tool_tokens[tool_name] else 0,
			},
		}
		for tool_name in sorted(per_tool_latencies)
	}


def _summarize_failures(records: list[ToolCallRecord], limit: int = 20) -> list[dict[str, Any]]:
	failures = [record for record in records if not record.success]
	return [
		{
			'tool_name': record.tool_name,
			'scenario': record.scenario,
			'error_text': record.error_text,
			'latency_ms': record.latency_ms,
			'result_preview': record.result_preview,
		}
		for record in failures[:limit]
	]


def _serialize_call_record(record: ToolCallRecord) -> dict[str, Any]:
	return {
		'tool_name': record.tool_name,
		'scenario': record.scenario,
		'latency_ms': record.latency_ms,
		'success': record.success,
		'error_text': record.error_text,
		'result_preview': record.result_preview,
	}


def _summarize_slowest_calls(records: list[ToolCallRecord], limit: int = 20) -> list[dict[str, Any]]:
	return [
		_serialize_call_record(record) for record in sorted(records, key=lambda record: record.latency_ms, reverse=True)[:limit]
	]


def _summarize_slowest_calls_by_tool(records: list[ToolCallRecord], per_tool_limit: int = 5) -> dict[str, list[dict[str, Any]]]:
	per_tool: dict[str, list[ToolCallRecord]] = defaultdict(list)
	for record in records:
		per_tool[record.tool_name].append(record)
	return {
		tool_name: _summarize_slowest_calls(tool_records, limit=per_tool_limit)
		for tool_name, tool_records in sorted(per_tool.items())
	}


async def _run_target(target: TargetConfig, minimum_total_calls: int, scratch_root: Path) -> dict[str, Any]:
	target_start = time.perf_counter()
	version = (
		importlib.metadata.version('agentyc')
		if target.name == 'source_tree'
		else _read_installed_version(target.python_path, target.cwd)
	)
	fixtures = BENCH.build_fixture_pack()
	served = BENCH.serve_fixture_pack(fixtures)
	try:
		root_path = Path(served.root.name)
		(root_path / 'upload.html').write_text(UPLOAD_HTML, encoding='utf-8')
		(root_path / 'frame-storage.html').write_text(FRAME_STORAGE_HTML, encoding='utf-8')
		(root_path / 'iframe-child.html').write_text(FRAME_STORAGE_CHILD_HTML, encoding='utf-8')
		async with MCPStdioAdapter(target) as adapter:
			coverage_outcomes: list[dict[str, Any]] = []
			fixture_results: dict[str, Any] = {}
			fixture_outcomes: list[dict[str, Any]] = []
			workflow_outcomes: list[dict[str, Any]] = []
			heavy_outcomes: list[dict[str, Any]] = []

			try:
				coverage_outcomes = await _run_remaining_tool_coverage(adapter, served.base_url, scratch_root / target.name)
			except Exception as exc:
				coverage_outcomes = [_scenario_result('coverage-suite', passed=False, error=str(exc))]

			try:
				fixture_results, fixture_outcomes = await _run_fixture_suite(adapter, fixtures, served.base_url)
			except Exception as exc:
				fixture_outcomes = [_scenario_result('fixture-suite', passed=False, error=str(exc))]

			try:
				workflow_outcomes = await _run_end_to_end_workflows(adapter, served.base_url, scratch_root / target.name)
			except Exception as exc:
				workflow_outcomes = [_scenario_result('workflow-suite', passed=False, error=str(exc))]

			try:
				heavy_outcomes = await _run_heavy_workload(adapter, served.base_url, minimum_total_calls)
			except Exception as exc:
				heavy_outcomes = [_scenario_result('heavy-suite', passed=False, error=str(exc))]

			cleanup_result = await adapter._close_all_sessions(scenario='cleanup')
			final_sessions = await adapter._list_sessions(scenario='cleanup')
			all_outcomes = coverage_outcomes + fixture_outcomes + workflow_outcomes + heavy_outcomes
			records = adapter.call_records
			successful_tools = {record.tool_name for record in records if record.success}
			failed_tools = {record.tool_name for record in records if not record.success}
			latencies = [record.latency_ms for record in records]
			outcomes_with_fixtures = coverage_outcomes + fixture_outcomes + workflow_outcomes + heavy_outcomes
			combined_preview = '\n'.join(record.result_preview for record in records)
			return {
				'target': target.name,
				'agentyc_version': version,
				'server_command': [target.command, *target.args],
				'server_cwd': str(target.cwd),
				'available_tool_count': len(adapter.available_tools),
				'available_tools': adapter.available_tools,
				'total_tool_calls': len(records),
				'overall_success_rate': round(sum(1 for record in records if record.success) / len(records), 4)
				if records
				else 0.0,
				'accuracy': _accuracy_from_outcomes(outcomes_with_fixtures),
				'precision': _scenario_precision(outcomes_with_fixtures),
				'overall_latency_ms': _latency_summary(latencies),
				'token_metrics': _context_utilization_from_text(combined_preview),
				'efficiency': _efficiency_summary(records, outcomes_with_fixtures),
				'per_tool': _summarize_tool_metrics(records),
				'successful_tool_coverage': sorted(successful_tools),
				'missing_successful_tool_coverage': [tool for tool in PUBLIC_TOOL_NAMES if tool not in successful_tools],
				'tools_with_failures': sorted(failed_tools),
				'scenario_outcomes': all_outcomes,
				'workflow_outcomes': workflow_outcomes,
				'workflow_summary': _summarize_workflows(workflow_outcomes),
				'fixture_results': fixture_results,
				'fixture_summary': BENCH.summarize_results(fixture_results),
				'scorecard': {
					'accuracy': _accuracy_from_outcomes(outcomes_with_fixtures),
					'precision': _scenario_precision(outcomes_with_fixtures),
					'estimated_tokens': _context_utilization_from_text(combined_preview),
					'efficiency': _efficiency_summary(records, outcomes_with_fixtures),
				},
				'slow_call_samples': _summarize_slowest_calls(records),
				'per_tool_slow_call_samples': _summarize_slowest_calls_by_tool(records),
				'failure_samples': _summarize_failures(records),
				'cleanup': {
					'close_all_result': cleanup_result,
					'final_sessions': final_sessions,
				},
				'duration_ms': round((time.perf_counter() - target_start) * 1000, 1),
			}
	finally:
		served.close()


def _read_installed_version(python_path: Path, cwd: Path) -> str:
	import subprocess

	command = [
		str(python_path),
		'-c',
		'import importlib.metadata; print(importlib.metadata.version("agentyc"))',
	]
	completed = subprocess.run(command, check=True, capture_output=True, text=True, cwd=cwd)
	return completed.stdout.strip()


def _build_targets(args: argparse.Namespace) -> list[TargetConfig]:
	targets: list[TargetConfig] = []
	for name in args.targets.split(','):
		trimmed = name.strip()
		if trimmed == 'source':
			targets.append(TargetConfig(name='source_tree', python_path=args.source_python, cwd=REPO_ROOT))
		elif trimmed == 'pypi':
			targets.append(TargetConfig(name='pypi_0_2_0', python_path=args.pypi_python, cwd=args.pypi_python.parent.parent))
		else:
			raise ValueError(f'Unknown target: {trimmed}')
	return targets


async def main_async(args: argparse.Namespace) -> dict[str, Any]:
	scratch_root = args.scratch_root
	scratch_root.mkdir(parents=True, exist_ok=True)
	for target_name in ('source_tree', 'pypi_0_2_0'):
		(scratch_root / target_name).mkdir(parents=True, exist_ok=True)
	results = {
		'generated_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
		'benchmark': 'mcp_stdio_e2e',
		'minimum_total_calls_per_target': args.minimum_total_calls,
		'public_tool_count': len(PUBLIC_TOOL_NAMES),
		'public_tools': PUBLIC_TOOL_NAMES,
		'targets': {},
	}
	for target in _build_targets(args):
		results['targets'][target.name] = await _run_target(target, args.minimum_total_calls, scratch_root)
	return results


def parse_args() -> argparse.Namespace:
	parser = argparse.ArgumentParser(description='Benchmark agentyc MCP over stdio with a real browser.')
	parser.add_argument('--targets', default='source,pypi', help='Comma-separated targets: source,pypi')
	parser.add_argument('--source-python', type=Path, default=DEFAULT_SOURCE_PYTHON)
	parser.add_argument('--pypi-python', type=Path, default=DEFAULT_PYPI_PYTHON)
	parser.add_argument('--minimum-total-calls', type=int, default=1000)
	parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
	parser.add_argument('--scratch-root', type=Path, default=Path(tempfile.gettempdir()) / 'agentyc-mcp-stdio-e2e')
	return parser.parse_args()


def main() -> None:
	args = parse_args()
	results = asyncio.run(main_async(args))
	args.output.parent.mkdir(parents=True, exist_ok=True)
	args.output.write_text(json.dumps(results, indent='\t'), encoding='utf-8')
	print(json.dumps(results, indent=2))
	print(f'\nWrote benchmark results to {args.output}')


if __name__ == '__main__':
	main()
