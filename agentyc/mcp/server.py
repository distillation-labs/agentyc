"""MCP Server for deterministic browser automation over Model Context Protocol."""

from __future__ import annotations

import os
import sys

# Set environment variables BEFORE any agentyc imports to prevent early logging
os.environ['AGENTYC_LOGGING_LEVEL'] = 'critical'
os.environ['AGENTYC_SETUP_LOGGING'] = 'false'

import asyncio
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

# Configure logging for MCP mode - redirect to stderr but preserve critical diagnostics
logging.basicConfig(
	stream=sys.stderr, level=logging.WARNING, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', force=True
)

try:
	import psutil

	PSUTIL_AVAILABLE = True
except ImportError:
	PSUTIL_AVAILABLE = False

# Add the repository root to path if running from source without shadowing the external `mcp` SDK.
source_root = str(Path(__file__).resolve().parents[2])
if source_root not in sys.path:
	sys.path.insert(0, source_root)

# Import and configure logging to use stderr before other imports
from agentyc.logging_config import setup_logging


def _configure_mcp_server_logging():
	"""Configure logging for MCP server mode - redirect all logs to stderr to prevent JSON RPC interference."""
	# Set environment to suppress agentyc logging during server mode
	os.environ['AGENTYC_LOGGING_LEVEL'] = 'warning'
	os.environ['AGENTYC_SETUP_LOGGING'] = 'false'  # Prevent automatic logging setup

	# Configure logging to stderr for MCP mode - preserve warnings and above for troubleshooting
	setup_logging(stream=sys.stderr, log_level='warning', force_setup=True)

	# Also configure the root logger and all existing loggers to use stderr
	logging.root.handlers = []
	stderr_handler = logging.StreamHandler(sys.stderr)
	stderr_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
	logging.root.addHandler(stderr_handler)
	logging.root.setLevel(logging.CRITICAL)

	# Configure all existing loggers to use stderr and CRITICAL level
	for name in list(logging.root.manager.loggerDict.keys()):
		logger_obj = logging.getLogger(name)
		logger_obj.handlers = []
		logger_obj.setLevel(logging.CRITICAL)
		logger_obj.addHandler(stderr_handler)
		logger_obj.propagate = False


# Configure MCP server logging before any agentyc imports to capture early log lines
_configure_mcp_server_logging()

# Additional suppression - disable all logging completely for MCP mode
logging.disable(logging.CRITICAL)

if TYPE_CHECKING:
	from agentyc.browser import BrowserSession
	from agentyc.filesystem.file_system import FileSystem
	from agentyc.tools.service import Tools

logger = logging.getLogger(__name__)

from agentyc.mcp.action_runtime import (
	_cache_state_payload,
	_classify_action_error,
	_click,
	_ensure_extract_runtime,
	_evaluate,
	_extract_content,
	_find_elements,
	_format_action_error,
	_get_browser_state,
	_get_dropdown_options,
	_go_back,
	_go_forward,
	_inject_extraction_metadata,
	_navigate,
	_new_tab_postcondition_satisfied,
	_press_key,
	_refresh,
	_refresh_selector_map,
	_resolve_element_index,
	_resolve_live_element,
	_resolve_upload_available_file_paths,
	_run_tool_action,
	_save_as_pdf,
	_scroll,
	_search_page,
	_select_option,
	_type_text,
	_upload_file,
	_validate_actionable_element,
	_wait,
	_wait_for_element,
)
from agentyc.mcp.cdp_tools import (
	_clear_cookies,
	_clear_logs,
	_close_tab,
	_double_click,
	_drag_to,
	_get_attribute,
	_get_console_logs,
	_get_cookies,
	_get_downloads,
	_get_focused_element,
	_get_html,
	_get_network_log,
	_get_viewport_coords,
	_handle_dialog,
	_hover,
	_list_tabs,
	_load_state,
	_new_tab,
	_register_cdp_event_listeners,
	_resolve_element_coords,
	_right_click,
	_save_state,
	_screenshot,
	_scroll_to_text,
	_set_cookies,
	_set_viewport,
	_start_trace,
	_stop_trace,
	_switch_tab,
	_wait_for_network_idle,
	_wait_for_stable_dom,
)
from agentyc.mcp.debug_tools import _export_debug_bundle, _wait_for_request, _wait_for_response
from agentyc.mcp.navigation_runtime import (
	_page_contains_visible_text,
	_recover_click_navigation_if_unavailable,
	_wait_for_click_navigation_settle,
)
from agentyc.mcp.session_lifecycle import (
	_browser_runtime_is_ready,
	_cleanup_expired_sessions,
	_close_all_sessions,
	_close_session,
	_init_browser_session,
	_list_sessions,
	_reset_broken_browser_runtime,
	_shutdown,
	_start_cleanup_task,
	_track_session,
	_update_session_activity,
	_update_session_url,
)
from agentyc.mcp.tool_dispatch import _execute_tool


def _ensure_all_loggers_use_stderr():
	"""Ensure ALL loggers only output to stderr, not stdout."""
	# Get the stderr handler
	stderr_handler = None
	for handler in logging.root.handlers:
		if hasattr(handler, 'stream') and handler.stream == sys.stderr:  # type: ignore
			stderr_handler = handler
			break

	if not stderr_handler:
		stderr_handler = logging.StreamHandler(sys.stderr)
		stderr_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))

	# Configure root logger
	logging.root.handlers = [stderr_handler]
	logging.root.setLevel(logging.CRITICAL)

	# Configure all existing loggers
	for name in list(logging.root.manager.loggerDict.keys()):
		logger_obj = logging.getLogger(name)
		logger_obj.handlers = [stderr_handler]
		logger_obj.setLevel(logging.CRITICAL)
		logger_obj.propagate = False


# Ensure stderr logging after all imports
_ensure_all_loggers_use_stderr()


# Try to import MCP SDK
try:
	import mcp.server.stdio
	import mcp.types as types
	from mcp.server import NotificationOptions, Server
	from mcp.server.models import InitializationOptions

	from agentyc.mcp.tool_schemas import get_tool_schemas

	MCP_AVAILABLE = True

	# Configure MCP SDK logging to stderr as well
	mcp_logger = logging.getLogger('mcp')
	mcp_logger.handlers = []
	mcp_logger.addHandler(logging.root.handlers[0] if logging.root.handlers else logging.StreamHandler(sys.stderr))
	mcp_logger.setLevel(logging.ERROR)
	mcp_logger.propagate = False
except ImportError:
	MCP_AVAILABLE = False
	logger.error('MCP SDK not installed. Install with: pip install mcp')
	sys.exit(1)


def get_parent_process_cmdline() -> str | None:
	"""Get the command line of all parent processes up the chain."""
	if not PSUTIL_AVAILABLE:
		return None

	try:
		cmdlines = []
		current_process = psutil.Process()
		parent = current_process.parent()

		while parent:
			try:
				cmdline = parent.cmdline()
				if cmdline:
					cmdlines.append(' '.join(cmdline))
			except (psutil.AccessDenied, psutil.NoSuchProcess):
				# Skip processes we can't access (like system processes)
				pass

			try:
				parent = parent.parent()
			except (psutil.AccessDenied, psutil.NoSuchProcess):
				# Can't go further up the chain
				break

		return ';'.join(cmdlines) if cmdlines else None
	except Exception:
		# If we can't get parent process info, just return None
		return None


class AgentycServer:
	"""MCP Server for agentyc capabilities."""

	_execute_tool: Callable[[str, dict[str, Any]], Awaitable[Any]]
	_start_cleanup_task: Callable[[], Awaitable[None]]
	_shutdown: Callable[[], Awaitable[None]]
	_save_as_pdf: Callable[..., Awaitable[str]]
	_get_downloads: Callable[[], Awaitable[str]]
	_set_viewport: Callable[..., Awaitable[str]]
	_wait_for_stable_dom: Callable[..., Awaitable[str]]
	_handle_dialog: Callable[..., Awaitable[str]]
	_get_attribute: Callable[..., Awaitable[str]]
	_clear_logs: Callable[..., Awaitable[str]]
	_start_trace: Callable[..., Awaitable[str]]
	_stop_trace: Callable[..., Awaitable[str]]
	_wait_for_request: Callable[..., Awaitable[str]]
	_wait_for_response: Callable[..., Awaitable[str]]
	_export_debug_bundle: Callable[..., Awaitable[tuple[str, str | None]]]

	def __init__(
		self,
		session_timeout_minutes: int = 0,
		cdp_url: str | None = None,
		*,
		runtime_label: str | None = None,
		runtime_role: str = 'primary',
		parent_runtime_id: str | None = None,
		shared_browser_mode: str = 'tab',
		shared_browser_window_bounds: dict[str, Any] | None = None,
		shared_browser_focus_policy: str = 'preserve',
	):
		# Ensure all logging goes to stderr (in case new loggers were created)
		_ensure_all_loggers_use_stderr()

		from agentyc.config import load_agentyc_config
		from agentyc.telemetry import ProductTelemetry

		self.server = Server('agentyc')
		self.config = load_agentyc_config()
		self.browser_session: BrowserSession | None = None
		self.tools: Tools | None = None
		self.file_system: FileSystem | None = None
		self._file_system_base_dir: Path | None = None
		self._telemetry = ProductTelemetry()
		self._start_time = time.time()
		self._cdp_url = cdp_url  # shared-browser CDP URL for parallel tab mode
		self._explicit_cdp_url = cdp_url is not None
		self._runtime_label = runtime_label
		self._runtime_role = runtime_role
		self._parent_runtime_id = parent_runtime_id
		self._shared_browser_mode = shared_browser_mode
		self._shared_browser_window_bounds = shared_browser_window_bounds
		self._shared_browser_focus_policy = shared_browser_focus_policy
		self._reuse_local_browser = True

		# Session management
		self.active_sessions: dict[str, dict[str, Any]] = {}  # session_id -> session info
		self.session_timeout_minutes = session_timeout_minutes
		self._cleanup_task: Any = None
		self._last_state_elements_by_ref: dict[str, dict[str, Any]] = {}
		self._last_state_cache_url: str | None = None
		self._action_model_cache: dict[str, Any] = {}

		# CDP event capture buffers — populated by native CDP events, not JS injection
		self._console_log_buffer: deque[dict[str, Any]] = deque(maxlen=500)
		self._network_log_buffer: deque[dict[str, Any]] = deque(maxlen=500)
		self._network_pending: dict[str, dict[str, Any]] = {}  # requestId -> in-flight entry
		self._cdp_events_registered: bool = False
		self._cdp_client_for_runtime: Any = None  # set after _register_cdp_event_listeners
		self._trace_active: bool = False
		self._trace_events: list[dict[str, Any]] = []
		self._trace_categories: str | None = None
		self._trace_started_at: float | None = None
		self._last_trace_summary: dict[str, Any] | None = None

		# MCP logging level — client uses logging/setLevel to control this
		self._min_log_level: types.LoggingLevel = 'info'

		# Setup handlers
		self._setup_handlers()

	def _setup_handlers(self):
		"""Setup MCP server handlers."""

		@self.server.list_tools()
		async def handle_list_tools() -> list[types.Tool]:
			"""List all available agentyc tools."""
			return get_tool_schemas()

		@self.server.list_resources()
		async def handle_list_resources() -> list[types.Resource]:
			"""List available resources (none for agentyc)."""
			return []

		@self.server.list_prompts()
		async def handle_list_prompts() -> list[types.Prompt]:
			"""List available prompts (none for agentyc)."""
			return []

		@self.server.set_logging_level()
		async def handle_set_logging_level(level: types.LoggingLevel) -> None:
			"""Handle client logging level changes."""
			self._min_log_level = level

		@self.server.call_tool()
		async def handle_call_tool(name: str, arguments: dict[str, Any] | None) -> types.CallToolResult:
			"""Handle tool execution."""
			start_time = time.time()
			error_msg = None
			args = arguments or {}

			# Send starting notification
			await self._send_log_notification('info', name, args)

			try:
				result = await self._execute_tool(name, args)
				duration = time.time() - start_time

				# Send completion notification (skip for rapid periodic tools to reduce noise)
				if name not in ('browser_get_state', 'browser_wait', 'browser_wait_for_element'):
					await self._send_log_notification('info', name, args, duration=duration, completed=True)

				if isinstance(result, list):
					content = self._attach_tool_result_metadata(
						name=name,
						arguments=args,
						content=result,
						started_at=start_time,
						is_error=self._tool_output_is_error(result),
					)
					return types.CallToolResult(
						content=cast(list[types.ContentBlock], content),
						isError=self._tool_output_is_error(content),
					)
				content = self._attach_tool_result_metadata(
					name=name,
					arguments=args,
					content=[types.TextContent(type='text', text=result)],
					started_at=start_time,
					is_error=self._tool_text_is_error(result),
				)
				return types.CallToolResult(
					content=cast(list[types.ContentBlock], content),
					isError=self._tool_output_is_error(content),
				)
			except Exception as e:
				error_msg = str(e)
				logger.error(f'Tool execution failed: {e}', exc_info=True)
				await self._send_log_notification('error', name, args, error=error_msg)
				content = self._attach_tool_result_metadata(
					name=name,
					arguments=args,
					content=[types.TextContent(type='text', text=f'Error: {str(e)}')],
					started_at=start_time,
					is_error=True,
				)
				return types.CallToolResult(content=cast(list[types.ContentBlock], content), isError=True)
			finally:
				# Capture telemetry for tool calls
				duration = time.time() - start_time
				from agentyc.telemetry import MCPServerTelemetryEvent
				from agentyc.utils import get_agentyc_version

				self._telemetry.capture(
					MCPServerTelemetryEvent(
						version=get_agentyc_version(),
						action='tool_call',
						tool_name=name,
						duration_seconds=duration,
						error_message=error_msg,
					)
				)

	async def run(self):
		"""Run the MCP server."""
		# Start the cleanup task
		await self._start_cleanup_task()

		if sys.stdin is None:
			raise RuntimeError('MCP stdio transport requires stdin, but this process was launched without one.')

		from agentyc.utils import get_agentyc_version

		try:
			async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
				try:
					await self.server.run(
						read_stream,
						write_stream,
						InitializationOptions(
							server_name='agentyc',
							server_version=get_agentyc_version(),
							capabilities=self.server.get_capabilities(
								notification_options=NotificationOptions(),
								experimental_capabilities={},
							),
						),
					)
				except BrokenPipeError:
					logger.warning('MCP client disconnected while writing to stdio; shutting down server cleanly.')
		finally:
			await self._shutdown()

	def _tool_phase_message(self, tool_name: str, arguments: dict[str, Any]) -> str:
		if tool_name == 'browser_navigate':
			return f'Navigating to {arguments.get("url", "page")}'
		if tool_name == 'browser_get_state':
			mode = arguments.get('mode', 'auto')
			if arguments.get('since_hash'):
				return f'Checking page state delta ({mode})'
			return f'Reading page state ({mode})'
		if tool_name == 'browser_click':
			if arguments.get('new_tab'):
				return 'Opening link in new tab'
			return 'Clicking page element'
		if tool_name == 'browser_type':
			return 'Typing into focused field'
		if tool_name == 'browser_wait_for_element':
			return 'Waiting for page element to change'
		if tool_name == 'browser_wait_for_network_idle':
			return 'Waiting for network to go idle'
		if tool_name == 'browser_screenshot':
			return 'Capturing screenshot'
		if tool_name == 'browser_extract_content':
			return 'Extracting structured page content'
		if tool_name == 'browser_switch_tab':
			return 'Switching browser tab'
		if tool_name == 'browser_close_tab':
			return 'Closing browser tab'
		return f'Running {tool_name}'

	_LOG_LEVEL_RANK = {
		'debug': 0,
		'info': 1,
		'notice': 2,
		'warning': 3,
		'error': 4,
		'critical': 5,
		'alert': 6,
		'emergency': 7,
	}

	def _should_log(self, level: types.LoggingLevel) -> bool:
		"""Return True if the given level meets the client's minimum threshold."""
		try:
			return self._LOG_LEVEL_RANK.get(level, 99) >= self._LOG_LEVEL_RANK.get(self._min_log_level, 1)
		except Exception:
			return True

	async def _send_log_notification(
		self,
		level: types.LoggingLevel,
		tool_name: str,
		arguments: dict[str, Any],
		*,
		duration: float | None = None,
		completed: bool = False,
		error: str | None = None,
	) -> None:
		"""Send an MCP log message notification for a tool action.

		This is best-effort — failures never propagate to the caller.
		"""
		if not self._should_log(level):
			return
		try:
			message = self._tool_phase_message(tool_name, arguments)
			if error:
				message = f'{message} — Error: {error}'
			elif completed and duration is not None:
				ms = round(duration * 1000)
				message = f'{message} — done ({ms}ms)'

			ctx = self.server.request_context
			await ctx.session.send_log_message(
				level=level,
				data=message,
				logger='agentyc',
			)
		except Exception:
			pass  # notification failures must never break tool execution

	def _tool_text_is_error(self, text: str) -> bool:
		return text.startswith('Error:') or text.startswith('Error [')

	def _tool_output_is_error(self, content: list[types.TextContent | types.ImageContent]) -> bool:
		for item in content:
			if isinstance(item, types.TextContent) and item.text:
				return self._tool_text_is_error(item.text)
		return False

	def _attach_tool_result_metadata(
		self,
		*,
		name: str,
		arguments: dict[str, Any],
		content: list[types.TextContent | types.ImageContent],
		started_at: float,
		is_error: bool,
	) -> list[types.TextContent | types.ImageContent]:
		duration_ms = round((time.time() - started_at) * 1000, 1)
		phase_message = self._tool_phase_message(name, arguments)
		metadata = {
			'agentyc/tool_name': name,
			'agentyc/tool_phase': phase_message,
			'agentyc/browser_duration_ms': duration_ms,
			'agentyc/is_error': is_error,
		}
		updated_content: list[types.TextContent | types.ImageContent] = []
		attached = False
		for item in content:
			if not attached and isinstance(item, types.TextContent):
				merged_meta = dict(getattr(item, 'meta', None) or {})
				merged_meta.update(metadata)
				updated_content.append(
					types.TextContent(
						type='text',
						text=item.text,
						annotations=item.annotations,
						_meta=merged_meta,
					)
				)
				attached = True
			else:
				updated_content.append(item)
		if not attached:
			updated_content.insert(
				0,
				types.TextContent(type='text', text='', _meta=metadata),
			)
		return updated_content


_SERVER_METHODS: dict[str, Any] = {
	'_execute_tool': _execute_tool,
	'_init_browser_session': _init_browser_session,
	'_browser_runtime_is_ready': _browser_runtime_is_ready,
	'_reset_broken_browser_runtime': _reset_broken_browser_runtime,
	'_ensure_extract_runtime': _ensure_extract_runtime,
	'_recover_click_navigation_if_unavailable': _recover_click_navigation_if_unavailable,
	'_resolve_element_index': _resolve_element_index,
	'_cache_state_payload': _cache_state_payload,
	'_refresh_selector_map': _refresh_selector_map,
	'_page_contains_visible_text': _page_contains_visible_text,
	'_resolve_live_element': _resolve_live_element,
	'_resolve_upload_available_file_paths': _resolve_upload_available_file_paths,
	'_validate_actionable_element': _validate_actionable_element,
	'_classify_action_error': _classify_action_error,
	'_format_action_error': _format_action_error,
	'_run_tool_action': _run_tool_action,
	'_wait_for_click_navigation_settle': _wait_for_click_navigation_settle,
	'_inject_extraction_metadata': _inject_extraction_metadata,
	'_new_tab_postcondition_satisfied': _new_tab_postcondition_satisfied,
	'_navigate': _navigate,
	'_click': _click,
	'_type_text': _type_text,
	'_upload_file': _upload_file,
	'_get_browser_state': _get_browser_state,
	'_extract_content': _extract_content,
	'_scroll': _scroll,
	'_go_back': _go_back,
	'_go_forward': _go_forward,
	'_refresh': _refresh,
	'_press_key': _press_key,
	'_wait': _wait,
	'_evaluate': _evaluate,
	'_select_option': _select_option,
	'_get_dropdown_options': _get_dropdown_options,
	'_find_elements': _find_elements,
	'_wait_for_element': _wait_for_element,
	'_save_as_pdf': _save_as_pdf,
	'_get_downloads': _get_downloads,
	'_set_viewport': _set_viewport,
	'_search_page': _search_page,
	'_get_html': _get_html,
	'_screenshot': _screenshot,
	'_get_viewport_coords': _get_viewport_coords,
	'_resolve_element_coords': _resolve_element_coords,
	'_hover': _hover,
	'_double_click': _double_click,
	'_drag_to': _drag_to,
	'_scroll_to_text': _scroll_to_text,
	'_save_state': _save_state,
	'_load_state': _load_state,
	'_wait_for_network_idle': _wait_for_network_idle,
	'_right_click': _right_click,
	'_get_cookies': _get_cookies,
	'_set_cookies': _set_cookies,
	'_clear_logs': _clear_logs,
	'_clear_cookies': _clear_cookies,
	'_register_cdp_event_listeners': _register_cdp_event_listeners,
	'_get_console_logs': _get_console_logs,
	'_get_attribute': _get_attribute,
	'_handle_dialog': _handle_dialog,
	'_wait_for_stable_dom': _wait_for_stable_dom,
	'_start_trace': _start_trace,
	'_stop_trace': _stop_trace,
	'_get_network_log': _get_network_log,
	'_wait_for_request': _wait_for_request,
	'_wait_for_response': _wait_for_response,
	'_export_debug_bundle': _export_debug_bundle,
	'_get_focused_element': _get_focused_element,
	'_list_tabs': _list_tabs,
	'_new_tab': _new_tab,
	'_switch_tab': _switch_tab,
	'_close_tab': _close_tab,
	'_track_session': _track_session,
	'_update_session_activity': _update_session_activity,
	'_update_session_url': _update_session_url,
	'_list_sessions': _list_sessions,
	'_close_session': _close_session,
	'_close_all_sessions': _close_all_sessions,
	'_cleanup_expired_sessions': _cleanup_expired_sessions,
	'_start_cleanup_task': _start_cleanup_task,
	'_shutdown': _shutdown,
}

for _method_name, _method in _SERVER_METHODS.items():
	setattr(AgentycServer, _method_name, _method)


async def main(
	session_timeout_minutes: int = 0,
	cdp_url: str | None = None,
	*,
	runtime_label: str | None = None,
	runtime_role: str = 'primary',
	parent_runtime_id: str | None = None,
	shared_browser_mode: str = 'tab',
	shared_browser_window_bounds: dict[str, Any] | None = None,
	shared_browser_focus_policy: str = 'preserve',
):
	if not MCP_AVAILABLE:
		print('MCP SDK is required. Install with: pip install mcp', file=sys.stderr)
		sys.exit(1)

	server = AgentycServer(
		session_timeout_minutes=session_timeout_minutes,
		cdp_url=cdp_url,
		runtime_label=runtime_label,
		runtime_role=runtime_role,
		parent_runtime_id=parent_runtime_id,
		shared_browser_mode=shared_browser_mode,
		shared_browser_window_bounds=shared_browser_window_bounds,
		shared_browser_focus_policy=shared_browser_focus_policy,
	)
	from agentyc.telemetry import MCPServerTelemetryEvent
	from agentyc.utils import get_agentyc_version

	server._telemetry.capture(
		MCPServerTelemetryEvent(
			version=get_agentyc_version(),
			action='start',
			parent_process_cmdline=get_parent_process_cmdline(),
		)
	)

	try:
		await server.run()
	finally:
		duration = time.time() - server._start_time
		server._telemetry.capture(
			MCPServerTelemetryEvent(
				version=get_agentyc_version(),
				action='stop',
				duration_seconds=duration,
				parent_process_cmdline=get_parent_process_cmdline(),
			)
		)
		server._telemetry.flush()


if __name__ == '__main__':
	asyncio.run(main())
