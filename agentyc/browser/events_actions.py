"""Browser action event models."""

from __future__ import annotations

from typing import Any, Literal

from bubus import BaseEvent
from bubus.models import T_EventResultType
from cdp_use.cdp.target import TargetID
from pydantic import Field, field_validator

from agentyc.browser.events_support import _get_timeout
from agentyc.browser.views import BrowserStateSummary
from agentyc.dom.views import EnhancedDOMTreeNode


class ElementSelectedEvent(BaseEvent[T_EventResultType]):
	"""An element was selected."""

	node: EnhancedDOMTreeNode

	@field_validator('node', mode='before')
	@classmethod
	def serialize_node(cls, data: EnhancedDOMTreeNode | None) -> EnhancedDOMTreeNode | None:
		if data is None:
			return None
		return EnhancedDOMTreeNode(
			node_id=data.node_id,
			backend_node_id=data.backend_node_id,
			session_id=data.session_id,
			frame_id=data.frame_id,
			target_id=data.target_id,
			node_type=data.node_type,
			node_name=data.node_name,
			node_value=data.node_value,
			attributes=data.attributes,
			is_scrollable=data.is_scrollable,
			is_visible=data.is_visible,
			absolute_position=data.absolute_position,
			# override the circular reference fields in EnhancedDOMTreeNode as they cant be serialized and aren't needed by event handlers
			# only used internally by the DOM service during DOM tree building process, not intended public API use
			content_document=None,
			shadow_root_type=None,
			shadow_roots=[],
			parent_node=None,
			children_nodes=[],
			ax_node=None,
			snapshot_node=None,
		)


# TODO: add page handle to events
# class PageHandle(share a base with browser.session.CDPSession?):
# 	url: str
# 	target_id: TargetID
#   @classmethod
#   def from_target_id(cls, target_id: TargetID) -> Self:
#     return cls(target_id=target_id)
#   @classmethod
#   def from_target_id(cls, target_id: TargetID) -> Self:
#     return cls(target_id=target_id)
#   @classmethod
#   def from_url(cls, url: str) -> Self:
#   @property
#   def root_frame_id(self) -> str:
#     return self.target_id
#   @property
#   def session_id(self) -> str:
#     return browser_session.get_or_create_cdp_session(self.target_id).session_id

# class PageSelectedEvent(BaseEvent[T_EventResultType]):
# 	"""An event like SwitchToTabEvent(page=PageHandle) or CloseTabEvent(page=PageHandle)"""
# 	page: PageHandle


class NavigateToUrlEvent(BaseEvent[None]):
	"""Navigate to a specific URL."""

	url: str
	wait_until: Literal['load', 'domcontentloaded', 'networkidle', 'commit'] = 'load'
	timeout_ms: int | None = None
	new_tab: bool = Field(
		default=False, description='Set True to leave the current tab alone and open a new tab in the foreground for the new URL'
	)
	# existing_tab: PageHandle | None = None  # TODO

	# time limits enforced by bubus, not exposed to LLM:
	event_timeout: float | None = Field(default_factory=lambda: _get_timeout('TIMEOUT_NavigateToUrlEvent', 30.0))  # seconds


class ClickElementEvent(ElementSelectedEvent[dict[str, Any] | None]):
	"""Click an element."""

	node: EnhancedDOMTreeNode
	button: Literal['left', 'right', 'middle'] = 'left'
	# click_count: int = 1           # TODO
	# expect_download: bool = False  # moved to downloads_watchdog.py

	event_timeout: float | None = Field(default_factory=lambda: _get_timeout('TIMEOUT_ClickElementEvent', 15.0))  # seconds


class ClickCoordinateEvent(BaseEvent[dict]):
	"""Click at specific coordinates."""

	coordinate_x: int
	coordinate_y: int
	button: Literal['left', 'right', 'middle'] = 'left'
	force: bool = False  # If True, skip safety checks (file input, print, select)

	event_timeout: float | None = Field(default_factory=lambda: _get_timeout('TIMEOUT_ClickCoordinateEvent', 15.0))  # seconds


class TypeTextEvent(ElementSelectedEvent[dict | None]):
	"""Type text into an element."""

	node: EnhancedDOMTreeNode
	text: str
	clear: bool = True
	is_sensitive: bool = False  # Flag to indicate if text contains sensitive data
	sensitive_key_name: str | None = None  # Name of the sensitive key being typed (e.g., 'username', 'password')

	event_timeout: float | None = Field(default_factory=lambda: _get_timeout('TIMEOUT_TypeTextEvent', 60.0))  # seconds


class ScrollEvent(ElementSelectedEvent[None]):
	"""Scroll the page or element."""

	direction: Literal['up', 'down', 'left', 'right']
	amount: int  # pixels
	node: EnhancedDOMTreeNode | None = None  # None means scroll page

	event_timeout: float | None = Field(default_factory=lambda: _get_timeout('TIMEOUT_ScrollEvent', 8.0))  # seconds


class SwitchTabEvent(BaseEvent[TargetID]):
	"""Switch to a different tab."""

	target_id: TargetID | None = Field(default=None, description='None means switch to the most recently opened tab')
	activate_target: bool = True

	event_timeout: float | None = Field(default_factory=lambda: _get_timeout('TIMEOUT_SwitchTabEvent', 10.0))  # seconds


class CloseTabEvent(BaseEvent[None]):
	"""Close a tab."""

	target_id: TargetID

	event_timeout: float | None = Field(default_factory=lambda: _get_timeout('TIMEOUT_CloseTabEvent', 10.0))  # seconds


class ScreenshotEvent(BaseEvent[str]):
	"""Request to take a screenshot."""

	full_page: bool = False
	clip: dict[str, float] | None = None  # {x, y, width, height}

	event_timeout: float | None = Field(default_factory=lambda: _get_timeout('TIMEOUT_ScreenshotEvent', 15.0))  # seconds


class BrowserStateRequestEvent(BaseEvent[BrowserStateSummary]):
	"""Request current browser state."""

	include_dom: bool = True
	include_screenshot: bool = True
	include_recent_events: bool = False

	event_timeout: float | None = Field(default_factory=lambda: _get_timeout('TIMEOUT_BrowserStateRequestEvent', 30.0))  # seconds


# class WaitForConditionEvent(BaseEvent):
# 	"""Wait for a condition."""

# 	condition: Literal['navigation', 'selector', 'timeout', 'load_state']
# 	timeout: float = 30000
# 	selector: str | None = None
# 	state: Literal['attached', 'detached', 'visible', 'hidden'] | None = None


class GoBackEvent(BaseEvent[None]):
	"""Navigate back in browser history."""

	event_timeout: float | None = Field(default_factory=lambda: _get_timeout('TIMEOUT_GoBackEvent', 15.0))  # seconds


class GoForwardEvent(BaseEvent[None]):
	"""Navigate forward in browser history."""

	event_timeout: float | None = Field(default_factory=lambda: _get_timeout('TIMEOUT_GoForwardEvent', 15.0))  # seconds


class RefreshEvent(BaseEvent[None]):
	"""Refresh/reload the current page."""

	event_timeout: float | None = Field(default_factory=lambda: _get_timeout('TIMEOUT_RefreshEvent', 15.0))  # seconds


class WaitEvent(BaseEvent[None]):
	"""Wait for a specified number of seconds."""

	seconds: float = 3.0
	max_seconds: float = 10.0  # Safety cap

	event_timeout: float | None = Field(default_factory=lambda: _get_timeout('TIMEOUT_WaitEvent', 60.0))  # seconds


class SendKeysEvent(BaseEvent[None]):
	"""Send keyboard keys/shortcuts."""

	keys: str  # e.g., "ctrl+a", "cmd+c", "Enter"

	event_timeout: float | None = Field(default_factory=lambda: _get_timeout('TIMEOUT_SendKeysEvent', 60.0))  # seconds


class UploadFileEvent(ElementSelectedEvent[None]):
	"""Upload a file to an element."""

	node: EnhancedDOMTreeNode
	file_path: str

	event_timeout: float | None = Field(default_factory=lambda: _get_timeout('TIMEOUT_UploadFileEvent', 30.0))  # seconds


class GetDropdownOptionsEvent(ElementSelectedEvent[dict[str, str]]):
	"""Get all options from any dropdown (native <select>, ARIA menus, or custom dropdowns).

	Returns a dict containing dropdown type, options list, and element metadata."""

	node: EnhancedDOMTreeNode

	event_timeout: float | None = Field(
		default_factory=lambda: _get_timeout('TIMEOUT_GetDropdownOptionsEvent', 15.0)
	)  # some dropdowns lazy-load the list of options on first interaction, so we need to wait for them to load (e.g. table filter lists can have thousands of options)


class SelectDropdownOptionEvent(ElementSelectedEvent[dict[str, str]]):
	"""Select a dropdown option by exact text from any dropdown type.

	Returns a dict containing success status and selection details."""

	node: EnhancedDOMTreeNode
	text: str  # The option text to select

	event_timeout: float | None = Field(default_factory=lambda: _get_timeout('TIMEOUT_SelectDropdownOptionEvent', 8.0))  # seconds


class ScrollToTextEvent(BaseEvent[None]):
	"""Scroll to specific text on the page. Raises exception if text not found."""

	text: str
	direction: Literal['up', 'down'] = 'down'

	event_timeout: float | None = Field(default_factory=lambda: _get_timeout('TIMEOUT_ScrollToTextEvent', 15.0))  # seconds


# ============================================================================
