"""Private click helpers for :mod:`agentyc.browser.element`."""

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
	from cdp_use.cdp.input.types import MouseButton

	from agentyc.browser.element import Element, ModifierType


async def element_click(
	element: 'Element',
	button: 'MouseButton' = 'left',
	click_count: int = 1,
	modifiers: list['ModifierType'] | None = None,
) -> None:
	"""Click an element using CDP geometry when possible, with JS fallback."""
	try:
		layout_metrics = await element._client.send.Page.getLayoutMetrics(session_id=element._session_id)
		viewport_width = layout_metrics['layoutViewport']['clientWidth']
		viewport_height = layout_metrics['layoutViewport']['clientHeight']

		quads = await _get_click_quads(element)
		if not quads:
			await _click_via_javascript(element)
			await asyncio.sleep(0.05)
			return

		best_quad = _get_best_quad(quads, viewport_width=viewport_width, viewport_height=viewport_height)
		center_x = sum(best_quad[i] for i in range(0, 8, 2)) / 4
		center_y = sum(best_quad[i] for i in range(1, 8, 2)) / 4

		center_x = max(0, min(viewport_width - 1, center_x))
		center_y = max(0, min(viewport_height - 1, center_y))

		try:
			await element._client.send.DOM.scrollIntoViewIfNeeded(
				params={'backendNodeId': element._backend_node_id}, session_id=element._session_id
			)
			await asyncio.sleep(0.05)
		except Exception:
			pass

		modifier_value = _get_modifier_value(modifiers)

		try:
			await element._client.send.Input.dispatchMouseEvent(
				params={
					'type': 'mouseMoved',
					'x': center_x,
					'y': center_y,
				},
				session_id=element._session_id,
			)
			await asyncio.sleep(0.05)

			try:
				await asyncio.wait_for(
					element._client.send.Input.dispatchMouseEvent(
						params={
							'type': 'mousePressed',
							'x': center_x,
							'y': center_y,
							'button': button,
							'clickCount': click_count,
							'modifiers': modifier_value,
						},
						session_id=element._session_id,
					),
					timeout=1.0,
				)
				await asyncio.sleep(0.08)
			except TimeoutError:
				pass

			try:
				await asyncio.wait_for(
					element._client.send.Input.dispatchMouseEvent(
						params={
							'type': 'mouseReleased',
							'x': center_x,
							'y': center_y,
							'button': button,
							'clickCount': click_count,
							'modifiers': modifier_value,
						},
						session_id=element._session_id,
					),
					timeout=3.0,
				)
			except TimeoutError:
				pass

		except Exception as click_error:
			try:
				await _click_via_javascript(element)
				await asyncio.sleep(0.1)
				return
			except Exception:
				raise Exception(f'Failed to click element: {click_error}')

	except Exception as error:
		raise RuntimeError(f'Failed to click element: {error}')


async def _get_click_quads(element: 'Element') -> list[list[float]]:
	quads = await _get_content_quads(element)
	if quads:
		return quads

	quads = await _get_box_model_quads(element)
	if quads:
		return quads

	return await _get_bounding_rect_quads(element)


async def _get_content_quads(element: 'Element') -> list[list[float]]:
	try:
		content_quads_result = await element._client.send.DOM.getContentQuads(
			params={'backendNodeId': element._backend_node_id}, session_id=element._session_id
		)
		if 'quads' in content_quads_result and content_quads_result['quads']:
			return content_quads_result['quads']
	except Exception:
		pass
	return []


async def _get_box_model_quads(element: 'Element') -> list[list[float]]:
	try:
		box_model = await element._client.send.DOM.getBoxModel(
			params={'backendNodeId': element._backend_node_id}, session_id=element._session_id
		)
		if 'model' not in box_model or 'content' not in box_model['model']:
			return []

		content_quad = box_model['model']['content']
		if len(content_quad) < 8:
			return []

		return [
			[
				content_quad[0],
				content_quad[1],
				content_quad[2],
				content_quad[3],
				content_quad[4],
				content_quad[5],
				content_quad[6],
				content_quad[7],
			]
		]
	except Exception:
		pass
	return []


async def _get_bounding_rect_quads(element: 'Element') -> list[list[float]]:
	try:
		result = await element._client.send.DOM.resolveNode(
			params={'backendNodeId': element._backend_node_id}, session_id=element._session_id
		)
		if 'object' not in result or 'objectId' not in result['object']:
			return []

		bounds_result = await element._client.send.Runtime.callFunctionOn(
			params={
				'functionDeclaration': """
					function() {
						const rect = this.getBoundingClientRect();
						return {
							x: rect.left,
							y: rect.top,
							width: rect.width,
							height: rect.height
						};
					}
				""",
				'objectId': result['object']['objectId'],
				'returnByValue': True,
			},
			session_id=element._session_id,
		)

		if 'result' not in bounds_result or 'value' not in bounds_result['result']:
			return []

		rect = bounds_result['result']['value']
		x = rect['x']
		y = rect['y']
		width = rect['width']
		height = rect['height']
		return [[x, y, x + width, y, x + width, y + height, x, y + height]]
	except Exception:
		pass
	return []


def _get_best_quad(quads: list[list[float]], viewport_width: float, viewport_height: float) -> list[float]:
	best_quad = None
	best_area = 0.0

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

		visible_width = visible_max_x - visible_min_x
		visible_height = visible_max_y - visible_min_y
		visible_area = visible_width * visible_height

		if visible_area > best_area:
			best_area = visible_area
			best_quad = quad

	if best_quad is not None:
		return best_quad
	return quads[0]


def _get_modifier_value(modifiers: list['ModifierType'] | None) -> int:
	modifier_value = 0
	if modifiers:
		modifier_map = {'Alt': 1, 'Control': 2, 'Meta': 4, 'Shift': 8}
		for modifier in modifiers:
			modifier_value |= modifier_map.get(modifier, 0)
	return modifier_value


async def _click_via_javascript(element: 'Element') -> None:
	result = await element._client.send.DOM.resolveNode(
		params={'backendNodeId': element._backend_node_id}, session_id=element._session_id
	)
	if 'object' not in result or 'objectId' not in result['object']:
		raise Exception('Failed to find DOM element based on backendNodeId, maybe page content changed?')

	await element._client.send.Runtime.callFunctionOn(
		params={
			'functionDeclaration': 'function() { this.click(); }',
			'objectId': result['object']['objectId'],
		},
		session_id=element._session_id,
	)
