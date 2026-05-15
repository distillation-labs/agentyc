"""MCP Server for deterministic browser automation over Model Context Protocol."""

from __future__ import annotations

import os
import sys

# Set environment variables BEFORE any agentyc imports to prevent early logging
os.environ['AGENTYC_LOGGING_LEVEL'] = 'critical'
os.environ['AGENTYC_SETUP_LOGGING'] = 'false'

import asyncio
import json
import logging
import time
from collections import deque
from contextlib import suppress
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

	def __init__(self, session_timeout_minutes: int = 10, cdp_url: str | None = None):
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

		# Setup handlers
		self._setup_handlers()

	def _setup_handlers(self):
		"""Setup MCP server handlers."""

		@self.server.list_tools()
		async def handle_list_tools() -> list[types.Tool]:
			"""List all available agentyc tools."""
			return [
				# Direct browser control tools
				types.Tool(
					name='browser_navigate',
					description='Navigate to a URL in the browser',
					inputSchema={
						'type': 'object',
						'properties': {
							'url': {'type': 'string', 'description': 'The URL to navigate to'},
							'new_tab': {'type': 'boolean', 'description': 'Whether to open in a new tab', 'default': False},
						},
						'required': ['url'],
					},
				),
				types.Tool(
					name='browser_click',
					description='Click an element by ref or index, or at specific viewport coordinates. Prefer ref from browser_get_state for stable targeting.',
					inputSchema={
						'type': 'object',
						'properties': {
							'ref': {
								'type': 'string',
								'description': 'Stable element ref from browser_get_state (for example "e123"). Provide this OR index OR coordinate_x+coordinate_y.',
							},
							'index': {
								'type': 'integer',
								'description': 'Legacy numeric element index from browser_get_state. Equivalent to the backend node id. Provide this OR ref OR coordinate_x+coordinate_y.',
							},
							'coordinate_x': {
								'type': 'integer',
								'description': 'X coordinate in pixels from the left edge of the viewport. Must be used together with coordinate_y. Provide this OR ref/index.',
							},
							'coordinate_y': {
								'type': 'integer',
								'description': 'Y coordinate in pixels from the top edge of the viewport. Must be used together with coordinate_x. Provide this OR ref/index.',
							},
							'new_tab': {
								'type': 'boolean',
								'description': 'Whether to open any resulting navigation in a new tab',
								'default': False,
							},
						},
					},
				),
				types.Tool(
					name='browser_type',
					description='Type text into an input field. Prefer ref from browser_get_state for stable targeting. Clears existing text by default; pass text="" to clear only.',
					inputSchema={
						'type': 'object',
						'properties': {
							'ref': {
								'type': 'string',
								'description': 'Stable element ref from browser_get_state (for example "e123"). Provide this OR index.',
							},
							'index': {
								'type': 'integer',
								'description': 'Legacy numeric element index from browser_get_state. Equivalent to the backend node id.',
							},
							'text': {
								'type': 'string',
								'description': 'The text to type. Pass an empty string ("") to clear the field without typing.',
							},
						},
						'required': ['text'],
					},
				),
				types.Tool(
					name='browser_upload_file',
					description='Upload a local file to a file input or upload control. Provide ref or index for the target control, and a path that is accessible to the MCP server.',
					inputSchema={
						'type': 'object',
						'properties': {
							'ref': {
								'type': 'string',
								'description': 'Stable element ref from browser_get_state (for example "e123"). Provide this OR index.',
							},
							'index': {
								'type': 'integer',
								'description': 'Legacy numeric element index from browser_get_state. Equivalent to the backend node id. Provide this OR ref.',
							},
							'path': {
								'type': 'string',
								'description': 'Local file path to upload. Use an absolute path for arbitrary files, or a filename from the agentyc file system.',
							},
						},
						'required': ['path'],
					},
				),
				types.Tool(
					name='browser_get_state',
					description='Get the current state of the page. Supports compact modes, stable refs, and lightweight unchanged-state checks.',
					inputSchema={
						'type': 'object',
						'properties': {
							'include_screenshot': {
								'type': 'boolean',
								'description': 'Whether to include a screenshot of the current page',
								'default': False,
							},
							'mode': {
								'type': 'string',
								'enum': ['auto', 'full', 'min', 'focus'],
								'description': 'State detail level. auto prefers full on small pages and ranked compaction on large ones. full returns all interactive elements, min returns a compact ranked subset, focus returns a single referenced element.',
								'default': 'auto',
							},
							'focus_ref': {
								'type': 'string',
								'description': 'Element ref to focus on when mode=focus.',
							},
							'since_hash': {
								'type': 'string',
								'description': 'Previous state_hash from browser_get_state. If unchanged, returns changed=false with no interactive element payload.',
							},
						},
					},
				),
				types.Tool(
					name='browser_extract_content',
					description='Deterministically extract compatible content from the current page based on a query. This MCP tool does not use an LLM fallback.',
					inputSchema={
						'type': 'object',
						'properties': {
							'query': {'type': 'string', 'description': 'What information to extract from the page'},
							'extract_links': {
								'type': 'boolean',
								'description': 'Whether to include links in the extraction',
								'default': False,
							},
							'output_schema': {
								'type': 'object',
								'description': 'Optional JSON Schema for deterministic structured extraction. Compatible table, list, key-value, link-collection, form-field, and image queries can be answered without an LLM.',
								'additionalProperties': True,
							},
						},
						'required': ['query'],
					},
				),
				types.Tool(
					name='browser_get_html',
					description='Get the raw HTML of the current page or a specific element by CSS selector',
					inputSchema={
						'type': 'object',
						'properties': {
							'selector': {
								'type': 'string',
								'description': 'Optional CSS selector to get HTML of a specific element. If omitted, returns full page HTML.',
							},
						},
					},
				),
				types.Tool(
					name='browser_screenshot',
					description='Take a screenshot of the current page. Returns viewport metadata as text and the screenshot as an image.',
					inputSchema={
						'type': 'object',
						'properties': {
							'full_page': {
								'type': 'boolean',
								'description': 'Whether to capture the full scrollable page or just the visible viewport',
								'default': False,
							},
						},
					},
				),
				# Tab management
				types.Tool(
					name='browser_list_tabs', description='List all open tabs', inputSchema={'type': 'object', 'properties': {}}
				),
				types.Tool(
					name='browser_switch_tab',
					description='Switch to a different tab',
					inputSchema={
						'type': 'object',
						'properties': {'tab_id': {'type': 'string', 'description': '4 Character Tab ID of the tab to switch to'}},
						'required': ['tab_id'],
					},
				),
				types.Tool(
					name='browser_close_tab',
					description='Close a tab',
					inputSchema={
						'type': 'object',
						'properties': {'tab_id': {'type': 'string', 'description': '4 Character Tab ID of the tab to close'}},
						'required': ['tab_id'],
					},
				),
				# Browser session management tools
				types.Tool(
					name='browser_list_sessions',
					description='List all active browser sessions with their details and last activity time',
					inputSchema={'type': 'object', 'properties': {}},
				),
				types.Tool(
					name='browser_close_session',
					description='Close a specific browser session by its ID',
					inputSchema={
						'type': 'object',
						'properties': {
							'session_id': {
								'type': 'string',
								'description': 'The browser session ID to close (get from browser_list_sessions)',
							}
						},
						'required': ['session_id'],
					},
				),
				types.Tool(
					name='browser_close_all',
					description='Close all active browser sessions and clean up resources',
					inputSchema={'type': 'object', 'properties': {}},
				),
				# Enhanced interaction tools
				types.Tool(
					name='browser_scroll',
					description='Scroll the page or a specific element. pages=10 reaches the bottom quickly.',
					inputSchema={
						'type': 'object',
						'properties': {
							'direction': {'type': 'string', 'enum': ['up', 'down'], 'default': 'down'},
							'pages': {'type': 'number', 'description': '0.5=half, 1=full page (default), 10=to bottom/top', 'default': 1.0},
							'ref': {'type': 'string', 'description': 'Scroll within element ref (e.g. "e123"). Omit for page scroll.'},
							'index': {'type': 'integer', 'description': 'Scroll within element by backend node id. Provide this OR ref.'},
						},
					},
				),
				types.Tool(
					name='browser_go_back',
					description='Go back to the previous page in browser history',
					inputSchema={'type': 'object', 'properties': {}},
				),
				types.Tool(
					name='browser_go_forward',
					description='Go forward to the next page in browser history',
					inputSchema={'type': 'object', 'properties': {}},
				),
				types.Tool(
					name='browser_refresh',
					description='Refresh/reload the current page',
					inputSchema={'type': 'object', 'properties': {}},
				),
				types.Tool(
					name='browser_press_key',
					description='Send a keyboard key or shortcut. Examples: "Enter", "Tab", "Escape", "ArrowDown", "Control+a", "Meta+r".',
					inputSchema={
						'type': 'object',
						'properties': {'key': {'type': 'string', 'description': 'Key or shortcut (e.g. "Enter", "Tab", "Control+a")'}},
						'required': ['key'],
					},
				),
				types.Tool(
					name='browser_wait',
					description='Wait for a number of seconds. Prefer since_hash polling for dynamic content.',
					inputSchema={
						'type': 'object',
						'properties': {'seconds': {'type': 'number', 'description': 'Seconds to wait (max 30)', 'default': 2}},
					},
				),
				types.Tool(
					name='browser_evaluate',
					description='Execute JavaScript in the page context and return the result. Wrap in IIFE: (function(){ ... })(). Browser APIs only.',
					inputSchema={
						'type': 'object',
						'properties': {'code': {'type': 'string', 'description': 'JavaScript to evaluate'}},
						'required': ['code'],
					},
				),
				types.Tool(
					name='browser_select_option',
					description='Select an option in a <select> dropdown by its visible text.',
					inputSchema={
						'type': 'object',
						'properties': {
							'ref': {'type': 'string', 'description': 'Stable ref of the <select> (e.g. "e123"). Provide this OR index.'},
							'index': {'type': 'integer', 'description': 'Backend node id of the <select>. Provide this OR ref.'},
							'text': {'type': 'string', 'description': 'Exact visible text of the option to select'},
						},
						'required': ['text'],
					},
				),
				types.Tool(
					name='browser_get_dropdown_options',
					description='Get all available options from a <select> or ARIA combobox element.',
					inputSchema={
						'type': 'object',
						'properties': {
							'ref': {'type': 'string', 'description': 'Stable ref (e.g. "e123"). Provide this OR index.'},
							'index': {'type': 'integer', 'description': 'Backend node id. Provide this OR ref.'},
						},
					},
				),
				types.Tool(
					name='browser_find_elements',
					description='Find elements by CSS selector. Returns tag, text, and optionally attributes for each match.',
					inputSchema={
						'type': 'object',
						'properties': {
							'selector': {'type': 'string', 'description': 'CSS selector (e.g. "table tr", "input[type=email]")'},
							'attributes': {'type': 'array', 'items': {'type': 'string'}, 'description': 'Attributes to extract per element.'},
							'max_results': {'type': 'integer', 'default': 50},
						},
						'required': ['selector'],
					},
				),
				types.Tool(
					name='browser_wait_for_element',
					description='Poll until an element matching text or ref appears (or disappears). Use for dynamic content and post-action confirmation.',
					inputSchema={
						'type': 'object',
						'properties': {
							'text': {'type': 'string', 'description': 'Text the element must contain (case-insensitive).'},
							'ref': {'type': 'string', 'description': 'Element ref that must appear (e.g. "e123").'},
							'appear': {'type': 'boolean', 'description': 'True=wait for element to appear, False=wait to disappear', 'default': True},
							'timeout_seconds': {'type': 'number', 'description': 'Max seconds to wait (default 10, max 30)', 'default': 10},
						},
					},
				),
				types.Tool(
					name='browser_search_page',
					description='Search for text or a regex pattern on the current page. Returns matches with surrounding context. Equivalent to Ctrl+F.',
					inputSchema={
						'type': 'object',
						'properties': {
							'pattern': {'type': 'string', 'description': 'Text or regex pattern to search for'},
							'regex': {'type': 'boolean', 'description': 'Treat pattern as a regular expression', 'default': False},
							'max_results': {'type': 'integer', 'description': 'Maximum matches to return', 'default': 25},
						},
						'required': ['pattern'],
					},
				),
				types.Tool(
					name='browser_get_focused_element',
					description='Return the element that currently has keyboard focus. Useful after Tab or click to confirm which field is active.',
					inputSchema={'type': 'object', 'properties': {}},
				),
				types.Tool(
					name='browser_hover',
					description='Hover over an element to trigger CSS :hover states and JS mouseover/mouseenter handlers. Essential for opening dropdown menus, tooltips, and hover-based UI. Use browser_get_state after hovering to see new elements.',
					inputSchema={
						'type': 'object',
						'properties': {
							'ref': {'type': 'string', 'description': 'Element ref (e.g. e123)'},
							'index': {'type': 'integer', 'description': 'Element index'},
							'coordinate_x': {'type': 'integer', 'description': 'X viewport coordinate'},
							'coordinate_y': {'type': 'integer', 'description': 'Y viewport coordinate'},
						},
					},
				),
				types.Tool(
					name='browser_double_click',
					description='Double-click an element or viewport coordinates. Use for text selection, opening files/folders, or activating double-click handlers in rich editors and file managers.',
					inputSchema={
						'type': 'object',
						'properties': {
							'ref': {'type': 'string', 'description': 'Element ref (e.g. e123)'},
							'index': {'type': 'integer', 'description': 'Element index from browser_state'},
							'coordinate_x': {'type': 'integer'},
							'coordinate_y': {'type': 'integer'},
						},
					},
				),
				types.Tool(
					name='browser_drag_to',
					description='Drag from one element or coordinate to another. Use for drag-and-drop in kanban boards, sortable lists, sliders, and file drop zones.',
					inputSchema={
						'type': 'object',
						'properties': {
							'source_ref': {'type': 'string', 'description': 'Source element ref (e.g. e123)'},
							'target_ref': {'type': 'string', 'description': 'Target element ref'},
							'source_x': {'type': 'integer'},
							'source_y': {'type': 'integer'},
							'target_x': {'type': 'integer'},
							'target_y': {'type': 'integer'},
							'steps': {'type': 'integer', 'description': 'Mouse movement interpolation steps (default: 10)', 'default': 10},
						},
					},
				),
				types.Tool(
					name='browser_scroll_to_text',
					description='Scroll the page until the given text string is visible in the viewport. Useful for locating content before interacting with it or verifying it exists.',
					inputSchema={
						'type': 'object',
						'properties': {
							'text': {'type': 'string', 'description': 'Text to scroll to'},
						},
						'required': ['text'],
					},
				),
				types.Tool(
					name='browser_save_state',
					description='Save the current browser session state (cookies, localStorage, sessionStorage) to a file. Use to persist authentication between sessions. Pass the returned path to browser_load_state in a future session.',
					inputSchema={
						'type': 'object',
						'properties': {
							'path': {'type': 'string', 'description': 'File path to save state to (e.g. /tmp/auth-state.json). Defaults to ~/.agentyc-mcp/browser-state.json'},
						},
					},
				),
				types.Tool(
					name='browser_load_state',
					description='Restore browser session state (cookies, localStorage) from a file previously saved with browser_save_state. Call this early in a session to restore authentication.',
					inputSchema={
						'type': 'object',
						'properties': {
							'path': {'type': 'string', 'description': 'File path to load state from'},
						},
						'required': ['path'],
					},
				),
				types.Tool(
					name='browser_wait_for_network_idle',
					description='Wait until the browser has no pending network requests for a specified duration. Use after triggering AJAX calls, form submissions, or SPA navigation to ensure data has loaded before reading state.',
					inputSchema={
						'type': 'object',
						'properties': {
							'timeout_seconds': {'type': 'number', 'description': 'Maximum time to wait (default: 10, max: 30)', 'default': 10},
							'idle_duration_ms': {'type': 'integer', 'description': 'How long network must be idle (ms, default: 500)', 'default': 500},
						},
					},
				),
				types.Tool(
					name='browser_right_click',
					description='Right-click an element or at specific coordinates to open a context menu. Use ref from browser_get_state for stable targeting.',
					inputSchema={
						'type': 'object',
						'properties': {
							'ref': {'type': 'string', 'description': 'Stable element ref from browser_get_state (e.g. e42)'},
							'index': {'type': 'integer', 'description': 'Element index (backend_node_id)'},
							'coordinate_x': {'type': 'number', 'description': 'Viewport X coordinate'},
							'coordinate_y': {'type': 'number', 'description': 'Viewport Y coordinate'},
						},
					},
				),
				types.Tool(
					name='browser_get_cookies',
					description='Get all cookies for the current page URL. Returns name, value, domain, path, and flags. Useful for reading auth tokens and session state.',
					inputSchema={'type': 'object', 'properties': {}},
				),
				types.Tool(
					name='browser_set_cookies',
					description='Set one or more cookies. Use to inject auth tokens or session cookies before navigating to a protected URL.',
					inputSchema={
						'type': 'object',
						'properties': {
							'cookies': {
								'type': 'array',
								'description': 'List of cookie objects to set',
								'items': {
									'type': 'object',
									'properties': {
										'name': {'type': 'string'},
										'value': {'type': 'string'},
										'domain': {'type': 'string', 'description': 'Cookie domain (e.g. .example.com)'},
										'path': {'type': 'string', 'default': '/'},
										'secure': {'type': 'boolean', 'default': False},
										'httpOnly': {'type': 'boolean', 'default': False},
									},
									'required': ['name', 'value'],
								},
							},
						},
						'required': ['cookies'],
					},
				),
				types.Tool(
					name='browser_clear_cookies',
					description='Clear cookies. Without arguments clears all cookies for the current page domain; pass a name to delete a specific cookie.',
					inputSchema={
						'type': 'object',
						'properties': {
							'name': {'type': 'string', 'description': 'Name of a specific cookie to delete (omit to clear all for current domain)'},
						},
					},
				),
				types.Tool(
					name='browser_get_console_logs',
					description='Return recent browser console messages (log, warn, error, info). Captured natively via CDP Runtime domain — includes errors from page load, not just after JS injection. Essential for debugging JavaScript errors and SPA state issues.',
					inputSchema={
						'type': 'object',
						'properties': {
							'level': {'type': 'string', 'description': 'Filter by level: all, log, warn, error, info (default: all)', 'default': 'all'},
							'max_entries': {'type': 'integer', 'description': 'Maximum number of entries to return (default: 50)', 'default': 50},
						},
					},
				),
				types.Tool(
					name='browser_get_network_log',
					description='Return recent network requests captured via CDP Network domain. Shows XHR/Fetch API calls, their HTTP status codes, and timing — essential for debugging SPA data flows, API failures, and understanding what a form submission actually does.',
					inputSchema={
						'type': 'object',
						'properties': {
							'type_filter': {'type': 'string', 'description': 'Filter by request type: all, XHR, Fetch, Document, Script, Stylesheet, Image (default: all)', 'default': 'all'},
							'status_filter': {'type': 'string', 'description': 'Filter by status: all, errors (4xx/5xx/failed), success (2xx/3xx) (default: all)', 'default': 'all'},
							'max_entries': {'type': 'integer', 'description': 'Maximum number of entries to return (default: 50)', 'default': 50},
						},
					},
				),
			]

		@self.server.list_resources()
		async def handle_list_resources() -> list[types.Resource]:
			"""List available resources (none for agentyc)."""
			return []

		@self.server.list_prompts()
		async def handle_list_prompts() -> list[types.Prompt]:
			"""List available prompts (none for agentyc)."""
			return []

		@self.server.call_tool()
		async def handle_call_tool(name: str, arguments: dict[str, Any] | None) -> list[types.TextContent | types.ImageContent]:
			"""Handle tool execution."""
			start_time = time.time()
			error_msg = None
			try:
				result = await self._execute_tool(name, arguments or {})
				if isinstance(result, list):
					return result
				return [types.TextContent(type='text', text=result)]
			except Exception as e:
				error_msg = str(e)
				logger.error(f'Tool execution failed: {e}', exc_info=True)
				return [types.TextContent(type='text', text=f'Error: {str(e)}')]
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

	async def _execute_tool(
		self, tool_name: str, arguments: dict[str, Any]
	) -> str | list[types.TextContent | types.ImageContent]:
		"""Execute a agentyc tool. Returns str for most tools, or a content list for tools with image output."""

		# Browser session management tools (don't require active session)
		if tool_name == 'browser_list_sessions':
			return await self._list_sessions()

		elif tool_name == 'browser_close_session':
			return await self._close_session(arguments['session_id'])

		elif tool_name == 'browser_close_all':
			return await self._close_all_sessions()

		# Direct browser control tools (require active session)
		elif tool_name.startswith('browser_'):
			# Ensure browser session exists
			if not self.browser_session:
				await self._init_browser_session()

			if tool_name == 'browser_navigate':
				return await self._navigate(arguments['url'], arguments.get('new_tab', False))

			elif tool_name == 'browser_click':
				return await self._click(
					ref=arguments.get('ref'),
					index=arguments.get('index'),
					coordinate_x=arguments.get('coordinate_x'),
					coordinate_y=arguments.get('coordinate_y'),
					new_tab=arguments.get('new_tab', False),
				)

			elif tool_name == 'browser_type':
				return await self._type_text(index=arguments.get('index'), ref=arguments.get('ref'), text=arguments['text'])

			elif tool_name == 'browser_upload_file':
				return await self._upload_file(path=arguments['path'], index=arguments.get('index'), ref=arguments.get('ref'))

			elif tool_name == 'browser_get_state':
				state_json, screenshot_b64 = await self._get_browser_state(
					include_screenshot=arguments.get('include_screenshot', False),
					mode=arguments.get('mode', 'auto'),
					focus_ref=arguments.get('focus_ref'),
					since_hash=arguments.get('since_hash'),
				)
				content: list[types.TextContent | types.ImageContent] = [types.TextContent(type='text', text=state_json)]
				if screenshot_b64:
					content.append(types.ImageContent(type='image', data=screenshot_b64, mimeType='image/png'))
				return content

			elif tool_name == 'browser_get_html':
				return await self._get_html(arguments.get('selector'))

			elif tool_name == 'browser_screenshot':
				meta_json, screenshot_b64 = await self._screenshot(arguments.get('full_page', False))
				content: list[types.TextContent | types.ImageContent] = [types.TextContent(type='text', text=meta_json)]
				if screenshot_b64:
					content.append(types.ImageContent(type='image', data=screenshot_b64, mimeType='image/png'))
				return content

			elif tool_name == 'browser_extract_content':
				return await self._extract_content(
					arguments['query'],
					arguments.get('extract_links', False),
					arguments.get('output_schema'),
				)

			elif tool_name == 'browser_scroll':
				return await self._scroll(
					direction=arguments.get('direction', 'down'),
					pages=arguments.get('pages', 1.0),
					ref=arguments.get('ref'),
					index=arguments.get('index'),
				)

			elif tool_name == 'browser_go_back':
				return await self._go_back()

			elif tool_name == 'browser_go_forward':
				return await self._go_forward()

			elif tool_name == 'browser_refresh':
				return await self._refresh()

			elif tool_name == 'browser_press_key':
				return await self._press_key(arguments['key'])

			elif tool_name == 'browser_wait':
				return await self._wait(arguments.get('seconds', 2))

			elif tool_name == 'browser_evaluate':
				return await self._evaluate(arguments['code'])

			elif tool_name == 'browser_select_option':
				return await self._select_option(
					ref=arguments.get('ref'),
					index=arguments.get('index'),
					text=arguments['text'],
				)

			elif tool_name == 'browser_get_dropdown_options':
				return await self._get_dropdown_options(
					ref=arguments.get('ref'),
					index=arguments.get('index'),
				)

			elif tool_name == 'browser_find_elements':
				return await self._find_elements(
					selector=arguments['selector'],
					attributes=arguments.get('attributes'),
					max_results=arguments.get('max_results', 50),
				)

			elif tool_name == 'browser_wait_for_element':
				return await self._wait_for_element(
					text=arguments.get('text'),
					ref=arguments.get('ref'),
					appear=arguments.get('appear', True),
					timeout_seconds=arguments.get('timeout_seconds', 10),
				)

			elif tool_name == 'browser_search_page':
				return await self._search_page(
					pattern=arguments['pattern'],
					regex=arguments.get('regex', False),
					max_results=arguments.get('max_results', 25),
				)

			elif tool_name == 'browser_get_focused_element':
				return await self._get_focused_element()

			elif tool_name == 'browser_list_tabs':
				return await self._list_tabs()

			elif tool_name == 'browser_switch_tab':
				return await self._switch_tab(arguments['tab_id'])

			elif tool_name == 'browser_close_tab':
				return await self._close_tab(arguments['tab_id'])

			elif tool_name == 'browser_hover':
				return await self._hover(ref=arguments.get('ref'), index=arguments.get('index'), coordinate_x=arguments.get('coordinate_x'), coordinate_y=arguments.get('coordinate_y'))

			elif tool_name == 'browser_double_click':
				return await self._double_click(ref=arguments.get('ref'), index=arguments.get('index'), coordinate_x=arguments.get('coordinate_x'), coordinate_y=arguments.get('coordinate_y'))

			elif tool_name == 'browser_drag_to':
				return await self._drag_to(source_ref=arguments.get('source_ref'), target_ref=arguments.get('target_ref'), source_x=arguments.get('source_x'), source_y=arguments.get('source_y'), target_x=arguments.get('target_x'), target_y=arguments.get('target_y'), steps=arguments.get('steps', 10))

			elif tool_name == 'browser_scroll_to_text':
				return await self._scroll_to_text(arguments['text'])

			elif tool_name == 'browser_save_state':
				return await self._save_state(path=arguments.get('path'))

			elif tool_name == 'browser_load_state':
				return await self._load_state(path=arguments['path'])

			elif tool_name == 'browser_wait_for_network_idle':
				return await self._wait_for_network_idle(timeout_seconds=arguments.get('timeout_seconds', 10.0), idle_duration_ms=arguments.get('idle_duration_ms', 500))

			elif tool_name == 'browser_right_click':
				return await self._right_click(ref=arguments.get('ref'), index=arguments.get('index'), coordinate_x=arguments.get('coordinate_x'), coordinate_y=arguments.get('coordinate_y'))

			elif tool_name == 'browser_get_cookies':
				return await self._get_cookies()

			elif tool_name == 'browser_set_cookies':
				return await self._set_cookies(arguments['cookies'])

			elif tool_name == 'browser_clear_cookies':
				return await self._clear_cookies(name=arguments.get('name'))

			elif tool_name == 'browser_get_console_logs':
				return await self._get_console_logs(level=arguments.get('level', 'all'), max_entries=arguments.get('max_entries', 50))

			elif tool_name == 'browser_get_network_log':
				return await self._get_network_log(type_filter=arguments.get('type_filter', 'all'), status_filter=arguments.get('status_filter', 'all'), max_entries=arguments.get('max_entries', 50))

		return f'Unknown tool: {tool_name}'

	async def _init_browser_session(self, allowed_domains: list[str] | None = None, **kwargs):
		"""Initialize browser session using config.

		When self._cdp_url is set (parallel tab mode), attaches to the shared browser
		and creates a new isolated tab instead of launching a new browser process.
		"""
		if self.browser_session:
			return

		# Ensure all logging goes to stderr before browser initialization
		_ensure_all_loggers_use_stderr()

		logger.debug('Initializing browser session...')

		# Get profile config
		from agentyc.browser import BrowserProfile, BrowserSession
		from agentyc.config import get_default_profile
		from agentyc.tools.service import Tools

		profile_config = get_default_profile(self.config)

		cdp_url = self._cdp_url or kwargs.pop('cdp_url', None)

		if cdp_url:
			# Parallel tab mode: attach to shared browser, create a new tab for this agent.
			# keep_alive=True so we don't kill the shared browser when this session ends.
			profile_data: dict[str, Any] = {
				'cdp_url': cdp_url,
				'keep_alive': True,
				'downloads_path': str(Path.home() / 'Downloads' / 'agentyc-mcp'),
				'device_scale_factor': 1.0,
				'disable_security': False,
				**profile_config,
			}
			if allowed_domains is not None:
				profile_data['allowed_domains'] = allowed_domains
			for key, value in kwargs.items():
				profile_data[key] = value
			profile = BrowserProfile(**profile_data)
			self.browser_session = BrowserSession(browser_profile=profile)
			await self.browser_session.start()
			# Open a fresh tab so this agent has its own isolated context within the shared browser
			new_target_id = await self.browser_session._cdp_create_new_page('about:blank')
			from agentyc.browser.events import TabCreatedEvent
			await self.browser_session.event_bus.dispatch(TabCreatedEvent(target_id=new_target_id, url='about:blank'))
			self.browser_session.agent_focus_target_id = new_target_id
		else:
			# Normal mode: launch a new browser process.
			profile_data = {
				'downloads_path': str(Path.home() / 'Downloads' / 'agentyc-mcp'),
				'keep_alive': False,
				'user_data_dir': '~/.config/agentyc/profiles/default',
				'device_scale_factor': 1.0,
				'disable_security': False,
				'headless': False,
				**profile_config,
			}
			if allowed_domains is not None:
				profile_data['allowed_domains'] = allowed_domains
			for key, value in kwargs.items():
				profile_data[key] = value
			profile = BrowserProfile(**profile_data)
			self.browser_session = BrowserSession(browser_profile=profile)
			await self.browser_session.start()

		# Track the session for management
		self._track_session(self.browser_session)

		# Create tools for direct actions
		self.tools = Tools()
		self.tools.set_coordinate_clicking(True)
		self.file_system = None
		file_system_path = profile_config.get('file_system_path', '~/.agentyc-mcp')
		self._file_system_base_dir = Path(file_system_path).expanduser()

		# Register native CDP event listeners for console and network capture
		try:
			await self._register_cdp_event_listeners()
		except Exception as _e:
			logger.debug(f'CDP event listener registration failed (non-critical): {_e}')

		logger.debug('Browser session initialized')

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

	def _cache_state_payload(self, payload: dict[str, Any]) -> None:
		payload_url = payload.get('url')
		if isinstance(payload_url, str) and payload_url != self._last_state_cache_url:
			self._last_state_elements_by_ref = {}
			self._last_state_cache_url = payload_url
		elements = payload.get('interactive_elements')
		if not isinstance(elements, list) or not elements:
			return
		self._last_state_elements_by_ref.update(
			{str(element['ref']): element for element in elements if isinstance(element, dict) and element.get('ref')}
		)

	async def _refresh_selector_map(self) -> None:
		if self.browser_session is None:
			return
		await self.browser_session.get_browser_state_summary(include_screenshot=False)

	async def _resolve_live_element(
		self,
		*,
		index: int | None = None,
		ref: str | None = None,
	) -> tuple[Any | None, int, bool]:
		if self.browser_session is None:
			raise RuntimeError('No browser session active')

		from agentyc.mcp.state import make_element_ref, summarize_interactive_element

		resolved_index = self._resolve_element_index(index=index, ref=ref)
		if ref is not None and self._last_state_elements_by_ref:
			await self._refresh_selector_map()
		element = await self.browser_session.get_dom_element_by_index(resolved_index)
		if element is not None:
			return element, resolved_index, False

		await self._refresh_selector_map()
		element = await self.browser_session.get_dom_element_by_index(resolved_index)
		if element is not None:
			return element, resolved_index, False

		reference_summary = self._last_state_elements_by_ref.get(make_element_ref(resolved_index))
		if reference_summary is None:
			return None, resolved_index, False

		selector_map = await self.browser_session.get_selector_map()
		best_candidate = None
		best_score = 0
		for candidate in selector_map.values():
			candidate_summary = summarize_interactive_element(candidate)
			score = 0
			strong_match = False
			if reference_summary.get('tag') == candidate_summary.get('tag'):
				score += 1
			for field_name, weight in (('text', 6), ('placeholder', 4), ('href', 5), ('context', 3), ('type', 2)):
				reference_value = str(reference_summary.get(field_name, '')).strip().lower()
				candidate_value = str(candidate_summary.get(field_name, '')).strip().lower()
				if not reference_value or not candidate_value:
					continue
				if reference_value == candidate_value:
					score += weight
					if field_name in {'text', 'placeholder', 'href'}:
						strong_match = True
			if reference_summary.get('disabled') == candidate_summary.get('disabled'):
				score += 1
			if strong_match and score > best_score:
				best_candidate = candidate
				best_score = score

		if best_candidate is None or best_score < 6:
			return None, resolved_index, False
		return best_candidate, best_candidate.backend_node_id, True

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
		return f'Error [{error_code}]: {message}'

	async def _run_tool_action(
		self,
		action_name: str,
		payload: dict[str, Any],
		available_file_paths: list[str] | None = None,
	) -> Any:
		if not self.browser_session:
			raise RuntimeError('No browser session active')
		if not self.tools:
			raise RuntimeError('Tools not initialized')

		from agentyc.actions import ActionModel
		from pydantic import create_model

		DynamicAction = self._action_model_cache.get(action_name)
		if DynamicAction is None:
			DynamicAction = cast(
				type[ActionModel],
				cast(Any, create_model)(
					f'MCP_{action_name}_Action',
					__base__=ActionModel,
					**{action_name: (dict[str, Any], ...)},
				),
			)
			self._action_model_cache[action_name] = DynamicAction
		action = DynamicAction.model_validate({action_name: payload})
		return await self.tools.act(
			action=action,
			browser_session=self.browser_session,
			page_extraction_llm=None,
			available_file_paths=available_file_paths,
			file_system=self.file_system,
		)

	def _inject_extraction_metadata(self, extracted_content: str, metadata: dict[str, Any] | None) -> str:
		if not metadata:
			return extracted_content
		visible_metadata = {
			'route': metadata.get('route') or metadata.get('strategy'),
			'llm_used': bool(metadata.get('llm_used', False)),
			'is_partial': bool(metadata.get('is_partial', False)),
			'structured_extraction': bool(metadata.get('structured_extraction', False)),
			'deterministic_extraction': bool(metadata.get('deterministic_extraction', False)),
		}
		if metadata.get('next_start_char') is not None:
			visible_metadata['next_start_char'] = metadata.get('next_start_char')
		return (
			f'{extracted_content}\n<extraction_metadata>\n{json.dumps(visible_metadata, sort_keys=True)}\n</extraction_metadata>'
		)

	async def _navigate(self, url: str, new_tab: bool = False) -> str:
		"""Navigate to a URL."""
		if not self.browser_session:
			return 'Error: No browser session active'

		# Update session activity
		self._update_session_activity(self.browser_session.id)
		before_tabs = len(await self.browser_session.get_tabs())
		action_result = await self._run_tool_action('navigate', {'url': url, 'new_tab': new_tab})
		if action_result.error:
			return self._format_action_error(action_result.error, default_code='navigation_failed')
		if new_tab:
			after_tabs = len(await self.browser_session.get_tabs())
			if after_tabs <= before_tabs:
				return self._format_action_error(
					f'Navigation to {url} did not open a new tab as requested.',
					default_code='postcondition_failed',
				)
			return f'Opened new tab with URL: {url}'
		# Ensure Runtime is enabled for the current session after navigation.
		# Idempotent — safe to call on every navigate. Enables console log capture
		# even when the tab was switched or a new tab was opened before this navigate.
		if self._cdp_client_for_runtime:
			try:
				_cdp_s = await self.browser_session.get_or_create_cdp_session(target_id=None, focus=False)
				await self._cdp_client_for_runtime.send.Runtime.enable(session_id=_cdp_s.session_id)
			except Exception:
				pass
		after_url = await self.browser_session.get_current_page_url()
		return f'Navigated to: {after_url}'

	async def _click(
		self,
		ref: str | None = None,
		index: int | None = None,
		coordinate_x: int | None = None,
		coordinate_y: int | None = None,
		new_tab: bool = False,
	) -> str:
		"""Click an element by index or at viewport coordinates."""
		if not self.browser_session:
			return 'Error: No browser session active'

		# Update session activity
		self._update_session_activity(self.browser_session.id)

		# Coordinate-based clicking
		if coordinate_x is not None and coordinate_y is not None:
			action_result = await self._run_tool_action(
				'click',
				{'coordinate_x': coordinate_x, 'coordinate_y': coordinate_y},
			)
			if action_result.error:
				return self._format_action_error(action_result.error, default_code='click_failed')
			return f'Clicked at coordinates ({coordinate_x}, {coordinate_y})'

		# Index-based clicking
		if ref is None and index is None:
			return 'Error: Provide either ref, index, or both coordinate_x and coordinate_y'

		element, resolved_index, drift_recovered = await self._resolve_live_element(index=index, ref=ref)
		if not element:
			return self._format_action_error(
				f'Element with ref/index {ref or resolved_index} was not found. Refresh browser state before retrying.',
				default_code='stale_ref',
			)
		validation_error = self._validate_actionable_element(element, action_name='click')
		if validation_error is not None:
			error_code, error_message = validation_error
			return f'Error [{error_code}]: {error_message}'

		if new_tab:
			# For links, extract href and open in new tab
			href = element.attributes.get('href')
			if href:
				# Convert relative href to absolute URL
				current_url = await self.browser_session.get_current_page_url()
				if href.startswith('/'):
					# Relative URL - construct full URL
					from urllib.parse import urlparse

					parsed = urlparse(current_url)
					full_url = f'{parsed.scheme}://{parsed.netloc}{href}'
				else:
					full_url = href

				# Open link in new tab
				before_tabs = len(await self.browser_session.get_tabs())
				action_result = await self._run_tool_action('navigate', {'url': full_url, 'new_tab': True})
				if action_result.error:
					return self._format_action_error(action_result.error, default_code='click_failed')
				after_tabs = len(await self.browser_session.get_tabs())
				if after_tabs <= before_tabs:
					return self._format_action_error(
						f'Click on element {ref or resolved_index} did not open a new tab.',
						default_code='postcondition_failed',
					)
				return f'Clicked element {ref or resolved_index} and opened in new tab {full_url[:20]}...'
			return self._format_action_error(
				f'Element {ref or resolved_index} does not support opening in a new tab because it has no href target.',
				default_code='invalid_target',
			)

		action_result = await self._run_tool_action('click', {'index': resolved_index})
		if action_result.error:
			return self._format_action_error(action_result.error, default_code='click_failed')
		if drift_recovered:
			return f'Clicked element {ref or resolved_index} (recovered after DOM drift)'
		return f'Clicked element {ref or resolved_index}'

	async def _type_text(self, text: str, index: int | None = None, ref: str | None = None) -> str:
		"""Type text into an element."""
		if not self.browser_session:
			return 'Error: No browser session active'

		element, resolved_index, drift_recovered = await self._resolve_live_element(index=index, ref=ref)
		if not element:
			return self._format_action_error(
				f'Element with ref/index {ref or resolved_index} was not found. Refresh browser state before retrying.',
				default_code='stale_ref',
			)
		validation_error = self._validate_actionable_element(element, action_name='type')
		if validation_error is not None:
			error_code, error_message = validation_error
			return f'Error [{error_code}]: {error_message}'

		from agentyc.browser.events import TypeTextEvent

		# Conservative heuristic to detect potentially sensitive data
		# Only flag very obvious patterns to minimize false positives
		is_potentially_sensitive = len(text) >= 6 and (
			# Email pattern: contains @ and a domain-like suffix
			('@' in text and '.' in text.split('@')[-1] if '@' in text else False)
			# Mixed alphanumeric with reasonable complexity (likely API keys/tokens)
			or (
				len(text) >= 16
				and any(char.isdigit() for char in text)
				and any(char.isalpha() for char in text)
				and any(char in '.-_' for char in text)
			)
		)

		# Use generic key names to avoid information leakage about detection patterns
		sensitive_key_name = None
		if is_potentially_sensitive:
			if '@' in text and '.' in text.split('@')[-1]:
				sensitive_key_name = 'email'
			else:
				sensitive_key_name = 'credential'

		event = self.browser_session.event_bus.dispatch(
			TypeTextEvent(node=element, text=text, is_sensitive=is_potentially_sensitive, sensitive_key_name=sensitive_key_name)
		)
		await event
		try:
			input_metadata = await event.event_result(raise_if_any=True, raise_if_none=False)
		except Exception as exc:
			return self._format_action_error(str(exc), default_code='type_failed')

		actual_value = None
		if isinstance(input_metadata, dict):
			actual_value = input_metadata.get('actual_value')
		if actual_value is not None and actual_value != text:
			if is_potentially_sensitive:
				# For sensitive inputs don't leak the actual value in the error
				return self._format_action_error(
					f'Element {ref or resolved_index} did not retain the typed sensitive value — the field may be read-only or disabled.',
					default_code='postcondition_failed',
				)
			# Allow format-only changes (phone, credit-card, date formatters strip/reorder punctuation).
			# If stripping non-alphanumeric characters from both produces the same result it is a
			# formatting transformation, not a content mismatch — no error needed.
			import re as _re

			_strip = lambda s: _re.sub(r'[^a-zA-Z0-9]', '', s).lower()
			if _strip(text) and _strip(text) == _strip(actual_value):
				pass  # format-only change, proceed normally
			else:
				return self._format_action_error(
					f"Element {ref or resolved_index} ended with value '{actual_value}' after typing '{text}' — the field may have transformed or rejected the input.",
					default_code='postcondition_failed',
				)

		if is_potentially_sensitive:
			if sensitive_key_name:
				prefix = f'Typed <{sensitive_key_name}> into element {ref or resolved_index}'
			else:
				prefix = f'Typed <sensitive> into element {ref or resolved_index}'
		else:
			prefix = f"Typed '{text}' into element {ref or resolved_index}"
		if drift_recovered:
			return f'{prefix} (recovered after DOM drift)'
		return prefix

	async def _upload_file(self, path: str, index: int | None = None, ref: str | None = None) -> str:
		"""Upload a file to a file input or nearby upload control."""
		if not self.browser_session:
			return 'Error: No browser session active'
		if not self.tools:
			return 'Error: Tools not initialized'
		if index is None and ref is None:
			return 'Error: Provide either ref or index'

		self._update_session_activity(self.browser_session.id)
		self._ensure_extract_runtime()

		element, resolved_index, drift_recovered = await self._resolve_live_element(index=index, ref=ref)
		if not element:
			return self._format_action_error(
				f'Element with ref/index {ref or resolved_index} was not found. Refresh browser state before retrying.',
				default_code='stale_ref',
			)

		action_result = await self._run_tool_action(
			'upload_file',
			{'index': resolved_index, 'path': path},
			available_file_paths=self._resolve_upload_available_file_paths(path),
		)
		if action_result.error:
			return self._format_action_error(action_result.error, default_code='upload_failed')

		message = action_result.extracted_content or f'Uploaded file {path} to element {ref or resolved_index}'
		if drift_recovered:
			message = f'{message} (recovered after DOM drift)'
		return message

	async def _get_browser_state(
		self,
		include_screenshot: bool = False,
		mode: str = 'auto',
		focus_ref: str | None = None,
		since_hash: str | None = None,
	) -> tuple[str, str | None]:
		"""Get current browser state. Returns (state_json, screenshot_b64 | None)."""
		if not self.browser_session:
			return 'Error: No browser session active', None

		from agentyc.mcp.state import build_browser_state_payload, make_element_ref

		state = await self.browser_session.get_browser_state_summary(include_screenshot=include_screenshot)
		try:
			result = build_browser_state_payload(
				state,
				mode=mode,  # type: ignore[arg-type]
				focus_ref=focus_ref,
				since_hash=since_hash,
			)
		except ValueError:
			if mode != 'focus' or focus_ref is None:
				raise
			element, resolved_index, _ = await self._resolve_live_element(ref=focus_ref)
			if element is None:
				raise
			recovered_focus_ref = make_element_ref(resolved_index)
			state = await self.browser_session.get_browser_state_summary(include_screenshot=include_screenshot)
			result = build_browser_state_payload(
				state,
				mode=mode,  # type: ignore[arg-type]
				focus_ref=recovered_focus_ref,
				since_hash=since_hash,
			)

		# Return screenshot separately as ImageContent instead of embedding base64 in JSON
		screenshot_b64 = None
		if include_screenshot and state.screenshot:
			screenshot_b64 = state.screenshot
			# Include viewport dimensions in JSON so callers can map pixels to coordinates
			if state.page_info:
				result['screenshot_dimensions'] = {
					'width': state.page_info.viewport_width,
					'height': state.page_info.viewport_height,
				}

		self._cache_state_payload(result)
		return json.dumps(result, indent=2), screenshot_b64

	async def _get_html(self, selector: str | None = None) -> str:
		"""Get raw HTML of the page or a specific element."""
		if not self.browser_session:
			return 'Error: No browser session active'

		self._update_session_activity(self.browser_session.id)

		cdp_session = await self.browser_session.get_or_create_cdp_session(target_id=None, focus=False)
		if not cdp_session:
			return 'Error: No active CDP session'

		if selector:
			js = (
				f'(function(){{ const el = document.querySelector({json.dumps(selector)}); return el ? el.outerHTML : null; }})()'
			)
		else:
			js = 'document.documentElement.outerHTML'

		result = await cdp_session.cdp_client.send.Runtime.evaluate(
			params={'expression': js, 'returnByValue': True},
			session_id=cdp_session.session_id,
		)
		html = result.get('result', {}).get('value')
		if html is None:
			return f'No element found for selector: {selector}' if selector else 'Error: Could not get page HTML'
		return html

	async def _screenshot(self, full_page: bool = False) -> tuple[str, str | None]:
		"""Take a screenshot. Returns (metadata_json, screenshot_b64 | None)."""
		if not self.browser_session:
			return 'Error: No browser session active', None

		import base64

		self._update_session_activity(self.browser_session.id)

		data = await self.browser_session.take_screenshot(full_page=full_page)
		b64 = base64.b64encode(data).decode()

		# Return screenshot separately as ImageContent instead of embedding base64 in JSON
		state = await self.browser_session.get_browser_state_summary(include_screenshot=False)
		result: dict[str, Any] = {
			'size_bytes': len(data),
		}
		if state.page_info:
			result['viewport'] = {
				'width': state.page_info.viewport_width,
				'height': state.page_info.viewport_height,
			}
		return json.dumps(result), b64

	async def _extract_content(
		self,
		query: str,
		extract_links: bool = False,
		output_schema: dict[str, Any] | None = None,
	) -> str:
		"""Extract content from current page."""
		if not self.browser_session:
			return 'Error: No browser session active'

		if not self.tools:
			return 'Error: Tools not initialized'

		self._ensure_extract_runtime()
		if not self.file_system:
			return 'Error: FileSystem not initialized'

		# Use the extract action
		# Create a dynamic action model that matches the tools's expectations
		from agentyc.actions import ActionModel
		from pydantic import create_model

		# Create action model dynamically
		ExtractAction = create_model(
			'ExtractAction',
			__base__=ActionModel,
			extract=dict[str, Any],
		)

		# Use model_validate because Pyright does not understand the dynamic model
		action = ExtractAction.model_validate(
			{
				'extract': {'query': query, 'extract_links': extract_links, 'output_schema': output_schema},
			}
		)
		action_result = await self.tools.act(
			action=action,
			browser_session=self.browser_session,
			page_extraction_llm=None,
			file_system=self.file_system,
		)
		if action_result.error:
			return self._format_action_error(action_result.error, default_code='extraction_failed')
		extracted_content = action_result.extracted_content or 'No content extracted'
		return self._inject_extraction_metadata(extracted_content, action_result.metadata)

	async def _scroll(
		self,
		direction: str = 'down',
		pages: float = 1.0,
		ref: str | None = None,
		index: int | None = None,
	) -> str:
		"""Scroll the page or a specific element."""
		if not self.browser_session:
			return 'Error: No browser session active'

		self._update_session_activity(self.browser_session.id)

		payload: dict[str, Any] = {'down': direction == 'down', 'pages': pages}
		if ref is not None or index is not None:
			element, resolved_index, _ = await self._resolve_live_element(index=index, ref=ref)
			if element is None:
				return self._format_action_error(
					f'Element {ref or index} not found for scroll.',
					default_code='stale_ref',
				)
			payload['index'] = resolved_index

		action_result = await self._run_tool_action('scroll', payload)
		if action_result.error:
			return self._format_action_error(action_result.error, default_code='scroll_failed')
		return action_result.extracted_content or f'Scrolled {direction}'

	async def _go_back(self) -> str:
		"""Go back in browser history."""
		if not self.browser_session:
			return 'Error: No browser session active'

		from agentyc.browser.events import GoBackEvent

		event = self.browser_session.event_bus.dispatch(GoBackEvent())
		await event
		await event.event_result(raise_if_any=True, raise_if_none=False)
		after_url = await self.browser_session.get_current_page_url()
		return f'Navigated back to: {after_url}'

	async def _go_forward(self) -> str:
		"""Go forward in browser history."""
		if not self.browser_session:
			return 'Error: No browser session active'

		from agentyc.browser.events import GoForwardEvent

		event = self.browser_session.event_bus.dispatch(GoForwardEvent())
		await event
		await event.event_result(raise_if_any=True, raise_if_none=False)
		after_url = await self.browser_session.get_current_page_url()
		return f'Navigated forward to: {after_url}'

	async def _refresh(self) -> str:
		"""Refresh the current page."""
		if not self.browser_session:
			return 'Error: No browser session active'

		from agentyc.browser.events import RefreshEvent

		self._update_session_activity(self.browser_session.id)
		event = self.browser_session.event_bus.dispatch(RefreshEvent())
		await event
		await event.event_result(raise_if_any=True, raise_if_none=False)
		after_url = await self.browser_session.get_current_page_url()
		return f'Refreshed page: {after_url}'

	async def _press_key(self, key: str) -> str:
		"""Send a keyboard key or shortcut."""
		if not self.browser_session:
			return 'Error: No browser session active'

		from agentyc.browser.events import SendKeysEvent

		self._update_session_activity(self.browser_session.id)
		event = self.browser_session.event_bus.dispatch(SendKeysEvent(keys=key))
		await event
		await event.event_result(raise_if_any=True, raise_if_none=False)
		return f'Pressed key: {key}'

	async def _wait(self, seconds: float = 2) -> str:
		"""Wait for a number of seconds (max 30)."""
		actual = min(max(float(seconds), 0), 30)
		await asyncio.sleep(actual)
		return f'Waited {actual:.1f}s'

	async def _evaluate(self, code: str) -> str:
		"""Execute JavaScript in the page context."""
		if not self.browser_session:
			return 'Error: No browser session active'

		self._update_session_activity(self.browser_session.id)
		action_result = await self._run_tool_action('evaluate', {'code': code})
		if action_result.error:
			return self._format_action_error(action_result.error, default_code='evaluate_failed')
		return action_result.extracted_content or 'undefined'

	async def _select_option(self, text: str, ref: str | None = None, index: int | None = None) -> str:
		"""Select an option in a <select> or ARIA dropdown."""
		if not self.browser_session:
			return 'Error: No browser session active'
		if ref is None and index is None:
			return 'Error: Provide either ref or index'

		self._update_session_activity(self.browser_session.id)
		element, resolved_index, drift_recovered = await self._resolve_live_element(index=index, ref=ref)
		if element is None:
			return self._format_action_error(
				f'Element {ref or index} not found. Refresh browser state before retrying.',
				default_code='stale_ref',
			)

		action_result = await self._run_tool_action('select_dropdown', {'index': resolved_index, 'text': text})
		if action_result.error:
			return self._format_action_error(action_result.error, default_code='select_failed')
		msg = action_result.extracted_content or f"Selected '{text}' in element {ref or resolved_index}"
		if drift_recovered:
			msg = f'{msg} (recovered after DOM drift)'
		return msg

	async def _get_dropdown_options(self, ref: str | None = None, index: int | None = None) -> str:
		"""Get all options from a dropdown element."""
		if not self.browser_session:
			return 'Error: No browser session active'
		if ref is None and index is None:
			return 'Error: Provide either ref or index'

		self._update_session_activity(self.browser_session.id)
		element, resolved_index, _ = await self._resolve_live_element(index=index, ref=ref)
		if element is None:
			return self._format_action_error(
				f'Element {ref or index} not found. Refresh browser state before retrying.',
				default_code='stale_ref',
			)

		action_result = await self._run_tool_action('dropdown_options', {'index': resolved_index})
		if action_result.error:
			return self._format_action_error(action_result.error, default_code='dropdown_failed')
		return action_result.extracted_content or 'No options found'

	async def _find_elements(
		self,
		selector: str,
		attributes: list[str] | None = None,
		max_results: int = 50,
	) -> str:
		"""Find elements by CSS selector."""
		if not self.browser_session:
			return 'Error: No browser session active'

		self._update_session_activity(self.browser_session.id)
		payload: dict[str, Any] = {'selector': selector, 'max_results': max_results}
		if attributes:
			payload['attributes'] = attributes

		action_result = await self._run_tool_action('find_elements', payload)
		if action_result.error:
			return self._format_action_error(action_result.error, default_code='find_elements_failed')
		return action_result.extracted_content or 'No elements found'

	async def _wait_for_element(
		self,
		text: str | None = None,
		ref: str | None = None,
		appear: bool = True,
		timeout_seconds: float = 10,
	) -> str:
		"""Poll until an element matching text or ref appears or disappears."""
		if not self.browser_session:
			return 'Error: No browser session active'
		if not text and not ref:
			return 'Error: Provide either text or ref to wait for'

		timeout = min(max(float(timeout_seconds), 0.5), 30)
		interval = 0.5
		elapsed = 0.0
		last_hash: str | None = None

		from agentyc.mcp.state import build_browser_state_payload, parse_element_ref

		ref_index: int | None = None
		if ref:
			try:
				ref_index = parse_element_ref(ref)
			except ValueError as e:
				return f'Error: {e}'

		while elapsed < timeout:
			state = await self.browser_session.get_browser_state_summary(include_screenshot=False)
			selector_map = state.dom_state.selector_map

			if ref_index is not None:
				found = ref_index in selector_map
			else:
				assert text is not None
				needle = text.lower()
				found = any(
					needle in (element.get_meaningful_text_for_llm() or '').lower()
					for element in selector_map.values()
				)

			if found == appear:
				verb = 'appeared' if appear else 'disappeared'
				target = ref or f'"{text}"'
				return f'Element {target} {verb} after {elapsed:.1f}s'

			await asyncio.sleep(interval)
			elapsed += interval

		verb = 'appear' if appear else 'disappear'
		target = ref or f'"{text}"'
		return f'Error [timeout]: Element {target} did not {verb} within {timeout:.0f}s'

	async def _search_page(self, pattern: str, regex: bool = False, max_results: int = 25) -> str:
		"""Search for text or regex pattern on the current page."""
		if not self.browser_session:
			return 'Error: No browser session active'

		self._update_session_activity(self.browser_session.id)
		action_result = await self._run_tool_action(
			'search_page',
			{'pattern': pattern, 'regex': regex, 'max_results': max_results, 'context_chars': 150},
		)
		if action_result.error:
			return self._format_action_error(action_result.error, default_code='search_failed')
		return action_result.extracted_content or f'No matches found for: {pattern}'

	async def _get_viewport_coords(self, backend_node_id: int) -> tuple[float, float] | None:
		"""Get viewport-relative center coordinates for an element using DOM.getContentQuads.

		This is the same approach used by the click watchdog — `getContentQuads` returns live
		viewport coordinates (not cached snapshot data, which may be stale or zeroed in headless mode).
		"""
		try:
			cdp_session = await self.browser_session.get_or_create_cdp_session(target_id=None, focus=False)  # type: ignore[union-attr]
			result = await cdp_session.cdp_client.send.DOM.getContentQuads(
				params={'backendNodeId': backend_node_id},
				session_id=cdp_session.session_id,
			)
			quads = result.get('quads', [])
			if not quads:
				return None
			quad = quads[0]
			if len(quad) < 8:
				return None
			cx = sum(quad[i] for i in range(0, 8, 2)) / 4
			cy = sum(quad[i] for i in range(1, 8, 2)) / 4
			return cx, cy
		except Exception:
			return None

	async def _resolve_element_coords(self, ref: str | None, index: int | None, fallback_x: int | None, fallback_y: int | None) -> tuple[float, float]:
		"""Resolve element ref/index to viewport coordinates using live CDP quads.

		Falls back to absolute_position (snapshot data) when getContentQuads is unavailable.
		Raises ValueError if coordinates cannot be determined.
		"""
		if fallback_x is not None and fallback_y is not None:
			return float(fallback_x), float(fallback_y)
		if ref is None and index is None:
			raise ValueError('Provide ref/index or explicit coordinates')
		resolved_index = self._resolve_element_index(index=index, ref=ref)
		# Try live CDP quads first (most accurate — viewport-relative, reflects current scroll)
		coords = await self._get_viewport_coords(resolved_index)
		if coords:
			return coords
		# Fallback: cached snapshot absolute_position (document coords, usually == viewport for top of page)
		element = await self.browser_session.get_dom_element_by_index(resolved_index)  # type: ignore[union-attr]
		if element is None:
			await self._refresh_selector_map()
			element = await self.browser_session.get_dom_element_by_index(resolved_index)  # type: ignore[union-attr]
		if element is None:
			raise ValueError(f'Element {ref or resolved_index} not found. Refresh state first.')
		if element.absolute_position:
			p = element.absolute_position
			return p.x + p.width / 2, p.y + p.height / 2
		raise ValueError(f'Could not determine coordinates for element {ref or resolved_index}')

	async def _hover(self, ref: str | None = None, index: int | None = None, coordinate_x: int | None = None, coordinate_y: int | None = None) -> str:
		"""Hover over element to trigger CSS :hover and JS mouseover/mouseenter."""
		if not self.browser_session:
			return 'Error: No browser session active'
		self._update_session_activity(self.browser_session.id)
		try:
			x, y = await self._resolve_element_coords(ref, index, coordinate_x, coordinate_y)
			cdp_session = await self.browser_session.get_or_create_cdp_session(target_id=None, focus=False)
			await cdp_session.cdp_client.send.Input.dispatchMouseEvent(
				params={'type': 'mouseMoved', 'x': x, 'y': y, 'button': 'none'},
				session_id=cdp_session.session_id,
			)
			await asyncio.sleep(0.1)
			label = ref or (f'index {index}' if index else f'({coordinate_x},{coordinate_y})')
			return f'Hovered over {label}. Use browser_get_state to see hover-triggered elements (dropdowns, tooltips, etc.).'
		except ValueError as e:
			return self._format_action_error(str(e), default_code='stale_ref')
		except Exception as e:
			return self._format_action_error(str(e), default_code='action_failed')

	async def _double_click(self, ref: str | None = None, index: int | None = None, coordinate_x: int | None = None, coordinate_y: int | None = None) -> str:
		"""Double-click an element."""
		if not self.browser_session:
			return 'Error: No browser session active'
		self._update_session_activity(self.browser_session.id)
		try:
			x, y = await self._resolve_element_coords(ref, index, coordinate_x, coordinate_y)
			cdp_session = await self.browser_session.get_or_create_cdp_session(target_id=None, focus=False)
			sid = cdp_session.session_id
			for _ in range(2):
				await cdp_session.cdp_client.send.Input.dispatchMouseEvent(
					params={'type': 'mousePressed', 'x': x, 'y': y, 'button': 'left', 'clickCount': 2},
					session_id=sid,
				)
				await cdp_session.cdp_client.send.Input.dispatchMouseEvent(
					params={'type': 'mouseReleased', 'x': x, 'y': y, 'button': 'left', 'clickCount': 2},
					session_id=sid,
				)
			label = ref or (f'index {index}' if index else f'({coordinate_x},{coordinate_y})')
			return f'Double-clicked {label}'
		except ValueError as e:
			return self._format_action_error(str(e), default_code='stale_ref')
		except Exception as e:
			return self._format_action_error(str(e), default_code='action_failed')

	async def _drag_to(self, source_ref: str | None = None, target_ref: str | None = None, source_x: int | None = None, source_y: int | None = None, target_x: int | None = None, target_y: int | None = None, steps: int = 10) -> str:
		"""Drag from one element or coordinate to another."""
		if not self.browser_session:
			return 'Error: No browser session active'
		self._update_session_activity(self.browser_session.id)
		try:
			sx, sy = await self._resolve_element_coords(source_ref, None, source_x, source_y)
			tx, ty = await self._resolve_element_coords(target_ref, None, target_x, target_y)

			cdp_session = await self.browser_session.get_or_create_cdp_session(target_id=None, focus=False)
			sid = cdp_session.session_id

			await cdp_session.cdp_client.send.Input.dispatchMouseEvent(
				params={'type': 'mouseMoved', 'x': sx, 'y': sy}, session_id=sid
			)
			await asyncio.sleep(0.05)
			await cdp_session.cdp_client.send.Input.dispatchMouseEvent(
				params={'type': 'mousePressed', 'x': sx, 'y': sy, 'button': 'left', 'clickCount': 1}, session_id=sid
			)
			await asyncio.sleep(0.05)

			n = max(steps, 2)
			for i in range(1, n + 1):
				mx = sx + (tx - sx) * i / n
				my = sy + (ty - sy) * i / n
				await cdp_session.cdp_client.send.Input.dispatchMouseEvent(
					params={'type': 'mouseMoved', 'x': mx, 'y': my, 'button': 'left'}, session_id=sid
				)
				await asyncio.sleep(0.02)

			await cdp_session.cdp_client.send.Input.dispatchMouseEvent(
				params={'type': 'mouseReleased', 'x': tx, 'y': ty, 'button': 'left', 'clickCount': 1}, session_id=sid
			)

			return f'Dragged from ({sx:.0f},{sy:.0f}) to ({tx:.0f},{ty:.0f})'
		except ValueError as e:
			return self._format_action_error(str(e), default_code='invalid_argument')
		except Exception as e:
			return self._format_action_error(str(e), default_code='action_failed')

	async def _scroll_to_text(self, text: str) -> str:
		"""Scroll page until given text is visible in the viewport."""
		if not self.browser_session:
			return 'Error: No browser session active'
		self._update_session_activity(self.browser_session.id)
		from agentyc.browser.events import ScrollToTextEvent

		event = self.browser_session.event_bus.dispatch(ScrollToTextEvent(text=text))
		await event
		try:
			await event.event_result(raise_if_any=True, raise_if_none=False)
		except Exception as e:
			return self._format_action_error(str(e), default_code='not_found')
		return f'Scrolled to text: {text!r}'

	async def _save_state(self, path: str | None = None) -> str:
		"""Save browser session state (cookies, localStorage) to a file."""
		if not self.browser_session:
			return 'Error: No browser session active'
		self._update_session_activity(self.browser_session.id)
		from agentyc.browser.events import SaveStorageStateEvent

		save_path = path or str(Path.home() / '.agentyc-mcp' / 'browser-state.json')
		event = self.browser_session.event_bus.dispatch(SaveStorageStateEvent(path=save_path))
		await event
		try:
			await event.event_result(raise_if_any=True, raise_if_none=False)
		except Exception as e:
			return self._format_action_error(str(e), default_code='action_failed')
		return f'Browser state saved to: {save_path}'

	async def _load_state(self, path: str) -> str:
		"""Restore browser session state from a file."""
		if not self.browser_session:
			return 'Error: No browser session active'
		self._update_session_activity(self.browser_session.id)
		from agentyc.browser.events import LoadStorageStateEvent

		event = self.browser_session.event_bus.dispatch(LoadStorageStateEvent(path=path))
		await event
		try:
			await event.event_result(raise_if_any=True, raise_if_none=False)
		except Exception as e:
			return self._format_action_error(str(e), default_code='action_failed')
		return f'Browser state loaded from: {path}'

	async def _wait_for_network_idle(self, timeout_seconds: float = 10.0, idle_duration_ms: int = 500) -> str:
		"""Wait until no pending network requests for idle_duration_ms."""
		if not self.browser_session:
			return 'Error: No browser session active'
		timeout = min(timeout_seconds, 30.0)
		idle_needed = idle_duration_ms / 1000.0
		import time as _time

		start = _time.monotonic()
		try:
			cdp_session = await self.browser_session.get_or_create_cdp_session(target_id=None, focus=False)
			if hasattr(cdp_session, '_lifecycle_events'):
				deadline = start + timeout
				while _time.monotonic() < deadline:
					events = list(cdp_session._lifecycle_events)
					if any(e.get('name') == 'networkIdle' for e in events):
						elapsed = _time.monotonic() - start
						return f'Network idle after {elapsed:.1f}s'
					await asyncio.sleep(0.2)
				elapsed = _time.monotonic() - start
				return f'Network idle wait timed out after {elapsed:.1f}s — proceeding'
			else:
				await asyncio.sleep(min(idle_needed * 2, 2.0))
				elapsed = _time.monotonic() - start
				return f'Waited {elapsed:.1f}s for network activity to settle'
		except Exception as e:
			await asyncio.sleep(min(idle_needed, 1.0))
			return f'Network idle wait completed (fallback mode): {e}'

	async def _right_click(self, ref: str | None = None, index: int | None = None, coordinate_x: int | None = None, coordinate_y: int | None = None) -> str:
		"""Right-click an element to open its context menu."""
		if not self.browser_session:
			return 'Error: No browser session active'
		self._update_session_activity(self.browser_session.id)
		try:
			cx, cy = await self._resolve_element_coords(ref=ref, index=index, fallback_x=coordinate_x, fallback_y=coordinate_y)
		except ValueError as e:
			return self._format_action_error(str(e), default_code='element_not_found')
		cdp_session = await self.browser_session.get_or_create_cdp_session(target_id=None, focus=False)
		for event_type in ('mousePressed', 'mouseReleased'):
			await cdp_session.cdp_client.send.Input.dispatchMouseEvent(
				params={'type': event_type, 'x': cx, 'y': cy, 'button': 'right', 'clickCount': 1, 'buttons': 2},
				session_id=cdp_session.session_id,
			)
		return f'Right-clicked at ({cx:.0f},{cy:.0f})'

	async def _get_cookies(self) -> str:
		"""Return all cookies for the current page URL."""
		if not self.browser_session:
			return 'Error: No browser session active'
		self._update_session_activity(self.browser_session.id)
		cdp_session = await self.browser_session.get_or_create_cdp_session(target_id=None, focus=False)
		try:
			url = await self.browser_session.get_current_page_url()
			result = await cdp_session.cdp_client.send.Network.getCookies(
				params={'urls': [url]},
				session_id=cdp_session.session_id,
			)
			cookies = result.get('cookies', [])
			if not cookies:
				return 'No cookies found for the current page'
			simplified = [
				{k: v for k, v in c.items() if k in ('name', 'value', 'domain', 'path', 'secure', 'httpOnly', 'expires')}
				for c in cookies
			]
			return json.dumps(simplified, indent=2)
		except Exception as e:
			return self._format_action_error(str(e), default_code='action_failed')

	async def _set_cookies(self, cookies: list[dict[str, Any]]) -> str:
		"""Set one or more cookies."""
		if not self.browser_session:
			return 'Error: No browser session active'
		self._update_session_activity(self.browser_session.id)
		cdp_session = await self.browser_session.get_or_create_cdp_session(target_id=None, focus=False)
		try:
			await cdp_session.cdp_client.send.Network.setCookies(
				params=cast(Any, {'cookies': cookies}),
				session_id=cdp_session.session_id,
			)
			names = [c.get('name', '?') for c in cookies]
			return f'Set {len(cookies)} cookie(s): {", ".join(names)}'
		except Exception as e:
			return self._format_action_error(str(e), default_code='action_failed')

	async def _clear_cookies(self, name: str | None = None) -> str:
		"""Clear cookies — all for the current domain, or a specific cookie by name."""
		if not self.browser_session:
			return 'Error: No browser session active'
		self._update_session_activity(self.browser_session.id)
		cdp_session = await self.browser_session.get_or_create_cdp_session(target_id=None, focus=False)
		try:
			if name:
				url = await self.browser_session.get_current_page_url()
				await cdp_session.cdp_client.send.Network.deleteCookies(
					params={'name': name, 'url': url},
					session_id=cdp_session.session_id,
				)
				return f'Deleted cookie: {name}'
			else:
				await cdp_session.cdp_client.send.Network.clearBrowserCookies(
					session_id=cdp_session.session_id,
				)
				return 'Cleared all browser cookies'
		except Exception as e:
			return self._format_action_error(str(e), default_code='action_failed')

	async def _register_cdp_event_listeners(self) -> None:
		"""Register native CDP event listeners for console and network capture.

		Called once after session start. Uses Runtime.consoleAPICalled (not JS injection)
		so logs are captured from page load, including errors in <script> tags.
		Network events are filtered by session_id so parallel tabs don't bleed into each other.
		"""
		if self._cdp_events_registered or not self.browser_session:
			return
		cdp_session = await self.browser_session.get_or_create_cdp_session(target_id=None, focus=False)
		our_session_id = cdp_session.session_id

		# Enable Runtime domain for all current sessions so consoleAPICalled events fire.
		# Must be done for EACH session (each open tab), not just the focus tab.
		sm = getattr(self.browser_session, 'session_manager', None)
		all_sids: list[str] = list(sm.get_all_sessions().keys()) if sm else [our_session_id]
		if not all_sids:
			all_sids = [our_session_id]
		for _sid in all_sids:
			try:
				await cdp_session.cdp_client.send.Runtime.enable(session_id=_sid)
			except Exception:
				pass

		# Store the cdp_client reference so _ensure_runtime_enabled can use it for new sessions
		self._cdp_client_for_runtime = cdp_session.cdp_client

		# --- Console capture ---
		def _arg_to_str(arg: Any) -> str:
			if isinstance(arg, dict):
				# RemoteObject: prefer description (e.g. "Error: msg"), then value, then type
				return str(arg.get('description') or arg.get('value') or arg.get('type') or '')
			return str(arg)

		def _ms_to_ts(ts_ms: float) -> str:
			# Runtime.Timestamp is milliseconds since epoch (not seconds)
			import datetime as _dt
			try:
				dt = _dt.datetime.utcfromtimestamp(ts_ms / 1000.0)
				return dt.strftime('%H:%M:%S.') + f'{int(ts_ms % 1000):03d}'
			except (OSError, OverflowError, ValueError):
				return ''

		def _is_our_session(session_id: str | None) -> bool:
			# Accept events from any session belonging to this browser instance.
			# SessionManager._sessions maps SessionID -> CDPSession; checking membership
			# automatically handles tab switches and new tab opens.
			if self.browser_session is None:
				return False
			sm = getattr(self.browser_session, 'session_manager', None)
			if sm is None:
				return True  # no session manager — accept all
			sessions: dict[str, Any] = getattr(sm, '_sessions', {}) or {}
			return session_id in sessions

		def on_console_api_called(event: Any, session_id: str | None = None) -> None:
			if not _is_our_session(session_id):
				return
			if not isinstance(event, dict):
				return
			level = str(event.get('type', 'log'))
			# Normalize: CDP uses 'warning' but convention is 'warn'
			if level == 'warning':
				level = 'warn'
			args = event.get('args') or []
			text = ' '.join(_arg_to_str(a) for a in args).strip()
			if not text:
				return
			self._console_log_buffer.append({'level': level, 'time': _ms_to_ts(event.get('timestamp') or 0), 'text': text})

		def on_exception_thrown(event: Any, session_id: str | None = None) -> None:
			if not _is_our_session(session_id):
				return
			if not isinstance(event, dict):
				return
			details = event.get('exceptionDetails') or {}
			msg = details.get('text') or ''
			exc = details.get('exception') or {}
			desc = exc.get('description') or exc.get('value') or ''
			text = f'{msg} {desc}'.strip() or 'Unknown exception'
			url = details.get('url', '')
			line = details.get('lineNumber', '')
			if url:
				text = f'{text} at {url}:{line}'
			# exceptionDetails.timestamp is also in milliseconds
			ts_ms = event.get('timestamp') or 0
			self._console_log_buffer.append({'level': 'error', 'time': _ms_to_ts(ts_ms), 'text': text})

		cdp_session.cdp_client.register.Runtime.consoleAPICalled(on_console_api_called)
		cdp_session.cdp_client.register.Runtime.exceptionThrown(on_exception_thrown)

		# --- Network capture ---
		# Network domain is already enabled by SessionManager; just register handlers.
		def on_request_will_be_sent(event: Any, session_id: str | None = None) -> None:
			if not _is_our_session(session_id):
				return
			if not isinstance(event, dict):
				return
			req_id = event.get('requestId', '')
			req = event.get('request') or {}
			entry: dict[str, Any] = {
				'request_id': req_id,
				'url': req.get('url', ''),
				'method': req.get('method', 'GET'),
				'type': event.get('type', 'Other'),
				'status': None,
				'status_text': None,
				'error': None,
				'start_time': event.get('timestamp', time.time()),
				'duration_ms': None,
			}
			self._network_pending[req_id] = entry

		def on_response_received(event: Any, session_id: str | None = None) -> None:
			if not _is_our_session(session_id):
				return
			if not isinstance(event, dict):
				return
			req_id = event.get('requestId', '')
			entry = self._network_pending.get(req_id)
			if entry is None:
				return
			resp = event.get('response') or {}
			entry['status'] = resp.get('status')
			entry['status_text'] = resp.get('statusText')
			now = event.get('timestamp', time.time())
			start = entry['start_time']
			entry['duration_ms'] = round((now - start) * 1000, 1)
			self._network_log_buffer.append(dict(entry))
			self._network_pending.pop(req_id, None)

		def on_loading_failed(event: Any, session_id: str | None = None) -> None:
			if not _is_our_session(session_id):
				return
			if not isinstance(event, dict):
				return
			req_id = event.get('requestId', '')
			entry = self._network_pending.get(req_id)
			if entry is None:
				return
			entry['error'] = event.get('errorText', 'Failed')
			now = event.get('timestamp', time.time())
			entry['duration_ms'] = round((now - entry['start_time']) * 1000, 1)
			self._network_log_buffer.append(dict(entry))
			self._network_pending.pop(req_id, None)

		cdp_session.cdp_client.register.Network.requestWillBeSent(on_request_will_be_sent)
		cdp_session.cdp_client.register.Network.responseReceived(on_response_received)
		cdp_session.cdp_client.register.Network.loadingFailed(on_loading_failed)

		self._cdp_events_registered = True

	async def _get_console_logs(self, level: str = 'all', max_entries: int = 50) -> str:
		"""Return recent browser console messages from the CDP-native capture buffer."""
		if not self.browser_session:
			return 'Error: No browser session active'
		self._update_session_activity(self.browser_session.id)
		entries = list(self._console_log_buffer)
		if level != 'all':
			entries = [e for e in entries if e.get('level') == level]
		entries = entries[-max_entries:]
		return json.dumps(entries, indent=2)

	async def _get_network_log(self, type_filter: str = 'all', status_filter: str = 'all', max_entries: int = 50) -> str:
		"""Return recent network requests captured via CDP Network domain events."""
		if not self.browser_session:
			return 'Error: No browser session active'
		self._update_session_activity(self.browser_session.id)
		entries = list(self._network_log_buffer)
		# Filter by request type (xhr, fetch, document, etc.)
		if type_filter != 'all':
			entries = [e for e in entries if e.get('type', '').lower() == type_filter.lower()]
		# Filter by status category
		if status_filter == 'errors':
			entries = [e for e in entries if e.get('error') or (e.get('status') or 0) >= 400]
		elif status_filter == 'success':
			entries = [e for e in entries if not e.get('error') and 200 <= (e.get('status') or 0) < 400]
		entries = entries[-max_entries:]
		if not entries:
			return json.dumps([], indent=2)
		# Strip internal fields not useful to the agent
		display = [{k: v for k, v in e.items() if k != 'request_id' and v is not None} for e in entries]
		return json.dumps(display, indent=2)

	async def _get_focused_element(self) -> str:
		"""Return information about the element that currently has keyboard focus."""
		if not self.browser_session:
			return 'Error: No browser session active'

		self._update_session_activity(self.browser_session.id)
		cdp_session = await self.browser_session.get_or_create_cdp_session(target_id=None, focus=False)
		if not cdp_session:
			return 'Error: No active CDP session'

		js = """(function() {
			const el = document.activeElement;
			if (!el || el.tagName === 'BODY' || el.tagName === 'HTML') return null;
			return {
				tag: el.tagName.toLowerCase(),
				id: el.id || null,
				name: el.getAttribute('name') || null,
				type: el.getAttribute('type') || null,
				placeholder: el.getAttribute('placeholder') || null,
				ariaLabel: el.getAttribute('aria-label') || null,
				value: el.value !== undefined ? String(el.value).substring(0, 100) : (el.textContent || '').trim().substring(0, 100),
				role: el.getAttribute('role') || null
			};
		})()"""

		result = await cdp_session.cdp_client.send.Runtime.evaluate(
			params={'expression': js, 'returnByValue': True},
			session_id=cdp_session.session_id,
		)
		value = result.get('result', {}).get('value')
		if value is None:
			return 'No element has focus (or focus is on document body)'
		return json.dumps(value, indent=2)

	async def _list_tabs(self) -> str:
		"""List all open tabs."""
		if not self.browser_session:
			return 'Error: No browser session active'

		tabs_info = await self.browser_session.get_tabs()
		tabs = []
		for i, tab in enumerate(tabs_info):
			tabs.append({'tab_id': tab.target_id[-4:], 'url': tab.url, 'title': tab.title or ''})
		return json.dumps(tabs, indent=2)

	async def _switch_tab(self, tab_id: str) -> str:
		"""Switch to a different tab."""
		if not self.browser_session:
			return 'Error: No browser session active'

		from agentyc.browser.events import SwitchTabEvent

		target_id = await self.browser_session.get_target_id_from_tab_id(tab_id)
		event = self.browser_session.event_bus.dispatch(SwitchTabEvent(target_id=target_id))
		await event
		await event.event_result(raise_if_any=True, raise_if_none=False)
		if self.browser_session.agent_focus_target_id != target_id:
			return self._format_action_error(
				f'Switch tab to {tab_id} completed but the requested tab did not become active.',
				default_code='postcondition_failed',
			)
		current_url = await self.browser_session.get_current_page_url()
		return f'Switched to tab {tab_id}: {current_url}'

	async def _close_tab(self, tab_id: str) -> str:
		"""Close a specific tab."""
		if not self.browser_session:
			return 'Error: No browser session active'

		from agentyc.browser.events import CloseTabEvent

		target_id = await self.browser_session.get_target_id_from_tab_id(tab_id)
		event = self.browser_session.event_bus.dispatch(CloseTabEvent(target_id=target_id))
		await event
		await event.event_result(raise_if_any=True, raise_if_none=False)
		if any(tab.target_id == target_id for tab in await self.browser_session.get_tabs()):
			return self._format_action_error(
				f'Close tab {tab_id} completed but the tab is still present.',
				default_code='postcondition_failed',
			)
		current_url = await self.browser_session.get_current_page_url()
		return f'Closed tab # {tab_id}, now on {current_url}'

	def _track_session(self, session: BrowserSession) -> None:
		"""Track a browser session for management."""
		self.active_sessions[session.id] = {
			'session': session,
			'created_at': time.time(),
			'last_activity': time.time(),
			'url': getattr(session, 'current_url', None),
		}

	def _update_session_activity(self, session_id: str) -> None:
		"""Update the last activity time for a session and schedule a URL refresh."""
		if session_id in self.active_sessions:
			self.active_sessions[session_id]['last_activity'] = time.time()
			asyncio.create_task(self._update_session_url(session_id))

	async def _update_session_url(self, session_id: str) -> None:
		"""Refresh the stored URL for a tracked session."""
		if session_id not in self.active_sessions or not self.browser_session:
			return
		try:
			url = await self.browser_session.get_current_page_url()
			if session_id in self.active_sessions:
				self.active_sessions[session_id]['url'] = url
		except Exception:
			pass

	async def _list_sessions(self) -> str:
		"""List all active browser sessions."""
		if not self.active_sessions:
			return 'No active browser sessions'

		sessions_info = []
		for session_id, session_data in self.active_sessions.items():
			session = session_data['session']
			created_at = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(session_data['created_at']))
			last_activity = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(session_data['last_activity']))

			# Check if session is still active
			is_active = hasattr(session, 'cdp_client') and session.cdp_client is not None

			sessions_info.append(
				{
					'session_id': session_id,
					'created_at': created_at,
					'last_activity': last_activity,
					'active': is_active,
					'current_url': session_data.get('url', 'Unknown'),
					'age_minutes': (time.time() - session_data['created_at']) / 60,
				}
			)

		return json.dumps(sessions_info, indent=2)

	async def _close_session(self, session_id: str) -> str:
		"""Close a specific browser session."""
		if session_id not in self.active_sessions:
			return f'Session {session_id} not found'

		session_data = self.active_sessions[session_id]
		session = session_data['session']

		try:
			# kill() terminates the browser process; prefer it for force-close
			if hasattr(session, 'kill'):
				await session.kill()

			# Remove from tracking
			del self.active_sessions[session_id]

			# If this was the current session, clear it
			if self.browser_session and self.browser_session.id == session_id:
				self.browser_session = None
				self.tools = None

			return f'Successfully closed session {session_id}'
		except Exception as e:
			return f'Error closing session {session_id}: {str(e)}'

	async def _close_all_sessions(self) -> str:
		"""Close all active browser sessions."""
		if not self.active_sessions:
			return 'No active sessions to close'

		closed_count = 0
		errors = []

		for session_id in list(self.active_sessions.keys()):
			try:
				result = await self._close_session(session_id)
				if 'Successfully closed' in result:
					closed_count += 1
				else:
					errors.append(f'{session_id}: {result}')
			except Exception as e:
				errors.append(f'{session_id}: {str(e)}')

		# Clear current session references
		self.browser_session = None
		self.tools = None

		result = f'Closed {closed_count} sessions'
		if errors:
			result += f'. Errors: {"; ".join(errors)}'

		return result

	async def _cleanup_expired_sessions(self) -> None:
		"""Background task to clean up expired sessions."""
		current_time = time.time()
		timeout_seconds = self.session_timeout_minutes * 60

		expired_sessions = []
		for session_id, session_data in self.active_sessions.items():
			last_activity = session_data['last_activity']
			if current_time - last_activity > timeout_seconds:
				expired_sessions.append(session_id)

		for session_id in expired_sessions:
			try:
				await self._close_session(session_id)
				logger.info(f'Auto-closed expired session {session_id}')
			except Exception as e:
				logger.error(f'Error auto-closing session {session_id}: {e}')

	async def _start_cleanup_task(self) -> None:
		"""Start the background cleanup task."""

		async def cleanup_loop():
			while True:
				try:
					await self._cleanup_expired_sessions()
					# Check every 2 minutes
					await asyncio.sleep(120)
				except Exception as e:
					logger.error(f'Error in cleanup task: {e}')
					await asyncio.sleep(120)

		from agentyc.utils import create_task_with_error_handling

		self._cleanup_task = create_task_with_error_handling(cleanup_loop(), name='mcp_cleanup_loop', suppress_exceptions=True)

	async def _shutdown(self) -> None:
		"""Stop background work and force-close any tracked browser sessions."""
		if self._cleanup_task is not None:
			self._cleanup_task.cancel()
			with suppress(asyncio.CancelledError):
				await self._cleanup_task
			self._cleanup_task = None

		if self.active_sessions:
			await self._close_all_sessions()

	async def run(self):
		"""Run the MCP server."""
		# Start the cleanup task
		await self._start_cleanup_task()

		if sys.stdin is None:
			raise RuntimeError('MCP stdio transport requires stdin, but this process was launched without one.')

		try:
			async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
				try:
					await self.server.run(
						read_stream,
						write_stream,
						InitializationOptions(
							server_name='agentyc',
							server_version='0.1.0',
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


async def main(session_timeout_minutes: int = 10, cdp_url: str | None = None):
	if not MCP_AVAILABLE:
		print('MCP SDK is required. Install with: pip install mcp', file=sys.stderr)
		sys.exit(1)

	server = AgentycServer(session_timeout_minutes=session_timeout_minutes, cdp_url=cdp_url)
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
