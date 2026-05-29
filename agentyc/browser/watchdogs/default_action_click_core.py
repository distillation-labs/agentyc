"""Core click execution helpers for the default action watchdog."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from agentyc.browser.views import BrowserError, URLNotAllowedError

if TYPE_CHECKING:
	from agentyc.browser.watchdogs.default_action_click_engine import DefaultActionClickEngineMixin


_POST_SCROLL_INTO_VIEW_SETTLE_S = 0.01
_POST_MOUSE_MOVE_SETTLE_S = 0.02
_POST_MOUSE_DOWN_SETTLE_S = 0.015


async def check_element_occlusion(
	watchdog: DefaultActionClickEngineMixin, backend_node_id: int, x: float, y: float, cdp_session
) -> bool:
	try:
		session_id = cdp_session.session_id
		target_result = await cdp_session.cdp_client.send.DOM.resolveNode(
			params={'backendNodeId': backend_node_id}, session_id=session_id
		)
		if 'object' not in target_result:
			watchdog.logger.debug('Could not resolve target element, assuming occluded')
			return True

		object_id = target_result['object']['objectId']
		target_info_result = await cdp_session.cdp_client.send.Runtime.callFunctionOn(
			params={
				'objectId': object_id,
				'functionDeclaration': """
				function() {
					const getElementInfo = (el) => {
						return {
							tagName: el.tagName,
							id: el.id || '',
							className: el.className || '',
							textContent: (el.textContent || '').substring(0, 100)
						};
					};

					const elementAtPoint = document.elementFromPoint(arguments[0], arguments[1]);
					if (!elementAtPoint) {
						return { targetInfo: getElementInfo(this), isClickable: false };
					}

					let isClickable = this === elementAtPoint ||
						this.contains(elementAtPoint) ||
						elementAtPoint.contains(this);

					if (!isClickable) {
						const target = this;
						const atPoint = elementAtPoint;

						if (target.tagName === 'INPUT' && target.id) {
							const escapedId = CSS.escape(target.id);
							const assocLabel = document.querySelector('label[for="' + escapedId + '"]');
							if (assocLabel && (assocLabel === atPoint || assocLabel.contains(atPoint))) {
								isClickable = true;
							}
						}

						if (!isClickable && target.tagName === 'INPUT') {
							let ancestor = atPoint;
							for (let i = 0; i < 3 && ancestor; i++) {
								if (ancestor.tagName === 'LABEL' && ancestor.contains(target)) {
									isClickable = true;
									break;
								}
								ancestor = ancestor.parentElement;
							}
						}

						if (!isClickable && target.tagName === 'LABEL') {
							if (target.htmlFor && atPoint.tagName === 'INPUT' && atPoint.id === target.htmlFor) {
								isClickable = true;
							}
							if (!isClickable && atPoint.tagName === 'INPUT' && target.contains(atPoint)) {
								isClickable = true;
							}
						}
					}

					return {
						targetInfo: getElementInfo(this),
						elementAtPointInfo: getElementInfo(elementAtPoint),
						isClickable: isClickable
					};
				}
				""",
				'arguments': [{'value': x}, {'value': y}],
				'returnByValue': True,
			},
			session_id=session_id,
		)
		if 'result' not in target_info_result or 'value' not in target_info_result['result']:
			watchdog.logger.debug('Could not get target element info, assuming occluded')
			return True

		target_data = target_info_result['result']['value']
		if target_data.get('isClickable', False):
			watchdog.logger.debug('Element is clickable (target, contained, or semantically related)')
			return False

		target_info = target_data.get('targetInfo', {})
		element_at_point_info = target_data.get('elementAtPointInfo', {})
		watchdog.logger.debug(
			f'Element is occluded. Target: {target_info.get("tagName", "unknown")} '
			f'(id={target_info.get("id", "none")}), '
			f'ElementAtPoint: {element_at_point_info.get("tagName", "unknown")} '
			f'(id={element_at_point_info.get("id", "none")})'
		)
		return True
	except Exception as error:
		watchdog.logger.debug(f'Occlusion check failed: {error}, assuming not occluded')
		return False


async def move_mouse_before_click(
	watchdog: DefaultActionClickEngineMixin, cdp_session, session_id: str, x: float, y: float
) -> None:
	try:
		await asyncio.wait_for(
			cdp_session.cdp_client.send.Input.dispatchMouseEvent(
				params={
					'type': 'mouseMoved',
					'x': x,
					'y': y,
				},
				session_id=session_id,
			),
			timeout=0.1,
		)
		await asyncio.sleep(_POST_MOUSE_MOVE_SETTLE_S)
	except TimeoutError:
		watchdog.logger.debug('⏱️ Mouse move timed out before click, continuing with direct press...')
	except Exception as error:
		watchdog.logger.debug(f'⚠️ Mouse move before click failed (non-critical): {type(error).__name__}: {error}')


async def click_element_node_impl(watchdog: DefaultActionClickEngineMixin, element_node) -> dict | None:
	try:
		tag_name = element_node.tag_name.lower() if element_node.tag_name else ''
		element_type = element_node.attributes.get('type', '').lower() if element_node.attributes else ''
		if tag_name == 'select':
			msg = f'Cannot click on <select> elements. Use dropdown_options(index={element_node.backend_node_id}) action instead.'
			return {'validation_error': msg}
		if tag_name == 'input' and element_type == 'file':
			msg = (
				f'Cannot click on file input element (index={element_node.backend_node_id}). '
				'File uploads must be handled using upload_file_to_element action.'
			)
			return {'validation_error': msg}

		cdp_session = await watchdog.browser_session.cdp_client_for_node(element_node)
		session_id = cdp_session.session_id
		backend_node_id = element_node.backend_node_id

		is_toggle_element = tag_name == 'input' and element_type in ('checkbox', 'radio')
		pre_click_checked: bool | None = None
		checkbox_object_id: str | None = None
		if is_toggle_element and backend_node_id:
			try:
				resolve_res = await cdp_session.cdp_client.send.DOM.resolveNode(
					params={'backendNodeId': backend_node_id}, session_id=session_id
				)
				obj_info = resolve_res.get('object', {})
				checkbox_object_id = obj_info.get('objectId') if obj_info else None
				if not checkbox_object_id:
					raise Exception('Failed to resolve checkbox element objectId')
				state_res = await cdp_session.cdp_client.send.Runtime.callFunctionOn(
					params={
						'functionDeclaration': 'function() { return this.checked; }',
						'objectId': checkbox_object_id,
						'returnByValue': True,
					},
					session_id=session_id,
				)
				pre_click_checked = state_res.get('result', {}).get('value')
				watchdog.logger.debug(f'Checkbox pre-click state: checked={pre_click_checked}')
			except Exception as error:
				watchdog.logger.debug(f'Could not capture pre-click checkbox state: {error}')

		layout_metrics = await cdp_session.cdp_client.send.Page.getLayoutMetrics(session_id=session_id)
		viewport_width = layout_metrics['layoutViewport']['clientWidth']
		viewport_height = layout_metrics['layoutViewport']['clientHeight']

		try:
			await cdp_session.cdp_client.send.DOM.scrollIntoViewIfNeeded(
				params={'backendNodeId': backend_node_id}, session_id=session_id
			)
			await asyncio.sleep(_POST_SCROLL_INTO_VIEW_SETTLE_S)
			watchdog.logger.debug('Scrolled element into view before getting coordinates')
		except Exception as error:
			watchdog.logger.debug(f'Failed to scroll element into view: {error}')

		element_rect = await watchdog.browser_session.get_element_coordinates(backend_node_id, cdp_session)
		quads = []
		if element_rect:
			x, y, width, height = element_rect.x, element_rect.y, element_rect.width, element_rect.height
			quads = [[x, y, x + width, y, x + width, y + height, x, y + height]]
			watchdog.logger.debug(
				f'Got coordinates from unified method: {element_rect.x}, {element_rect.y}, {element_rect.width}x{element_rect.height}'
			)

		if not quads:
			watchdog.logger.warning('Could not get element geometry from any method, falling back to JavaScript click')
			try:
				result = await cdp_session.cdp_client.send.DOM.resolveNode(
					params={'backendNodeId': backend_node_id},
					session_id=session_id,
				)
				assert 'object' in result and 'objectId' in result['object'], (
					'Failed to find DOM element based on backendNodeId, maybe page content changed?'
				)
				object_id = result['object']['objectId']
				await cdp_session.cdp_client.send.Runtime.callFunctionOn(
					params={
						'functionDeclaration': 'function() { this.click(); }',
						'objectId': object_id,
					},
					session_id=session_id,
				)
				await asyncio.sleep(0.05)
				return None
			except Exception as js_error:
				watchdog.logger.warning(f'CDP JavaScript click also failed: {js_error}')
				if 'No node with given id found' in str(js_error):
					raise Exception('Element with given id not found')
				raise Exception(f'Failed to click element: {js_error}')

		best_quad = None
		best_area = 0
		for quad in quads:
			if len(quad) < 8:
				continue
			xs = [quad[i] for i in range(0, 8, 2)]
			ys = [quad[i] for i in range(1, 8, 2)]
			min_x, max_x = min(xs), max(xs)
			min_y, max_y = min(ys), max(ys)
			if max_x < 0 or max_y < 0 or min_x > viewport_width or min_y > viewport_height:
				continue
			visible_min_x = max(0, min_x)
			visible_max_x = min(viewport_width, max_x)
			visible_min_y = max(0, min_y)
			visible_max_y = min(viewport_height, max_y)
			visible_area = (visible_max_x - visible_min_x) * (visible_max_y - visible_min_y)
			if visible_area > best_area:
				best_area = visible_area
				best_quad = quad

		if not best_quad:
			best_quad = quads[0]
			watchdog.logger.warning('No visible quad found, using first quad')

		center_x = sum(best_quad[i] for i in range(0, 8, 2)) / 4
		center_y = sum(best_quad[i] for i in range(1, 8, 2)) / 4
		center_x = max(0, min(viewport_width - 1, center_x))
		center_y = max(0, min(viewport_height - 1, center_y))

		is_occluded = await check_element_occlusion(watchdog, backend_node_id, center_x, center_y, cdp_session)
		if is_occluded:
			watchdog.logger.debug('🚫 Element is occluded, falling back to JavaScript click')
			try:
				result = await cdp_session.cdp_client.send.DOM.resolveNode(
					params={'backendNodeId': backend_node_id},
					session_id=session_id,
				)
				assert 'object' in result and 'objectId' in result['object'], 'Failed to find DOM element based on backendNodeId'
				object_id = result['object']['objectId']
				await cdp_session.cdp_client.send.Runtime.callFunctionOn(
					params={
						'functionDeclaration': 'function() { this.click(); }',
						'objectId': object_id,
					},
					session_id=session_id,
				)
				await asyncio.sleep(0.05)
				return None
			except Exception as js_error:
				watchdog.logger.error(f'JavaScript click fallback failed: {js_error}')
				raise Exception(f'Failed to click occluded element: {js_error}')

		try:
			watchdog.logger.debug(f'👆 Dragging mouse over element before clicking x: {center_x}px y: {center_y}px ...')
			await move_mouse_before_click(watchdog, cdp_session, session_id, center_x, center_y)

			watchdog.logger.debug(f'👆🏾 Clicking x: {center_x}px y: {center_y}px ...')
			try:
				await asyncio.wait_for(
					cdp_session.cdp_client.send.Input.dispatchMouseEvent(
						params={
							'type': 'mousePressed',
							'x': center_x,
							'y': center_y,
							'button': 'left',
							'clickCount': 1,
						},
						session_id=session_id,
					),
					timeout=3.0,
				)
				await asyncio.sleep(_POST_MOUSE_DOWN_SETTLE_S)
			except TimeoutError:
				watchdog.logger.debug('⏱️ Mouse down timed out (likely due to dialog), continuing...')

			try:
				await asyncio.wait_for(
					cdp_session.cdp_client.send.Input.dispatchMouseEvent(
						params={
							'type': 'mouseReleased',
							'x': center_x,
							'y': center_y,
							'button': 'left',
							'clickCount': 1,
						},
						session_id=session_id,
					),
					timeout=0.5,
				)
			except TimeoutError:
				watchdog.logger.debug('⏱️ Mouse up timed out (possibly due to lag or dialog popup), continuing...')

			watchdog.logger.debug('🖱️ Clicked successfully using x,y coordinates')
			if is_toggle_element and pre_click_checked is not None and checkbox_object_id:
				try:
					await asyncio.sleep(0.05)
					state_res = await cdp_session.cdp_client.send.Runtime.callFunctionOn(
						params={
							'functionDeclaration': 'function() { return this.checked; }',
							'objectId': checkbox_object_id,
							'returnByValue': True,
						},
						session_id=session_id,
					)
					post_click_checked = state_res.get('result', {}).get('value')
					if post_click_checked == pre_click_checked:
						watchdog.logger.debug(
							f'Checkbox state unchanged after CDP click (checked={pre_click_checked}), using JS fallback'
						)
						await cdp_session.cdp_client.send.Runtime.callFunctionOn(
							params={'functionDeclaration': 'function() { this.click(); }', 'objectId': checkbox_object_id},
							session_id=session_id,
						)
						await asyncio.sleep(0.05)
						final_res = await cdp_session.cdp_client.send.Runtime.callFunctionOn(
							params={
								'functionDeclaration': 'function() { return this.checked; }',
								'objectId': checkbox_object_id,
								'returnByValue': True,
							},
							session_id=session_id,
						)
						post_click_checked = final_res.get('result', {}).get('value')
					watchdog.logger.debug(f'Checkbox post-click state: checked={post_click_checked}')
					return {'click_x': center_x, 'click_y': center_y, 'checked': post_click_checked}
				except Exception as error:
					watchdog.logger.debug(f'Checkbox state verification failed (non-critical): {error}')

			return {'click_x': center_x, 'click_y': center_y}
		except Exception as error:
			watchdog.logger.warning(f'CDP click failed: {type(error).__name__}: {error}')
			try:
				result = await cdp_session.cdp_client.send.DOM.resolveNode(
					params={'backendNodeId': backend_node_id},
					session_id=session_id,
				)
				assert 'object' in result and 'objectId' in result['object'], (
					'Failed to find DOM element based on backendNodeId, maybe page content changed?'
				)
				object_id = result['object']['objectId']
				await cdp_session.cdp_client.send.Runtime.callFunctionOn(
					params={
						'functionDeclaration': 'function() { this.click(); }',
						'objectId': object_id,
					},
					session_id=session_id,
				)
				await asyncio.sleep(0.1)
				return None
			except Exception as js_error:
				watchdog.logger.warning(f'CDP JavaScript click also failed: {js_error}')
				raise Exception(f'Failed to click element: {error}')
		finally:
			try:
				cdp_session = await asyncio.wait_for(watchdog.browser_session.get_or_create_cdp_session(focus=True), timeout=0.4)
				await asyncio.wait_for(
					cdp_session.cdp_client.send.Runtime.runIfWaitingForDebugger(session_id=cdp_session.session_id),
					timeout=0.3,
				)
			except TimeoutError:
				watchdog.logger.debug('⏱️ Refocus after click timed out (page may be blocked by dialog). Continuing...')
			except Exception as refocus_error:
				watchdog.logger.debug(f'⚠️ Refocus error (non-critical): {type(refocus_error).__name__}: {refocus_error}')
	except URLNotAllowedError as error:
		raise error
	except BrowserError as error:
		raise error
	except Exception as error:
		element_info = f'<{element_node.tag_name or "unknown"}'
		if element_node.backend_node_id:
			element_info += f' index={element_node.backend_node_id}'
		element_info += '>'
		error_detail = f'Failed to click element {element_info}. The element may not be interactable or visible.'
		if element_node.backend_node_id:
			error_detail += (
				f' If the page changed after navigation/interaction, the index [{element_node.backend_node_id}] '
				'may be stale. Get fresh browser state before retrying.'
			)
		raise BrowserError(
			message=f'Failed to click element: {str(error)}',
			long_term_memory=error_detail,
		)


async def click_on_coordinate(
	watchdog: DefaultActionClickEngineMixin, coordinate_x: int, coordinate_y: int, force: bool = False
) -> dict | None:
	try:
		cdp_session = await watchdog.browser_session.get_or_create_cdp_session()
		session_id = cdp_session.session_id
		watchdog.logger.debug(f'👆 Moving mouse to ({coordinate_x}, {coordinate_y})...')

		await move_mouse_before_click(watchdog, cdp_session, session_id, coordinate_x, coordinate_y)

		watchdog.logger.debug(f'👆🏾 Clicking at ({coordinate_x}, {coordinate_y})...')
		try:
			await asyncio.wait_for(
				cdp_session.cdp_client.send.Input.dispatchMouseEvent(
					params={
						'type': 'mousePressed',
						'x': coordinate_x,
						'y': coordinate_y,
						'button': 'left',
						'clickCount': 1,
					},
					session_id=session_id,
				),
				timeout=3.0,
			)
			await asyncio.sleep(_POST_MOUSE_MOVE_SETTLE_S)
		except TimeoutError:
			watchdog.logger.debug('⏱️ Mouse down timed out (likely due to dialog), continuing...')

		try:
			await asyncio.wait_for(
				cdp_session.cdp_client.send.Input.dispatchMouseEvent(
					params={
						'type': 'mouseReleased',
						'x': coordinate_x,
						'y': coordinate_y,
						'button': 'left',
						'clickCount': 1,
					},
					session_id=session_id,
				),
				timeout=0.5,
			)
		except TimeoutError:
			watchdog.logger.debug('⏱️ Mouse up timed out (possibly due to lag or dialog popup), continuing...')

		watchdog.logger.debug(f'🖱️ Clicked successfully at ({coordinate_x}, {coordinate_y})')
		return {'click_x': coordinate_x, 'click_y': coordinate_y}
	except Exception as error:
		watchdog.logger.error(f'Failed to click at coordinates ({coordinate_x}, {coordinate_y}): {type(error).__name__}: {error}')
		raise BrowserError(
			message=f'Failed to click at coordinates: {error}',
			long_term_memory=(
				f'Failed to click at coordinates ({coordinate_x}, {coordinate_y}). '
				'The coordinates may be outside viewport or the page may have changed.'
			),
		)
