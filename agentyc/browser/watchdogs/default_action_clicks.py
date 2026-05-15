"""Click-related event handlers for the default action watchdog."""

from __future__ import annotations

from agentyc.browser.events import ClickCoordinateEvent, ClickElementEvent
from agentyc.browser.views import BrowserError
from agentyc.browser.watchdogs.default_action_click_engine import DefaultActionClickEngineMixin
from agentyc.observability import observe_debug


class DefaultActionClickMixin(DefaultActionClickEngineMixin):
	"""Event handlers that delegate to the click execution helpers."""

	@observe_debug(ignore_input=True, ignore_output=True, name='click_element_event')
	async def on_ClickElementEvent(self, event: ClickElementEvent) -> dict | None:
		try:
			if not self.browser_session.agent_focus_target_id:
				error_msg = 'Cannot execute click: browser session is corrupted (target_id=None). Session may have crashed.'
				self.logger.error(error_msg)
				raise BrowserError(error_msg)

			element_node = event.node
			index_for_logging = element_node.backend_node_id or 'unknown'

			if self.browser_session.is_file_input(element_node):
				msg = (
					f'Index {index_for_logging} - has an element which opens file upload dialog. '
					'To upload files please use a specific function to upload files'
				)
				self.logger.info(msg)
				return {'validation_error': msg}

			if self._is_print_related_element(element_node):
				self.logger.info(
					f'🖨️ Detected print button (index {index_for_logging}), generating PDF directly instead of opening dialog...'
				)
				click_metadata = await self._handle_print_button_click(element_node)
				if click_metadata and click_metadata.get('pdf_generated'):
					msg = f'Generated PDF: {click_metadata.get("path")}'
					self.logger.info(f'💾 {msg}')
					return click_metadata
				self.logger.warning('⚠️ PDF generation failed, falling back to regular click')

			click_metadata = await self._execute_click_with_download_detection(self._click_element_node_impl(element_node))
			if isinstance(click_metadata, dict) and 'validation_error' in click_metadata:
				self.logger.info(click_metadata['validation_error'])
				return click_metadata

			if 'download' not in (click_metadata or {}):
				msg = f'Clicked button {element_node.node_name}: {element_node.get_all_children_text(max_depth=2)}'
				self.logger.debug(f'🖱️ {msg}')
			self.logger.debug(f'Element xpath: {element_node.xpath}')
			return click_metadata
		except Exception:
			raise

	async def on_ClickCoordinateEvent(self, event: ClickCoordinateEvent) -> dict | None:
		try:
			if not self.browser_session.agent_focus_target_id:
				error_msg = 'Cannot execute click: browser session is corrupted (target_id=None). Session may have crashed.'
				self.logger.error(error_msg)
				raise BrowserError(error_msg)

			if event.force:
				self.logger.debug(f'Force clicking at coordinates ({event.coordinate_x}, {event.coordinate_y})')
				return await self._execute_click_with_download_detection(
					self._click_on_coordinate(event.coordinate_x, event.coordinate_y, force=True)
				)

			element_node = await self.browser_session.get_dom_element_at_coordinates(event.coordinate_x, event.coordinate_y)
			if element_node is None:
				self.logger.debug(
					f'No element found at coordinates ({event.coordinate_x}, {event.coordinate_y}), proceeding with click anyway'
				)
				return await self._execute_click_with_download_detection(
					self._click_on_coordinate(event.coordinate_x, event.coordinate_y, force=False)
				)

			if self.browser_session.is_file_input(element_node):
				msg = (
					f'Cannot click at ({event.coordinate_x}, {event.coordinate_y}) - element is a file input. '
					'To upload files please use upload_file action'
				)
				self.logger.info(msg)
				return {'validation_error': msg}

			tag_name = element_node.tag_name.lower() if element_node.tag_name else ''
			if tag_name == 'select':
				msg = (
					f'Cannot click at ({event.coordinate_x}, {event.coordinate_y}) - element is a <select>. '
					'Use dropdown_options action instead.'
				)
				self.logger.info(msg)
				return {'validation_error': msg}

			if self._is_print_related_element(element_node):
				self.logger.info(
					f'🖨️ Detected print button at ({event.coordinate_x}, {event.coordinate_y}), generating PDF directly instead of opening dialog...'
				)
				click_metadata = await self._handle_print_button_click(element_node)
				if click_metadata and click_metadata.get('pdf_generated'):
					msg = f'Generated PDF: {click_metadata.get("path")}'
					self.logger.info(f'💾 {msg}')
					return click_metadata
				self.logger.warning('⚠️ PDF generation failed, falling back to regular click')

			return await self._execute_click_with_download_detection(
				self._click_on_coordinate(event.coordinate_x, event.coordinate_y, force=False)
			)
		except Exception:
			raise
