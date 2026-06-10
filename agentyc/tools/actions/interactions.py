import asyncio
import logging
import os
from typing import Any

from agentyc.actions import ActionResult
from agentyc.browser import BrowserSession
from agentyc.browser.events import (
	ClickCoordinateEvent,
	ClickElementEvent,
	GetDropdownOptionsEvent,
	SwitchTabEvent,
	TypeTextEvent,
	UploadFileEvent,
)
from agentyc.browser.views import BrowserError
from agentyc.dom.service import EnhancedDOMTreeNode
from agentyc.filesystem.file_system import FileSystem
from agentyc.tools.runtime import handle_browser_error
from agentyc.tools.utils import get_click_description
from agentyc.tools.views import (
	ClickElementAction,
	ClickElementActionIndexOnly,
	GetDropdownOptionsAction,
	InputTextAction,
	SelectDropdownOptionAction,
	UploadFileAction,
)
from agentyc.utils import create_task_with_error_handling

logger = logging.getLogger(__name__)


def _convert_llm_coordinates_to_viewport(llm_x: int, llm_y: int, browser_session: BrowserSession) -> tuple[int, int]:
	if browser_session.llm_screenshot_size and browser_session._original_viewport_size:
		original_width, original_height = browser_session._original_viewport_size
		llm_width, llm_height = browser_session.llm_screenshot_size
		actual_x = int((llm_x / llm_width) * original_width)
		actual_y = int((llm_y / llm_height) * original_height)
		logger.info(
			f'🔄 Converting coordinates: LLM ({llm_x}, {llm_y}) @ {llm_width}x{llm_height} '
			f'→ Viewport ({actual_x}, {actual_y}) @ {original_width}x{original_height}'
		)
		return actual_x, actual_y
	return llm_x, llm_y


def _snapshot_page_target_ids(browser_session: BrowserSession) -> set[str]:
	session_manager = getattr(browser_session, 'session_manager', None)
	if session_manager is None:
		return set()
	try:
		return {target.target_id for target in session_manager.get_all_page_targets()}
	except Exception:
		return set()


def _might_open_new_tab(node: EnhancedDOMTreeNode) -> bool:
	attrs = node.attributes or {}
	tag_name = (node.tag_name or '').lower()
	onclick = str(attrs.get('onclick', '')).lower()
	role = str(attrs.get('role', '')).lower()
	return bool(
		attrs.get('target') == '_blank' or attrs.get('href') or tag_name == 'a' or role == 'link' or 'window.open' in onclick
	)


async def _detect_new_tab_opened(
	browser_session: BrowserSession,
	page_target_ids_before: set[str],
	*,
	wait_for_target: bool = False,
) -> str:
	try:
		session_manager = getattr(browser_session, 'session_manager', None)
		if session_manager is None:
			return ''
		attempts = 4 if wait_for_target else 1
		for attempt in range(attempts):
			if attempt > 0:
				await asyncio.sleep(0.05)
			page_targets_after = session_manager.get_all_page_targets()
			new_tabs = [target for target in page_targets_after if target.target_id not in page_target_ids_before]
			if not new_tabs:
				continue
			new_tab = new_tabs[-1]
			new_tab_id = new_tab.target_id[-4:]
			try:
				switch_event = browser_session.event_bus.dispatch(SwitchTabEvent(target_id=new_tab.target_id))
				await switch_event
				await switch_event.event_result(raise_if_any=False, raise_if_none=False)
				return f'. Automatically switched to new tab (tab_id: {new_tab_id}).'
			except Exception:
				return (
					f'. Note: This opened a new tab (tab_id: {new_tab_id}) '
					'- switch to it if you need to interact with the new page.'
				)
	except Exception:
		pass
	return ''


def _is_autocomplete_field(node: EnhancedDOMTreeNode) -> bool:
	attrs = node.attributes or {}
	if attrs.get('role') == 'combobox':
		return True
	aria_autocomplete = attrs.get('aria-autocomplete', '')
	if aria_autocomplete and aria_autocomplete != 'none':
		return True
	if attrs.get('list'):
		return True
	has_popup = attrs.get('aria-haspopup', '')
	if has_popup and has_popup != 'false' and (attrs.get('aria-controls') or attrs.get('aria-owns')):
		return True
	return False


def register_click_action(tools: Any) -> None:
	if 'click' in tools.registry.registry.actions:
		del tools.registry.registry.actions['click']

	if tools._coordinate_clicking_enabled:

		@tools.registry.action(
			'Click element by index or coordinates. Use coordinates only if the index is not available. Either provide coordinates or index.',
			param_model=ClickElementAction,
		)
		async def click(params: ClickElementAction, browser_session: BrowserSession):
			if params.index is None and (params.coordinate_x is None or params.coordinate_y is None):
				return ActionResult(error='Must provide either index or both coordinate_x and coordinate_y')
			if params.index is not None:
				return await tools._click_by_index(params, browser_session)
			return await tools._click_by_coordinate(params, browser_session)
	else:

		@tools.registry.action('Click element by index.', param_model=ClickElementActionIndexOnly)
		async def click(params: ClickElementActionIndexOnly, browser_session: BrowserSession):
			return await tools._click_by_index(params, browser_session)


def register_interaction_actions(tools: Any) -> None:
	@tools.registry.action('', param_model=GetDropdownOptionsAction)
	async def dropdown_options(params: GetDropdownOptionsAction, browser_session: BrowserSession):
		node = await browser_session.get_element_by_index(params.index)
		if node is None:
			msg = f'Element index {params.index} not available - page may have changed. Try refreshing browser state.'
			logger.warning(f'⚠️ {msg}')
			return ActionResult(extracted_content=msg)

		event = browser_session.event_bus.dispatch(GetDropdownOptionsEvent(node=node))
		dropdown_data = await event.event_result(timeout=3.0, raise_if_none=True, raise_if_any=True)
		if not dropdown_data:
			raise ValueError('Failed to get dropdown options - no data returned')
		return ActionResult(
			extracted_content=dropdown_data['short_term_memory'],
			long_term_memory=dropdown_data['long_term_memory'],
			include_extracted_content_only_once=True,
		)

	async def click_by_coordinate(params: ClickElementAction, browser_session: BrowserSession) -> ActionResult:
		if params.coordinate_x is None or params.coordinate_y is None:
			return ActionResult(error='Both coordinate_x and coordinate_y must be provided')

		try:
			actual_x, actual_y = _convert_llm_coordinates_to_viewport(
				params.coordinate_x,
				params.coordinate_y,
				browser_session,
			)
			page_target_ids_before = _snapshot_page_target_ids(browser_session)
			asyncio.create_task(browser_session.highlight_coordinate_click(actual_x, actual_y))
			event = browser_session.event_bus.dispatch(
				ClickCoordinateEvent(coordinate_x=actual_x, coordinate_y=actual_y, force=True)
			)
			await event
			click_metadata = await event.event_result(raise_if_any=True, raise_if_none=False)
			if isinstance(click_metadata, dict) and 'validation_error' in click_metadata:
				return ActionResult(error=click_metadata['validation_error'])

			memory = f'Clicked on coordinate {params.coordinate_x}, {params.coordinate_y}'
			memory += await _detect_new_tab_opened(browser_session, page_target_ids_before)
			logger.info(f'🖱️ {memory}')
			return ActionResult(
				extracted_content=memory,
				metadata={'click_x': actual_x, 'click_y': actual_y},
			)
		except BrowserError as error:
			return handle_browser_error(error)
		except Exception:
			return ActionResult(error=f'Failed to click at coordinates ({params.coordinate_x}, {params.coordinate_y}).')

	async def click_by_index(
		params: ClickElementAction | ClickElementActionIndexOnly,
		browser_session: BrowserSession,
	) -> ActionResult:
		assert params.index is not None
		try:
			assert params.index != 0, (
				'Cannot click on element with index 0. If there are no interactive elements use wait(), refresh(), etc. to troubleshoot'
			)

			node = await browser_session.get_element_by_index(params.index)
			if node is None:
				msg = f'Element index {params.index} not available - page may have changed. Try refreshing browser state.'
				logger.warning(f'⚠️ {msg}')
				return ActionResult(extracted_content=msg)

			element_desc = get_click_description(node)
			page_target_ids_before = _snapshot_page_target_ids(browser_session) if _might_open_new_tab(node) else None
			create_task_with_error_handling(
				browser_session.highlight_interaction_element(node),
				name='highlight_click_element',
				suppress_exceptions=True,
			)

			event = browser_session.event_bus.dispatch(ClickElementEvent(node=node))
			await event
			click_metadata = await event.event_result(raise_if_any=True, raise_if_none=False)
			if isinstance(click_metadata, dict) and 'validation_error' in click_metadata:
				error_msg = click_metadata['validation_error']
				if 'Cannot click on <select> elements.' in error_msg:
					try:
						return await dropdown_options(
							params=GetDropdownOptionsAction(index=params.index),
							browser_session=browser_session,
						)
					except Exception as dropdown_error:
						logger.debug(
							'Failed to get dropdown options as shortcut during click on dropdown: '
							f'{type(dropdown_error).__name__}: {dropdown_error}'
						)
					return ActionResult(error=error_msg)

			memory = f'Clicked {element_desc}'
			if page_target_ids_before is not None:
				memory += await _detect_new_tab_opened(
					browser_session,
					page_target_ids_before,
					wait_for_target=True,
				)
			logger.info(f'🖱️ {memory}')
			return ActionResult(
				extracted_content=memory,
				metadata=click_metadata if isinstance(click_metadata, dict) else None,
			)
		except BrowserError as error:
			return handle_browser_error(error)
		except Exception as error:
			return ActionResult(error=f'Failed to click element {params.index}: {error}')

	tools._click_by_index = click_by_index
	tools._click_by_coordinate = click_by_coordinate
	register_click_action(tools)

	@tools.registry.action(
		'Input text into element by index. Clears existing text by default; pass text="" to clear only, or clear=False to append.',
		param_model=InputTextAction,
	)
	async def input(
		params: InputTextAction,
		browser_session: BrowserSession,
	):
		node = await browser_session.get_element_by_index(params.index)
		if node is None:
			msg = f'Element index {params.index} not available - page may have changed. Try refreshing browser state.'
			logger.warning(f'⚠️ {msg}')
			return ActionResult(extracted_content=msg)

		create_task_with_error_handling(
			browser_session.highlight_interaction_element(node),
			name='highlight_type_element',
			suppress_exceptions=True,
		)

		try:
			sensitive_key_name = None
			if has_sensitive_data and sensitive_data:
				sensitive_key_name = detect_sensitive_key_name(params.text, sensitive_data)

			event = browser_session.event_bus.dispatch(
				TypeTextEvent(
					node=node,
					text=params.text,
					clear=params.clear,
					is_sensitive=has_sensitive_data,
					sensitive_key_name=sensitive_key_name,
				)
			)
			await event
			input_metadata = await event.event_result(raise_if_any=True, raise_if_none=False)

			if has_sensitive_data:
				if sensitive_key_name:
					msg = f'Typed {sensitive_key_name}'
					log_msg = f'Typed <{sensitive_key_name}>'
				else:
					msg = 'Typed sensitive data'
					log_msg = 'Typed <sensitive>'
			else:
				msg = f"Typed '{params.text}'"
				log_msg = f"Typed '{params.text}'"

			logger.debug(log_msg)

			actual_value = None
			if isinstance(input_metadata, dict):
				actual_value = input_metadata.pop('actual_value', None)

			if not has_sensitive_data and actual_value is not None and actual_value != params.text:
				msg += (
					f"\n⚠️ Note: the field's actual value '{actual_value}' differs from typed text '{params.text}'. "
					'The page may have reformatted or autocompleted your input.'
				)

			if _is_autocomplete_field(node):
				msg += (
					'\n💡 This is an autocomplete field. Wait for suggestions to appear, then click the '
					'correct suggestion instead of pressing Enter.'
				)
				attrs = node.attributes or {}
				if attrs.get('role') == 'combobox' or (attrs.get('aria-autocomplete', '') not in ('', 'none')):
					await asyncio.sleep(0.4)

			return ActionResult(
				extracted_content=msg,
				long_term_memory=msg,
				metadata=input_metadata if isinstance(input_metadata, dict) else None,
			)
		except BrowserError as error:
			return handle_browser_error(error)
		except Exception as error:
			logger.error(f'Failed to dispatch TypeTextEvent: {type(error).__name__}: {error}')
			return ActionResult(error=f'Failed to type text into element {params.index}: {error}')

	@tools.registry.action('', param_model=UploadFileAction)
	async def upload_file(
		params: UploadFileAction,
		browser_session: BrowserSession,
		available_file_paths: list[str],
		file_system: FileSystem,
	):
		if params.path not in available_file_paths:
			downloaded_files = browser_session.downloaded_files
			if params.path not in downloaded_files:
				if file_system and file_system.get_dir():
					file_obj = file_system.get_file(params.path)
					if file_obj:
						params = UploadFileAction(index=params.index, path=str(file_system.get_dir() / params.path))
					elif browser_session.is_local:
						msg = (
							f'File path {params.path} is not available. '
							f'Pass it through available_file_paths when invoking the tool runtime. '
							f'Example: available_file_paths=["{params.path}"]'
						)
						logger.error(f'❌ {msg}')
						return ActionResult(error=msg)
				elif browser_session.is_local:
					msg = (
						f'File path {params.path} is not available. '
						f'Pass it through available_file_paths when invoking the tool runtime. '
						f'Example: available_file_paths=["{params.path}"]'
					)
					raise BrowserError(message=msg, long_term_memory=msg)

		if browser_session.is_local:
			if not os.path.exists(params.path):
				return ActionResult(error=f'File {params.path} does not exist')
			file_size = os.path.getsize(params.path)
			if file_size == 0:
				return ActionResult(error=f'File {params.path} is empty (0 bytes). The file may not have been saved correctly.')

		selector_map = await browser_session.get_selector_map()
		if params.index not in selector_map:
			return ActionResult(error=f'Element with index {params.index} does not exist.')

		node = selector_map[params.index]
		file_input_node = browser_session.find_file_input_near_element(node)
		if file_input_node:
			create_task_with_error_handling(
				browser_session.highlight_interaction_element(file_input_node),
				name='highlight_file_input',
				suppress_exceptions=True,
			)

		if file_input_node is None:
			logger.info(
				f'No file upload element found near index {params.index}, searching for closest file input to scroll position'
			)
			cdp_session = await browser_session.get_or_create_cdp_session()
			try:
				scroll_info = await cdp_session.cdp_client.send.Runtime.evaluate(
					params={'expression': 'window.scrollY || window.pageYOffset || 0'},
					session_id=cdp_session.session_id,
				)
				current_scroll_y = scroll_info.get('result', {}).get('value', 0)
			except Exception:
				current_scroll_y = 0

			closest_file_input = None
			min_distance = float('inf')
			for element in selector_map.values():
				if browser_session.is_file_input(element) and element.absolute_position:
					distance = abs(element.absolute_position.y - current_scroll_y)
					if distance < min_distance:
						min_distance = distance
						closest_file_input = element

			if closest_file_input:
				file_input_node = closest_file_input
				logger.info(f'Found file input closest to scroll position (distance: {min_distance}px)')
				create_task_with_error_handling(
					browser_session.highlight_interaction_element(file_input_node),
					name='highlight_file_input_fallback',
					suppress_exceptions=True,
				)
			else:
				msg = 'No file upload element found on the page'
				logger.error(msg)
				raise BrowserError(msg)

		try:
			event = browser_session.event_bus.dispatch(UploadFileEvent(node=file_input_node, file_path=params.path))
			await event
			await event.event_result(raise_if_any=True, raise_if_none=False)
			msg = f'Successfully uploaded file to index {params.index}'
			logger.info(f'📁 {msg}')
			return ActionResult(
				extracted_content=msg,
				long_term_memory=f'Uploaded file {params.path} to element {params.index}',
			)
		except Exception as error:
			logger.error(f'Failed to upload file: {error}')
			raise BrowserError(f'Failed to upload file: {error}')

	@tools.registry.action('Set the option of a <select> element.', param_model=SelectDropdownOptionAction)
	async def select_dropdown(params: SelectDropdownOptionAction, browser_session: BrowserSession):
		node = await browser_session.get_element_by_index(params.index)
		if node is None:
			msg = f'Element index {params.index} not available - page may have changed. Try refreshing browser state.'
			logger.warning(f'⚠️ {msg}')
			return ActionResult(extracted_content=msg)

		from agentyc.browser.events import SelectDropdownOptionEvent

		event = browser_session.event_bus.dispatch(SelectDropdownOptionEvent(node=node, text=params.text))
		selection_data = await event.event_result()
		if not selection_data:
			raise ValueError('Failed to select dropdown option - no data returned')

		if selection_data.get('success') == 'true':
			msg = selection_data.get('message', f'Selected option: {params.text}')
			return ActionResult(
				extracted_content=msg,
				include_in_memory=True,
				long_term_memory=f"Selected dropdown option '{params.text}' at index {params.index}",
			)

		if 'short_term_memory' in selection_data and 'long_term_memory' in selection_data:
			return ActionResult(
				extracted_content=selection_data['short_term_memory'],
				long_term_memory=selection_data['long_term_memory'],
				include_extracted_content_only_once=True,
			)
		return ActionResult(error=selection_data.get('error', f'Failed to select option: {params.text}'))
