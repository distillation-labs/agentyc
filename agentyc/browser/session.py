"""Event-driven browser session with backwards compatibility."""

import asyncio
import logging
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Self

import httpx
from bubus import EventBus
from cdp_use import CDPClient
from cdp_use.cdp.target import TargetID
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr
from uuid_extensions import uuid7str

from agentyc.browser._cdp_timeout import TimeoutWrappedCDPClient

# CDP logging is now handled by setup_logging() in logging_config.py
# It automatically sets CDP logs to the same level as agentyc logs
from agentyc.browser.profile import BrowserProfile, ProxySettings
from agentyc.browser.session_models import (
	BrowserWindowBounds,
	RuntimeOwnershipMetadata,
)
from agentyc.dom.views import EnhancedDOMTreeNode

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


from agentyc.browser.session_dom_mixin import SessionDOMMixin
from agentyc.browser.session_runtime_mixin import SessionRuntimeMixin
from agentyc.browser.session_targets_mixin import SessionTargetsMixin


class BrowserSession(SessionRuntimeMixin, SessionTargetsMixin, SessionDOMMixin, BaseModel):
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
	_network_mock_rules: dict[str, Any] = PrivateAttr(default_factory=dict)
	_network_conditions_by_target: dict[str, Any] = PrivateAttr(default_factory=dict)
	_fetch_handlers_registered: bool = PrivateAttr(default=False)

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
