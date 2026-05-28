"""Element class for browser DOM element operations."""

from typing import TYPE_CHECKING, Literal, Union

from cdp_use.client import logger
from typing_extensions import TypedDict

from agentyc.browser._element_click import element_click
from agentyc.browser._element_dom import (
	element_evaluate,
	get_attribute,
	get_basic_info,
	get_bounding_box,
	screenshot_element,
)
from agentyc.browser._element_input import (
	clear_text_field,
	fill_element,
	focus_element_simple,
	get_char_modifiers_and_vk,
	get_key_code_for_char,
)

if TYPE_CHECKING:
	from cdp_use.cdp.dom.commands import (
		DescribeNodeParameters,
		FocusParameters,
		PushNodesByBackendIdsToFrontendParameters,
		RequestChildNodesParameters,
		ResolveNodeParameters,
	)
	from cdp_use.cdp.input.commands import (
		DispatchMouseEventParameters,
	)
	from cdp_use.cdp.input.types import MouseButton

	from agentyc.browser.session import BrowserSession

# Type definitions for element operations
ModifierType = Literal['Alt', 'Control', 'Meta', 'Shift']


class Position(TypedDict):
	"""2D position coordinates."""

	x: float
	y: float


class BoundingBox(TypedDict):
	"""Element bounding box with position and dimensions."""

	x: float
	y: float
	width: float
	height: float


class ElementInfo(TypedDict):
	"""Basic information about a DOM element."""

	backendNodeId: int
	nodeId: int | None
	nodeName: str
	nodeType: int
	nodeValue: str | None
	attributes: dict[str, str]
	boundingBox: BoundingBox | None
	error: str | None


class Element:
	"""Element operations using BackendNodeId."""

	def __init__(
		self,
		browser_session: 'BrowserSession',
		backend_node_id: int,
		session_id: str | None = None,
	):
		self._browser_session = browser_session
		self._client = browser_session.cdp_client
		self._backend_node_id = backend_node_id
		self._session_id = session_id

	async def _get_node_id(self) -> int:
		"""Get DOM node ID from backend node ID."""
		params: 'PushNodesByBackendIdsToFrontendParameters' = {'backendNodeIds': [self._backend_node_id]}
		result = await self._client.send.DOM.pushNodesByBackendIdsToFrontend(params, session_id=self._session_id)
		return result['nodeIds'][0]

	async def _get_remote_object_id(self) -> str | None:
		"""Get remote object ID for this element."""
		node_id = await self._get_node_id()
		params: 'ResolveNodeParameters' = {'nodeId': node_id}
		result = await self._client.send.DOM.resolveNode(params, session_id=self._session_id)
		object_id = result['object'].get('objectId', None)

		if not object_id:
			return None
		return object_id

	async def click(
		self,
		button: 'MouseButton' = 'left',
		click_count: int = 1,
		modifiers: list[ModifierType] | None = None,
	) -> None:
		"""Click the element using the advanced watchdog implementation."""

		await element_click(self, button=button, click_count=click_count, modifiers=modifiers)

	async def fill(self, value: str, clear: bool = True) -> None:
		"""Fill the input element using proper CDP methods with improved focus handling."""
		await fill_element(self, value, clear=clear)

	async def hover(self) -> None:
		"""Hover over the element."""
		box = await self.get_bounding_box()
		if not box:
			raise RuntimeError('Element is not visible or has no bounding box')

		x = box['x'] + box['width'] / 2
		y = box['y'] + box['height'] / 2

		params: 'DispatchMouseEventParameters' = {'type': 'mouseMoved', 'x': x, 'y': y}
		await self._client.send.Input.dispatchMouseEvent(params, session_id=self._session_id)

	async def focus(self) -> None:
		"""Focus the element."""
		node_id = await self._get_node_id()
		params: 'FocusParameters' = {'nodeId': node_id}
		await self._client.send.DOM.focus(params, session_id=self._session_id)

	async def check(self) -> None:
		"""Check or uncheck a checkbox/radio button."""
		await self.click()

	async def select_option(self, values: str | list[str]) -> None:
		"""Select option(s) in a select element."""
		if isinstance(values, str):
			values = [values]

		# Focus the element first
		try:
			await self.focus()
		except Exception:
			logger.warning('Failed to focus element')

		# For select elements, we need to find option elements and click them
		# This is a simplified approach - in practice, you might need to handle
		# different select types (single vs multi-select) differently
		node_id = await self._get_node_id()

		# Request child nodes to get the options
		params: 'RequestChildNodesParameters' = {'nodeId': node_id, 'depth': 1}
		await self._client.send.DOM.requestChildNodes(params, session_id=self._session_id)

		# Get the updated node description with children
		describe_params: 'DescribeNodeParameters' = {'nodeId': node_id, 'depth': 1}
		describe_result = await self._client.send.DOM.describeNode(describe_params, session_id=self._session_id)

		select_node = describe_result['node']

		# Find and select matching options
		for child in select_node.get('children', []):
			if child.get('nodeName', '').lower() == 'option':
				# Get option attributes
				attrs = child.get('attributes', [])
				option_attrs = {}
				for i in range(0, len(attrs), 2):
					if i + 1 < len(attrs):
						option_attrs[attrs[i]] = attrs[i + 1]

				option_value = option_attrs.get('value', '')
				option_text = child.get('nodeValue', '')

				# Check if this option should be selected
				should_select = option_value in values or option_text in values

				if should_select:
					# Click the option to select it
					option_node_id = child.get('nodeId')
					if option_node_id:
						# Get backend node ID for the option
						option_describe_params: 'DescribeNodeParameters' = {'nodeId': option_node_id}
						option_backend_result = await self._client.send.DOM.describeNode(
							option_describe_params, session_id=self._session_id
						)
						option_backend_id = option_backend_result['node']['backendNodeId']

						# Create an Element for the option and click it
						option_element = Element(self._browser_session, option_backend_id, self._session_id)
						await option_element.click()

	async def drag_to(
		self,
		target: Union['Element', Position],
		source_position: Position | None = None,
		target_position: Position | None = None,
	) -> None:
		"""Drag this element to another element or position."""
		# Get source coordinates
		if source_position:
			source_x = source_position['x']
			source_y = source_position['y']
		else:
			source_box = await self.get_bounding_box()
			if not source_box:
				raise RuntimeError('Source element is not visible')
			source_x = source_box['x'] + source_box['width'] / 2
			source_y = source_box['y'] + source_box['height'] / 2

		# Get target coordinates
		if isinstance(target, dict) and 'x' in target and 'y' in target:
			target_x = target['x']
			target_y = target['y']
		else:
			if target_position:
				target_box = await target.get_bounding_box()
				if not target_box:
					raise RuntimeError('Target element is not visible')
				target_x = target_box['x'] + target_position['x']
				target_y = target_box['y'] + target_position['y']
			else:
				target_box = await target.get_bounding_box()
				if not target_box:
					raise RuntimeError('Target element is not visible')
				target_x = target_box['x'] + target_box['width'] / 2
				target_y = target_box['y'] + target_box['height'] / 2

		# Perform drag operation
		await self._client.send.Input.dispatchMouseEvent(
			{'type': 'mousePressed', 'x': source_x, 'y': source_y, 'button': 'left'},
			session_id=self._session_id,
		)

		await self._client.send.Input.dispatchMouseEvent(
			{'type': 'mouseMoved', 'x': target_x, 'y': target_y},
			session_id=self._session_id,
		)

		await self._client.send.Input.dispatchMouseEvent(
			{'type': 'mouseReleased', 'x': target_x, 'y': target_y, 'button': 'left'},
			session_id=self._session_id,
		)

	# Element properties and queries
	async def get_attribute(self, name: str) -> str | None:
		"""Get an attribute value."""
		return await get_attribute(self, name)

	async def get_bounding_box(self) -> BoundingBox | None:
		"""Get the bounding box of the element."""
		return await get_bounding_box(self)

	async def screenshot(self, format: str = 'png', quality: int | None = None) -> str:
		"""Take a screenshot of this element and return base64 encoded image.

		Args:
			format: Image format ('jpeg', 'png', 'webp')
			quality: Quality 0-100 for JPEG format

		Returns:
			Base64-encoded image data
		"""
		return await screenshot_element(self, format=format, quality=quality)

	async def evaluate(self, page_function: str, *args) -> str:
		"""Execute JavaScript code in the context of this element.

		The JavaScript code executes with 'this' bound to the element, allowing direct
		access to element properties and methods.

		Args:
			page_function: JavaScript code that MUST start with (...args) => format
			*args: Arguments to pass to the function

		Returns:
			String representation of the JavaScript execution result.
			Objects and arrays are JSON-stringified.

		Example:
			# Get element's text content
			text = await element.evaluate("() => this.textContent")

			# Set style with argument
			await element.evaluate("(color) => this.style.color = color", "red")

			# Get computed style
			color = await element.evaluate("() => getComputedStyle(this).color")

			# Async operations
			result = await element.evaluate("async () => { await new Promise(r => setTimeout(r, 100)); return this.id; }")
		"""
		return await element_evaluate(self, page_function, *args)

	# Helpers for modifiers etc
	def _get_char_modifiers_and_vk(self, char: str) -> tuple[int, int, str]:
		"""Get modifiers, virtual key code, and base key for a character.

		Returns:
			(modifiers, windowsVirtualKeyCode, base_key)
		"""
		return get_char_modifiers_and_vk(char)

	def _get_key_code_for_char(self, char: str) -> str:
		"""Get the proper key code for a character (like Playwright does)."""
		return get_key_code_for_char(char)

	async def _clear_text_field(self, object_id: str, cdp_client, session_id: str) -> bool:
		"""Clear text field using multiple strategies, starting with the most reliable."""
		return await clear_text_field(self, object_id, cdp_client, session_id)

	async def _focus_element_simple(
		self, backend_node_id: int, object_id: str, cdp_client, session_id: str, input_coordinates=None
	) -> bool:
		"""Focus element using multiple strategies with robust fallbacks."""
		return await focus_element_simple(self, backend_node_id, object_id, cdp_client, session_id, input_coordinates)

	async def get_basic_info(self) -> ElementInfo:
		"""Get basic information about the element including coordinates and properties."""
		return await get_basic_info(self)
