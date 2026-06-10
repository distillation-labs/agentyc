"""Page wrapper for browser target operations."""

from typing import TYPE_CHECKING

from agentyc import logger
from agentyc.browser.keymap import get_key_info

if TYPE_CHECKING:
	from cdp_use.cdp.dom.commands import (
		DescribeNodeParameters,
		QuerySelectorAllParameters,
	)
	from cdp_use.cdp.emulation.commands import SetDeviceMetricsOverrideParameters
	from cdp_use.cdp.input.commands import (
		DispatchKeyEventParameters,
	)
	from cdp_use.cdp.page.commands import CaptureScreenshotParameters, NavigateParameters, NavigateToHistoryEntryParameters
	from cdp_use.cdp.runtime.commands import EvaluateParameters
	from cdp_use.cdp.target.commands import (
		AttachToTargetParameters,
		GetTargetInfoParameters,
	)
	from cdp_use.cdp.target.types import TargetInfo

	from agentyc.browser.session import BrowserSession

	from .element import Element
	from .mouse import Mouse


class Page:
	"""Page operations (tab or iframe)."""

	def __init__(
		self, browser_session: 'BrowserSession', target_id: str, session_id: str | None = None
	):
		self._browser_session = browser_session
		self._client = browser_session.cdp_client
		self._target_id = target_id
		self._session_id: str | None = session_id
		self._mouse: 'Mouse | None' = None

	async def _ensure_session(self) -> str:
		"""Ensure we have a session ID for this target."""
		if not self._session_id:
			params: 'AttachToTargetParameters' = {'targetId': self._target_id, 'flatten': True}
			result = await self._client.send.Target.attachToTarget(params)
			self._session_id = result['sessionId']

			# Enable necessary domains
			import asyncio

			await asyncio.gather(
				self._client.send.Page.enable(session_id=self._session_id),
				self._client.send.DOM.enable(session_id=self._session_id),
				self._client.send.Runtime.enable(session_id=self._session_id),
				self._client.send.Network.enable(session_id=self._session_id),
			)

		return self._session_id

	@property
	async def session_id(self) -> str:
		"""Get the session ID for this target.

		@dev Pass this to an arbitrary CDP call
		"""
		return await self._ensure_session()

	@property
	async def mouse(self) -> 'Mouse':
		"""Get the mouse interface for this target."""
		if not self._mouse:
			session_id = await self._ensure_session()
			from .mouse import Mouse

			self._mouse = Mouse(self._browser_session, session_id, self._target_id)
		return self._mouse

	async def reload(self) -> None:
		"""Reload the target."""
		session_id = await self._ensure_session()
		await self._client.send.Page.reload(session_id=session_id)

	async def get_element(self, backend_node_id: int) -> 'Element':
		"""Get an element by its backend node ID."""
		session_id = await self._ensure_session()

		from .element import Element as Element_

		return Element_(self._browser_session, backend_node_id, session_id)

	async def evaluate(self, page_function: str, *args) -> str:
		"""Execute JavaScript in the target.

		Args:
			page_function: JavaScript code that MUST start with (...args) => format
			*args: Arguments to pass to the function

		Returns:
			String representation of the JavaScript execution result.
			Objects and arrays are JSON-stringified.
		"""
		session_id = await self._ensure_session()

		# Clean and fix common JavaScript string parsing issues
		page_function = self._fix_javascript_string(page_function)

		# Enforce arrow function format
		if not (page_function.startswith('(') and '=>' in page_function):
			raise ValueError(f'JavaScript code must start with (...args) => format. Got: {page_function[:50]}...')

		# Build the expression - call the arrow function with provided args
		if args:
			# Convert args to JSON representation for safe passing
			import json

			arg_strs = [json.dumps(arg) for arg in args]
			expression = f'({page_function})({", ".join(arg_strs)})'
		else:
			expression = f'({page_function})()'

		# Debug: log the actual expression being evaluated
		logger.debug(f'Evaluating JavaScript: {repr(expression)}')

		params: 'EvaluateParameters' = {'expression': expression, 'returnByValue': True, 'awaitPromise': True}
		result = await self._client.send.Runtime.evaluate(
			params,
			session_id=session_id,
		)

		if 'exceptionDetails' in result:
			raise RuntimeError(f'JavaScript evaluation failed: {result["exceptionDetails"]}')

		value = result.get('result', {}).get('value')

		# Always return string representation
		if value is None:
			return ''
		elif isinstance(value, str):
			return value
		else:
			# Convert objects, numbers, booleans to string
			import json

			try:
				return json.dumps(value) if isinstance(value, (dict, list)) else str(value)
			except (TypeError, ValueError):
				return str(value)

	def _fix_javascript_string(self, js_code: str) -> str:
		"""Fix common JavaScript string parsing issues when written as Python string."""

		# Just do minimal, safe cleaning
		js_code = js_code.strip()

		# Only fix the most common and safe issues:

		# 1. Remove obvious Python string wrapper quotes if they exist
		if (js_code.startswith('"') and js_code.endswith('"')) or (js_code.startswith("'") and js_code.endswith("'")):
			# Check if it's a wrapped string (not part of JS syntax)
			inner = js_code[1:-1]
			if inner.count('"') + inner.count("'") == 0 or '() =>' in inner:
				js_code = inner

		# 2. Only fix clearly escaped quotes that shouldn't be
		# But be very conservative - only if we're sure it's a Python string artifact
		if '\\"' in js_code and js_code.count('\\"') > js_code.count('"'):
			js_code = js_code.replace('\\"', '"')
		if "\\'" in js_code and js_code.count("\\'") > js_code.count("'"):
			js_code = js_code.replace("\\'", "'")

		# 3. Basic whitespace normalization only
		js_code = js_code.strip()

		# Final validation - ensure it's not empty
		if not js_code:
			raise ValueError('JavaScript code is empty after cleaning')

		return js_code

	async def screenshot(self, format: str = 'png', quality: int | None = None) -> str:
		"""Take a screenshot and return base64 encoded image.

		Args:
		    format: Image format ('jpeg', 'png', 'webp')
		    quality: Quality 0-100 for JPEG format

		Returns:
		    Base64-encoded image data
		"""
		session_id = await self._ensure_session()

		params: 'CaptureScreenshotParameters' = {'format': format}

		if quality is not None and format.lower() == 'jpeg':
			params['quality'] = quality

		result = await self._client.send.Page.captureScreenshot(params, session_id=session_id)

		return result['data']

	async def press(self, key: str) -> None:
		"""Press a key on the page (sends keyboard input to the focused element or page)."""
		session_id = await self._ensure_session()

		# Handle key combinations like "Control+A"
		if '+' in key:
			parts = key.split('+')
			modifiers = parts[:-1]
			main_key = parts[-1]

			# Calculate modifier bitmask
			modifier_value = 0
			modifier_map = {'Alt': 1, 'Control': 2, 'Meta': 4, 'Shift': 8}
			for mod in modifiers:
				modifier_value |= modifier_map.get(mod, 0)

			# Press modifier keys
			for mod in modifiers:
				code, vk_code = get_key_info(mod)
				params: 'DispatchKeyEventParameters' = {'type': 'keyDown', 'key': mod, 'code': code}
				if vk_code is not None:
					params['windowsVirtualKeyCode'] = vk_code
				await self._client.send.Input.dispatchKeyEvent(params, session_id=session_id)

			# Press main key with modifiers bitmask
			main_code, main_vk_code = get_key_info(main_key)
			main_down_params: 'DispatchKeyEventParameters' = {
				'type': 'keyDown',
				'key': main_key,
				'code': main_code,
				'modifiers': modifier_value,
			}
			if main_vk_code is not None:
				main_down_params['windowsVirtualKeyCode'] = main_vk_code
			await self._client.send.Input.dispatchKeyEvent(main_down_params, session_id=session_id)

			main_up_params: 'DispatchKeyEventParameters' = {
				'type': 'keyUp',
				'key': main_key,
				'code': main_code,
				'modifiers': modifier_value,
			}
			if main_vk_code is not None:
				main_up_params['windowsVirtualKeyCode'] = main_vk_code
			await self._client.send.Input.dispatchKeyEvent(main_up_params, session_id=session_id)

			# Release modifier keys
			for mod in reversed(modifiers):
				code, vk_code = get_key_info(mod)
				release_params: 'DispatchKeyEventParameters' = {'type': 'keyUp', 'key': mod, 'code': code}
				if vk_code is not None:
					release_params['windowsVirtualKeyCode'] = vk_code
				await self._client.send.Input.dispatchKeyEvent(release_params, session_id=session_id)
		else:
			# Simple key press
			code, vk_code = get_key_info(key)
			key_down_params: 'DispatchKeyEventParameters' = {'type': 'keyDown', 'key': key, 'code': code}
			if vk_code is not None:
				key_down_params['windowsVirtualKeyCode'] = vk_code
			await self._client.send.Input.dispatchKeyEvent(key_down_params, session_id=session_id)

			key_up_params: 'DispatchKeyEventParameters' = {'type': 'keyUp', 'key': key, 'code': code}
			if vk_code is not None:
				key_up_params['windowsVirtualKeyCode'] = vk_code
			await self._client.send.Input.dispatchKeyEvent(key_up_params, session_id=session_id)

	async def set_viewport_size(self, width: int, height: int) -> None:
		"""Set the viewport size."""
		session_id = await self._ensure_session()

		params: 'SetDeviceMetricsOverrideParameters' = {
			'width': width,
			'height': height,
			'deviceScaleFactor': 1.0,
			'mobile': False,
		}
		await self._client.send.Emulation.setDeviceMetricsOverride(
			params,
			session_id=session_id,
		)

	# Target properties (from CDP getTargetInfo)
	async def get_target_info(self) -> 'TargetInfo':
		"""Get target information."""
		params: 'GetTargetInfoParameters' = {'targetId': self._target_id}
		result = await self._client.send.Target.getTargetInfo(params)
		return result['targetInfo']

	async def get_url(self) -> str:
		"""Get the current URL."""
		info = await self.get_target_info()
		return info.get('url', '')

	async def get_title(self) -> str:
		"""Get the current title."""
		info = await self.get_target_info()
		return info.get('title', '')

	async def goto(self, url: str) -> None:
		"""Navigate this target to a URL."""
		session_id = await self._ensure_session()

		params: 'NavigateParameters' = {'url': url}
		await self._client.send.Page.navigate(params, session_id=session_id)

	async def navigate(self, url: str) -> None:
		"""Alias for goto."""
		await self.goto(url)

	async def go_back(self) -> None:
		"""Navigate back in history."""
		session_id = await self._ensure_session()

		try:
			# Get navigation history
			history = await self._client.send.Page.getNavigationHistory(session_id=session_id)
			current_index = history['currentIndex']
			entries = history['entries']

			# Check if we can go back
			if current_index <= 0:
				raise RuntimeError('Cannot go back - no previous entry in history')

			# Navigate to the previous entry
			previous_entry_id = entries[current_index - 1]['id']
			params: 'NavigateToHistoryEntryParameters' = {'entryId': previous_entry_id}
			await self._client.send.Page.navigateToHistoryEntry(params, session_id=session_id)

		except Exception as e:
			raise RuntimeError(f'Failed to navigate back: {e}')

	async def go_forward(self) -> None:
		"""Navigate forward in history."""
		session_id = await self._ensure_session()

		try:
			# Get navigation history
			history = await self._client.send.Page.getNavigationHistory(session_id=session_id)
			current_index = history['currentIndex']
			entries = history['entries']

			# Check if we can go forward
			if current_index >= len(entries) - 1:
				raise RuntimeError('Cannot go forward - no next entry in history')

			# Navigate to the next entry
			next_entry_id = entries[current_index + 1]['id']
			params: 'NavigateToHistoryEntryParameters' = {'entryId': next_entry_id}
			await self._client.send.Page.navigateToHistoryEntry(params, session_id=session_id)

		except Exception as e:
			raise RuntimeError(f'Failed to navigate forward: {e}')

	# Element finding methods (these would need to be implemented based on DOM queries)
	async def get_elements_by_css_selector(self, selector: str) -> list['Element']:
		"""Get elements by CSS selector."""
		session_id = await self._ensure_session()

		# Get document first
		doc_result = await self._client.send.DOM.getDocument(session_id=session_id)
		document_node_id = doc_result['root']['nodeId']

		# Query selector all
		query_params: 'QuerySelectorAllParameters' = {'nodeId': document_node_id, 'selector': selector}
		result = await self._client.send.DOM.querySelectorAll(query_params, session_id=session_id)

		elements = []
		from .element import Element as Element_

		# Convert node IDs to backend node IDs
		for node_id in result['nodeIds']:
			# Get backend node ID
			describe_params: 'DescribeNodeParameters' = {'nodeId': node_id}
			node_result = await self._client.send.DOM.describeNode(describe_params, session_id=session_id)
			backend_node_id = node_result['node']['backendNodeId']
			elements.append(Element_(self._browser_session, backend_node_id, session_id))

		return elements
