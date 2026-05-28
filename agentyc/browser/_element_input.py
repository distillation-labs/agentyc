"""Private input helpers for :mod:`agentyc.browser.element`."""

import asyncio
from typing import TYPE_CHECKING

from cdp_use.client import logger

if TYPE_CHECKING:
	from agentyc.browser.element import Element


async def fill_element(element: 'Element', value: str, clear: bool = True) -> None:
	"""Fill an input element using CDP methods with robust focus handling."""
	try:
		cdp_client = element._client
		session_id = element._session_id
		backend_node_id = element._backend_node_id
		input_coordinates = None

		try:
			await cdp_client.send.DOM.scrollIntoViewIfNeeded(params={'backendNodeId': backend_node_id}, session_id=session_id)
			await asyncio.sleep(0.01)
		except Exception as e:
			logger.warning(f'Failed to scroll element into view: {e}')

		result = await cdp_client.send.DOM.resolveNode(
			params={'backendNodeId': backend_node_id},
			session_id=session_id,
		)
		if 'object' not in result or 'objectId' not in result['object']:
			raise RuntimeError('Failed to get object ID for element')
		object_id = result['object']['objectId']

		try:
			bounds_result = await cdp_client.send.Runtime.callFunctionOn(
				params={
					'functionDeclaration': 'function() { return this.getBoundingClientRect(); }',
					'objectId': object_id,
					'returnByValue': True,
				},
				session_id=session_id,
			)
			if bounds_result.get('result', {}).get('value'):
				bounds = bounds_result['result']['value']  # type: ignore[index]
				center_x = bounds['x'] + bounds['width'] / 2
				center_y = bounds['y'] + bounds['height'] / 2
				input_coordinates = {'input_x': center_x, 'input_y': center_y}
				logger.debug(f'Using element coordinates: x={center_x:.1f}, y={center_y:.1f}')
		except Exception as e:
			logger.debug(f'Could not get element coordinates: {e}')

		if session_id is None:
			raise RuntimeError('Session ID is required for fill operation')

		focused_successfully = await focus_element_simple(
			element,
			backend_node_id=backend_node_id,
			object_id=object_id,
			cdp_client=cdp_client,
			session_id=session_id,
			input_coordinates=input_coordinates,
		)
		if not focused_successfully:
			logger.warning('Element focus failed, typing may not reach the intended field')

		if clear:
			cleared_successfully = await clear_text_field(
				element,
				object_id=object_id,
				cdp_client=cdp_client,
				session_id=session_id,
			)
			if not cleared_successfully:
				logger.warning('Text field clearing failed, typing may append to existing text')

		logger.debug(f'Typing text character by character: "[REDACTED {len(value)} chars]"')

		for char in value:
			if char == '\n':
				await cdp_client.send.Input.dispatchKeyEvent(
					params={'type': 'keyDown', 'key': 'Enter', 'code': 'Enter', 'windowsVirtualKeyCode': 13},
					session_id=session_id,
				)
				await asyncio.sleep(0.001)
				await cdp_client.send.Input.dispatchKeyEvent(
					params={'type': 'char', 'text': '\r', 'key': 'Enter'},
					session_id=session_id,
				)
				await cdp_client.send.Input.dispatchKeyEvent(
					params={'type': 'keyUp', 'key': 'Enter', 'code': 'Enter', 'windowsVirtualKeyCode': 13},
					session_id=session_id,
				)
			else:
				modifiers, vk_code, base_key = get_char_modifiers_and_vk(char)
				key_code = get_key_code_for_char(base_key)
				await cdp_client.send.Input.dispatchKeyEvent(
					params={
						'type': 'keyDown',
						'key': base_key,
						'code': key_code,
						'modifiers': modifiers,
						'windowsVirtualKeyCode': vk_code,
					},
					session_id=session_id,
				)
				await asyncio.sleep(0.001)
				await cdp_client.send.Input.dispatchKeyEvent(
					params={'type': 'char', 'text': char, 'key': char},
					session_id=session_id,
				)
				await cdp_client.send.Input.dispatchKeyEvent(
					params={
						'type': 'keyUp',
						'key': base_key,
						'code': key_code,
						'modifiers': modifiers,
						'windowsVirtualKeyCode': vk_code,
					},
					session_id=session_id,
				)

			await asyncio.sleep(0.018)

	except Exception as e:
		raise Exception(f'Failed to fill element: {str(e)}')


def get_char_modifiers_and_vk(char: str) -> tuple[int, int, str]:
	"""Get modifiers, virtual key code, and base key for a character."""
	shift_chars = {
		'!': ('1', 49),
		'@': ('2', 50),
		'#': ('3', 51),
		'$': ('4', 52),
		'%': ('5', 53),
		'^': ('6', 54),
		'&': ('7', 55),
		'*': ('8', 56),
		'(': ('9', 57),
		')': ('0', 48),
		'_': ('-', 189),
		'+': ('=', 187),
		'{': ('[', 219),
		'}': (']', 221),
		'|': ('\\', 220),
		':': (';', 186),
		'"': ("'", 222),
		'<': (',', 188),
		'>': ('.', 190),
		'?': ('/', 191),
		'~': ('`', 192),
	}

	if char in shift_chars:
		base_key, vk_code = shift_chars[char]
		return (8, vk_code, base_key)

	if char.isupper():
		return (8, ord(char), char.lower())

	if char.islower():
		return (0, ord(char.upper()), char)

	if char.isdigit():
		return (0, ord(char), char)

	no_shift_chars = {
		' ': 32,
		'-': 189,
		'=': 187,
		'[': 219,
		']': 221,
		'\\': 220,
		';': 186,
		"'": 222,
		',': 188,
		'.': 190,
		'/': 191,
		'`': 192,
	}
	if char in no_shift_chars:
		return (0, no_shift_chars[char], char)

	return (0, ord(char.upper()) if char.isalpha() else ord(char), char)


def get_key_code_for_char(char: str) -> str:
	"""Get the proper key code for a character."""
	key_codes = {
		' ': 'Space',
		'.': 'Period',
		',': 'Comma',
		'-': 'Minus',
		'_': 'Minus',
		'@': 'Digit2',
		'!': 'Digit1',
		'?': 'Slash',
		':': 'Semicolon',
		';': 'Semicolon',
		'(': 'Digit9',
		')': 'Digit0',
		'[': 'BracketLeft',
		']': 'BracketRight',
		'{': 'BracketLeft',
		'}': 'BracketRight',
		'/': 'Slash',
		'\\': 'Backslash',
		'=': 'Equal',
		'+': 'Equal',
		'*': 'Digit8',
		'&': 'Digit7',
		'%': 'Digit5',
		'$': 'Digit4',
		'#': 'Digit3',
		'^': 'Digit6',
		'~': 'Backquote',
		'`': 'Backquote',
		'"': 'Quote',
		"'": 'Quote',
		'<': 'Comma',
		'>': 'Period',
		'|': 'Backslash',
	}

	if char in key_codes:
		return key_codes[char]
	if char.isalpha():
		return f'Key{char.upper()}'
	if char.isdigit():
		return f'Digit{char}'
	return f'Key{char.upper()}' if char.isascii() and char.isalpha() else 'Unidentified'


async def clear_text_field(element: 'Element', object_id: str, cdp_client, session_id: str) -> bool:
	"""Clear text field using multiple strategies, starting with the most reliable."""
	try:
		logger.debug('Clearing text field using JavaScript value setting')
		await cdp_client.send.Runtime.callFunctionOn(
			params={
				'functionDeclaration': """
					function() {
						try {
							this.select();
						} catch (e) {
						}
						this.value = "";
						this.dispatchEvent(new Event("input", { bubbles: true }));
						this.dispatchEvent(new Event("change", { bubbles: true }));
						return this.value;
					}
				""",
				'objectId': object_id,
				'returnByValue': True,
			},
			session_id=session_id,
		)
		verify_result = await cdp_client.send.Runtime.callFunctionOn(
			params={
				'functionDeclaration': 'function() { return this.value; }',
				'objectId': object_id,
				'returnByValue': True,
			},
			session_id=session_id,
		)
		current_value = verify_result.get('result', {}).get('value', '')
		if not current_value:
			logger.debug('Text field cleared successfully using JavaScript')
			return True
		logger.debug(f'JavaScript clear partially failed, field still contains: "{current_value}"')
	except Exception as e:
		logger.debug(f'JavaScript clear failed: {e}')

	try:
		logger.debug('Fallback: Clearing using triple-click + Delete')
		bounds_result = await cdp_client.send.Runtime.callFunctionOn(
			params={
				'functionDeclaration': 'function() { return this.getBoundingClientRect(); }',
				'objectId': object_id,
				'returnByValue': True,
			},
			session_id=session_id,
		)
		if bounds_result.get('result', {}).get('value'):
			bounds = bounds_result['result']['value']  # type: ignore[index]
			center_x = bounds['x'] + bounds['width'] / 2
			center_y = bounds['y'] + bounds['height'] / 2
			await cdp_client.send.Input.dispatchMouseEvent(
				params={'type': 'mousePressed', 'x': center_x, 'y': center_y, 'button': 'left', 'clickCount': 3},
				session_id=session_id,
			)
			await cdp_client.send.Input.dispatchMouseEvent(
				params={'type': 'mouseReleased', 'x': center_x, 'y': center_y, 'button': 'left', 'clickCount': 3},
				session_id=session_id,
			)
			await cdp_client.send.Input.dispatchKeyEvent(
				params={'type': 'keyDown', 'key': 'Delete', 'code': 'Delete'},
				session_id=session_id,
			)
			await cdp_client.send.Input.dispatchKeyEvent(
				params={'type': 'keyUp', 'key': 'Delete', 'code': 'Delete'},
				session_id=session_id,
			)
			logger.debug('Text field cleared using triple-click + Delete')
			return True
	except Exception as e:
		logger.debug(f'Triple-click clear failed: {e}')

	logger.warning('All text clearing strategies failed')
	return False


async def focus_element_simple(
	element: 'Element',
	backend_node_id: int,
	object_id: str,
	cdp_client,
	session_id: str,
	input_coordinates=None,
) -> bool:
	"""Focus an element using multiple strategies with robust fallbacks."""
	try:
		logger.debug('Focusing element using CDP focus')
		await cdp_client.send.DOM.focus(params={'backendNodeId': backend_node_id}, session_id=session_id)
		logger.debug('Element focused successfully using CDP focus')
		return True
	except Exception as e:
		logger.debug(f'CDP focus failed: {e}, trying JavaScript focus')

	try:
		logger.debug('Focusing element using JavaScript focus')
		await cdp_client.send.Runtime.callFunctionOn(
			params={'functionDeclaration': 'function() { this.focus(); }', 'objectId': object_id},
			session_id=session_id,
		)
		logger.debug('Element focused successfully using JavaScript')
		return True
	except Exception as e:
		logger.debug(f'JavaScript focus failed: {e}, trying click focus')

	try:
		if input_coordinates:
			logger.debug(f'Focusing element by clicking at coordinates: {input_coordinates}')
			center_x = input_coordinates['input_x']
			center_y = input_coordinates['input_y']
			await cdp_client.send.Input.dispatchMouseEvent(
				params={'type': 'mousePressed', 'x': center_x, 'y': center_y, 'button': 'left', 'clickCount': 1},
				session_id=session_id,
			)
			await cdp_client.send.Input.dispatchMouseEvent(
				params={'type': 'mouseReleased', 'x': center_x, 'y': center_y, 'button': 'left', 'clickCount': 1},
				session_id=session_id,
			)
			logger.debug('Element focused using click')
			return True
		logger.debug('No coordinates available for click focus')
	except Exception as e:
		logger.warning(f'All focus strategies failed: {e}')
	return False
