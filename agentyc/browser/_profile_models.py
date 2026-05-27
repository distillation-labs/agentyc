import tempfile
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator

from agentyc.config import CONFIG
from agentyc.utils import _log_pretty_path, logger

from ._profile_constants import AGENTYC_DEFAULT_CHANNEL, DOMAIN_OPTIMIZATION_THRESHOLD, _get_enable_default_extensions_default
from ._profile_display import detect_display_configuration
from ._profile_extensions import (
	apply_minimal_extension_patch,
	build_profile_args,
	check_extension_manifest_version,
	copy_profile_to_temp_dir,
	download_extension,
	ensure_default_extensions_downloaded,
	extract_extension,
	get_extension_args,
)
from ._profile_types import (
	BrowserChannel,
	CliArgStr,
	NonNegativeFloat,
	RecordHarContent,
	RecordHarMode,
	ViewportSize,
)
from ._profile_validation import optimize_large_domain_list
from .session_models import BrowserWindowBounds


class BrowserContextArgs(BaseModel):
	"""Base model for common browser context parameters."""

	model_config = ConfigDict(extra='ignore', validate_assignment=False, revalidate_instances='always', populate_by_name=True)

	accept_downloads: bool = True
	permissions: list[str] = Field(
		default_factory=lambda: ['clipboardReadWrite', 'notifications'],
		description='Browser permissions to grant (CDP Browser.grantPermissions).',
	)
	user_agent: str | None = None
	screen: ViewportSize | None = None
	viewport: ViewportSize | None = Field(default=None)
	no_viewport: bool | None = None
	device_scale_factor: NonNegativeFloat | None = None
	record_har_content: RecordHarContent = RecordHarContent.EMBED
	record_har_mode: RecordHarMode = RecordHarMode.FULL
	record_har_path: str | Path | None = Field(default=None, validation_alias=AliasChoices('save_har_path', 'record_har_path'))
	record_video_dir: str | Path | None = Field(
		default=None,
		validation_alias=AliasChoices('save_recording_path', 'record_video_dir'),
	)


class BrowserConnectArgs(BaseModel):
	"""Base model for common browser connect parameters."""

	model_config = ConfigDict(extra='ignore', validate_assignment=True, revalidate_instances='always', populate_by_name=True)

	headers: dict[str, str] | None = Field(default=None, description='Additional HTTP headers to be sent with connect request')


class BrowserLaunchArgs(BaseModel):
	"""Base model for common browser launch parameters."""

	model_config = ConfigDict(
		extra='ignore',
		validate_assignment=True,
		revalidate_instances='always',
		from_attributes=True,
		validate_by_name=True,
		validate_by_alias=True,
		populate_by_name=True,
	)

	env: dict[str, str | float | bool] | None = Field(
		default=None,
		description='Extra environment variables to set when launching the browser. If None, inherits from the current process.',
	)
	executable_path: str | Path | None = Field(
		default=None,
		validation_alias=AliasChoices('browser_binary_path', 'chrome_binary_path'),
		description='Path to the chromium-based browser executable to use.',
	)
	headless: bool | None = Field(default=None, description='Whether to run the browser in headless or windowed mode.')
	args: list[CliArgStr] = Field(
		default_factory=list, description='List of *extra* CLI args to pass to the browser when launching.'
	)
	ignore_default_args: list[CliArgStr] | Literal[True] = Field(
		default_factory=lambda: [
			'--enable-automation',
			'--disable-extensions',
			'--hide-scrollbars',
			'--disable-features=AcceptCHFrame,AutoExpandDetailsElement,AvoidUnnecessaryBeforeUnloadCheckSync,CertificateTransparencyComponentUpdater,DeferRendererTasksAfterInput,DestroyProfileOnBrowserClose,DialMediaRouteProvider,ExtensionManifestV2Disabled,GlobalMediaControls,HttpsUpgrades,ImprovedCookieControls,LazyFrameLoading,LensOverlay,MediaRouter,PaintHolding,ThirdPartyStoragePartitioning,Translate',
		],
		description='List of default CLI args to stop playwright from applying (see https://github.com/microsoft/playwright/blob/41008eeddd020e2dee1c540f7c0cdfa337e99637/packages/playwright-core/src/server/chromium/chromiumSwitches.ts)',
	)
	channel: BrowserChannel | None = None
	chromium_sandbox: bool = Field(
		default=not CONFIG.IN_DOCKER,
		description='Whether to enable Chromium sandboxing (recommended unless inside Docker).',
	)
	devtools: bool = Field(
		default=False,
		description='Whether to open DevTools panel automatically for every page, only works when headless=False.',
	)
	downloads_path: str | Path | None = Field(
		default=None,
		description='Directory to save downloads to.',
		validation_alias=AliasChoices('downloads_dir', 'save_downloads_path'),
	)
	traces_dir: str | Path | None = Field(
		default=None,
		description='Directory for saving playwright trace.zip files (playwright actions, screenshots, DOM snapshots, HAR traces).',
		validation_alias=AliasChoices('trace_path', 'traces_dir'),
	)

	@model_validator(mode='after')
	def validate_devtools_headless(self) -> Self:
		assert not (self.headless and self.devtools), 'headless=True and devtools=True cannot both be set at the same time'
		return self

	@model_validator(mode='after')
	def set_default_downloads_path(self) -> Self:
		if self.downloads_path is None:
			import uuid

			unique_id = str(uuid.uuid4())[:8]
			downloads_path = Path(tempfile.gettempdir()) / f'agentyc-downloads-{unique_id}'
			while downloads_path.exists():
				unique_id = str(uuid.uuid4())[:8]
				downloads_path = Path(tempfile.gettempdir()) / f'agentyc-downloads-{unique_id}'
			self.downloads_path = downloads_path
			self.downloads_path.mkdir(parents=True, exist_ok=True)
		return self

	@staticmethod
	def args_as_dict(args: list[str]) -> dict[str, str]:
		args_dict = {}
		for arg in args:
			key, value, *_ = [*arg.split('=', 1), '', '', '']
			args_dict[key.strip().lstrip('-')] = value.strip()
		return args_dict

	@staticmethod
	def args_as_list(args: dict[str, str]) -> list[str]:
		return [f'--{key.lstrip("-")}={value}' if value else f'--{key.lstrip("-")}' for key, value in args.items()]


class BrowserNewContextArgs(BrowserContextArgs):
	"""Pydantic model for new_context() arguments."""

	model_config = ConfigDict(extra='ignore', validate_assignment=False, revalidate_instances='always', populate_by_name=True)

	storage_state: str | Path | dict[str, Any] | None = None


class BrowserLaunchPersistentContextArgs(BrowserLaunchArgs, BrowserContextArgs):
	"""Pydantic model for launch_persistent_context() arguments."""

	model_config = ConfigDict(extra='ignore', validate_assignment=False, revalidate_instances='always')

	user_data_dir: str | Path | None = None

	@field_validator('user_data_dir', mode='after')
	@classmethod
	def validate_user_data_dir(cls, value: str | Path | None) -> str | Path:
		if value is None:
			return tempfile.mkdtemp(prefix='agentyc-user-data-dir-')
		return Path(value).expanduser().resolve()


class ProxySettings(BaseModel):
	"""Typed proxy settings for Chromium traffic."""

	server: str | None = Field(default=None, description='Proxy URL, e.g. http://host:8080 or socks5://host:1080')
	bypass: str | None = Field(default=None, description='Comma-separated hosts to bypass, e.g. localhost,127.0.0.1,*.internal')
	username: str | None = Field(default=None, description='Proxy auth username')
	password: str | None = Field(default=None, description='Proxy auth password')

	def __getitem__(self, key: str) -> str | None:
		return getattr(self, key)


class BrowserProfile(BrowserConnectArgs, BrowserLaunchPersistentContextArgs, BrowserLaunchArgs, BrowserNewContextArgs):
	"""Static template of kwargs for launch/connect/context/session browser entry points."""

	model_config = ConfigDict(
		extra='ignore',
		validate_assignment=True,
		revalidate_instances='always',
		from_attributes=True,
		validate_by_name=True,
		validate_by_alias=True,
	)

	cdp_url: str | None = Field(default=None, description='CDP URL for connecting to existing browser instance')
	runtime_label: str | None = Field(default=None, description='Human-readable collaboration label for this runtime.')
	runtime_role: str = Field(default='primary', description='Collaboration role for this runtime.')
	parent_runtime_id: str | None = Field(
		default=None, description='Optional parent runtime identifier for nested collaboration flows.'
	)
	shared_browser_mode: Literal['tab', 'window'] | None = Field(
		default=None,
		description='When attaching to a shared browser, create a background tab or a separate window for this runtime.',
	)
	shared_browser_window_bounds: BrowserWindowBounds | None = Field(
		default=None,
		description='Optional bounds to apply when the shared browser creates a separate runtime window.',
	)
	shared_browser_focus_policy: Literal['preserve', 'activate'] = Field(
		default='preserve',
		description='Whether internal attach/new-page flows preserve the human-focused surface or activate the runtime target.',
	)
	is_local: bool = Field(default=False, description='Whether this is a local browser instance')
	disable_security: bool = Field(default=False, description='Disable browser security features.')
	deterministic_rendering: bool = Field(default=False, description='Enable deterministic rendering flags.')
	allowed_domains: list[str] | set[str] | None = Field(
		default=None,
		description='List of allowed domains for navigation e.g. ["*.google.com", "https://example.com", "chrome-extension://*"]. Lists with 100+ items are auto-optimized to sets (no pattern matching).',
	)
	prohibited_domains: list[str] | set[str] | None = Field(
		default=None,
		description='List of prohibited domains for navigation e.g. ["*.google.com", "https://example.com", "chrome-extension://*"]. Allowed domains take precedence over prohibited domains. Lists with 100+ items are auto-optimized to sets (no pattern matching).',
	)
	block_ip_addresses: bool = Field(
		default=False,
		description='Block navigation to URLs containing IP addresses (both IPv4 and IPv6). When True, blocks all IP-based URLs including localhost and private networks.',
	)
	keep_alive: bool | None = Field(default=None, description='Keep browser alive after the session is asked to stop.')
	proxy: ProxySettings | None = Field(
		default=None,
		description='Proxy settings. Use agentyc.browser.profile.ProxySettings(server, bypass, username, password)',
	)
	enable_default_extensions: bool = Field(
		default_factory=_get_enable_default_extensions_default,
		description="Enable automation-optimized extensions: ad blocking (uBlock Origin), cookie handling (I still don't care about cookies), and URL cleaning (ClearURLs). All extensions work automatically without manual intervention. Extensions are automatically downloaded and loaded when enabled. Can be disabled via AGENTYC_DISABLE_EXTENSIONS=1 environment variable.",
	)
	captcha_solver: bool = Field(
		default=True,
		description='Enable the captcha solver watchdog that listens for captcha events from the browser proxy. Automatically pauses agent steps while a CAPTCHA is being solved when those events are emitted. Harmless when disabled or when events are not emitted.',
	)
	demo_mode: bool = Field(
		default=False,
		description='Enable demo mode side panel that streams runtime logs directly inside the browser window (requires headless=False).',
	)
	cookie_whitelist_domains: list[str] = Field(
		default_factory=lambda: ['nature.com', 'qatarairways.com'],
		description='List of domains to whitelist in the "I still don\'t care about cookies" extension, preventing automatic cookie banner handling on these sites.',
	)
	window_size: ViewportSize | None = Field(default=None, description='Browser window size to use when headless=False.')
	window_height: int | None = Field(default=None, description='DEPRECATED, use window_size["height"] instead', exclude=True)
	window_width: int | None = Field(default=None, description='DEPRECATED, use window_size["width"] instead', exclude=True)
	window_position: ViewportSize | None = Field(
		default=ViewportSize(width=0, height=0),
		description='Window position to use for the browser x,y from the top left when headless=False.',
	)
	cross_origin_iframes: bool = Field(
		default=True,
		description='Enable cross-origin iframe support (OOPIF/Out-of-Process iframes). When False, only same-origin frames are processed to avoid complexity and hanging.',
	)
	max_iframes: int = Field(default=100, description='Maximum number of iframe documents to process to prevent crashes.')
	max_iframe_depth: int = Field(
		ge=0, default=5, description='Maximum depth for cross-origin iframe recursion (default: 5 levels deep).'
	)
	minimum_wait_page_load_time: float = Field(default=0.25, description='Minimum time to wait before capturing page state.')
	wait_for_network_idle_page_load_time: float = Field(default=0.5, description='Time to wait for network idle.')
	wait_between_actions: float = Field(default=0.1, description='Time to wait between actions.')
	highlight_elements: bool = Field(default=True, description='Highlight interactive elements on the page.')
	dom_highlight_elements: bool = Field(
		default=False, description='Highlight interactive elements in the DOM (only for debugging purposes).'
	)
	filter_highlight_ids: bool = Field(
		default=True,
		description='Only show element IDs in highlights if llm_representation is less than 10 characters.',
	)
	paint_order_filtering: bool = Field(default=True, description='Enable paint order filtering. Slightly experimental.')
	interaction_highlight_color: str = Field(
		default='rgb(194, 88, 24)',
		description='Color to use for highlighting elements during interactions (CSS color string).',
	)
	interaction_highlight_duration: float = Field(default=0.3, description='Duration in seconds to show interaction highlights.')
	auto_download_pdfs: bool = Field(default=True, description='Automatically download PDFs when navigating to PDF viewer pages.')
	profile_directory: str = 'Default'
	record_video_dir: Path | None = Field(
		default=None,
		description='Directory to save video recordings. If set, a video of the session will be recorded.',
		validation_alias=AliasChoices('save_recording_path', 'record_video_dir'),
	)
	record_video_size: ViewportSize | None = Field(
		default=None, description='Video frame size. If not set, it will use the viewport size.'
	)
	record_video_framerate: int = Field(default=30, description='The framerate to use for the video recording.')

	def __repr__(self) -> str:
		short_dir = _log_pretty_path(self.user_data_dir) if self.user_data_dir else '<incognito>'
		return f'BrowserProfile(user_data_dir= {short_dir}, headless={self.headless})'

	def __str__(self) -> str:
		return 'BrowserProfile'

	@field_validator('allowed_domains', 'prohibited_domains', mode='after')
	@classmethod
	def optimize_large_domain_lists(cls, value: list[str] | set[str] | None) -> list[str] | set[str] | None:
		return optimize_large_domain_list(value, DOMAIN_OPTIMIZATION_THRESHOLD)

	@model_validator(mode='after')
	def copy_old_config_names_to_new(self) -> Self:
		if self.window_width or self.window_height:
			logger.warning(
				'⚠️ BrowserProfile(window_width=..., window_height=...) are deprecated, use BrowserProfile(window_size={"width": 1920, "height": 1080}) instead.'
			)
			window_size = self.window_size or ViewportSize(width=0, height=0)
			window_size['width'] = window_size['width'] or self.window_width or 1920
			window_size['height'] = window_size['height'] or self.window_height or 1080
			self.window_size = window_size
		return self

	@model_validator(mode='after')
	def warn_storage_state_user_data_dir_conflict(self) -> Self:
		has_storage_state = self.storage_state is not None
		has_user_data_dir = (self.user_data_dir is not None) and ('tmp' not in str(self.user_data_dir).lower())
		if has_storage_state and has_user_data_dir:
			logger.warning(
				f'⚠️ BrowserSession(...) was passed both storage_state AND user_data_dir. storage_state={self.storage_state} will forcibly overwrite '
				f'cookies/localStorage/sessionStorage in user_data_dir={self.user_data_dir}. '
				f'For multiple browsers in parallel, use only storage_state with user_data_dir=None, '
				f'or use a separate user_data_dir for each browser and set storage_state=None.'
			)
		return self

	@model_validator(mode='after')
	def warn_user_data_dir_non_default_version(self) -> Self:
		is_not_using_default_chromium = self.executable_path or self.channel not in (AGENTYC_DEFAULT_CHANNEL, None)
		if self.user_data_dir == CONFIG.AGENTYC_DEFAULT_USER_DATA_DIR and is_not_using_default_chromium:
			alternate_name = (
				Path(self.executable_path).name.lower().replace(' ', '-')
				if self.executable_path
				else self.channel.name.lower()
				if self.channel
				else 'None'
			)
			logger.warning(
				f'⚠️ {self} Changing user_data_dir= {_log_pretty_path(self.user_data_dir)} ➡️ .../default-{alternate_name} to avoid {alternate_name.upper()} corruping default profile created by {AGENTYC_DEFAULT_CHANNEL.name}'
			)
			self.user_data_dir = CONFIG.AGENTYC_DEFAULT_USER_DATA_DIR.parent / f'default-{alternate_name}'
		return self

	@model_validator(mode='after')
	def warn_deterministic_rendering_weirdness(self) -> Self:
		if self.deterministic_rendering:
			logger.warning(
				'⚠️ BrowserSession(deterministic_rendering=True) is NOT RECOMMENDED. It breaks many sites and increases chances of getting blocked by anti-bot systems. '
				'It hardcodes the JS random seed and forces browsers across Linux/Mac/Windows to use the same font rendering engine so that identical screenshots can be generated.'
			)
		return self

	@model_validator(mode='after')
	def validate_proxy_settings(self) -> Self:
		if self.proxy and (self.proxy.bypass and not self.proxy.server):
			logger.warning('BrowserProfile.proxy.bypass provided but proxy has no server; bypass will be ignored.')
		return self

	@model_validator(mode='after')
	def validate_highlight_elements_conflict(self) -> Self:
		if self.highlight_elements and self.dom_highlight_elements:
			logger.warning(
				'⚠️ Both highlight_elements and dom_highlight_elements are enabled. '
				'dom_highlight_elements takes priority. Setting highlight_elements=False.'
			)
			self.highlight_elements = False
		return self

	def model_post_init(self, __context: Any) -> None:
		self.detect_display_configuration()
		self._copy_profile()

	def _copy_profile(self) -> None:
		copy_profile_to_temp_dir(self)

	def get_args(self) -> list[str]:
		return build_profile_args(self)

	def _get_extension_args(self) -> list[str]:
		return get_extension_args(self)

	@staticmethod
	def _check_extension_manifest_version(ext_dir: Path, ext_name: str) -> bool:
		return check_extension_manifest_version(ext_dir, ext_name)

	def _ensure_default_extensions_downloaded(self) -> list[str]:
		return ensure_default_extensions_downloaded(self)

	def _apply_minimal_extension_patch(self, ext_dir: Path, whitelist_domains: list[str]) -> None:
		apply_minimal_extension_patch(ext_dir, whitelist_domains)

	def _download_extension(self, url: str, output_path: Path) -> None:
		download_extension(url, output_path)

	def _extract_extension(self, crx_path: Path, extract_dir: Path) -> None:
		extract_extension(crx_path, extract_dir)

	def detect_display_configuration(self) -> None:
		detect_display_configuration(self)
