"""Scroll, navigation, keyboard, upload, and text-search helpers."""

from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING, Any

from agentyc.browser.events import (
	GoBackEvent,
	GoForwardEvent,
	RefreshEvent,
	ScrollEvent,
	ScrollToTextEvent,
	SendKeysEvent,
	UploadFileEvent,
	WaitEvent,
)
from agentyc.browser.views import BrowserError
from agentyc.browser.watchdogs.default_action_navigation_keys import dispatch_key_event, send_keys_event


class DefaultActionNavigationMixin:
	"""Navigation-adjacent helpers for default actions."""

	if TYPE_CHECKING:
		logger: Any
		browser_session: Any

		def _get_char_modifiers_and_vk(self, char: str) -> tuple[int, int, str]: ...
		def _get_key_code_for_char(self, char: str) -> str: ...

	async def _wait_for_history_navigation_settle(
		self,
		*,
		cdp_session,
		pre_navigation_url: str,
		expected_url: str,
		detect_timeout: float = 0.4,
		readiness_timeout: float = 3.0,
	) -> str:
		loop = asyncio.get_event_loop()
		detect_deadline = loop.time() + detect_timeout
		settled_url = expected_url
		while loop.time() < detect_deadline:
			current_url = await self.browser_session.get_current_page_url()
			if current_url and (current_url == expected_url or current_url != pre_navigation_url):
				settled_url = current_url
				break
			await asyncio.sleep(0.05)
		else:
			current_url = await self.browser_session.get_current_page_url()
			if current_url:
				settled_url = current_url

		readiness_deadline = loop.time() + readiness_timeout
		while loop.time() < readiness_deadline:
			if await self.browser_session._navigation_ready_via_dom(
				cdp_session=cdp_session,
				url=settled_url,
				wait_until='load',
			):
				return settled_url
			await asyncio.sleep(0.05)
		raise BrowserError(f'History navigation timed out waiting for load on {settled_url}')

	async def on_ScrollEvent(self, event: ScrollEvent) -> None:
		if not self.browser_session.agent_focus_target_id:
			raise BrowserError('No active target for scrolling')
		try:

			def invalidate_dom_cache() -> None:
				if self.browser_session._dom_watchdog:
					self.browser_session._dom_watchdog.clear_cache()

			pixels = event.amount if event.direction == 'down' else -event.amount
			if event.node is not None:
				element_node = event.node
				index_for_logging = element_node.backend_node_id or 'unknown'
				is_iframe = element_node.tag_name and element_node.tag_name.upper() == 'IFRAME'
				success = await self._scroll_element_container(element_node, pixels)
				if success:
					self.logger.debug(
						f'📜 Scrolled element {index_for_logging} container {event.direction} by {event.amount} pixels'
					)
					if is_iframe:
						self.logger.debug('🔄 Forcing DOM refresh after iframe scroll')
					await asyncio.sleep(0.2)
				invalidate_dom_cache()
				return None

			gesture_scrolled = await self._scroll_with_cdp_gesture(pixels)
			if not gesture_scrolled:
				raise BrowserError('Failed to scroll page via CDP gesture')
			invalidate_dom_cache()
			self.logger.debug(f'📜 Scrolled {event.direction} by {event.amount} pixels')
			return None
		except Exception:
			raise

	async def _scroll_with_cdp_gesture(self, pixels: int) -> bool:
		try:
			cdp_session = await self.browser_session.get_or_create_cdp_session()
			cdp_client = cdp_session.cdp_client
			session_id = cdp_session.session_id
			if self.browser_session._original_viewport_size:
				viewport_width, viewport_height = self.browser_session._original_viewport_size
			else:
				layout_metrics = await cdp_client.send.Page.getLayoutMetrics(session_id=session_id)
				viewport_width = layout_metrics['layoutViewport']['clientWidth']
				viewport_height = layout_metrics['layoutViewport']['clientHeight']

			center_x = viewport_width / 2
			center_y = viewport_height / 2
			y_distance = -pixels
			await cdp_client.send.Input.synthesizeScrollGesture(
				params={
					'x': center_x,
					'y': center_y,
					'xDistance': 0,
					'yDistance': y_distance,
					'speed': 50000,
				},
				session_id=session_id,
			)
			self.logger.debug(f'📄 Scrolled via CDP gesture: {pixels}px')
			return True
		except Exception as error:
			self.logger.debug(f'CDP gesture scroll failed ({type(error).__name__}: {error}), falling back to JS')
			return False

	async def _scroll_element_container(self, element_node, pixels: int) -> bool:
		try:
			cdp_session = await self.browser_session.cdp_client_for_node(element_node)
			if element_node.tag_name and element_node.tag_name.upper() == 'IFRAME':
				backend_node_id = element_node.backend_node_id
				result = await cdp_session.cdp_client.send.DOM.resolveNode(
					params={'backendNodeId': backend_node_id},
					session_id=cdp_session.session_id,
				)
				if 'object' in result and 'objectId' in result['object']:
					object_id = result['object']['objectId']
					scroll_result = await cdp_session.cdp_client.send.Runtime.callFunctionOn(
						params={
							'functionDeclaration': f"""
                                function() {{
                                    try {{
                                        const doc = this.contentDocument || this.contentWindow.document;
                                        if (doc) {{
                                            const scrollElement = doc.documentElement || doc.body;
                                            if (scrollElement) {{
                                                const oldScrollTop = scrollElement.scrollTop;
                                                scrollElement.scrollTop += {pixels};
                                                const newScrollTop = scrollElement.scrollTop;
                                                return {{
                                                    success: true,
                                                    oldScrollTop: oldScrollTop,
                                                    newScrollTop: newScrollTop,
                                                    scrolled: newScrollTop - oldScrollTop
                                                }};
                                            }}
                                        }}
                                        return {{success: false, error: 'Could not access iframe content'}};
                                    }} catch (e) {{
                                        return {{success: false, error: e.toString()}};
                                    }}
                                }}
                            """,
							'objectId': object_id,
							'returnByValue': True,
						},
						session_id=cdp_session.session_id,
					)
					if scroll_result and 'result' in scroll_result and 'value' in scroll_result['result']:
						result_value = scroll_result['result']['value']
						if result_value.get('success'):
							self.logger.debug(f'Successfully scrolled iframe content by {result_value.get("scrolled", 0)}px')
							return True
						self.logger.debug(f'Failed to scroll iframe: {result_value.get("error", "Unknown error")}')

			backend_node_id = element_node.backend_node_id
			box_model = await cdp_session.cdp_client.send.DOM.getBoxModel(
				params={'backendNodeId': backend_node_id}, session_id=cdp_session.session_id
			)
			content_quad = box_model['model']['content']
			center_x = (content_quad[0] + content_quad[2] + content_quad[4] + content_quad[6]) / 4
			center_y = (content_quad[1] + content_quad[3] + content_quad[5] + content_quad[7]) / 4
			await cdp_session.cdp_client.send.Input.dispatchMouseEvent(
				params={
					'type': 'mouseWheel',
					'x': center_x,
					'y': center_y,
					'deltaX': 0,
					'deltaY': pixels,
				},
				session_id=cdp_session.session_id,
			)
			return True
		except Exception as error:
			self.logger.debug(f'Failed to scroll element container via CDP: {error}')
			return False

	async def _get_session_id_for_element(self, element_node) -> str | None:
		if element_node.frame_id:
			try:
				all_targets = self.browser_session.session_manager.get_all_targets()
				for target_id, target in all_targets.items():
					if target.target_type == 'iframe' and element_node.frame_id in str(target_id):
						temp_session = await self.browser_session.get_or_create_cdp_session(target_id, focus=False)
						return temp_session.session_id
				self.logger.debug(f'Frame {element_node.frame_id} not found in targets, using main session')
			except Exception as error:
				self.logger.debug(f'Error getting frame session: {error}, using main session')
		cdp_session = await self.browser_session.get_or_create_cdp_session()
		return cdp_session.session_id

	async def on_GoBackEvent(self, event: GoBackEvent) -> None:
		cdp_session = await self.browser_session.get_or_create_cdp_session()
		try:
			history = await cdp_session.cdp_client.send.Page.getNavigationHistory(session_id=cdp_session.session_id)
			current_index = history['currentIndex']
			entries = history['entries']
			if current_index <= 0:
				self.logger.warning('⚠️ Cannot go back - no previous entry in history')
				return
			pre_navigation_url = entries[current_index]['url']
			expected_url = entries[current_index - 1]['url']
			previous_entry_id = entries[current_index - 1]['id']
			await cdp_session.cdp_client.send.Page.navigateToHistoryEntry(
				params={'entryId': previous_entry_id}, session_id=cdp_session.session_id
			)
			settled_url = await self._wait_for_history_navigation_settle(
				cdp_session=cdp_session,
				pre_navigation_url=pre_navigation_url,
				expected_url=expected_url,
			)
			self.logger.info(f'🔙 Navigated back to {settled_url}')
		except Exception:
			raise

	async def on_GoForwardEvent(self, event: GoForwardEvent) -> None:
		cdp_session = await self.browser_session.get_or_create_cdp_session()
		try:
			history = await cdp_session.cdp_client.send.Page.getNavigationHistory(session_id=cdp_session.session_id)
			current_index = history['currentIndex']
			entries = history['entries']
			if current_index >= len(entries) - 1:
				self.logger.warning('⚠️ Cannot go forward - no next entry in history')
				return
			pre_navigation_url = entries[current_index]['url']
			expected_url = entries[current_index + 1]['url']
			next_entry_id = entries[current_index + 1]['id']
			await cdp_session.cdp_client.send.Page.navigateToHistoryEntry(
				params={'entryId': next_entry_id}, session_id=cdp_session.session_id
			)
			settled_url = await self._wait_for_history_navigation_settle(
				cdp_session=cdp_session,
				pre_navigation_url=pre_navigation_url,
				expected_url=expected_url,
			)
			self.logger.info(f'🔜 Navigated forward to {settled_url}')
		except Exception:
			raise

	async def on_RefreshEvent(self, event: RefreshEvent) -> None:
		cdp_session = await self.browser_session.get_or_create_cdp_session()
		try:
			target_id = cdp_session.target_id or self.browser_session.agent_focus_target_id
			if not target_id:
				raise BrowserError('No active target for refresh')
			target = self.browser_session.session_manager.get_target(target_id)
			current_url = target.url if target else await self.browser_session.get_current_page_url()
			await cdp_session.cdp_client.send.Page.reload(session_id=cdp_session.session_id)
			from agentyc.browser import session_navigation

			await session_navigation._navigate_and_wait(
				self.browser_session,
				current_url,
				target_id,
				timeout=3.0,
				wait_until='load',
				nav_timeout=8.0,
			)
			self.logger.info('🔄 Target refreshed')
		except Exception:
			raise

	async def on_WaitEvent(self, event: WaitEvent) -> None:
		try:
			actual_seconds = min(max(event.seconds, 0), event.max_seconds)
			if actual_seconds != event.seconds:
				self.logger.info(f'🕒 Waiting for {actual_seconds} seconds (capped from {event.seconds}s)')
			else:
				self.logger.info(f'🕒 Waiting for {actual_seconds} seconds')
			await asyncio.sleep(actual_seconds)
		except Exception:
			raise

	async def _dispatch_key_event(self, cdp_session, event_type: str, key: str, modifiers: int = 0) -> None:
		await dispatch_key_event(self, cdp_session, event_type, key, modifiers=modifiers)

	async def on_SendKeysEvent(self, event: SendKeysEvent) -> None:
		await send_keys_event(self, event)

	async def on_UploadFileEvent(self, event: UploadFileEvent) -> None:
		try:
			element_node = event.node
			index_for_logging = element_node.backend_node_id or 'unknown'
			if not self.browser_session.is_file_input(element_node):
				msg = f'Upload failed - element {index_for_logging} is not a file input.'
				raise BrowserError(message=msg, long_term_memory=msg)

			cdp_client = self.browser_session.cdp_client
			session_id = await self._get_session_id_for_element(element_node)
			if os.path.exists(event.file_path):
				file_size = os.path.getsize(event.file_path)
				if file_size == 0:
					msg = f'Upload failed - file {event.file_path} is empty (0 bytes).'
					raise BrowserError(message=msg, long_term_memory=msg)
				self.logger.debug(f'📎 File {event.file_path} validated ({file_size} bytes)')

			backend_node_id = element_node.backend_node_id
			await cdp_client.send.DOM.setFileInputFiles(
				params={'files': [event.file_path], 'backendNodeId': backend_node_id},
				session_id=session_id,
			)
			self.logger.info(f'📎 Uploaded file {event.file_path} to element {index_for_logging}')
		except Exception:
			raise

	async def on_ScrollToTextEvent(self, event: ScrollToTextEvent) -> None:
		cdp_session = await self.browser_session.get_or_create_cdp_session()
		cdp_client = cdp_session.cdp_client
		session_id = cdp_session.session_id
		await cdp_client.send.DOM.enable(session_id=session_id)
		doc = await cdp_client.send.DOM.getDocument(params={'depth': -1}, session_id=session_id)
		root_node_id = doc['root']['nodeId']
		search_queries = [
			f'//*[contains(text(), "{event.text}")]',
			f'//*[contains(., "{event.text}")]',
			f'//*[@*[contains(., "{event.text}")]]',
		]

		found = False
		for query in search_queries:
			try:
				search_result = await cdp_client.send.DOM.performSearch(params={'query': query}, session_id=session_id)
				search_id = search_result['searchId']
				result_count = search_result['resultCount']
				if result_count > 0:
					node_ids = await cdp_client.send.DOM.getSearchResults(
						params={'searchId': search_id, 'fromIndex': 0, 'toIndex': 1},
						session_id=session_id,
					)
					if node_ids['nodeIds']:
						node_id = node_ids['nodeIds'][0]
						await cdp_client.send.DOM.scrollIntoViewIfNeeded(params={'nodeId': node_id}, session_id=session_id)
						found = True
						self.logger.debug(f'📜 Scrolled to text: "{event.text}"')
						break
				await cdp_client.send.DOM.discardSearchResults(params={'searchId': search_id}, session_id=session_id)
			except Exception as error:
				self.logger.debug(f'Search query failed: {query}, error: {error}')
				continue

		if not found:
			js_result = await cdp_client.send.Runtime.evaluate(
				params={
					'expression': f'''
                            (() => {{
                                const walker = document.createTreeWalker(
                                    document.body,
                                    NodeFilter.SHOW_TEXT,
                                    null,
                                    false
                                );
                                let node;
                                while (node = walker.nextNode()) {{
                                    if (node.textContent.includes("{event.text}")) {{
                                        node.parentElement.scrollIntoView({{behavior: 'smooth', block: 'center'}});
                                        return true;
                                    }}
                                }}
                                return false;
                            }})()
                        '''
				},
				session_id=session_id,
			)
			if js_result.get('result', {}).get('value'):
				self.logger.debug(f'📜 Scrolled to text: "{event.text}" (via JS)')
				return None
			self.logger.warning(f'⚠️ Text not found: "{event.text}"')
			raise BrowserError(f'Text not found: "{event.text}"', details={'text': event.text})

		if found:
			return None
		raise BrowserError(f'Text not found: "{event.text}"', details={'text': event.text})
