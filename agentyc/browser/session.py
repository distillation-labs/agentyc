"""Event-driven browser session with backwards compatibility."""

import asyncio
import logging
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Self, Union

import httpx
from bubus import EventBus
from cdp_use import CDPClient
from cdp_use.cdp.network import Cookie
from cdp_use.cdp.target import TargetID
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr
from uuid_extensions import uuid7str

from agentyc.browser import session_connection, session_navigation, session_runtime, session_shared_browser, session_targets
from agentyc.browser._cdp_timeout import TimeoutWrappedCDPClient
from agentyc.browser.events import (
	AgentFocusChangedEvent,
	BrowserStartEvent,
	BrowserStopEvent,
	CloseTabEvent,
	FileDownloadedEvent,
	NavigateToUrlEvent,
	SwitchTabEvent,
	TabClosedEvent,
	TabCreatedEvent,
)

# CDP logging is now handled by setup_logging() in logging_config.py
# It automatically sets CDP logs to the same level as agentyc logs
from agentyc.browser.page import Page
from agentyc.browser.profile import BrowserProfile, ProxySettings
from agentyc.browser.screenshot_processing import resize_screenshot_for_llm as process_screenshot_for_llm
from agentyc.browser.session_dom import (
	_get_element_bounds as session_dom_get_element_bounds,
)
from agentyc.browser.session_dom import (
	add_highlights as session_dom_add_highlights,
)
from agentyc.browser.session_dom import (
	find_file_input_near_element as session_dom_find_file_input_near_element,
)
from agentyc.browser.session_dom import (
	get_dom_element_at_coordinates as session_dom_get_dom_element_at_coordinates,
)
from agentyc.browser.session_dom import (
	get_dom_element_by_index as session_dom_get_dom_element_by_index,
)
from agentyc.browser.session_dom import (
	get_element_by_index as session_dom_get_element_by_index,
)
from agentyc.browser.session_dom import (
	get_element_coordinates as session_dom_get_element_coordinates,
)
from agentyc.browser.session_dom import (
	get_index_by_class as session_dom_get_index_by_class,
)
from agentyc.browser.session_dom import (
	get_index_by_id as session_dom_get_index_by_id,
)
from agentyc.browser.session_dom import (
	get_selector_map as session_dom_get_selector_map,
)
from agentyc.browser.session_dom import (
	highlight_coordinate_click as session_dom_highlight_coordinate_click,
)
from agentyc.browser.session_dom import (
	highlight_interaction_element as session_dom_highlight_interaction_element,
)
from agentyc.browser.session_dom import (
	is_file_input as session_dom_is_file_input,
)
from agentyc.browser.session_dom import (
	remove_highlights as session_dom_remove_highlights,
)
from agentyc.browser.session_dom import (
	screenshot_element as session_dom_screenshot_element,
)
from agentyc.browser.session_dom import (
	update_cached_selector_map as session_dom_update_cached_selector_map,
)
from agentyc.browser.session_models import (
	BrowserWindowBounds,
	CDPSession,
	RuntimeOwnershipMetadata,
	Target,
)
from agentyc.browser.views import BrowserStateSummary, TabInfo
from agentyc.dom.views import DOMRect, EnhancedDOMTreeNode, TargetInfo
from agentyc.observability import observe_debug

if TYPE_CHECKING:
	from agentyc.browser.demo_mode import DemoMode
	from agentyc.browser.watchdogs.captcha_watchdog import CaptchaWaitResult


def __getattr__(name: str) -> Any:
	if name == 'TimeoutWrappedCDPClient':
		return TimeoutWrappedCDPClient
	if name == 'httpx':
		return httpx
	raise AttributeError(f"module '{__name__}' has no attribute '{name}'")


DEFAULT_BROWSER_PROFILE = BrowserProfile()

_LOGGED_UNIQUE_SESSION_IDS = set()  # track unique session IDs that have been logged to make sure we always assign a unique enough id to new sessions and avoid ambiguity in logs
red = '\033[91m'
reset = '\033[0m'


class BrowserSession(BaseModel):
	"""Event-driven browser session with backwards compatibility.

	This class provides a 2-layer architecture:
	- High-level event handling for agents/tools
	- Direct CDP/Playwright calls for browser operations

	Supports both event-driven and imperative calling styles.

	Browser configuration is stored in the browser_profile, session identity in direct fields:
	```python
	# Direct settings (recommended for most users)
	session = BrowserSession(headless=True, user_data_dir='./profile')

	# Or use a profile (for advanced use cases)
	session = BrowserSession(browser_profile=BrowserProfile(...))

	# Access session fields directly, browser settings via profile or property
	print(session.id)  # Session field
	```
	"""

	model_config = ConfigDict(
		arbitrary_types_allowed=True,
		validate_assignment=True,
		extra='forbid',
		revalidate_instances='never',  # resets private attrs on every model rebuild
	)

	def __init__(
		self,
		# Core configuration
		id: str | None = None,
		cdp_url: str | None = None,
		is_local: bool = False,
		browser_profile: BrowserProfile | None = None,
		# BrowserProfile fields that can be passed directly
		# From BrowserConnectArgs
		headers: dict[str, str] | None = None,
		# From BrowserLaunchArgs
		env: dict[str, str | float | bool] | None = None,
		executable_path: str | Path | None = None,
		headless: bool | None = None,
		args: list[str] | None = None,
		ignore_default_args: list[str] | Literal[True] | None = None,
		channel: str | None = None,
		chromium_sandbox: bool | None = None,
		devtools: bool | None = None,
		downloads_path: str | Path | None = None,
		traces_dir: str | Path | None = None,
		# From BrowserContextArgs
		accept_downloads: bool | None = None,
		permissions: list[str] | None = None,
		user_agent: str | None = None,
		screen: dict | None = None,
		viewport: dict | None = None,
		no_viewport: bool | None = None,
		device_scale_factor: float | None = None,
		record_har_content: str | None = None,
		record_har_mode: str | None = None,
		record_har_path: str | Path | None = None,
		record_video_dir: str | Path | None = None,
		record_video_framerate: int | None = None,
		record_video_size: dict | None = None,
		# From BrowserLaunchPersistentContextArgs
		user_data_dir: str | Path | None = None,
		# From BrowserNewContextArgs
		storage_state: str | Path | dict[str, Any] | None = None,
		# BrowserProfile specific fields
		disable_security: bool | None = None,
		deterministic_rendering: bool | None = None,
		allowed_domains: list[str] | None = None,
		prohibited_domains: list[str] | None = None,
		keep_alive: bool | None = None,
		runtime_label: str | None = None,
		runtime_role: str | None = None,
		parent_runtime_id: str | None = None,
		shared_browser_mode: Literal['tab', 'window'] | None = None,
		shared_browser_window_bounds: BrowserWindowBounds | dict[str, Any] | None = None,
		shared_browser_focus_policy: Literal['preserve', 'activate'] | None = None,
		proxy: ProxySettings | None = None,
		enable_default_extensions: bool | None = None,
		captcha_solver: bool | None = None,
		window_size: dict | None = None,
		window_position: dict | None = None,
		minimum_wait_page_load_time: float | None = None,
		wait_for_network_idle_page_load_time: float | None = None,
		wait_between_actions: float | None = None,
		filter_highlight_ids: bool | None = None,
		auto_download_pdfs: bool | None = None,
		profile_directory: str | None = None,
		cookie_whitelist_domains: list[str] | None = None,
		# DOM extraction layer configuration
		cross_origin_iframes: bool | None = None,
		highlight_elements: bool | None = None,
		dom_highlight_elements: bool | None = None,
		paint_order_filtering: bool | None = None,
		# Iframe processing limits
		max_iframes: int | None = None,
		max_iframe_depth: int | None = None,
		# LLM screenshot configuration
		llm_screenshot_size: tuple[int, int] | None = None,
		llm_screenshot_format: str | None = None,
		llm_screenshot_quality: int | None = None,
		llm_screenshot_grayscale: bool | None = None,
	):
		# Following the same pattern as AgentSettings in service.py
		# Only pass non-None values to avoid validation errors
		profile_kwargs = {
			k: v
			for k, v in locals().items()
			if k
			not in [
				'self',
				'browser_profile',
				'id',
				'llm_screenshot_size',
				'llm_screenshot_format',
				'llm_screenshot_quality',
				'llm_screenshot_grayscale',
			]
			and v is not None
		}

		# if is_local is False but executable_path is provided, set is_local to True
		if is_local is False and executable_path is not None:
			profile_kwargs['is_local'] = True
		if not cdp_url:
			profile_kwargs['is_local'] = True

		# Create browser profile from direct parameters or use provided one
		if browser_profile is not None:
			# Merge any direct kwargs into the provided browser_profile (direct kwargs take precedence)
			merged_kwargs = {**browser_profile.model_dump(exclude_unset=True), **profile_kwargs}
			resolved_browser_profile = BrowserProfile(**merged_kwargs)
		else:
			resolved_browser_profile = BrowserProfile(**profile_kwargs)

		# Build llm screenshot kwargs (only non-None)
		llm_kwargs = {}
		if llm_screenshot_size is not None:
			llm_kwargs['llm_screenshot_size'] = llm_screenshot_size
		if llm_screenshot_format is not None:
			llm_kwargs['llm_screenshot_format'] = llm_screenshot_format
		if llm_screenshot_quality is not None:
			llm_kwargs['llm_screenshot_quality'] = llm_screenshot_quality
		if llm_screenshot_grayscale is not None:
			llm_kwargs['llm_screenshot_grayscale'] = llm_screenshot_grayscale

		# Initialize the Pydantic model
		super().__init__(
			id=id or str(uuid7str()),
			browser_profile=resolved_browser_profile,
			**llm_kwargs,
		)

	# Session configuration (session identity only)
	id: str = Field(default_factory=lambda: str(uuid7str()), description='Unique identifier for this browser session')

	# Browser configuration (reusable profile)
	browser_profile: BrowserProfile = Field(
		default_factory=lambda: DEFAULT_BROWSER_PROFILE,
		description='BrowserProfile() options to use for the session, otherwise a default profile will be used',
	)

	# LLM screenshot resizing configuration
	llm_screenshot_size: tuple[int, int] | None = Field(
		default=(480, 270),
		description='Target size (width, height) to resize screenshots before sending to LLM. '
		'Set to None for full-resolution. Default 480x270 keeps the screenshot compact while preserving UI layout. '
		'Coordinates from LLM are scaled back to original viewport size.',
	)
	llm_screenshot_format: str = Field(
		default='webp',
		description='Image format for LLM-targeted screenshots ("png", "jpeg", or "webp"). '
		'WebP gives the best size/quality ratio (~5x reduction at q=85). '
		'JPEG is universally supported (~2.6x reduction at q=85).',
	)
	llm_screenshot_quality: int = Field(
		default=85,
		ge=1,
		le=100,
		description='Compression quality 1-100 (used for jpeg and webp). '
		'Higher = better quality, larger size. 85 is a good balance.',
	)
	llm_screenshot_grayscale: bool = Field(
		default=False,
		description='Convert screenshot to grayscale before encoding. '
		'Reduces size ~20-30% with minimal loss for UI understanding.',
	)

	# Cache of original viewport size for coordinate conversion (set when browser state is captured)
	_original_viewport_size: tuple[int, int] | None = PrivateAttr(default=None)
	_runtime_metadata: RuntimeOwnershipMetadata | None = PrivateAttr(default=None)
	_target_init_scripts: dict[str, set[str]] = PrivateAttr(default_factory=dict)
	_runtime_marker_script_ids: dict[str, str] = PrivateAttr(default_factory=dict)
	_global_init_script_targets: dict[str, set[str]] = PrivateAttr(default_factory=dict)
	_browser_context_id: str | None = PrivateAttr(default=None)

	@classmethod
	def from_system_chrome(cls, profile_directory: str | None = None, **kwargs: Any) -> Self:
		"""Create a BrowserSession using system's Chrome installation and profile"""
		from agentyc.browser.chrome_profiles import find_chrome_executable, get_chrome_profile_path, list_chrome_profiles

		executable_path = find_chrome_executable()
		if executable_path is None:
			raise RuntimeError(
				'Chrome not found. Please install Chrome or use Browser() with explicit executable_path.\n'
				'Expected locations:\n'
				'  macOS: /Applications/Google Chrome.app/Contents/MacOS/Google Chrome\n'
				'  Linux: /usr/bin/google-chrome or /usr/bin/chromium\n'
				'  Windows: C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe'
			)

		user_data_dir = get_chrome_profile_path(None)
		if user_data_dir is None:
			raise RuntimeError(
				'Could not detect Chrome profile directory for your platform.\n'
				'Expected locations:\n'
				'  macOS: ~/Library/Application Support/Google/Chrome\n'
				'  Linux: ~/.config/google-chrome or ~/.config/chromium\n'
				'  Windows: %LocalAppData%\\Google\\Chrome\\User Data'
			)

		# Auto-select profile if not specified
		profiles = list_chrome_profiles()
		if profile_directory is None:
			if profiles:
				# Use first available profile
				profile_directory = profiles[0]['directory']
				logging.getLogger('agentyc').info(f'Auto-selected Chrome profile: {profiles[0]["name"]} ({profile_directory})')
			else:
				profile_directory = 'Default'

		return cls(
			executable_path=executable_path,
			user_data_dir=user_data_dir,
			profile_directory=profile_directory,
			**kwargs,
		)

	@classmethod
	def list_chrome_profiles(cls) -> list[dict[str, str]]:
		"""List available Chrome profiles on the system"""
		from agentyc.browser.chrome_profiles import list_chrome_profiles

		return list_chrome_profiles()

	# Convenience properties for common browser settings
	@property
	def cdp_url(self) -> str | None:
		"""CDP URL from browser profile."""
		return self.browser_profile.cdp_url

	@property
	def is_local(self) -> bool:
		"""Whether this is a local browser instance from browser profile."""
		return self.browser_profile.is_local

	@property
	def is_shared_browser_runtime(self) -> bool:
		return self.browser_profile.shared_browser_mode is not None and bool(self.cdp_url)

	@property
	def is_cdp_connected(self) -> bool:
		"""Check if the CDP WebSocket connection is alive and usable.

		Returns True only if the root CDP client exists and its WebSocket is in OPEN state.
		A dead/closing/closed WebSocket returns False, preventing handlers from dispatching
		CDP commands that would hang until timeout on a broken connection.
		"""
		if self._cdp_client_root is None or self._cdp_client_root.ws is None:
			return False
		try:
			from websockets.protocol import State

			return self._cdp_client_root.ws.state is State.OPEN
		except Exception:
			return False

	async def wait_if_captcha_solving(self, timeout: float | None = None) -> 'CaptchaWaitResult | None':
		"""Wait if a captcha is currently being solved by the browser proxy.

		Returns:
			A CaptchaWaitResult if we had to wait, or None if no captcha was in progress.
		"""
		if self._captcha_watchdog is not None:
			return await self._captcha_watchdog.wait_if_captcha_solving(timeout=timeout)
		return None

	@property
	def is_reconnecting(self) -> bool:
		"""Whether a WebSocket reconnection attempt is currently in progress."""
		return self._reconnecting

	@property
	def demo_mode(self) -> 'DemoMode | None':
		"""Lazy init demo mode helper when enabled."""
		if not self.browser_profile.demo_mode:
			return None
		if self._demo_mode is None:
			from agentyc.browser.demo_mode import DemoMode

			self._demo_mode = DemoMode(self)
		return self._demo_mode

	# Main shared event bus for all browser session + all watchdogs
	event_bus: EventBus = Field(default_factory=EventBus)

	# Mutable public state - which target has agent focus
	agent_focus_target_id: TargetID | None = None

	# Mutable private state shared between watchdogs
	_cdp_client_root: CDPClient | None = PrivateAttr(default=None)
	_connection_lock: Any = PrivateAttr(default=None)  # asyncio.Lock for preventing concurrent connections

	# PUBLIC: SessionManager instance (OWNS all targets and sessions)
	session_manager: Any = Field(default=None, exclude=True)  # SessionManager

	_cached_browser_state_summary: Any = PrivateAttr(default=None)
	_cached_selector_map: dict[int, EnhancedDOMTreeNode] = PrivateAttr(default_factory=dict)
	_downloaded_files: list[str] = PrivateAttr(default_factory=list)  # Track files downloaded during this session
	_closed_popup_messages: list[str] = PrivateAttr(default_factory=list)  # Store messages from auto-closed JavaScript dialogs
	_pending_auto_handled_dialogs: list[str] = PrivateAttr(
		default_factory=list
	)  # Queue of auto-handled dialogs awaiting MCP acknowledgment

	# Watchdogs
	_crash_watchdog: Any | None = PrivateAttr(default=None)
	_downloads_watchdog: Any | None = PrivateAttr(default=None)
	_aboutblank_watchdog: Any | None = PrivateAttr(default=None)
	_security_watchdog: Any | None = PrivateAttr(default=None)
	_storage_state_watchdog: Any | None = PrivateAttr(default=None)
	_local_browser_watchdog: Any | None = PrivateAttr(default=None)
	_default_action_watchdog: Any | None = PrivateAttr(default=None)
	_dom_watchdog: Any | None = PrivateAttr(default=None)
	_screenshot_watchdog: Any | None = PrivateAttr(default=None)
	_permissions_watchdog: Any | None = PrivateAttr(default=None)
	_popups_watchdog: Any | None = PrivateAttr(default=None)
	_recording_watchdog: Any | None = PrivateAttr(default=None)
	_har_recording_watchdog: Any | None = PrivateAttr(default=None)
	_captcha_watchdog: Any | None = PrivateAttr(default=None)
	_watchdogs_attached: bool = PrivateAttr(default=False)

	_demo_mode: 'DemoMode | None' = PrivateAttr(default=None)

	# WebSocket reconnection state
	# Max wait = attempts * timeout_per_attempt + sum(delays) + small buffer
	# Default: 3 * 15s + (1+2+4)s + 2s = 54s
	RECONNECT_WAIT_TIMEOUT: float = 54.0
	_reconnecting: bool = PrivateAttr(default=False)
	_reconnect_event: asyncio.Event = PrivateAttr(default_factory=asyncio.Event)
	_reconnect_lock: asyncio.Lock = PrivateAttr(default_factory=asyncio.Lock)
	_reconnect_task: asyncio.Task | None = PrivateAttr(default=None)
	_intentional_stop: bool = PrivateAttr(default=False)

	_logger: Any = PrivateAttr(default=None)

	@property
	def logger(self) -> Any:
		"""Get instance-specific logger with session ID in the name"""
		# **regenerate it every time** because our id and str(self) can change as browser connection state changes
		# if self._logger is None or not self._cdp_client_root:
		# 	self._logger = logging.getLogger(f'agentyc.{self}')
		return logging.getLogger(f'agentyc.{self}')

	@cached_property
	def _id_for_logs(self) -> str:
		"""Get human-friendly semi-unique identifier for differentiating different BrowserSession instances in logs"""
		str_id = self.id[-4:]  # default to last 4 chars of truly random uuid, less helpful than cdp port but always unique enough
		port_number = (self.cdp_url or 'no-cdp').rsplit(':', 1)[-1].split('/', 1)[0].strip()
		port_is_random = not port_number.startswith('922')
		port_is_unique_enough = port_number not in _LOGGED_UNIQUE_SESSION_IDS
		if port_number and port_number.isdigit() and port_is_random and port_is_unique_enough:
			# if cdp port is random/unique enough to identify this session, use it as our id in logs
			_LOGGED_UNIQUE_SESSION_IDS.add(port_number)
			str_id = port_number
		return str_id

	@property
	def _tab_id_for_logs(self) -> str:
		return self.agent_focus_target_id[-2:] if self.agent_focus_target_id else f'{red}--{reset}'

	def __repr__(self) -> str:
		return f'BrowserSession🅑 {self._id_for_logs} 🅣 {self._tab_id_for_logs} (cdp_url={self.cdp_url}, profile={self.browser_profile})'

	def __str__(self) -> str:
		return f'BrowserSession🅑 {self._id_for_logs} 🅣 {self._tab_id_for_logs}'

	async def reset(self) -> None:
		await session_runtime.reset(self)

	def model_post_init(self, __context) -> None:
		session_runtime.model_post_init(self, __context)

	@observe_debug(ignore_input=True, ignore_output=True, name='browser_session_start')
	async def start(self) -> None:
		"""Start the browser session."""
		start_event = self.event_bus.dispatch(BrowserStartEvent())
		await start_event
		# Ensure any exceptions from the event handler are propagated
		await start_event.event_result(raise_if_any=True, raise_if_none=False)

	async def kill(self) -> None:
		await session_runtime.kill(self)

	async def stop(self) -> None:
		await session_runtime.stop(self)

	async def close(self) -> None:
		await session_runtime.close(self)

	@observe_debug(ignore_input=True, ignore_output=True, name='browser_start_event_handler')
	async def on_BrowserStartEvent(self, event: BrowserStartEvent) -> dict[str, str]:
		return await session_connection.on_BrowserStartEvent(self, event)

	async def on_NavigateToUrlEvent(self, event: NavigateToUrlEvent) -> None:
		await session_navigation.on_NavigateToUrlEvent(self, event)

	async def _navigate_and_wait(
		self,
		url: str,
		target_id: str,
		timeout: float | None = None,
		wait_until: str = 'load',
		nav_timeout: float | None = None,
	) -> None:
		await session_navigation._navigate_and_wait(
			self, url, target_id, timeout=timeout, wait_until=wait_until, nav_timeout=nav_timeout
		)

	@staticmethod
	def _urls_match_for_navigation_ready(current_url: str, target_url: str) -> bool:
		return session_navigation._urls_match_for_navigation_ready(current_url, target_url)

	async def _navigation_ready_via_dom(self, *, cdp_session: Any, url: str, wait_until: str) -> bool:
		return await session_navigation._navigation_ready_via_dom(self, cdp_session=cdp_session, url=url, wait_until=wait_until)

	async def on_SwitchTabEvent(self, event: SwitchTabEvent) -> TargetID:
		return await session_navigation.on_SwitchTabEvent(self, event)

	async def on_CloseTabEvent(self, event: CloseTabEvent) -> None:
		await session_navigation.on_CloseTabEvent(self, event)

	async def on_TabCreatedEvent(self, event: TabCreatedEvent) -> None:
		await session_navigation.on_TabCreatedEvent(self, event)

	async def on_TabClosedEvent(self, event: TabClosedEvent) -> None:
		await session_navigation.on_TabClosedEvent(self, event)

	async def on_AgentFocusChangedEvent(self, event: AgentFocusChangedEvent) -> None:
		await session_navigation.on_AgentFocusChangedEvent(self, event)

	async def on_FileDownloadedEvent(self, event: FileDownloadedEvent) -> None:
		await session_runtime.on_FileDownloadedEvent(self, event)

	async def on_BrowserStopEvent(self, event: BrowserStopEvent) -> None:
		await session_connection.on_BrowserStopEvent(self, event)

	# region - ========== CDP-based replacements for browser_context operations ==========
	@property
	def cdp_client(self) -> CDPClient:
		"""Get the cached root CDP cdp_session.cdp_client. The client is created and started in self.connect()."""
		assert self._cdp_client_root is not None, 'CDP client not initialized - browser may not be connected yet'
		return self._cdp_client_root

	async def new_page(self, url: str | None = None) -> 'Page':
		return await session_shared_browser.new_page(self, url)

	async def get_current_page(self) -> 'Page | None':
		return await session_shared_browser.get_current_page(self)

	async def must_get_current_page(self) -> 'Page':
		return await session_shared_browser.must_get_current_page(self)

	async def get_pages(self) -> list['Page']:
		return await session_shared_browser.get_pages(self)

	def get_focused_target(self) -> 'Target | None':
		return session_runtime.get_focused_target(self)

	def get_page_targets(self) -> list['Target']:
		return session_runtime.get_page_targets(self)

	async def close_page(self, page: 'Union[Page, str]') -> None:
		await session_shared_browser.close_page(self, page)

	async def cookies(self) -> list['Cookie']:
		"""Get cookies, optionally filtered by URLs."""

		result = await self.cdp_client.send.Storage.getCookies()
		return result['cookies']

	async def clear_cookies(self) -> None:
		"""Clear all cookies."""
		await self.cdp_client.send.Network.clearBrowserCookies()

	async def export_storage_state(self, output_path: str | Path | None = None) -> dict[str, Any]:
		return await session_targets.export_storage_state(self, output_path)

	async def get_or_create_cdp_session(self, target_id: TargetID | None = None, focus: bool = True) -> CDPSession:
		return await session_targets.get_or_create_cdp_session(self, target_id, focus)

	async def set_extra_headers(self, headers: dict[str, str], target_id: TargetID | None = None) -> None:
		await session_targets.set_extra_headers(self, headers, target_id)

	# endregion - ========== CDP-based ... ==========

	# region - ========== Helper Methods ==========
	@observe_debug(ignore_input=True, ignore_output=True, name='get_browser_state_summary')
	async def get_browser_state_summary(
		self,
		include_screenshot: bool = True,
		cached: bool = False,
		include_recent_events: bool = False,
	) -> BrowserStateSummary:
		return await session_runtime.get_browser_state_summary(
			self,
			include_screenshot=include_screenshot,
			cached=cached,
			include_recent_events=include_recent_events,
		)

	async def get_state_as_text(self) -> str:
		return await session_runtime.get_state_as_text(self)

	async def attach_all_watchdogs(self) -> None:
		await session_connection.attach_all_watchdogs(self)

	async def connect(self, cdp_url: str | None = None) -> Self:
		return await session_connection.connect(self, cdp_url)

	async def _setup_proxy_auth(self) -> None:
		await session_connection._setup_proxy_auth(self)

	async def reconnect(self) -> None:
		await session_connection.reconnect(self)

	async def _auto_reconnect(self, max_attempts: int = 3) -> None:
		await session_connection._auto_reconnect(self, max_attempts)

	def _attach_ws_drop_callback(self) -> None:
		session_connection._attach_ws_drop_callback(self)

	@property
	def runtime_metadata(self) -> RuntimeOwnershipMetadata:
		assert self._runtime_metadata is not None
		return self._runtime_metadata

	def _tab_display_title(self, target: Target) -> str:
		return session_shared_browser._tab_display_title(self, target)

	def _assign_shared_browser_ownership(self, page_targets: list[Target]) -> None:
		session_shared_browser._assign_shared_browser_ownership(self, page_targets)

	def is_target_owned_by_current_runtime(self, target_id: TargetID) -> bool:
		return session_shared_browser.is_target_owned_by_current_runtime(self, target_id)

	def get_owned_page_targets(self) -> list[Target]:
		return session_shared_browser.get_owned_page_targets(self)

	def require_owned_target(self, target_id: TargetID, *, action: str) -> Target:
		return session_shared_browser.require_owned_target(self, target_id, action=action)

	async def _apply_runtime_markers_to_target(
		self,
		target_id: TargetID,
		*,
		include_title_prefix: bool = True,
	) -> None:
		await session_shared_browser._apply_runtime_markers_to_target(
			self,
			target_id,
			include_title_prefix=include_title_prefix,
		)

	async def get_target_runtime_metadata(self, target_id: TargetID | None = None) -> dict[str, Any] | None:
		return await session_shared_browser.get_target_runtime_metadata(self, target_id)

	async def get_tabs(self) -> list[TabInfo]:
		return await session_shared_browser.get_tabs(self)

	# endregion - ========== Helper Methods ==========

	# region - ========== ID Lookup Methods ==========
	async def get_current_target_info(self) -> TargetInfo | None:
		return await session_shared_browser.get_current_target_info(self)

	async def get_current_page_url(self) -> str:
		return await session_shared_browser.get_current_page_url(self)

	async def get_current_page_title(self) -> str:
		return await session_shared_browser.get_current_page_title(self)

	async def navigate_to(self, url: str, new_tab: bool = False) -> None:
		await session_navigation.navigate_to(self, url, new_tab)

	# endregion - ========== ID Lookup Methods ==========

	# region - ========== DOM Helper Methods ==========

	async def get_dom_element_by_index(self, index: int) -> EnhancedDOMTreeNode | None:
		return await session_dom_get_dom_element_by_index(self, index)

	def update_cached_selector_map(self, selector_map: dict[int, EnhancedDOMTreeNode]) -> None:
		session_dom_update_cached_selector_map(self, selector_map)

	# Alias for backwards compatibility
	async def get_element_by_index(self, index: int) -> EnhancedDOMTreeNode | None:
		return await session_dom_get_element_by_index(self, index)

	async def get_dom_element_at_coordinates(self, x: int, y: int) -> EnhancedDOMTreeNode | None:
		return await session_dom_get_dom_element_at_coordinates(self, x, y)

	async def get_target_id_from_tab_id(self, tab_id: str) -> TargetID:
		return await session_targets.get_target_id_from_tab_id(self, tab_id)

	async def get_target_id_from_url(self, url: str) -> TargetID:
		return await session_targets.get_target_id_from_url(self, url)

	async def get_most_recently_opened_target_id(self) -> TargetID:
		return await session_targets.get_most_recently_opened_target_id(self)

	def is_file_input(self, element: Any) -> bool:
		return session_dom_is_file_input(self, element)

	def find_file_input_near_element(
		self,
		node: 'EnhancedDOMTreeNode',
		max_height: int = 3,
		max_descendant_depth: int = 3,
	) -> 'EnhancedDOMTreeNode | None':
		return session_dom_find_file_input_near_element(self, node, max_height, max_descendant_depth)

	async def get_selector_map(self) -> dict[int, EnhancedDOMTreeNode]:
		return await session_dom_get_selector_map(self)

	async def get_index_by_id(self, element_id: str) -> int | None:
		return await session_dom_get_index_by_id(self, element_id)

	async def get_index_by_class(self, class_name: str) -> int | None:
		return await session_dom_get_index_by_class(self, class_name)

	async def remove_highlights(self) -> None:
		await session_dom_remove_highlights(self)

	@observe_debug(ignore_input=True, ignore_output=True, name='get_element_coordinates')
	async def get_element_coordinates(self, backend_node_id: int, cdp_session: CDPSession) -> DOMRect | None:
		return await session_dom_get_element_coordinates(self, backend_node_id, cdp_session)

	async def highlight_interaction_element(self, node: 'EnhancedDOMTreeNode') -> None:
		await session_dom_highlight_interaction_element(self, node)

	async def highlight_coordinate_click(self, x: int, y: int) -> None:
		await session_dom_highlight_coordinate_click(self, x, y)

	async def add_highlights(self, selector_map: dict[int, 'EnhancedDOMTreeNode']) -> None:
		await session_dom_add_highlights(self, selector_map)

	async def _close_extension_options_pages(self) -> None:
		await session_navigation._close_extension_options_pages(self)

	async def send_demo_mode_log(self, message: str, level: str = 'info', metadata: dict[str, Any] | None = None) -> None:
		await session_runtime.send_demo_mode_log(self, message, level=level, metadata=metadata)

	@property
	def downloaded_files(self) -> list[str]:
		return session_runtime.downloaded_files(self)

	# endregion - ========== Helper Methods ==========

	# region - ========== CDP-based replacements for browser_context operations ==========

	async def _cdp_get_all_pages(
		self,
		include_http: bool = True,
		include_about: bool = True,
		include_pages: bool = True,
		include_iframes: bool = False,
		include_workers: bool = False,
		include_chrome: bool = False,
		include_chrome_extensions: bool = False,
		include_chrome_error: bool = False,
	) -> list[TargetInfo]:
		return await session_targets._cdp_get_all_pages(
			self,
			include_http=include_http,
			include_about=include_about,
			include_pages=include_pages,
			include_iframes=include_iframes,
			include_workers=include_workers,
			include_chrome=include_chrome,
			include_chrome_extensions=include_chrome_extensions,
			include_chrome_error=include_chrome_error,
		)

	async def _cdp_create_new_page(
		self,
		url: str = 'about:blank',
		background: bool = False,
		new_window: bool = False,
		window_bounds: BrowserWindowBounds | dict[str, Any] | None = None,
		browser_context_id: str | None = None,
	) -> str:
		return await session_shared_browser._cdp_create_new_page(
			self,
			url=url,
			background=background,
			new_window=new_window,
			window_bounds=window_bounds,
			browser_context_id=browser_context_id,
		)

	async def _cdp_close_page(self, target_id: TargetID) -> None:
		await session_shared_browser._cdp_close_page(self, target_id)

	async def _cdp_get_cookies(self) -> list[Cookie]:
		return await session_targets._cdp_get_cookies(self)

	async def _cdp_set_cookies(self, cookies: list[Cookie]) -> None:
		await session_targets._cdp_set_cookies(self, cookies)

	async def _cdp_clear_cookies(self) -> None:
		await session_targets._cdp_clear_cookies(self)

	async def _cdp_grant_permissions(self, permissions: list[str], origin: str | None = None) -> None:
		await session_targets._cdp_grant_permissions(self, permissions, origin)

	async def _cdp_set_geolocation(self, latitude: float, longitude: float, accuracy: float = 100) -> None:
		await session_targets._cdp_set_geolocation(self, latitude, longitude, accuracy)

	async def _cdp_clear_geolocation(self) -> None:
		await session_targets._cdp_clear_geolocation(self)

	async def _cdp_add_init_script(self, script: str, target_id: TargetID | None = None) -> str:
		return await session_targets._cdp_add_init_script(self, script, target_id)

	async def _cdp_remove_init_script(self, identifier: str, target_id: TargetID | None = None) -> None:
		await session_targets._cdp_remove_init_script(self, identifier, target_id)

	async def _cdp_get_window_context(self, target_id: TargetID) -> dict[str, Any] | None:
		return await session_shared_browser._cdp_get_window_context(self, target_id)

	async def _cdp_set_window_bounds(
		self,
		target_id: TargetID,
		bounds: BrowserWindowBounds | dict[str, Any],
	) -> dict[str, Any] | None:
		return await session_shared_browser._cdp_set_window_bounds(self, target_id, bounds)

	async def _cdp_set_viewport(
		self, width: int, height: int, device_scale_factor: float = 1.0, mobile: bool = False, target_id: str | None = None
	) -> None:
		await session_targets._cdp_set_viewport(self, width, height, device_scale_factor, mobile, target_id)

	async def _cdp_get_origins(self) -> list[dict[str, Any]]:
		return await session_targets._cdp_get_origins(self)

	async def _cdp_get_storage_state(self) -> dict:
		return await session_targets._cdp_get_storage_state(self)

	async def _cdp_navigate(self, url: str, target_id: TargetID | None = None) -> None:
		await session_targets._cdp_navigate(self, url, target_id)

	@staticmethod
	def _is_valid_target(
		target_info: TargetInfo,
		include_http: bool = True,
		include_chrome: bool = False,
		include_chrome_extensions: bool = False,
		include_chrome_error: bool = False,
		include_about: bool = True,
		include_iframes: bool = True,
		include_pages: bool = True,
		include_workers: bool = False,
	) -> bool:
		return session_targets._is_valid_target(
			target_info,
			include_http=include_http,
			include_chrome=include_chrome,
			include_chrome_extensions=include_chrome_extensions,
			include_chrome_error=include_chrome_error,
			include_about=include_about,
			include_iframes=include_iframes,
			include_pages=include_pages,
			include_workers=include_workers,
		)

	async def get_all_frames(self) -> tuple[dict[str, dict], dict[str, str]]:
		return await session_targets.get_all_frames(self)

	async def _populate_frame_metadata(self, all_frames: dict[str, dict], target_sessions: dict[str, str]) -> None:
		await session_targets._populate_frame_metadata(self, all_frames, target_sessions)

	async def find_frame_target(self, frame_id: str, all_frames: dict[str, dict] | None = None) -> dict | None:
		return await session_targets.find_frame_target(self, frame_id, all_frames)

	async def cdp_client_for_target(self, target_id: TargetID) -> CDPSession:
		return await session_targets.cdp_client_for_target(self, target_id)

	async def cdp_client_for_frame(self, frame_id: str) -> CDPSession:
		return await session_targets.cdp_client_for_frame(self, frame_id)

	async def cdp_client_for_node(self, node: EnhancedDOMTreeNode) -> CDPSession:
		return await session_targets.cdp_client_for_node(self, node)

	@observe_debug(ignore_input=True, ignore_output=True, name='take_screenshot')
	async def take_screenshot(
		self,
		path: str | None = None,
		full_page: bool = False,
		format: str = 'png',
		quality: int | None = None,
		clip: dict | None = None,
	) -> bytes:
		return await session_targets.take_screenshot(
			self,
			path=path,
			full_page=full_page,
			format=format,
			quality=quality,
			clip=clip,
		)

	@staticmethod
	def resize_screenshot_for_llm(
		data: bytes,
		target_size: tuple[int, int] | None,
		target_format: str = 'png',
		quality: int = 85,
		grayscale: bool = False,
	) -> bytes:
		"""Resize/convert screenshot for compact LLM consumption."""
		return process_screenshot_for_llm(
			data,
			target_size=target_size,
			target_format=target_format,
			quality=quality,
			grayscale=grayscale,
		)

	async def get_window_bounds(self, target_id: TargetID | None = None) -> dict[str, Any] | None:
		return await session_shared_browser.get_window_bounds(self, target_id)

	async def set_window_bounds(
		self,
		bounds: BrowserWindowBounds | dict[str, Any],
		target_id: TargetID | None = None,
	) -> dict[str, Any] | None:
		return await session_shared_browser.set_window_bounds(self, bounds, target_id)

	async def create_collaborative_page(
		self,
		url: str = 'about:blank',
		*,
		new_window: bool | None = None,
		window_bounds: BrowserWindowBounds | dict[str, Any] | None = None,
	) -> Page:
		return await session_shared_browser.create_collaborative_page(
			self,
			url,
			new_window=new_window,
			window_bounds=window_bounds,
		)

	async def screenshot_element(
		self,
		selector: str,
		path: str | None = None,
		format: str = 'png',
		quality: int | None = None,
	) -> bytes:
		return await session_dom_screenshot_element(self, selector, path=path, format=format, quality=quality)

	async def _get_element_bounds(self, selector: str) -> dict | None:
		return await session_dom_get_element_bounds(self, selector)
