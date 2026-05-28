"""Storage, download, dialog, and captcha event models."""

from __future__ import annotations

from bubus import BaseEvent
from cdp_use.cdp.target import TargetID
from pydantic import Field

from agentyc.browser.events_support import _get_timeout


class SaveStorageStateEvent(BaseEvent):
	"""Request to save browser storage state."""

	path: str | None = None  # Optional path, uses profile default if not provided

	event_timeout: float | None = Field(default_factory=lambda: _get_timeout('TIMEOUT_SaveStorageStateEvent', 45.0))  # seconds


class StorageStateSavedEvent(BaseEvent):
	"""Notification that storage state was saved."""

	path: str
	cookies_count: int
	origins_count: int

	event_timeout: float | None = Field(default_factory=lambda: _get_timeout('TIMEOUT_StorageStateSavedEvent', 30.0))  # seconds


class LoadStorageStateEvent(BaseEvent):
	"""Request to load browser storage state."""

	path: str | None = None  # Optional path, uses profile default if not provided

	event_timeout: float | None = Field(default_factory=lambda: _get_timeout('TIMEOUT_LoadStorageStateEvent', 45.0))  # seconds


# TODO: refactor this to:
# - on_BrowserConnectedEvent() -> dispatch(LoadStorageStateEvent()) -> _copy_storage_state_from_json_to_browser(json_file, new_cdp_session) + return storage_state from handler
# - on_BrowserStopEvent() -> dispatch(SaveStorageStateEvent()) -> _copy_storage_state_from_browser_to_json(new_cdp_session, json_file)
# and get rid of StorageStateSavedEvent and StorageStateLoadedEvent, have the original events + provide handler return values for any results
class StorageStateLoadedEvent(BaseEvent):
	"""Notification that storage state was loaded."""

	path: str
	cookies_count: int
	origins_count: int

	event_timeout: float | None = Field(default_factory=lambda: _get_timeout('TIMEOUT_StorageStateLoadedEvent', 30.0))  # seconds


# ============================================================================
# File Download Events
# ============================================================================


class DownloadStartedEvent(BaseEvent):
	"""A file download has started (CDP downloadWillBegin received)."""

	guid: str  # CDP download GUID to correlate with FileDownloadedEvent
	url: str
	suggested_filename: str
	auto_download: bool = False  # Whether this was triggered automatically

	event_timeout: float | None = Field(default_factory=lambda: _get_timeout('TIMEOUT_DownloadStartedEvent', 5.0))  # seconds


class DownloadProgressEvent(BaseEvent):
	"""A file download progress update (CDP downloadProgress received)."""

	guid: str  # CDP download GUID to correlate with other download events
	received_bytes: int
	total_bytes: int  # 0 if unknown
	state: str  # 'inProgress', 'completed', or 'canceled'

	event_timeout: float | None = Field(default_factory=lambda: _get_timeout('TIMEOUT_DownloadProgressEvent', 5.0))  # seconds


class FileDownloadedEvent(BaseEvent):
	"""A file has been downloaded."""

	guid: str | None = None  # CDP download GUID to correlate with DownloadStartedEvent
	url: str
	path: str
	file_name: str
	file_size: int
	file_type: str | None = None  # e.g., 'pdf', 'zip', 'docx', etc.
	mime_type: str | None = None  # e.g., 'application/pdf'
	from_cache: bool = False
	auto_download: bool = False  # Whether this was an automatic download (e.g., PDF auto-download)

	event_timeout: float | None = Field(default_factory=lambda: _get_timeout('TIMEOUT_FileDownloadedEvent', 30.0))  # seconds


class AboutBlankDVDScreensaverShownEvent(BaseEvent):
	"""AboutBlankWatchdog has shown DVD screensaver animation on an about:blank tab."""

	target_id: TargetID
	error: str | None = None


class DialogOpenedEvent(BaseEvent):
	"""Event dispatched when a JavaScript dialog is opened and handled."""

	dialog_type: str  # 'alert', 'confirm', 'prompt', or 'beforeunload'
	message: str
	url: str
	frame_id: str | None = None  # Can be None when frameId is not provided by CDP
	# target_id: TargetID   # TODO: add this to avoid needing target_id_from_frame() later


# ============================================================================
# Captcha Solver Events
# ============================================================================


class CaptchaSolverStartedEvent(BaseEvent):
	"""Captcha solving started by the browser proxy.

	Emitted when the browser proxy detects a CAPTCHA and begins solving it.
	The agent should wait for a corresponding CaptchaSolverFinishedEvent before proceeding.
	"""

	target_id: TargetID
	vendor: str  # e.g. 'cloudflare', 'recaptcha', 'hcaptcha', 'datadome', 'perimeterx', 'geetest'
	url: str
	started_at: int  # Unix millis

	event_timeout: float | None = Field(default_factory=lambda: _get_timeout('TIMEOUT_CaptchaSolverStartedEvent', 5.0))


class CaptchaSolverFinishedEvent(BaseEvent):
	"""Captcha solving finished by the browser proxy.

	Emitted when the browser proxy finishes solving a CAPTCHA (successfully or not).
	"""

	target_id: TargetID
	vendor: str
	url: str
	duration_ms: int
	finished_at: int  # Unix millis
	success: bool  # Whether the captcha was solved successfully

	event_timeout: float | None = Field(default_factory=lambda: _get_timeout('TIMEOUT_CaptchaSolverFinishedEvent', 5.0))


# Note: Model rebuilding for forward references is handled in the importing modules
# Events with 'EnhancedDOMTreeNode' forward references (ClickElementEvent, TypeTextEvent,
# ScrollEvent, UploadFileEvent) need model_rebuild() called after imports are complete
