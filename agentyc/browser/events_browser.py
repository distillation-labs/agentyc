"""Browser lifecycle and tab event models."""

from __future__ import annotations

from typing import Any

from bubus import BaseEvent
from cdp_use.cdp.target import TargetID
from pydantic import BaseModel, Field

from agentyc.browser.events_support import _get_timeout


class BrowserStartEvent(BaseEvent):
	"""Start/connect to browser."""

	cdp_url: str | None = None
	launch_options: dict[str, Any] = Field(default_factory=dict)

	# Cold local Chrome startup in stdio mode can take materially longer than a normal tool action.
	event_timeout: float | None = Field(default_factory=lambda: _get_timeout('TIMEOUT_BrowserStartEvent', 90.0))  # seconds


class BrowserStopEvent(BaseEvent):
	"""Stop/disconnect from browser."""

	force: bool = False

	event_timeout: float | None = Field(default_factory=lambda: _get_timeout('TIMEOUT_BrowserStopEvent', 45.0))  # seconds


class BrowserLaunchResult(BaseModel):
	"""Result of launching a browser."""

	# TODO: add browser executable_path, pid, version, latency, user_data_dir, X11 $DISPLAY, host IP address, etc.
	cdp_url: str


class BrowserLaunchEvent(BaseEvent[BrowserLaunchResult]):
	"""Launch a local browser process."""

	# TODO: add executable_path, proxy settings, preferences, extra launch args, etc.

	event_timeout: float | None = Field(default_factory=lambda: _get_timeout('TIMEOUT_BrowserLaunchEvent', 60.0))  # seconds


class BrowserKillEvent(BaseEvent):
	"""Kill local browser subprocess."""

	event_timeout: float | None = Field(default_factory=lambda: _get_timeout('TIMEOUT_BrowserKillEvent', 30.0))  # seconds


# TODO: replace all Runtime.evaluate() calls with this event
# class ExecuteJavaScriptEvent(BaseEvent):
# 	"""Execute JavaScript in page context."""

# 	target_id: TargetID
# 	expression: str
# 	await_promise: bool = True

# 	event_timeout: float | None = 60.0  # seconds

# TODO: add this and use the old BrowserProfile.viewport options to set it
# class SetViewportEvent(BaseEvent):
# 	"""Set the viewport size."""

# 	width: int
# 	height: int
# 	device_scale_factor: float = 1.0

# 	event_timeout: float | None = 15.0  # seconds


# Moved to storage state
# class SetCookiesEvent(BaseEvent):
# 	"""Set browser cookies."""

# 	cookies: list[dict[str, Any]]

# 	event_timeout: float | None = (
# 		30.0  # only long to support the edge case of restoring a big localStorage / on many origins (has to O(n) visit each origin to restore)
# 	)


# class GetCookiesEvent(BaseEvent):
# 	"""Get browser cookies."""

# 	urls: list[str] | None = None

# 	event_timeout: float | None = 30.0  # seconds


# ============================================================================
# DOM-related Events
# ============================================================================


class BrowserConnectedEvent(BaseEvent):
	"""Browser has started/connected."""

	cdp_url: str

	event_timeout: float | None = Field(default_factory=lambda: _get_timeout('TIMEOUT_BrowserConnectedEvent', 30.0))  # seconds


class BrowserStoppedEvent(BaseEvent):
	"""Browser has stopped/disconnected."""

	reason: str | None = None

	event_timeout: float | None = Field(default_factory=lambda: _get_timeout('TIMEOUT_BrowserStoppedEvent', 30.0))  # seconds


class TabCreatedEvent(BaseEvent):
	"""A new tab was created."""

	target_id: TargetID
	url: str

	event_timeout: float | None = Field(default_factory=lambda: _get_timeout('TIMEOUT_TabCreatedEvent', 30.0))  # seconds


class TabClosedEvent(BaseEvent):
	"""A tab was closed."""

	target_id: TargetID

	# TODO:
	# new_focus_target_id: int | None = None
	# new_focus_url: str | None = None

	event_timeout: float | None = Field(default_factory=lambda: _get_timeout('TIMEOUT_TabClosedEvent', 3.0))  # seconds


# TODO: emit this when DOM changes significantly, inner frame navigates, form submits, history.pushState(), etc.
# class TabUpdatedEvent(BaseEvent):
# 	"""Tab information updated (URL changed, etc.)."""

# 	target_id: TargetID
# 	url: str


class AgentFocusChangedEvent(BaseEvent):
	"""Agent focus changed to a different tab."""

	target_id: TargetID
	url: str

	event_timeout: float | None = Field(default_factory=lambda: _get_timeout('TIMEOUT_AgentFocusChangedEvent', 10.0))  # seconds


class TargetCrashedEvent(BaseEvent):
	"""A target has crashed."""

	target_id: TargetID
	error: str

	event_timeout: float | None = Field(default_factory=lambda: _get_timeout('TIMEOUT_TargetCrashedEvent', 10.0))  # seconds


class NavigationStartedEvent(BaseEvent):
	"""Navigation started."""

	target_id: TargetID
	url: str

	event_timeout: float | None = Field(default_factory=lambda: _get_timeout('TIMEOUT_NavigationStartedEvent', 30.0))  # seconds


class NavigationCompleteEvent(BaseEvent):
	"""Navigation completed."""

	target_id: TargetID
	url: str
	status: int | None = None
	error_message: str | None = None  # Error/timeout message if navigation had issues
	loading_status: str | None = None  # Detailed loading status (e.g., network timeout info)

	event_timeout: float | None = Field(default_factory=lambda: _get_timeout('TIMEOUT_NavigationCompleteEvent', 30.0))  # seconds


# ============================================================================
# Error Events
# ============================================================================


class BrowserErrorEvent(BaseEvent):
	"""An error occurred in the browser layer."""

	error_type: str
	message: str
	details: dict[str, Any] = Field(default_factory=dict)

	event_timeout: float | None = Field(default_factory=lambda: _get_timeout('TIMEOUT_BrowserErrorEvent', 30.0))  # seconds


class BrowserReconnectingEvent(BaseEvent):
	"""WebSocket reconnection attempt is starting."""

	cdp_url: str
	attempt: int
	max_attempts: int

	event_timeout: float | None = Field(default_factory=lambda: _get_timeout('TIMEOUT_BrowserReconnectingEvent', 30.0))  # seconds


class BrowserReconnectedEvent(BaseEvent):
	"""WebSocket reconnection succeeded."""

	cdp_url: str
	attempt: int
	downtime_seconds: float

	event_timeout: float | None = Field(default_factory=lambda: _get_timeout('TIMEOUT_BrowserReconnectedEvent', 30.0))  # seconds


# ============================================================================
# Storage State Events
# ============================================================================
