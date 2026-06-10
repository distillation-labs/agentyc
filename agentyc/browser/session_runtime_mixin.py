"""Lifecycle and runtime delegate methods for BrowserSession."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from cdp_use.cdp.target import TargetID

from agentyc.browser import (
	session_connection,
	session_navigation,
	session_network,
	session_reconnect,
	session_runtime,
	session_watchdogs,
)
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
from agentyc.browser.session_models import CDPSession, Target
from agentyc.browser.views import BrowserStateSummary, TabInfo
from agentyc.observability import observe_debug

if TYPE_CHECKING:
	from agentyc.browser.session import BrowserSession


class SessionRuntimeMixin:
	def _session(self) -> 'BrowserSession':
		return cast('BrowserSession', self)

	async def reset(self) -> None:
		await session_runtime.reset(self._session())

	def model_post_init(self, __context) -> None:
		session_runtime.model_post_init(self._session(), __context)

	@observe_debug(ignore_input=True, ignore_output=True, name='browser_session_start')
	async def start(self) -> None:
		session = self._session()
		start_event = session.event_bus.dispatch(BrowserStartEvent())
		await start_event
		await start_event.event_result(raise_if_any=True, raise_if_none=False)

	async def kill(self) -> None:
		await session_runtime.kill(self._session())

	async def stop(self) -> None:
		await session_runtime.stop(self._session())

	async def close(self) -> None:
		await session_runtime.close(self._session())

	@observe_debug(ignore_input=True, ignore_output=True, name='browser_start_event_handler')
	async def on_BrowserStartEvent(self, event: BrowserStartEvent) -> dict[str, str]:
		return await session_connection.on_BrowserStartEvent(self._session(), event)

	async def on_NavigateToUrlEvent(self, event: NavigateToUrlEvent) -> None:
		await session_navigation.on_NavigateToUrlEvent(self._session(), event)

	async def _navigate_and_wait(self, url: str, target_id: str, timeout: float | None = None, wait_until: str = 'load', nav_timeout: float | None = None) -> None:
		await session_navigation._navigate_and_wait(self._session(), url, target_id, timeout=timeout, wait_until=wait_until, nav_timeout=nav_timeout)

	@staticmethod
	def _urls_match_for_navigation_ready(current_url: str, target_url: str) -> bool:
		return session_navigation._urls_match_for_navigation_ready(current_url, target_url)

	async def _navigation_ready_via_dom(self, *, cdp_session: Any, url: str, wait_until: str) -> bool:
		return await session_navigation._navigation_ready_via_dom(self._session(), cdp_session=cdp_session, url=url, wait_until=wait_until)

	async def on_SwitchTabEvent(self, event: SwitchTabEvent) -> TargetID:
		return await session_navigation.on_SwitchTabEvent(self._session(), event)

	async def on_CloseTabEvent(self, event: CloseTabEvent) -> None:
		await session_navigation.on_CloseTabEvent(self._session(), event)

	async def on_TabCreatedEvent(self, event: TabCreatedEvent) -> None:
		await session_navigation.on_TabCreatedEvent(self._session(), event)

	async def on_TabClosedEvent(self, event: TabClosedEvent) -> None:
		await session_navigation.on_TabClosedEvent(self._session(), event)

	async def on_AgentFocusChangedEvent(self, event: AgentFocusChangedEvent) -> None:
		await session_navigation.on_AgentFocusChangedEvent(self._session(), event)

	async def on_FileDownloadedEvent(self, event: FileDownloadedEvent) -> None:
		await session_runtime.on_FileDownloadedEvent(self._session(), event)

	async def on_BrowserStopEvent(self, event: BrowserStopEvent) -> None:
		await session_connection.on_BrowserStopEvent(self._session(), event)

	def get_focused_target(self) -> 'Target | None':
		return session_runtime.get_focused_target(self._session())

	def get_page_targets(self) -> 'list[Target]':
		return session_runtime.get_page_targets(self._session())

	async def get_browser_state_summary(self, include_screenshot: bool = True, cached: bool = False, include_recent_events: bool = False) -> BrowserStateSummary:
		return await session_runtime.get_browser_state_summary(self._session(), include_screenshot=include_screenshot, cached=cached, include_recent_events=include_recent_events)

	async def get_state_as_text(self) -> str:
		return await session_runtime.get_state_as_text(self._session())

	async def get_tabs(self) -> list[TabInfo]:
		from agentyc.browser.session_targets import get_tabs
		return await get_tabs(self._session())

	async def attach_all_watchdogs(self) -> None:
		await session_watchdogs.attach_all_watchdogs(self._session())

	async def connect(self, cdp_url: str | None = None) -> 'BrowserSession':
		return await session_connection.connect(self._session(), cdp_url)

	async def _setup_proxy_auth(self) -> None:
		await session_connection._setup_proxy_auth(self._session())

	async def configure_fetch_interception(self) -> None:
		await session_network.configure_fetch_interception(self._session())

	async def configure_attached_network_session(self, cdp_session: CDPSession) -> None:
		await session_network.configure_attached_network_session(self._session(), cdp_session)

	async def add_network_mock(self, **kwargs: Any) -> dict[str, Any]:
		return await session_network.add_network_mock(self._session(), **kwargs)

	async def remove_network_mock(self, mock_id: str | None = None) -> dict[str, Any]:
		return await session_network.remove_network_mock(self._session(), mock_id)

	def list_network_mocks(self) -> list[dict[str, Any]]:
		return session_network.list_network_mocks(self._session())

	async def set_network_conditions(self, **kwargs: Any) -> dict[str, Any]:
		return await session_network.set_network_conditions(self._session(), **kwargs)

	def get_network_conditions(self) -> list[dict[str, Any]]:
		return session_network.get_network_conditions(self._session())

	def sanitize_replay_headers(self, headers: dict[str, Any] | None) -> dict[str, str]:
		return session_network.sanitize_replay_headers(headers)

	async def reconnect(self) -> None:
		await session_reconnect.reconnect(self._session())

	async def _auto_reconnect(self, max_attempts: int = 3) -> None:
		await session_reconnect._auto_reconnect(self._session(), max_attempts)

	def _attach_ws_drop_callback(self) -> None:
		session_reconnect._attach_ws_drop_callback(self._session())

	async def _close_extension_options_pages(self) -> None:
		await session_navigation._close_extension_options_pages(self._session())

	@property
	def downloaded_files(self) -> list[str]:
		return session_runtime.downloaded_files(self._session())
