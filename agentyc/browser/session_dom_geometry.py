"""Geometry and screenshot helpers for BrowserSession DOM access."""

from __future__ import annotations

from typing import TYPE_CHECKING

from agentyc.browser.session_models import CDPSession
from agentyc.dom.views import DOMRect, EnhancedDOMTreeNode, NodeType

if TYPE_CHECKING:
	from agentyc.browser.session import BrowserSession


async def get_dom_element_at_coordinates(session: BrowserSession, x: int, y: int) -> EnhancedDOMTreeNode | None:
	"""Get DOM element at coordinates as EnhancedDOMTreeNode."""
	page = await session.get_current_page()
	if page is None:
		raise RuntimeError('No active page found')

	session_id = await page._ensure_session()

	try:
		result = await session.cdp_client.send.DOM.getNodeForLocation(
			params={
				'x': x,
				'y': y,
				'includeUserAgentShadowDOM': False,
				'ignorePointerEventsNone': False,
			},
			session_id=session_id,
		)

		backend_node_id = result.get('backendNodeId')
		if backend_node_id is None:
			session.logger.debug(f'No element found at coordinates ({x}, {y})')
			return None

		if session._cached_selector_map:
			for node in session._cached_selector_map.values():
				if node.backend_node_id == backend_node_id:
					session.logger.debug(f'Found element at ({x}, {y}) in cached selector_map')
					return node

		try:
			describe_result = await session.cdp_client.send.DOM.describeNode(
				params={'backendNodeId': backend_node_id},
				session_id=session_id,
			)
			node_info = describe_result.get('node', {})
			node_name = node_info.get('nodeName', '')

			attrs_list = node_info.get('attributes', [])
			attributes = {attrs_list[i]: attrs_list[i + 1] for i in range(0, len(attrs_list), 2)}

			return EnhancedDOMTreeNode(
				node_id=result.get('nodeId', 0),
				backend_node_id=backend_node_id,
				node_type=NodeType(node_info.get('nodeType', NodeType.ELEMENT_NODE.value)),
				node_name=node_name,
				node_value=node_info.get('nodeValue', '') or '',
				attributes=attributes,
				is_scrollable=None,
				frame_id=result.get('frameId'),
				session_id=session_id,
				target_id=session.agent_focus_target_id or '',
				content_document=None,
				shadow_root_type=None,
				shadow_roots=None,
				parent_node=None,
				children_nodes=None,
				ax_node=None,
				snapshot_node=None,
				is_visible=None,
				absolute_position=None,
			)
		except Exception as e:
			session.logger.debug(f'DOM.describeNode failed for backend_node_id={backend_node_id}: {e}')
			return EnhancedDOMTreeNode(
				node_id=result.get('nodeId', 0),
				backend_node_id=backend_node_id,
				node_type=NodeType.ELEMENT_NODE,
				node_name='',
				node_value='',
				attributes={},
				is_scrollable=None,
				frame_id=result.get('frameId'),
				session_id=session_id,
				target_id=session.agent_focus_target_id or '',
				content_document=None,
				shadow_root_type=None,
				shadow_roots=None,
				parent_node=None,
				children_nodes=None,
				ax_node=None,
				snapshot_node=None,
				is_visible=None,
				absolute_position=None,
			)

	except Exception as e:
		session.logger.warning(f'Failed to get DOM element at coordinates ({x}, {y}): {e}')
		return None


async def get_element_coordinates(session: BrowserSession, backend_node_id: int, cdp_session: CDPSession) -> DOMRect | None:
	"""Get element coordinates for a backend node ID using multiple methods."""
	session_id = cdp_session.session_id
	quads = []

	try:
		content_quads_result = await cdp_session.cdp_client.send.DOM.getContentQuads(
			params={'backendNodeId': backend_node_id}, session_id=session_id
		)
		if 'quads' in content_quads_result and content_quads_result['quads']:
			quads = content_quads_result['quads']
			session.logger.debug(f'Got {len(quads)} quads from DOM.getContentQuads')
		else:
			session.logger.debug(f'No quads found from DOM.getContentQuads {content_quads_result}')
	except Exception as e:
		session.logger.debug(f'DOM.getContentQuads failed: {e}')

	if not quads:
		try:
			box_model = await cdp_session.cdp_client.send.DOM.getBoxModel(
				params={'backendNodeId': backend_node_id}, session_id=session_id
			)
			if 'model' in box_model and 'content' in box_model['model']:
				content_quad = box_model['model']['content']
				if len(content_quad) >= 8:
					quads = [
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
					session.logger.debug('Got quad from DOM.getBoxModel')
		except Exception as e:
			session.logger.debug(f'DOM.getBoxModel failed: {e}')

	if not quads:
		try:
			result = await cdp_session.cdp_client.send.DOM.resolveNode(
				params={'backendNodeId': backend_node_id},
				session_id=session_id,
			)
			if 'object' in result and 'objectId' in result['object']:
				object_id = result['object']['objectId']
				js_result = await cdp_session.cdp_client.send.Runtime.callFunctionOn(
					params={
						'objectId': object_id,
						'functionDeclaration': """
						function() {
							const rect = this.getBoundingClientRect();
							return {
								x: rect.x,
								y: rect.y,
								width: rect.width,
								height: rect.height
							};
						}
						""",
						'returnByValue': True,
					},
					session_id=session_id,
				)
				if 'result' in js_result and 'value' in js_result['result']:
					rect_data = js_result['result']['value']
					if rect_data['width'] > 0 and rect_data['height'] > 0:
						return DOMRect(x=rect_data['x'], y=rect_data['y'], width=rect_data['width'], height=rect_data['height'])
		except Exception as e:
			session.logger.debug(f'JavaScript getBoundingClientRect failed: {e}')

	if quads:
		quad = quads[0]
		if len(quad) >= 8:
			x_coords = [quad[i] for i in range(0, 8, 2)]
			y_coords = [quad[i] for i in range(1, 8, 2)]

			min_x = min(x_coords)
			min_y = min(y_coords)
			max_x = max(x_coords)
			max_y = max(y_coords)

			width = max_x - min_x
			height = max_y - min_y

			if width > 0 and height > 0:
				return DOMRect(x=min_x, y=min_y, width=width, height=height)

	return None


async def screenshot_element(
	session: BrowserSession,
	selector: str,
	path: str | None = None,
	format: str = 'png',
	quality: int | None = None,
) -> bytes:
	"""Take a screenshot of a specific element."""
	bounds = await _get_element_bounds(session, selector)
	if not bounds:
		raise ValueError(f"Element '{selector}' not found or has no bounds")

	return await session.take_screenshot(
		path=path,
		format=format,
		quality=quality,
		clip=bounds,
	)


async def _get_element_bounds(session: BrowserSession, selector: str) -> dict | None:
	"""Get element bounding box using CDP."""
	cdp_session = await session.get_or_create_cdp_session()

	doc = await cdp_session.cdp_client.send.DOM.getDocument(params={'depth': 1}, session_id=cdp_session.session_id)
	node_result = await cdp_session.cdp_client.send.DOM.querySelector(
		params={'nodeId': doc['root']['nodeId'], 'selector': selector}, session_id=cdp_session.session_id
	)

	node_id = node_result.get('nodeId')
	if not node_id:
		return None

	box_result = await cdp_session.cdp_client.send.DOM.getBoxModel(params={'nodeId': node_id}, session_id=cdp_session.session_id)

	box_model = box_result.get('model')
	if not box_model:
		return None

	content = box_model['content']
	return {
		'x': min(content[0], content[2], content[4], content[6]),
		'y': min(content[1], content[3], content[5], content[7]),
		'width': max(content[0], content[2], content[4], content[6]) - min(content[0], content[2], content[4], content[6]),
		'height': max(content[1], content[3], content[5], content[7]) - min(content[1], content[3], content[5], content[7]),
	}
