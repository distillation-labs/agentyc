"""Private DOM/introspection helpers for :mod:`agentyc.browser.element`."""

import json
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
	from cdp_use.cdp.dom.commands import GetAttributesParameters, GetBoxModelParameters
	from cdp_use.cdp.page.commands import CaptureScreenshotParameters
	from cdp_use.cdp.page.types import Viewport
	from cdp_use.cdp.runtime.commands import CallFunctionOnParameters

	from agentyc.browser.element import BoundingBox, Element, ElementInfo


async def get_attribute(element: 'Element', name: str) -> str | None:
	"""Get an attribute value."""
	node_id = await element._get_node_id()
	params: 'GetAttributesParameters' = {'nodeId': node_id}
	result = await element._client.send.DOM.getAttributes(params, session_id=element._session_id)
	attributes = result['attributes']
	for i in range(0, len(attributes), 2):
		if attributes[i] == name:
			return attributes[i + 1]
	return None


async def get_bounding_box(element: 'Element') -> 'BoundingBox | None':
	"""Get the bounding box of the element."""
	try:
		node_id = await element._get_node_id()
		params: 'GetBoxModelParameters' = {'nodeId': node_id}
		result = await element._client.send.DOM.getBoxModel(params, session_id=element._session_id)
		if 'model' not in result:
			return None

		content = result['model']['content']
		if len(content) < 8:
			return None

		x_coords = [content[i] for i in range(0, 8, 2)]
		y_coords = [content[i] for i in range(1, 8, 2)]
		x = min(x_coords)
		y = min(y_coords)
		return {
			'x': x,
			'y': y,
			'width': max(x_coords) - x,
			'height': max(y_coords) - y,
		}
	except Exception:
		return None


async def screenshot_element(element: 'Element', format: str = 'png', quality: int | None = None) -> str:
	"""Take a screenshot of this element and return base64-encoded image data."""
	box = await get_bounding_box(element)
	if not box:
		raise RuntimeError('Element is not visible or has no bounding box')

	viewport: 'Viewport' = {
		'x': box['x'],
		'y': box['y'],
		'width': box['width'],
		'height': box['height'],
		'scale': 1.0,
	}
	params: 'CaptureScreenshotParameters' = {'format': format, 'clip': viewport}
	if quality is not None and format.lower() == 'jpeg':
		params['quality'] = quality

	result = await element._client.send.Page.captureScreenshot(params, session_id=element._session_id)
	return result['data']


async def element_evaluate(element: 'Element', page_function: str, *args) -> str:
	"""Execute JavaScript in the context of this element."""
	object_id = await element._get_remote_object_id()
	if not object_id:
		raise RuntimeError('Element has no remote object ID (element may be detached from DOM)')

	page_function = page_function.strip()
	if not ('=>' in page_function and (page_function.startswith('(') or page_function.startswith('async'))):
		raise ValueError(
			f'JavaScript code must start with (...args) => or async (...args) => format. Got: {page_function[:50]}...'
		)

	is_async = page_function.startswith('async')
	async_prefix = 'async ' if is_async else ''
	func_to_parse = page_function[5:].strip() if is_async else page_function
	arrow_match = re.match(r'\s*\(([^)]*)\)\s*=>\s*(.+)', func_to_parse, re.DOTALL)
	if not arrow_match:
		raise ValueError(f'Could not parse arrow function: {page_function[:50]}...')

	params_str = arrow_match.group(1).strip()
	body = arrow_match.group(2).strip()
	function_declaration = (
		f'{async_prefix}function({params_str}) {{ return {body}; }}'
		if not body.startswith('{')
		else f'{async_prefix}function({params_str}) {body}'
	)

	call_arguments = []
	if args:
		from cdp_use.cdp.runtime.types import CallArgument

		for arg in args:
			call_arguments.append(CallArgument(value=arg))

	params: 'CallFunctionOnParameters' = {
		'functionDeclaration': function_declaration,
		'objectId': object_id,
		'returnByValue': True,
		'awaitPromise': True,
	}
	if call_arguments:
		params['arguments'] = call_arguments

	result = await element._client.send.Runtime.callFunctionOn(params, session_id=element._session_id)
	if 'exceptionDetails' in result:
		raise RuntimeError(f'JavaScript evaluation failed: {result["exceptionDetails"]}')

	value = result.get('result', {}).get('value')
	if value is None:
		return ''
	if isinstance(value, str):
		return value
	try:
		return json.dumps(value) if isinstance(value, (dict, list)) else str(value)
	except (TypeError, ValueError):
		return str(value)


async def get_basic_info(element: 'Element') -> 'ElementInfo':
	"""Get basic information about the element including coordinates and properties."""
	try:
		node_id = await element._get_node_id()
		describe_result = await element._client.send.DOM.describeNode({'nodeId': node_id}, session_id=element._session_id)
		node_info = describe_result['node']
		bounding_box = await get_bounding_box(element)
		attributes_list = node_info.get('attributes', [])
		attributes_dict: dict[str, str] = {}
		for i in range(0, len(attributes_list), 2):
			if i + 1 < len(attributes_list):
				attributes_dict[attributes_list[i]] = attributes_list[i + 1]

		return {
			'backendNodeId': element._backend_node_id,
			'nodeId': node_id,
			'nodeName': node_info.get('nodeName', ''),
			'nodeType': node_info.get('nodeType', 0),
			'nodeValue': node_info.get('nodeValue'),
			'attributes': attributes_dict,
			'boundingBox': bounding_box,
			'error': None,
		}
	except Exception as e:
		return {
			'backendNodeId': element._backend_node_id,
			'nodeId': None,
			'nodeName': '',
			'nodeType': 0,
			'nodeValue': None,
			'attributes': {},
			'boundingBox': None,
			'error': str(e),
		}
