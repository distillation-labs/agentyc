"""Page, target, and window delegate methods for BrowserSession."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from cdp_use import CDPClient
from cdp_use.cdp.network import Cookie
from cdp_use.cdp.target import TargetID

from agentyc.browser import session_navigation, session_shared_browser, session_targets
from agentyc.browser.page import Page
from agentyc.browser.screenshot_processing import resize_screenshot_for_llm as process_screenshot_for_llm
from agentyc.browser.session_models import BrowserWindowBounds, CDPSession
from agentyc.dom.views import EnhancedDOMTreeNode, TargetInfo
from agentyc.observability import observe_debug

if TYPE_CHECKING:
	from agentyc.browser.session import BrowserSession


class SessionTargetsMixin:
	def _session(self) -> BrowserSession:
		return cast('BrowserSession', self)

	@property
	def cdp_client(self) -> CDPClient:
		"""Get the cached root CDP cdp_session.cdp_client. The client is created and started in self.connect()."""
		session = self._session()
		assert session._cdp_client_root is not None, 'CDP client not initialized - browser may not be connected yet'
		return session._cdp_client_root

	async def new_page(self, url: str | None = None) -> Page:
		return await session_shared_browser.new_page(self._session(), url)

	async def get_current_page(self) -> Page | None:
		return await session_shared_browser.get_current_page(self._session())

	async def must_get_current_page(self) -> Page:
		return await session_shared_browser.must_get_current_page(self._session())

	async def get_pages(self) -> list[Page]:
		return await session_shared_browser.get_pages(self._session())

	async def close_page(self, page: Page | str) -> None:
		await session_shared_browser.close_page(self._session(), page)

	async def cookies(self) -> list[Cookie]:
		"""Get cookies, optionally filtered by URLs."""

		result = await self.cdp_client.send.Storage.getCookies()
		return result['cookies']

	async def clear_cookies(self) -> None:
		"""Clear all cookies."""
		await self.cdp_client.send.Network.clearBrowserCookies()

	async def export_storage_state(self, output_path: str | Path | None = None) -> dict[str, Any]:
		return await session_targets.export_storage_state(self._session(), output_path)

	async def get_or_create_cdp_session(self, target_id: TargetID | None = None, focus: bool = True) -> CDPSession:
		return await session_targets.get_or_create_cdp_session(self._session(), target_id, focus)

	async def set_extra_headers(self, headers: dict[str, str], target_id: TargetID | None = None) -> None:
		await session_targets.set_extra_headers(self._session(), headers, target_id)

	async def get_current_target_info(self) -> TargetInfo | None:
		return await session_shared_browser.get_current_target_info(self._session())

	async def get_current_page_url(self) -> str:
		return await session_shared_browser.get_current_page_url(self._session())

	async def get_current_page_title(self) -> str:
		return await session_shared_browser.get_current_page_title(self._session())

	async def navigate_to(self, url: str, new_tab: bool = False) -> None:
		await session_navigation.navigate_to(self._session(), url, new_tab)

	# endregion - ========== ID Lookup Methods ==========

	# region - ========== DOM Helper Methods ==========

	async def get_target_id_from_tab_id(self, tab_id: str) -> TargetID:
		return await session_targets.get_target_id_from_tab_id(self._session(), tab_id)

	async def get_target_id_from_url(self, url: str) -> TargetID:
		return await session_targets.get_target_id_from_url(self._session(), url)

	async def get_most_recently_opened_target_id(self) -> TargetID:
		return await session_targets.get_most_recently_opened_target_id(self._session())

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
			self._session(),
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
			self._session(),
			url=url,
			background=background,
			new_window=new_window,
			window_bounds=window_bounds,
			browser_context_id=browser_context_id,
		)

	async def _cdp_close_page(self, target_id: TargetID) -> None:
		await session_shared_browser._cdp_close_page(self._session(), target_id)

	async def _cdp_get_cookies(self) -> list[Cookie]:
		return await session_targets._cdp_get_cookies(self._session())

	async def _cdp_set_cookies(self, cookies: list[Cookie]) -> None:
		await session_targets._cdp_set_cookies(self._session(), cookies)

	async def _cdp_clear_cookies(self) -> None:
		await session_targets._cdp_clear_cookies(self._session())

	async def _cdp_grant_permissions(self, permissions: list[str], origin: str | None = None) -> None:
		await session_targets._cdp_grant_permissions(self._session(), permissions, origin)

	async def _cdp_set_geolocation(self, latitude: float, longitude: float, accuracy: float = 100) -> None:
		await session_targets._cdp_set_geolocation(self._session(), latitude, longitude, accuracy)

	async def _cdp_clear_geolocation(self) -> None:
		await session_targets._cdp_clear_geolocation(self._session())

	async def _cdp_add_init_script(self, script: str, target_id: TargetID | None = None) -> str:
		return await session_targets._cdp_add_init_script(self._session(), script, target_id)

	async def _cdp_remove_init_script(self, identifier: str, target_id: TargetID | None = None) -> None:
		await session_targets._cdp_remove_init_script(self._session(), identifier, target_id)

	async def _cdp_get_window_context(self, target_id: TargetID) -> dict[str, Any] | None:
		return await session_shared_browser._cdp_get_window_context(self._session(), target_id)

	async def _cdp_set_window_bounds(
		self,
		target_id: TargetID,
		bounds: BrowserWindowBounds | dict[str, Any],
	) -> dict[str, Any] | None:
		return await session_shared_browser._cdp_set_window_bounds(self._session(), target_id, bounds)

	async def _cdp_set_viewport(
		self,
		width: int,
		height: int,
		device_scale_factor: float = 1.0,
		mobile: bool = False,
		target_id: str | None = None,
	) -> None:
		await session_targets._cdp_set_viewport(self._session(), width, height, device_scale_factor, mobile, target_id)

	async def _cdp_get_origins(self) -> list[dict[str, Any]]:
		return await session_targets._cdp_get_origins(self._session())

	async def _cdp_get_storage_state(self) -> dict:
		return await session_targets._cdp_get_storage_state(self._session())

	async def _cdp_navigate(self, url: str, target_id: TargetID | None = None) -> None:
		await session_targets._cdp_navigate(self._session(), url, target_id)

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
		return await session_targets.get_all_frames(self._session())

	async def _populate_frame_metadata(self, all_frames: dict[str, dict], target_sessions: dict[str, str]) -> None:
		await session_targets._populate_frame_metadata(self._session(), all_frames, target_sessions)

	async def find_frame_target(self, frame_id: str, all_frames: dict[str, dict] | None = None) -> dict | None:
		return await session_targets.find_frame_target(self._session(), frame_id, all_frames)

	async def cdp_client_for_target(self, target_id: TargetID) -> CDPSession:
		return await session_targets.cdp_client_for_target(self._session(), target_id)

	async def cdp_client_for_frame(self, frame_id: str) -> CDPSession:
		return await session_targets.cdp_client_for_frame(self._session(), frame_id)

	async def cdp_client_for_node(self, node: EnhancedDOMTreeNode) -> CDPSession:
		return await session_targets.cdp_client_for_node(self._session(), node)

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
			self._session(),
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
		return await session_shared_browser.get_window_bounds(self._session(), target_id)

	async def set_window_bounds(
		self,
		bounds: BrowserWindowBounds | dict[str, Any],
		target_id: TargetID | None = None,
	) -> dict[str, Any] | None:
		return await session_shared_browser.set_window_bounds(self._session(), bounds, target_id)

	async def create_collaborative_page(
		self,
		url: str = 'about:blank',
		*,
		new_window: bool | None = None,
		window_bounds: BrowserWindowBounds | dict[str, Any] | None = None,
	) -> Page:
		return await session_shared_browser.create_collaborative_page(
			self._session(),
			url,
			new_window=new_window,
			window_bounds=window_bounds,
		)
