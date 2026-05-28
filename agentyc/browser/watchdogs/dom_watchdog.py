"""DOM watchdog for browser DOM tree management using CDP."""

import asyncio
from typing import TYPE_CHECKING

from agentyc.browser.events import (
	BrowserErrorEvent,
	BrowserStateRequestEvent,
	TabCreatedEvent,
)
from agentyc.browser.watchdog_base import BaseWatchdog
from agentyc.browser.watchdogs.dom_watchdog_dom_ops import (
	build_dom_tree_without_highlights,
	capture_clean_screenshot,
)
from agentyc.browser.watchdogs.dom_watchdog_network import (
	get_pending_network_requests,
	get_recent_events_str,
)
from agentyc.browser.watchdogs.dom_watchdog_page_meta import (
	detect_pagination_buttons,
	get_page_info,
)
from agentyc.dom.service import DomService
from agentyc.dom.views import (
	EnhancedDOMTreeNode,
	SerializedDOMState,
)
from agentyc.observability import observe_debug
from agentyc.utils import create_task_with_error_handling, time_execution_async

if TYPE_CHECKING:
	from agentyc.browser.views import BrowserStateSummary, NetworkRequest, PageInfo, PaginationButton


class DOMWatchdog(BaseWatchdog):
	"""Handles DOM tree building, serialization, and element access via CDP.

	This watchdog acts as a bridge between the event-driven browser session
	and the DomService implementation, maintaining cached state and providing
	helper methods for other watchdogs.
	"""

	LISTENS_TO = [TabCreatedEvent, BrowserStateRequestEvent]
	EMITS = [BrowserErrorEvent]

	# Public properties for other watchdogs
	selector_map: dict[int, EnhancedDOMTreeNode] | None = None
	current_dom_state: SerializedDOMState | None = None
	enhanced_dom_tree: EnhancedDOMTreeNode | None = None

	# Internal DOM service
	_dom_service: DomService | None = None

	# Network tracking - maps request_id to (url, start_time, method, resource_type)
	_pending_requests: dict[str, tuple[str, float, str, str | None]] = {}

	async def on_TabCreatedEvent(self, event: TabCreatedEvent) -> None:
		# self.logger.debug('Setting up init scripts in browser')
		return None

	def _get_recent_events_str(self, limit: int = 10) -> str | None:
		return get_recent_events_str(self, limit=limit)

	async def _get_pending_network_requests(self) -> list['NetworkRequest']:
		return await get_pending_network_requests(self)

	@observe_debug(ignore_input=True, ignore_output=True, name='browser_state_request_event')
	async def on_BrowserStateRequestEvent(self, event: BrowserStateRequestEvent) -> 'BrowserStateSummary':
		"""Handle browser state request by coordinating DOM building and screenshot capture.

		This is the main entry point for getting the complete browser state.

		Args:
			event: The browser state request event with options

		Returns:
			Complete BrowserStateSummary with DOM, screenshot, and target info
		"""
		from agentyc.browser.views import BrowserStateSummary, PageInfo

		self.logger.debug('🔍 DOMWatchdog.on_BrowserStateRequestEvent: STARTING browser state request')
		page_url = await self.browser_session.get_current_page_url()
		self.logger.debug(f'🔍 DOMWatchdog.on_BrowserStateRequestEvent: Got page URL: {page_url}')

		# Get focused session for logging (validation already done by get_current_page_url)
		if self.browser_session.agent_focus_target_id:
			self.logger.debug(f'Current page URL: {page_url}, target_id: {self.browser_session.agent_focus_target_id}')

		# check if we should skip DOM tree build for pointless pages
		not_a_meaningful_website = page_url.lower().split(':', 1)[0] not in ('http', 'https')

		# Check for pending network requests BEFORE waiting (so we can see what's loading)
		# Timeout after 2s — on slow CI machines or heavy pages, this call can hang
		# for 15s+ eating into the 30s BrowserStateRequestEvent budget.
		pending_requests_before_wait = []
		if not not_a_meaningful_website:
			try:
				pending_requests_before_wait = await asyncio.wait_for(self._get_pending_network_requests(), timeout=2.0)
				if pending_requests_before_wait:
					self.logger.debug(f'🔍 Found {len(pending_requests_before_wait)} pending requests before stability wait')
			except TimeoutError:
				self.logger.debug('Pending network request check timed out (2s), skipping')
			except Exception as e:
				self.logger.debug(f'Failed to get pending requests before wait: {e}')
		pending_requests = pending_requests_before_wait
		# Wait for page stability using browser profile settings (main branch pattern)
		if not not_a_meaningful_website:
			self.logger.debug('🔍 DOMWatchdog.on_BrowserStateRequestEvent: ⏳ Waiting for page stability...')
			try:
				if pending_requests_before_wait:
					# Reduced from 1s to 0.3s for faster DOM builds while still allowing critical resources to load
					await asyncio.sleep(0.3)
				self.logger.debug('🔍 DOMWatchdog.on_BrowserStateRequestEvent: ✅ Page stability complete')
			except Exception as e:
				self.logger.warning(
					f'🔍 DOMWatchdog.on_BrowserStateRequestEvent: Network waiting failed: {e}, continuing anyway...'
				)

		# Get tabs info once at the beginning for all paths
		self.logger.debug('🔍 DOMWatchdog.on_BrowserStateRequestEvent: Getting tabs info...')
		tabs_info = await self.browser_session.get_tabs()
		self.logger.debug(f'🔍 DOMWatchdog.on_BrowserStateRequestEvent: Got {len(tabs_info)} tabs')
		self.logger.debug(f'🔍 DOMWatchdog.on_BrowserStateRequestEvent: Tabs info: {tabs_info}')

		# Get viewport / scroll position info, remember changing scroll position should invalidate selector_map cache because it only includes visible elements
		# cdp_session = await self.browser_session.get_or_create_cdp_session(focus=True)
		# scroll_info = await cdp_session.cdp_client.send.Runtime.evaluate(
		# 	params={'expression': 'JSON.stringify({y: document.body.scrollTop, x: document.body.scrollLeft, width: document.documentElement.clientWidth, height: document.documentElement.clientHeight})'},
		# 	session_id=cdp_session.session_id,
		# )
		# self.logger.debug(f'🔍 DOMWatchdog.on_BrowserStateRequestEvent: Got scroll info: {scroll_info["result"]}')

		try:
			# Fast path for empty pages
			if not_a_meaningful_website:
				self.logger.debug(f'⚡ Skipping BuildDOMTree for empty target: {page_url}')
				self.logger.debug(f'📸 Not taking screenshot for empty page: {page_url} (non-http/https URL)')

				# Create minimal DOM state
				content = SerializedDOMState(_root=None, selector_map={})

				# Skip screenshot for empty pages
				screenshot_b64 = None

				# Try to get page info from CDP, fall back to defaults if unavailable
				try:
					page_info = await self._get_page_info()
				except Exception as e:
					self.logger.debug(f'Failed to get page info from CDP for empty page: {e}, using fallback')
					# Use default viewport dimensions
					viewport = self.browser_session.browser_profile.viewport or {'width': 1280, 'height': 720}
					page_info = PageInfo(
						viewport_width=viewport['width'],
						viewport_height=viewport['height'],
						page_width=viewport['width'],
						page_height=viewport['height'],
						scroll_x=0,
						scroll_y=0,
						pixels_above=0,
						pixels_below=0,
						pixels_left=0,
						pixels_right=0,
					)

				return BrowserStateSummary(
					dom_state=content,
					url=page_url,
					title='Empty Tab',
					tabs=tabs_info,
					current_tab_id=self.browser_session.agent_focus_target_id,
					screenshot=screenshot_b64,
					page_info=page_info,
					pixels_above=0,
					pixels_below=0,
					browser_errors=[],
					is_pdf_viewer=False,
					recent_events=self._get_recent_events_str() if event.include_recent_events else None,
					pending_network_requests=[],  # Empty page has no pending requests
					pagination_buttons=[],  # Empty page has no pagination
					closed_popup_messages=self.browser_session._closed_popup_messages.copy(),
				)

			# Execute DOM building and screenshot capture in parallel
			dom_task = None
			screenshot_task = None

			# Start DOM building task if requested
			if event.include_dom:
				self.logger.debug('🔍 DOMWatchdog.on_BrowserStateRequestEvent: 🌳 Starting DOM tree build task...')

				previous_state = (
					self.browser_session._cached_browser_state_summary.dom_state
					if self.browser_session._cached_browser_state_summary
					else None
				)

				dom_task = create_task_with_error_handling(
					self._build_dom_tree_without_highlights(previous_state),
					name='build_dom_tree',
					logger_instance=self.logger,
					suppress_exceptions=True,
				)

			# Start clean screenshot task if requested (without JS highlights)
			if event.include_screenshot:
				self.logger.debug('🔍 DOMWatchdog.on_BrowserStateRequestEvent: 📸 Starting clean screenshot task...')
				screenshot_task = create_task_with_error_handling(
					self._capture_clean_screenshot(),
					name='capture_screenshot',
					logger_instance=self.logger,
					suppress_exceptions=True,
				)

			# Wait for both tasks to complete
			content = None
			screenshot_b64 = None

			if dom_task:
				try:
					content = await dom_task
					self.logger.debug('🔍 DOMWatchdog.on_BrowserStateRequestEvent: ✅ DOM tree build completed')
				except Exception as e:
					self.logger.warning(f'🔍 DOMWatchdog.on_BrowserStateRequestEvent: DOM build failed: {e}, using minimal state')
					content = SerializedDOMState(_root=None, selector_map={})
			else:
				content = SerializedDOMState(_root=None, selector_map={})

			if screenshot_task:
				try:
					screenshot_b64 = await screenshot_task
					self.logger.debug('🔍 DOMWatchdog.on_BrowserStateRequestEvent: ✅ Clean screenshot captured')
				except Exception as e:
					self.logger.warning(f'🔍 DOMWatchdog.on_BrowserStateRequestEvent: Clean screenshot failed: {e}')
					screenshot_b64 = None

			# Add browser-side highlights for user visibility
			if content and content.selector_map and self.browser_session.browser_profile.dom_highlight_elements:
				try:
					self.logger.debug('🔍 DOMWatchdog.on_BrowserStateRequestEvent: 🎨 Adding browser-side highlights...')
					await self.browser_session.add_highlights(content.selector_map)
					self.logger.debug(
						f'🔍 DOMWatchdog.on_BrowserStateRequestEvent: ✅ Added browser highlights for {len(content.selector_map)} elements'
					)
				except Exception as e:
					self.logger.warning(f'🔍 DOMWatchdog.on_BrowserStateRequestEvent: Browser highlighting failed: {e}')

			# Ensure we have valid content
			if not content:
				content = SerializedDOMState(_root=None, selector_map={})

			# Tabs info already fetched at the beginning

			# Get target title safely
			try:
				self.logger.debug('🔍 DOMWatchdog.on_BrowserStateRequestEvent: Getting page title...')
				title = await asyncio.wait_for(self.browser_session.get_current_page_title(), timeout=1.0)
				self.logger.debug(f'🔍 DOMWatchdog.on_BrowserStateRequestEvent: Got title: {title}')
			except Exception as e:
				self.logger.debug(f'🔍 DOMWatchdog.on_BrowserStateRequestEvent: Failed to get title: {e}')
				title = 'Page'

			# Get comprehensive page info from CDP with timeout
			try:
				self.logger.debug('🔍 DOMWatchdog.on_BrowserStateRequestEvent: Getting page info from CDP...')
				page_info = await asyncio.wait_for(self._get_page_info(), timeout=1.0)
				self.logger.debug(f'🔍 DOMWatchdog.on_BrowserStateRequestEvent: Got page info from CDP: {page_info}')
			except Exception as e:
				self.logger.debug(
					f'🔍 DOMWatchdog.on_BrowserStateRequestEvent: Failed to get page info from CDP: {e}, using fallback'
				)
				# Fallback to default viewport dimensions
				viewport = self.browser_session.browser_profile.viewport or {'width': 1280, 'height': 720}
				page_info = PageInfo(
					viewport_width=viewport['width'],
					viewport_height=viewport['height'],
					page_width=viewport['width'],
					page_height=viewport['height'],
					scroll_x=0,
					scroll_y=0,
					pixels_above=0,
					pixels_below=0,
					pixels_left=0,
					pixels_right=0,
				)

			# Check for PDF viewer
			is_pdf_viewer = page_url.endswith('.pdf') or '/pdf/' in page_url

			# Detect pagination buttons from the DOM
			pagination_buttons_data = []
			if content and content.selector_map:
				pagination_buttons_data = self._detect_pagination_buttons(content.selector_map)

			# Build and cache the browser state summary
			if screenshot_b64:
				self.logger.debug(
					f'🔍 DOMWatchdog.on_BrowserStateRequestEvent: 📸 Creating BrowserStateSummary with screenshot, length: {len(screenshot_b64)}'
				)
			else:
				self.logger.debug(
					'🔍 DOMWatchdog.on_BrowserStateRequestEvent: 📸 Creating BrowserStateSummary WITHOUT screenshot'
				)

			browser_state = BrowserStateSummary(
				dom_state=content,
				url=page_url,
				title=title,
				tabs=tabs_info,
				current_tab_id=self.browser_session.agent_focus_target_id,
				screenshot=screenshot_b64,
				page_info=page_info,
				pixels_above=0,
				pixels_below=0,
				browser_errors=[],
				is_pdf_viewer=is_pdf_viewer,
				recent_events=self._get_recent_events_str() if event.include_recent_events else None,
				pending_network_requests=pending_requests,
				pagination_buttons=pagination_buttons_data,
				closed_popup_messages=self.browser_session._closed_popup_messages.copy(),
			)

			# Cache the state
			self.browser_session._cached_browser_state_summary = browser_state

			# Cache viewport size for coordinate conversion (if llm_screenshot_size is enabled)
			if page_info:
				self.browser_session._original_viewport_size = (page_info.viewport_width, page_info.viewport_height)

			self.logger.debug('🔍 DOMWatchdog.on_BrowserStateRequestEvent: ✅ COMPLETED - Returning browser state')
			return browser_state

		except Exception as e:
			self.logger.error(f'Failed to get browser state: {e}')

			# Return minimal recovery state
			return BrowserStateSummary(
				dom_state=SerializedDOMState(_root=None, selector_map={}),
				url=page_url if 'page_url' in locals() else '',
				title='Error',
				tabs=[],
				current_tab_id=self.browser_session.agent_focus_target_id if self.browser_session else None,
				screenshot=None,
				page_info=PageInfo(
					viewport_width=1280,
					viewport_height=720,
					page_width=1280,
					page_height=720,
					scroll_x=0,
					scroll_y=0,
					pixels_above=0,
					pixels_below=0,
					pixels_left=0,
					pixels_right=0,
				),
				pixels_above=0,
				pixels_below=0,
				browser_errors=[str(e)],
				is_pdf_viewer=False,
				recent_events=None,
				pending_network_requests=[],  # Error state has no pending requests
				pagination_buttons=[],  # Error state has no pagination
				closed_popup_messages=self.browser_session._closed_popup_messages.copy()
				if hasattr(self, 'browser_session') and self.browser_session is not None
				else [],
			)

	@time_execution_async('build_dom_tree_without_highlights')
	@observe_debug(ignore_input=True, ignore_output=True, name='build_dom_tree_without_highlights')
	async def _build_dom_tree_without_highlights(self, previous_state: SerializedDOMState | None = None) -> SerializedDOMState:
		return await build_dom_tree_without_highlights(self, previous_state)

	@time_execution_async('capture_clean_screenshot')
	@observe_debug(ignore_input=True, ignore_output=True, name='capture_clean_screenshot')
	async def _capture_clean_screenshot(self) -> str:
		return await capture_clean_screenshot(self)

	def _detect_pagination_buttons(self, selector_map: dict[int, EnhancedDOMTreeNode]) -> list['PaginationButton']:
		return detect_pagination_buttons(self, selector_map)

	async def _get_page_info(self) -> 'PageInfo':
		return await get_page_info(self)

	# ========== Public Helper Methods ==========

	async def get_element_by_index(self, index: int) -> EnhancedDOMTreeNode | None:
		"""Get DOM element by index from cached selector map.

		Builds DOM if not cached.

		Returns:
			EnhancedDOMTreeNode or None if index not found
		"""
		if not self.selector_map:
			# Build DOM if not cached
			await self._build_dom_tree_without_highlights()

		return self.selector_map.get(index) if self.selector_map else None

	def clear_cache(self) -> None:
		"""Clear cached DOM state to force rebuild on next access."""
		self.selector_map = None
		self.current_dom_state = None
		self.enhanced_dom_tree = None
		if self._dom_service:
			self._dom_service.clear_cache()
		# Keep the DOM service instance to reuse its CDP client connection

	def is_file_input(self, element: EnhancedDOMTreeNode) -> bool:
		"""Check if element is a file input."""
		return element.node_name.upper() == 'INPUT' and element.attributes.get('type', '').lower() == 'file'

	@staticmethod
	def is_element_visible_according_to_all_parents(node: EnhancedDOMTreeNode, html_frames: list[EnhancedDOMTreeNode]) -> bool:
		"""Check if the element is visible according to all its parent HTML frames.

		Delegates to the DomService static method.
		"""
		return DomService.is_element_visible_according_to_all_parents(node, html_frames)

	async def __aexit__(self, exc_type, exc_value, traceback):
		"""Clean up DOM service on exit."""
		if self._dom_service:
			await self._dom_service.__aexit__(exc_type, exc_value, traceback)
			self._dom_service = None

	def __del__(self):
		"""Clean up DOM service on deletion."""
		super().__del__()
		# DOM service will clean up its own CDP client
		self._dom_service = None
