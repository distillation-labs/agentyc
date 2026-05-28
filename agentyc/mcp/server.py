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

# Add the repository root to path if running from source without shadowing the external `mcp` SDK.
source_root = str(Path(__file__).resolve().parents[2])
if source_root not in sys.path:
	sys.path.insert(0, source_root)

from agentyc.mcp.server_bootstrap import (
	_configure_mcp_server_logging,
	_ensure_all_loggers_use_stderr,
)

# Configure MCP server logging before any agentyc imports to capture early log lines
_configure_mcp_server_logging()

# Additional suppression - disable all logging completely for MCP mode
logging.disable(logging.CRITICAL)

if TYPE_CHECKING:
	from agentyc.browser import BrowserSession
	from agentyc.filesystem.file_system import FileSystem
	from agentyc.tools.service import Tools

logger = logging.getLogger(__name__)

# Try to import MCP SDK
try:
	import mcp.server.stdio
	import mcp.types as types
	from mcp.server import NotificationOptions, Server
	from mcp.server.models import InitializationOptions

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


class AgentycServer:
	"""MCP Server for agentyc capabilities."""

	_methods_bound = False
	_execute_tool: Callable[[str, dict[str, Any]], Awaitable[Any]]
	_set_intent: Callable[[str], Awaitable[str]]
	_start_cleanup_task: Callable[[], Awaitable[None]]
	_shutdown: Callable[[], Awaitable[None]]
	_tool_phase_message: Callable[[str, dict[str, Any]], str]
	_should_log: Callable[[types.LoggingLevel], bool]
	_send_log_notification: Callable[..., Awaitable[None]]
	_tool_text_is_error: Callable[[str], bool]
	_tool_output_is_error: Callable[[list[types.TextContent | types.ImageContent]], bool]
	_attach_tool_result_metadata: Callable[..., list[types.TextContent | types.ImageContent]]
	_publish_hud_event: Callable[..., None]
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
	_inspect_network_entry: Callable[..., Awaitable[str]]
	_list_frames: Callable[[], Awaitable[str]]
	_get_frame_html: Callable[[str], Awaitable[str]]
	_get_storage: Callable[..., Awaitable[str]]
	_set_storage: Callable[..., Awaitable[str]]
	_clear_storage: Callable[..., Awaitable[str]]
	_add_network_mock: Callable[..., Awaitable[str]]
	_remove_network_mock: Callable[..., Awaitable[str]]
	_list_network_mocks: Callable[[], Awaitable[str]]
	_set_network_conditions: Callable[..., Awaitable[str]]
	_get_network_conditions: Callable[[], Awaitable[str]]
	_replay_request: Callable[..., Awaitable[str]]

	@classmethod
	def _bind_server_methods(cls) -> None:
		if cls._methods_bound:
			return
		from agentyc.mcp.server_methods import SERVER_METHODS

		for method_name, method in SERVER_METHODS.items():
			setattr(cls, method_name, method)
		cls._methods_bound = True

	def __init__(
		self,
		session_timeout_minutes: int = 0,
		cdp_url: str | None = None,
		*,
		hud_overlay: bool | None = None,
		runtime_label: str | None = None,
		runtime_role: str = 'primary',
		parent_runtime_id: str | None = None,
		shared_browser_mode: str = 'tab',
		shared_browser_window_bounds: dict[str, Any] | None = None,
		shared_browser_focus_policy: str = 'preserve',
	):
		type(self)._bind_server_methods()

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
		self._hud_overlay_enabled = bool(
			self.config.get('browser_profile', {}).get('hud_overlay', False) if hud_overlay is None else hud_overlay
		)
		self._hud_overlay = None
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
		self._browser_state_cache_clean: bool = False
		self._browser_state_cache_timestamp: float = 0.0
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

		if self._hud_overlay_enabled:
			try:
				from agentyc.browser.hud_overlay import HudOverlay

				self._hud_overlay = HudOverlay()
				self._hud_overlay.start()
			except Exception:
				self._hud_overlay = None

		# Setup handlers
		self._setup_handlers()

	def _setup_handlers(self):
		"""Setup MCP server handlers."""

		@self.server.list_tools()
		async def handle_list_tools() -> list[types.Tool]:
			"""List all available agentyc tools."""
			from agentyc.mcp.tool_schemas import get_tool_schemas

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
			from agentyc.mcp.tool_feedback import _extract_tool_error_message

			self._publish_hud_event('tool_start', name, args)
			# Send starting notification
			await self._send_log_notification('info', name, args)

			try:
				result = await self._execute_tool(name, args)
				duration = time.time() - start_time

				# Send completion notification (skip for rapid periodic tools to reduce noise)
				if name not in ('browser_get_state', 'browser_wait', 'browser_wait_for_element'):
					await self._send_log_notification('info', name, args, duration=duration, completed=True)

				if isinstance(result, list):
					is_error = self._tool_output_is_error(result)
					content = self._attach_tool_result_metadata(
						name=name,
						arguments=args,
						content=result,
						started_at=start_time,
						is_error=is_error,
					)
					is_error = self._tool_output_is_error(content)
					if is_error:
						self._publish_hud_event(
							'tool_error',
							name,
							args,
							duration=duration,
							error=_extract_tool_error_message(content),
						)
					else:
						self._publish_hud_event('tool_done', name, args, duration=duration)
					return types.CallToolResult(
						content=cast(list[types.ContentBlock], content),
						isError=is_error,
					)
				is_error = self._tool_text_is_error(result)
				content = self._attach_tool_result_metadata(
					name=name,
					arguments=args,
					content=[types.TextContent(type='text', text=result)],
					started_at=start_time,
					is_error=is_error,
				)
				is_error = self._tool_output_is_error(content)
				if is_error:
					self._publish_hud_event(
						'tool_error',
						name,
						args,
						duration=duration,
						error=_extract_tool_error_message(content),
					)
				else:
					self._publish_hud_event('tool_done', name, args, duration=duration)
				return types.CallToolResult(
					content=cast(list[types.ContentBlock], content),
					isError=is_error,
				)
			except Exception as e:
				error_msg = str(e)
				logger.error(f'Tool execution failed: {e}', exc_info=True)
				self._publish_hud_event('tool_error', name, args, error=error_msg)
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


if __name__ == '__main__':
	from agentyc.mcp.server_main import main

	asyncio.run(main())
