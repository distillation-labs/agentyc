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

	def __init__(self, session_timeout_minutes: int = 10):
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

		# Session management
		self.active_sessions: dict[str, dict[str, Any]] = {}  # session_id -> session info
		self.session_timeout_minutes = session_timeout_minutes
		self._cleanup_task: Any = None
		self._last_state_elements_by_ref: dict[str, dict[str, Any]] = {}
		self._last_state_cache_url: str | None = None
		self._action_model_cache: dict[str, Any] = {}

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
				types.Tool(
					name='browser_scroll',
					description='Scroll the page',
					inputSchema={
						'type': 'object',
						'properties': {
							'direction': {
								'type': 'string',
								'enum': ['up', 'down'],
								'description': 'Direction to scroll',
								'default': 'down',
							}
						},
					},
				),
				types.Tool(
					name='browser_go_back',
					description='Go back to the previous page',
					inputSchema={'type': 'object', 'properties': {}},
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

			elif tool_name == 'browser_list_tabs':
				return await self._list_tabs()

			elif tool_name == 'browser_switch_tab':
				return await self._switch_tab(arguments['tab_id'])

			elif tool_name == 'browser_close_tab':
				return await self._close_tab(arguments['tab_id'])

		return f'Unknown tool: {tool_name}'

	async def _init_browser_session(self, allowed_domains: list[str] | None = None, **kwargs):
		"""Initialize browser session using config"""
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

		# Merge profile config with defaults and overrides
		profile_data = {
			'downloads_path': str(Path.home() / 'Downloads' / 'agentyc-mcp'),
			'keep_alive': False,
			'user_data_dir': '~/.config/agentyc/profiles/default',
			'device_scale_factor': 1.0,
			'disable_security': False,
			'headless': False,
			**profile_config,  # Config values override defaults
		}

		# Tool parameter overrides (highest priority)
		if allowed_domains is not None:
			profile_data['allowed_domains'] = allowed_domains

		# Merge any additional kwargs that are valid BrowserProfile fields
		for key, value in kwargs.items():
			profile_data[key] = value

		# Create browser profile
		profile = BrowserProfile(**profile_data)

		# Create browser session
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
		before_url = await self.browser_session.get_current_page_url()
		before_tabs = len(await self.browser_session.get_tabs())
		action_result = await self._run_tool_action('navigate', {'url': url, 'new_tab': new_tab})
		if action_result.error:
			return self._format_action_error(action_result.error, default_code='navigation_failed')
		after_url = await self.browser_session.get_current_page_url()
		if new_tab:
			after_tabs = len(await self.browser_session.get_tabs())
			if after_tabs <= before_tabs:
				return self._format_action_error(
					f'Navigation to {url} did not open a new tab as requested.',
					default_code='postcondition_failed',
				)
			return f'Opened new tab with URL: {url}'
		if after_url == before_url and url != before_url:
			return self._format_action_error(
				f'Navigation to {url} did not change the active page URL.',
				default_code='postcondition_failed',
			)
		return f'Navigated to: {url}'

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
				return self._format_action_error(
					f'Element {ref or resolved_index} did not retain the requested sensitive value after typing.',
					default_code='postcondition_failed',
				)
			return self._format_action_error(
				f"Element {ref or resolved_index} ended with value '{actual_value}' instead of requested text '{text}'.",
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

	async def _scroll(self, direction: str = 'down') -> str:
		"""Scroll the page."""
		if not self.browser_session:
			return 'Error: No browser session active'

		from agentyc.browser.events import ScrollEvent

		# Scroll by a standard amount (500 pixels)
		event = self.browser_session.event_bus.dispatch(
			ScrollEvent(
				direction=direction,  # type: ignore
				amount=500,
			)
		)
		await event
		return f'Scrolled {direction}'

	async def _go_back(self) -> str:
		"""Go back in browser history."""
		if not self.browser_session:
			return 'Error: No browser session active'

		from agentyc.browser.events import GoBackEvent

		before_url = await self.browser_session.get_current_page_url()
		event = self.browser_session.event_bus.dispatch(GoBackEvent())
		await event
		await event.event_result(raise_if_any=True, raise_if_none=False)
		after_url = await self.browser_session.get_current_page_url()
		if after_url == before_url:
			return self._format_action_error(
				'Go back did not change the active page URL.',
				default_code='postcondition_failed',
			)
		return 'Navigated back'

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
		"""Update the last activity time for a session."""
		if session_id in self.active_sessions:
			self.active_sessions[session_id]['last_activity'] = time.time()

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
			# Close the session
			if hasattr(session, 'kill'):
				await session.kill()
			elif hasattr(session, 'close'):
				await session.close()

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


async def main(session_timeout_minutes: int = 10):
	if not MCP_AVAILABLE:
		print('MCP SDK is required. Install with: pip install mcp', file=sys.stderr)
		sys.exit(1)

	server = AgentycServer(session_timeout_minutes=session_timeout_minutes)
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
